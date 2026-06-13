"""
검증: 레짐별 종목(ETF) 선택이 데이터로 타당한가 — 비중 확대 자산이 실제로 그 레짐에서 유리한가?

질문(2026-06-13): regime_targets는 이론적으로 손으로 배정됐다. 한 번도 "그 레짐에서
  비중을 늘린 자산이 유니버스 내 다른 ETF보다 실제로 유리했는지" 데이터로 확인한 적이 없다.

방법(사용자 합의):
  - 레짐 라벨 = 라이브 acting regime(rule). regime_timing_source=rule이라 final=detect_regime,
    HMM/평활은 비중(blend)만 건드리고 acting regime은 안 바꾸므로 일별로 detect_regime만 호출.
  - 수익 = 동시점(레짐 라벨된 날 보유 수익) 메인 + forward 21일 보조(분류 후행 V자 편향 구분).
  - 대상 = ETF만(개별주식 TSLA/PLTR/NVDA/LLY 제외). KRW ETF는 백테스트 프록시(US ETF)로 측정.
  - 레짐별 각 ETF의 연환산수익·변동성·Sharpe·forward를 측정해 순위. regime_targets가 그
    레짐에서 비중을 늘린 자산군이 상위인지, 0으로 둔 자산군이 하위인지 대조.

한계: 프록시 대체(환율 무시), AVUV·DBMF 2019년 상장(샘플 짧음), Crisis 분류 후행 V자 편향,
  레짐별 샘플 비연속(회복기간·롤링CAGR 적용 곤란 → 연환산수익·Sharpe·forward 사용).
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

START = "2010-01-01"
END = "2025-04-30"
MAIN_REGIMES = ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis"]
FWD_DAYS = 21
EXCLUDE_CLASSES = {"equity_individual", "commodity_krw"}  # 개별주식·합성브릿지 제외
EXCLUDE_TICKERS = {"SGOV"}  # BIL과 동일 프록시(cash 469830와 중복) — cash는 469830으로 대표

ANN = 252


def daily_rule_regime(signal_px, lookback=130, buffer=60):
    """라이브 acting regime(rule)을 일별로 산출."""
    idx = signal_px.index
    out = {}
    min_start = signal_px.index.min() + pd.Timedelta(days=lookback + buffer)
    for as_of in idx:
        if as_of < min_start:
            continue
        sig = signal_px[as_of - pd.Timedelta(days=lookback + buffer):as_of]
        if len(sig) < 30:
            continue
        feats = compute_features(sig)
        out[as_of] = detect_regime(feats)
    return pd.Series(out).sort_index()


def asset_stats(ret, fwd, mask):
    """레짐 마스크 구간의 자산 통계."""
    r = ret[mask].dropna()
    n = len(r)
    if n < 10:
        return None
    ann_ret = float(r.mean() * ANN)
    ann_vol = float(r.std() * np.sqrt(ANN))
    sharpe = ann_ret / ann_vol if ann_vol > 0 else float("nan")
    f = fwd[mask].dropna()
    fwd_ann = float(f.mean() * (ANN / FWD_DAYS)) if len(f) else float("nan")
    return {"n": n, "ann_ret": ann_ret, "ann_vol": ann_vol, "sharpe": sharpe, "fwd_ann": fwd_ann}


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    universe = cfg["universe"]
    targets = cfg["regime_targets"]

    # 대상 티커: ETF만 (개별주식·합성 제외), SGOV 중복 제외
    tickers = [t for t, m in universe.items()
               if m["asset_class"] not in EXCLUDE_CLASSES and t not in EXCLUDE_TICKERS]
    cls_of = {t: universe[t]["asset_class"] for t in tickers}

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=cfg, start=START, end=END, use_cache=True)
    px = universe_px[[t for t in tickers if t in universe_px.columns]].copy()
    present = list(px.columns)

    print("일별 rule 레짐 산출 중...")
    regime = daily_rule_regime(signal_px)
    regime = regime.reindex(px.index).ffill().dropna()

    ret = px.pct_change()
    fwd = px.shift(-FWD_DAYS) / px - 1.0  # 21일 forward 수익

    counts = regime.value_counts()
    print(f"\n레짐별 일수: " + ", ".join(f"{k} {int(v)}" for k, v in counts.items()))
    print(f"대상 ETF({len(present)}): " + ", ".join(present))

    # 자산군별 레짐 비중 (within-class routing은 무시, 자산군 비중 그대로)
    def w(regime_name, ticker):
        return float(targets.get(regime_name, {}).get(cls_of[ticker], 0.0))

    # 자산군 cross-regime 평균(메인 5레짐) → 비중확대 판정 기준
    cls_mean = {}
    for c in set(cls_of.values()):
        cls_mean[c] = np.mean([float(targets[r].get(c, 0.0)) for r in MAIN_REGIMES])

    for rg in MAIN_REGIMES:
        mask = regime == rg
        ndays = int(mask.sum())
        rows = []
        for t in present:
            st = asset_stats(ret[t], fwd[t], mask.reindex(ret.index, fill_value=False))
            if st is None:
                continue
            wt = w(rg, t)
            over = wt - cls_mean[cls_of[t]]
            rows.append({"ticker": t, "class": cls_of[t], "w": wt, "over": over, **st})
        df = pd.DataFrame(rows).sort_values("sharpe", ascending=False).reset_index(drop=True)

        print(f"\n{'='*108}")
        print(f"  [{rg}]  ({ndays}일)  — Sharpe 내림차순. ▲=이 레짐 비중확대(자산군 평균 초과), ·=중립, ▽=축소/제외")
        print(f"{'='*108}")
        print(f"  {'순위':>4}{'티커':>9}{'자산군':>18}{'레짐비중':>9}{'':>3}"
              f"{'연수익':>9}{'변동성':>8}{'Sharpe':>8}{'fwd21연':>9}{'n일':>6}")
        print("  " + "─" * 102)
        for i, r in df.iterrows():
            tag = "▲" if r["over"] > 0.02 else ("▽" if r["w"] <= 0.001 else "·")
            print(f"  {i+1:>4}{r['ticker']:>9}{r['class']:>18}{r['w']:>8.0%}{tag:>3}"
                  f"{r['ann_ret']:>9.1%}{r['ann_vol']:>8.1%}{r['sharpe']:>8.2f}"
                  f"{r['fwd_ann']:>9.1%}{int(r['n']):>6}")

        # 판정 보조: 비중확대(▲) 자산의 Sharpe 순위가 상위 절반인가
        over_rows = df[df["over"] > 0.02]
        if len(over_rows):
            ranks = [df.index[df["ticker"] == t][0] + 1 for t in over_rows["ticker"]]
            med_rank = np.median(ranks)
            half = len(df) / 2
            verdict = "타당(상위권)" if med_rank <= half else "재검토(하위권)"
            print(f"  → 비중확대 자산({', '.join(over_rows['ticker'])}) Sharpe 순위 "
                  f"{ranks} / {len(df)}개 중 (중앙 {med_rank:.0f}) → {verdict}")

    print("\n  주의: 프록시 대체(환율 무시)·in-sample. Crisis는 분류 후행 V자 편향 → 동시점 수익 과대.")
    print("  fwd21연 = 레짐 라벨일 기준 향후 21거래일 수익 연환산(분류 시점 이후 실제 성과).")


if __name__ == "__main__":
    main()
