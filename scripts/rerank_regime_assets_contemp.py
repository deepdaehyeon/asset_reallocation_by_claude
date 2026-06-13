"""
#2 동시점(실현수익) 재순위 — forward 제거, "그 레짐을 들고 있을 때 실제 번 것"만으로.

질문(2026-06-13): #1에서 모델은 예측기가 아니라 늦은 식별기로 확인됐다(라벨 시점엔
  움직임이 이미 끝났고 직후는 평균회귀). 따라서 forward 수익은 *다음 레짐의 회복*을 미리
  빌려와 그 레짐 선택을 과대평가한다(라벨이 바뀌면 이미 다른 비중으로 옮겨가 있으므로
  forward 구간을 그 종목으로 들고 있지 않다). 정직한 척도는 동시점뿐이다.

방법(규칙4 4지표 적용):
  - 레짐 라벨 = 라이브 acting regime(rule, 일별 detect_regime).
  - 각 ETF·레짐별로 그 레짐으로 라벨된 날의 일별 수익만 모아 *레짐 조건부 실현 경로*를
    구성(비연속일을 이어붙임 = "이 레짐일 때만 이 종목을 들었을 때의 체감").
  - 이 경로에 compute_metrics 적용 → Martin(1차)·Ulcer·MaxDD·CAGR·최장UW(보조).
  - Martin 내림차순 순위. regime_targets 비중확대(▲)/제외(▽) 자산이 상위인지 대조.
  - forward는 산출하지 않는다(의도적으로 제거).

한계: 비연속일 이어붙이기 → 레짐 경계의 점프가 경로에 들어감(평균회귀 꼬리 포함).
  하지만 이것이 바로 "그 레짐 동안 든 체감"이라 동시점 척도로는 정당. 프록시·in-sample.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from data import load_all_prices  # noqa: E402
from features import compute_features  # noqa: E402
from regime import detect_regime  # noqa: E402
from metrics import compute_metrics, recovery_duration  # noqa: E402

START = "2010-01-01"
END = "2025-04-30"
MAIN_REGIMES = ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis"]
EXCLUDE_CLASSES = {"equity_individual", "commodity_krw"}
EXCLUDE_TICKERS = {"SGOV"}


def daily_rule_regime(signal_px, lookback=130, buffer=60):
    idx = signal_px.index
    out = {}
    min_start = signal_px.index.min() + pd.Timedelta(days=lookback + buffer)
    for as_of in idx:
        if as_of < min_start:
            continue
        sig = signal_px[as_of - pd.Timedelta(days=lookback + buffer):as_of]
        if len(sig) < 30:
            continue
        out[as_of] = detect_regime(compute_features(sig))
    return pd.Series(out).sort_index()


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    universe = cfg["universe"]
    targets = cfg["regime_targets"]

    tickers = [t for t, m in universe.items()
               if m["asset_class"] not in EXCLUDE_CLASSES and t not in EXCLUDE_TICKERS]
    cls_of = {t: universe[t]["asset_class"] for t in tickers}

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=cfg, start=START, end=END, use_cache=True)
    px = universe_px[[t for t in tickers if t in universe_px.columns]].copy()
    present = list(px.columns)

    print("일별 rule 레짐 산출 중...")
    regime = daily_rule_regime(signal_px).reindex(px.index).ffill().dropna()
    ret = px.pct_change().reindex(regime.index)

    cls_mean = {}
    for c in set(cls_of.values()):
        cls_mean[c] = np.mean([float(targets[r].get(c, 0.0)) for r in MAIN_REGIMES])

    def w(rg, t):
        return float(targets.get(rg, {}).get(cls_of[t], 0.0))

    counts = regime.value_counts()
    print(f"\n레짐별 일수: " + ", ".join(f"{k} {int(v)}" for k, v in counts.items()))
    print(f"대상 ETF({len(present)}): " + ", ".join(present))
    print("\n  ※ forward 제거. 각 셀은 '그 레짐일 때만 이 종목을 든' 실현 경로의 동시점 4지표.")

    for rg in MAIN_REGIMES:
        mask = regime == rg
        ndays = int(mask.sum())
        rows = []
        for t in present:
            r = ret[t][mask].dropna()
            if len(r) < 20:
                continue
            m = compute_metrics(r)
            if not m:
                continue
            rec = recovery_duration(r)
            wt = w(rg, t)
            rows.append({
                "ticker": t, "class": cls_of[t], "w": wt,
                "over": wt - cls_mean[cls_of[t]],
                "cagr": m["cagr"], "ulcer": m["ulcer"], "martin": m["martin"],
                "maxdd": m["max_drawdown"], "uw": rec["max_underwater_days"],
                "n": m["n_days"],
            })
        df = pd.DataFrame(rows).sort_values("martin", ascending=False).reset_index(drop=True)

        print(f"\n{'='*112}")
        print(f"  [{rg}]  ({ndays}일)  — Martin 내림차순(규칙4 1차). "
              f"▲=비중확대  ·=중립  ▽=제외/축소  (forward 없음, 동시점 실현경로)")
        print(f"{'='*112}")
        print(f"  {'순위':>4}{'티커':>9}{'자산군':>18}{'레짐비중':>9}{'':>3}"
              f"{'CAGR':>9}{'Ulcer':>8}{'Martin':>8}{'MaxDD':>9}{'최장UW':>8}{'n일':>6}")
        print("  " + "─" * 106)
        for i, r in df.iterrows():
            tag = "▲" if r["over"] > 0.02 else ("▽" if r["w"] <= 0.001 else "·")
            print(f"  {i+1:>4}{r['ticker']:>9}{r['class']:>18}{r['w']:>8.0%}{tag:>3}"
                  f"{r['cagr']:>9.1%}{r['ulcer']:>8.1f}{r['martin']:>8.2f}"
                  f"{r['maxdd']:>9.1%}{int(r['uw']):>7}d{int(r['n']):>6}")

        over_rows = df[df["over"] > 0.02]
        if len(over_rows):
            ranks = [df.index[df["ticker"] == t][0] + 1 for t in over_rows["ticker"]]
            med = np.median(ranks)
            half = len(df) / 2
            verdict = "타당(상위권)" if med <= half else "재검토(하위권)"
            print(f"  → 비중확대({', '.join(over_rows['ticker'])}) Martin순위 {ranks}"
                  f" / {len(df)}개 중 (중앙 {med:.0f}) → {verdict}")

    print("\n  주의: Martin은 cash류 저변동 자산을 과대평가(분모 효과) → CAGR·MaxDD 함께 해석.")
    print("  비연속일 이어붙이기라 레짐 경계 점프(평균회귀 꼬리) 포함 = 그 레짐 든 동안의 정직한 체감.")


if __name__ == "__main__":
    main()
