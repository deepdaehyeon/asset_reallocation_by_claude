"""
Walk-forward robustness 분석.

본 세션의 모든 변경은 단일 백테스트(2010-2025) 기반.
in-sample bias 위험 (특히 regime_targets 조정)을 sub-period 분할로 검증.

분석:
  1. 5년 sub-period 3개: 2010-15, 2015-20, 2020-25
  2. 3년 sliding window: 매년 윈도우 (13개)
  3. 연도별 metric
  4. 안정성 판정 (CV = std / mean)

사용:
  python scripts/walk_forward_robustness.py [--start 2010-01-01] [--end 2025-04-30]
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

from fetcher import fetch_fred_history  # noqa: E402
from data import load_all_prices  # noqa: E402
from engine import BacktestEngine  # noqa: E402


def _annualized_metrics(returns: pd.Series) -> dict:
    """단순 metric (compute_metrics 의존성 줄이고 sub-period 안전)."""
    if len(returns) < 5:
        return {"cagr": 0, "vol": 0, "sharpe": 0, "max_drawdown": 0, "calmar": 0}
    daily_mean = returns.mean()
    daily_std = returns.std()
    cagr = (1 + returns).prod() ** (252 / len(returns)) - 1
    vol = daily_std * np.sqrt(252)
    sharpe = (daily_mean * 252) / vol if vol > 0 else 0
    cumret = (1 + returns).cumprod()
    drawdown = (cumret - cumret.cummax()) / cumret.cummax()
    max_dd = drawdown.min()
    calmar = cagr / abs(max_dd) if max_dd < 0 else 0
    return {
        "cagr": float(cagr),
        "vol": float(vol),
        "sharpe": float(sharpe),
        "max_drawdown": float(max_dd),
        "calmar": float(calmar),
    }


def _fmt(m: dict) -> str:
    return (f"CAGR {m['cagr']:+.1%} | Sharpe {m['sharpe']:>5.2f} | "
            f"MaxDD {m['max_drawdown']:>6.1%} | Calmar {m['calmar']:>5.2f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--end", default="2025-04-30")
    args = p.parse_args()

    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)

    print(f"[1] 백테스트 (drift 1.5%, 현재 라이브 config) [{args.start} ~ {args.end}]")
    uni, sig = load_all_prices(config=cfg, start=args.start, end=args.end, use_cache=True)
    fh = fetch_fred_history(args.start, args.end)
    engine = BacktestEngine(config=cfg, universe_px=uni, signal_px=sig,
        start=args.start, end=args.end,
        drift_threshold=0.015, cooldown_days=0,
        rebal_freq="W-FRI", tx_cost=0.001, fred_history=fh)
    result = engine.run()
    full = _annualized_metrics(result["returns"])
    print(f"  전체 기간: {_fmt(full)}")

    rets = result["returns"]

    # ── 5년 sub-period 3개 ──────────────────────────────────────────────
    print(f"\n[2] 5년 sub-period 3개")
    print(f"{'='*70}")
    sub5 = [
        ("2010-01 ~ 2014-12", "2010-01-01", "2014-12-31"),
        ("2015-01 ~ 2019-12", "2015-01-01", "2019-12-31"),
        ("2020-01 ~ 2024-12", "2020-01-01", "2024-12-31"),
    ]
    for label, s, e in sub5:
        sub = rets.loc[s:e]
        if len(sub) > 0:
            m = _annualized_metrics(sub)
            print(f"  {label}: {_fmt(m)}")

    # ── 3년 sliding window ───────────────────────────────────────────────
    print(f"\n[3] 3년 sliding window (1년 step)")
    print(f"{'='*70}")
    sliding = []
    for start_year in range(2010, 2023):
        s = f"{start_year}-01-01"
        e = f"{start_year + 2}-12-31"
        sub = rets.loc[s:e]
        if len(sub) < 252:
            continue
        m = _annualized_metrics(sub)
        sliding.append((f"{start_year}-{start_year+2}", m))
        print(f"  {start_year}-{start_year+2}: {_fmt(m)}")

    # ── 연도별 ─────────────────────────────────────────────────────────────
    print(f"\n[4] 연도별 metric")
    print(f"{'='*70}")
    annual = []
    for year in range(2010, 2026):
        s = f"{year}-01-01"
        e = f"{year}-12-31"
        sub = rets.loc[s:e]
        if len(sub) < 100:
            continue
        m = _annualized_metrics(sub)
        annual.append((year, m))
        print(f"  {year}: {_fmt(m)}")

    # ── 안정성 판정 ────────────────────────────────────────────────────────
    print(f"\n[5] 안정성 판정 (CV = std / |mean|)")
    print(f"{'='*70}")
    for fld, fname in [("sharpe", "Sharpe"), ("cagr", "CAGR"), ("max_drawdown", "MaxDD")]:
        # sliding window 기준
        vals = [m[fld] for _, m in sliding]
        mean = np.mean(vals)
        std = np.std(vals)
        cv = std / abs(mean) if mean != 0 else float("inf")
        # 연도별 기준
        ann_vals = [m[fld] for _, m in annual]
        ann_mean = np.mean(ann_vals)
        ann_std = np.std(ann_vals)
        ann_cv = ann_std / abs(ann_mean) if ann_mean != 0 else float("inf")

        print(f"  {fname:>8}: sliding mean {mean:+.3f} std {std:.3f} CV {cv:.2f} | "
              f"annual mean {ann_mean:+.3f} std {ann_std:.3f} CV {ann_cv:.2f}")

    # ── 핵심 진단 ──────────────────────────────────────────────────────────
    print(f"\n[6] 안정성 평가")
    print(f"{'='*70}")
    sharpe_vals_slide = [m["sharpe"] for _, m in sliding]
    sharpe_vals_ann = [m["sharpe"] for _, m in annual]
    n_negative_slide = sum(1 for s in sharpe_vals_slide if s < 0)
    n_negative_ann = sum(1 for s in sharpe_vals_ann if s < 0)
    print(f"  3년 sliding window 중 Sharpe < 0: {n_negative_slide}/{len(sliding)}")
    print(f"  연도별 Sharpe < 0: {n_negative_ann}/{len(annual)}")
    print(f"  연도별 Sharpe 최저: {min(sharpe_vals_ann):.2f} ({annual[sharpe_vals_ann.index(min(sharpe_vals_ann))][0]})")
    print(f"  연도별 Sharpe 최고: {max(sharpe_vals_ann):.2f} ({annual[sharpe_vals_ann.index(max(sharpe_vals_ann))][0]})")

    # 첫 5년 vs 마지막 5년 비교 (in-sample bias 검증)
    print()
    print(f"  ── 첫 5년 (2010-14) vs 마지막 5년 (2020-24) ──")
    for fld, fname in [("sharpe", "Sharpe"), ("cagr", "CAGR"), ("max_drawdown", "MaxDD"), ("calmar", "Calmar")]:
        first = _annualized_metrics(rets.loc["2010-01-01":"2014-12-31"])[fld]
        last = _annualized_metrics(rets.loc["2020-01-01":"2024-12-31"])[fld]
        diff = last - first
        print(f"    {fname:>8}: 첫 {first:+.3f} → 마지막 {last:+.3f}  Δ {diff:+.3f}")


if __name__ == "__main__":
    main()
