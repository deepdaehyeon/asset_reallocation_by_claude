"""
신뢰도 결합 산식 비교 백테스트 — 외부 비평 #4 후속 개선.

3가지 method를 동일 백테스트 + 동일 진단으로 비교:
  mean    : (rule + hmm) / 2    (기존)
  min     : min(rule, hmm)      (보수)
  product : rule * hmm          (보수)

평가 metric:
  - Spearman ρ (confidence vs ensemble↔rule 일치율): 단조성 회복 여부 (#4 본질)
  - 백테스트 Sharpe / MaxDD: 실제 성과 영향
  - 0.40 threshold에서 fallback rate
  - bin별 accuracy 분포

사용:
  python scripts/compare_confidence_methods.py [--start 2010-01-01] [--end 2025-04-30]
"""
from __future__ import annotations

import argparse
import sys
import warnings
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from fetcher import fetch_fred_history  # noqa: E402
from data import load_all_prices  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from metrics import compute_metrics  # noqa: E402


METHODS = ["mean", "min", "product"]


def run_one(method: str, base_config: dict, universe_px, signal_px, args, fred_history):
    cfg = deepcopy(base_config)
    cfg.setdefault("regime_filter", {})["confidence_method"] = method
    print(f"\n{'=' * 60}\n  method = {method}\n{'=' * 60}")
    engine = BacktestEngine(
        config=cfg, universe_px=universe_px, signal_px=signal_px,
        start=args.start, end=args.end, rebal_freq="W-FRI", tx_cost=0.001,
        fred_history=fred_history,
    )
    result = engine.run()
    m = compute_metrics(result["returns"])

    # 진단
    df = result.copy()
    df = df[df["combined_conf"].notna()].copy()
    df["correct"] = (df["regime"] == df["rule_regime"]).astype(int)
    df["bin"] = pd.cut(df["combined_conf"], bins=10, include_lowest=True)
    grouped = df.groupby("bin", observed=True).agg(
        n=("correct", "size"),
        acc=("correct", lambda x: float(x.mean())),
        conf=("combined_conf", "mean"),
    ).reset_index()
    # Spearman 단조성
    rho, _ = spearmanr(grouped["conf"], grouped["acc"]) if len(grouped) >= 3 else (float("nan"), 1.0)

    # threshold 0.40 fallback rate (라이브와 동일)
    threshold = 0.40
    df["below"] = (df["combined_conf"] < threshold).astype(int)
    fallback_rate = float(df["below"].mean())

    print(
        f"  CAGR {m.get('cagr',0):+.1%} | "
        f"Sharpe {m.get('sharpe',0):.2f} | "
        f"MaxDD {m.get('max_drawdown',0):.1%} | "
        f"Calmar {m.get('calmar',0):.2f}"
    )
    print(f"  Spearman ρ (conf vs acc): {rho:+.3f}")
    print(f"  fallback rate (conf < {threshold}): {fallback_rate:.1%}")
    print("\n  bin별 accuracy:")
    for _, r in grouped.iterrows():
        bar = "█" * int(r["acc"] * 20)
        print(f"    {str(r['bin']):<18} n={int(r['n']):>4}  acc={r['acc']:.0%}  {bar}")

    return {
        "method": method,
        "metrics": m,
        "spearman_rho": float(rho),
        "fallback_rate": fallback_rate,
        "bin_accuracy": grouped.to_dict("records"),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--end", default="2025-04-30")
    args = p.parse_args()

    cfg_path = ROOT / "trading" / "config.yaml"
    with open(cfg_path) as f:
        base_config = yaml.safe_load(f)

    print(f"[1] 데이터 로딩  [{args.start} ~ {args.end}]")
    universe_px, signal_px = load_all_prices(
        config=base_config, start=args.start, end=args.end, use_cache=True
    )
    fred_history = fetch_fred_history(args.start, args.end)
    if not fred_history.empty:
        print(f"    FRED 매크로 {len(fred_history.columns)}개 포함")

    results = []
    for m in METHODS:
        results.append(run_one(m, base_config, universe_px, signal_px, args, fred_history))

    # ── 요약 비교 ────────────────────────────────────────────────────────
    print(f"\n{'=' * 70}\n  비교 요약\n{'=' * 70}")
    print(f"  {'method':<10}{'Sharpe':>10}{'MaxDD':>10}{'Calmar':>10}{'ρ':>10}{'fallback':>12}")
    print("  " + "-" * 62)
    for r in results:
        m = r["metrics"]
        print(
            f"  {r['method']:<10}"
            f"{m.get('sharpe',0):>10.3f}"
            f"{m.get('max_drawdown',0):>10.2%}"
            f"{m.get('calmar',0):>10.3f}"
            f"{r['spearman_rho']:>+10.3f}"
            f"{r['fallback_rate']:>12.1%}"
        )

    # ── 권장 ──────────────────────────────────────────────────────────────
    # 1순위: 단조성 (ρ 양수 → 단조 회복)
    # 2순위: Sharpe 동등 이상, MaxDD 악화 ≤2pp
    baseline = next(r for r in results if r["method"] == "mean")
    print(f"\n  {'─' * 68}")
    print(f"  baseline (mean): ρ {baseline['spearman_rho']:+.3f}, "
          f"Sharpe {baseline['metrics'].get('sharpe',0):.2f}, "
          f"MaxDD {baseline['metrics'].get('max_drawdown',0):.1%}")

    cands = []
    for r in results:
        if r["method"] == "mean":
            continue
        rho_gain = r["spearman_rho"] - baseline["spearman_rho"]
        sharpe_diff = r["metrics"].get("sharpe", 0) - baseline["metrics"].get("sharpe", 0)
        mdd_diff = r["metrics"].get("max_drawdown", 0) - baseline["metrics"].get("max_drawdown", 0)
        cands.append((r["method"], rho_gain, sharpe_diff, mdd_diff, r))

    print()
    for method, rho_gain, sharpe_diff, mdd_diff, _ in cands:
        ok_rho = rho_gain > 0
        ok_sharpe = sharpe_diff >= -0.05
        ok_mdd = mdd_diff >= -0.02
        verdict = "✓ 채택 가능" if (ok_rho and ok_sharpe and ok_mdd) else "✗ 채택 보류"
        print(
            f"  {method:<8} vs mean: "
            f"Δρ {rho_gain:+.3f} | "
            f"ΔSharpe {sharpe_diff:+.3f} | "
            f"ΔMaxDD {mdd_diff:+.2%}  → {verdict}"
        )


if __name__ == "__main__":
    main()
