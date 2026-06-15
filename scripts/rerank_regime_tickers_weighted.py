"""
레짐별 *종목(티커)* 1~N위 + 종목 유효비중 — 동시점 실현수익(forward 제거).

질문(2026-06-15): 사용자 "비중이랑 종목 선택이 잘못된 것 같다, 레짐별 1~10위를 종목 비율과
  함께 보여달라". #2(rerank, 종목순위)·#3(contrast, 자산군)을 합쳐, 각 레짐에서 모든 종목을
  동시점 Martin으로 순위 매기고 *종목 유효비중*(=regime_targets[class] × asset_routing 내
  종목배분)을 나란히 표기한다. 이번엔 개별주식(equity_individual)도 포함(선택 검증 목적).

방법(규칙4):
  - 레짐 = 라이브 acting regime(rule, 일별 detect_regime).
  - 각 티커·레짐별로 그 레짐 라벨된 날의 일별수익만 이어붙인 실현경로 → compute_metrics.
  - 종목 유효비중 w_eff = targets[rg][class] × routing[class][ticker].
  - Martin 내림차순. forward 산출 안 함(늦은 식별기라 forward는 다음 레짐 회복 차용 착시).

한계: 개별주식·일부 ETF는 상장 짧음(PLTR 2020·DBMF 2019·AVUV 2019) → n일 함께 표기.
  비연속일 이어붙이기(레짐 경계 평균회귀 꼬리 포함). cash류 Martin 분모왜곡. 프록시·in-sample.
  종목 단독 동시점이라 상관·분산 미반영(단독 약함≠제거).
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

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
EXCLUDE_CLASSES = {"commodity_krw", "cash_usd"}  # 합성브릿지·USD현금중복 제외, 개별주식은 포함
LOWVOL_CLASSES = {"cash"}  # Martin 분모왜곡 → 별도 표기


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
    routing = cfg["asset_routing"]
    targets = cfg["regime_targets"]

    tickers = [t for t, m in universe.items() if m["asset_class"] not in EXCLUDE_CLASSES]
    cls_of = {t: universe[t]["asset_class"] for t in tickers}

    def w_eff(rg, t):
        c = cls_of[t]
        cls_w = float(targets.get(rg, {}).get(c, 0.0))
        within = float(routing.get(c, {}).get(t, 1.0 if len(routing.get(c, {})) == 0 else 0.0))
        return cls_w * within

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=cfg, start=START, end=END, use_cache=True)
    px = universe_px[[t for t in tickers if t in universe_px.columns]].copy()
    present = list(px.columns)

    print("일별 rule 레짐 산출 중...")
    regime = daily_rule_regime(signal_px).reindex(px.index).ffill().dropna()
    ret = px.pct_change().reindex(regime.index)

    counts = regime.value_counts()
    print("\n레짐별 일수: " + ", ".join(f"{k} {int(v)}" for k, v in counts.items()))

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
            rows.append({
                "ticker": t, "class": cls_of[t], "weff": w_eff(rg, t),
                "name": universe[t].get("name", "")[:16],
                "cagr": m["cagr"], "martin": m["martin"], "maxdd": m["max_drawdown"],
                "uw": rec["max_underwater_days"], "n": m["n_days"],
                "lowvol": cls_of[t] in LOWVOL_CLASSES,
            })
        df = pd.DataFrame(rows).sort_values("martin", ascending=False).reset_index(drop=True)
        tot_w = df["weff"].sum()

        print(f"\n{'='*116}")
        print(f"  [{rg}]  ({ndays}일)  — 종목 Martin 내림차순. 종목비중=자산군비중×라우팅. "
              f"표시종목 합계 {tot_w:.0%}")
        print(f"{'='*116}")
        print(f"  {'순위':>4}{'티커':>9}{'자산군':>18}{'종목비중':>9}"
              f"{'CAGR':>9}{'Martin':>8}{'MaxDD':>9}{'최장UW':>8}{'n일':>6}  비고")
        print("  " + "─" * 110)
        for i, r in df.iterrows():
            note = "저변동(Martin왜곡)" if r["lowvol"] else ""
            wtag = f"{r['weff']:>8.1%}" if r["weff"] > 0.0005 else f"{'0%':>9}"
            print(f"  {i+1:>4}{r['ticker']:>9}{r['class']:>18}{wtag}"
                  f"{r['cagr']:>9.1%}{r['martin']:>8.2f}{r['maxdd']:>9.1%}"
                  f"{int(r['uw']):>7}d{int(r['n']):>6}  {note}")

    print("\n  주의: 종목 단독 동시점(상관·분산 미반영, 단독약함≠제거). forward 제거(늦은 식별기).")
    print("  개별주식·신상장 ETF는 n일 작음(표본부족). cash류는 Martin 분모왜곡 → CAGR·MaxDD 병행.")


if __name__ == "__main__":
    main()
