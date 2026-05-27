"""
detect_regime 임계 시뮬레이션 비교 — 외부 비평 #6-a (forward return 분리도 회복).

진단에서 발견된 문제:
  - 현재 Goldilocks: 1773일에 평균 forward 21일 수익률 +0.71% (5개 레짐 중 최하)
  - Reflation +1.25%, Stagflation +0.88%, Slowdown +0.82% 모두 더 높음
  - Goldilocks 정의가 너무 광범위(growth>=2 + infl>=1)해 평균을 흡수.

본 스크립트는 코드 변경 없이 시뮬레이션으로 분리도 개선 여부 검증:
  1) walk-forward로 features 시계열 생성 (compute_features + FRED ffill)
  2) 시나리오별 임계로 detect_regime 재적용
  3) 각 시나리오의 레짐별 forward 21일 수익률 분리도 측정
  4) 의미 있는 후보 추천 → 채택 시 코드 변경 후 본격 백테스트

시나리오:
  baseline       : 현재 (growth>=2, infl_low>=1)
  strict_gold    : Goldilocks growth>=3
  very_strict    : Goldilocks growth>=3, infl_low>=2
  flip_fallback  : 혼재 fallback 'Goldilocks' → 'Slowdown' (지금은 strict 아닌 케이스가 Goldilocks로)
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from features import compute_features  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
from data import load_all_prices  # noqa: E402


REGIMES = ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis"]


def _signals(features: dict) -> tuple[int, int, int, int]:
    """regime._growth_inflation_signals와 동일 임계."""
    mom1m  = features["momentum_1m"]
    mom3m  = features["momentum_3m"]
    vix    = features["vix"]
    credit = features["credit_signal"]
    hy_spr = features.get("hy_spread", 4.5)
    curve  = features.get("curve_10y2y", 0.5)
    commod = features.get("commodity_mom_1m", 0.0)

    growth_bullish = sum([mom1m > 0.02, mom3m > 0.03, credit > 0.01, curve > 1.0])
    growth_bearish = sum([mom1m < -0.02, mom3m < -0.03, credit < -0.02, curve < 0.0])
    infl_rising    = sum([hy_spr > 5.0, vix > 25, commod > 0.05])
    infl_low       = sum([hy_spr < 4.0, vix < 18, commod < -0.05])
    return growth_bullish, growth_bearish, infl_rising, infl_low


def detect_with(features: dict, *,
                gold_growth_min: int = 2,
                gold_infl_low_min: int = 1,
                fallback_to: str = "Goldilocks") -> str:
    """현재 detect_regime을 임계 customizable 버전으로 재구현."""
    rvol = features["realized_vol"]
    vix = features["vix"]
    if rvol > 0.30 or vix > 40:
        return "Crisis"

    gb, gB, ir, il = _signals(features)

    if gB >= 2 and ir >= 1:
        return "Stagflation"
    if gB >= 2:
        return "Slowdown"
    if gb >= gold_growth_min and il >= gold_infl_low_min:
        return "Goldilocks"
    if gb >= 2 and ir >= 1:
        return "Reflation"
    if gB >= 1:
        return "Slowdown"
    return fallback_to


SCENARIOS = [
    {"label": "baseline",       "gold_growth_min": 2, "gold_infl_low_min": 1, "fallback_to": "Goldilocks"},
    {"label": "strict_gold",    "gold_growth_min": 3, "gold_infl_low_min": 1, "fallback_to": "Goldilocks"},
    {"label": "very_strict",    "gold_growth_min": 3, "gold_infl_low_min": 2, "fallback_to": "Goldilocks"},
    {"label": "flip_fallback",  "gold_growth_min": 2, "gold_infl_low_min": 1, "fallback_to": "Slowdown"},
]


def build_features_series(signal_px: pd.DataFrame,
                          fred_history: pd.DataFrame,
                          start: str, end: str) -> pd.DataFrame:
    """walk-forward로 일별 features 계산."""
    dates = signal_px.loc[start:end].index
    # fred ffill 결합용
    fh_aligned = (
        fred_history.reindex(dates, method="ffill").ffill(limit=45)
        if fred_history is not None and not fred_history.empty else None
    )

    rows = []
    for i, date in enumerate(dates):
        sig = signal_px.loc[:date].tail(70)
        if len(sig) < 30:
            continue
        try:
            feat = compute_features(sig)
        except Exception:
            continue
        # FRED 매크로 합류 (compute_features는 라이브 fetch_fred_data 의존이라 우회)
        if fh_aligned is not None and date in fh_aligned.index:
            row = fh_aligned.loc[date]
            for c in fh_aligned.columns:
                v = row[c]
                if not pd.isna(v):
                    feat[c] = float(v)
        feat["date"] = date
        rows.append(feat)
    df = pd.DataFrame(rows).set_index("date")
    return df


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--end", default="2025-04-30")
    p.add_argument("--forward-window", type=int, default=21)
    args = p.parse_args()

    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)

    print(f"[1] 데이터 로딩 [{args.start} ~ {args.end}]")
    universe_px, signal_px = load_all_prices(
        config=cfg, start=args.start, end=args.end, use_cache=True
    )
    fred_history = fetch_fred_history(args.start, args.end)
    print(f"    FRED 매크로 {len(fred_history.columns) if not fred_history.empty else 0}개")

    print("[2] features walk-forward 계산 중...")
    fdf = build_features_series(signal_px, fred_history, args.start, args.end)
    print(f"    {len(fdf)}일")

    # 미래 21일 SPY 수익률
    spy = signal_px["SPY"].reindex(fdf.index).ffill()
    spy_fwd = spy.pct_change(args.forward_window).shift(-args.forward_window)
    fdf["fwd_ret"] = spy_fwd

    print("[3] 시나리오 적용 + 분리도 측정")
    summary_rows = []
    for sc in SCENARIOS:
        label = sc.pop("label")
        regimes = [detect_with(r.to_dict(), **sc) for _, r in fdf.iterrows()]
        fdf[label] = regimes

        # 레짐별 forward 수익률 분리도
        print(f"\n  ── {label} ──────────────────────────────────────")
        for r in REGIMES:
            mask = fdf[label] == r
            sub = fdf.loc[mask, "fwd_ret"].dropna()
            if len(sub) == 0:
                continue
            mean_pct = sub.mean() * 100
            std_pct = sub.std() * 100
            sharpe = (sub.mean() / sub.std() * np.sqrt(252 / args.forward_window)
                      if sub.std() > 0 else float("nan"))
            print(f"    {r:<12} n={int(mask.sum()):>5}  "
                  f"mean={mean_pct:+.2f}%  std={std_pct:.2f}%  "
                  f"Sharpe={sharpe:>+5.2f}")
            summary_rows.append({
                "scenario": label, "regime": r,
                "n": int(mask.sum()), "mean_pct": mean_pct,
                "std_pct": std_pct, "sharpe": sharpe,
            })

    # ── Goldilocks 분리도 비교 ─────────────────────────────────────────
    print(f"\n{'=' * 70}\n  Goldilocks fwd_mean 비교 (현재 Goldilocks가 최하인 문제 추적)\n{'=' * 70}")
    sdf = pd.DataFrame(summary_rows)
    for scenario in [s for s in sdf["scenario"].unique()]:
        sub = sdf[sdf["scenario"] == scenario].set_index("regime")
        if "Goldilocks" not in sub.index:
            continue
        gold = sub.loc["Goldilocks", "mean_pct"]
        others = [r for r in REGIMES if r != "Goldilocks" and r in sub.index]
        avg_others = sub.loc[others, "mean_pct"].mean()
        delta = gold - avg_others
        rank = (sub["mean_pct"].rank(ascending=False).loc["Goldilocks"])
        print(f"  {scenario:<14}: Goldilocks mean {gold:+.2f}%  "
              f"vs 평균(other) {avg_others:+.2f}%  "
              f"Δ {delta:+.2f}pp  순위 {int(rank)}/{len(sub)}")


if __name__ == "__main__":
    main()
