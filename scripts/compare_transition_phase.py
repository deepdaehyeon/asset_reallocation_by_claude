"""
A안 검증 — Transition 비중 phase의 N(일수) sensitivity.

new confirmed regime 진입 후 N일 동안 regime_targets['Transition'] 비중을 사용,
N일 경과 후 본 레짐 비중으로 복귀.

비교:
  N=0   비활성 (baseline, 현재 동작)
  N=7   짧은 transition phase
  N=14  중간
  N=21  길게
"""
from __future__ import annotations

import argparse
import sys
import warnings
from copy import deepcopy
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from fetcher import fetch_fred_history  # noqa: E402
from data import load_all_prices  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from metrics import compute_metrics, regime_classification_metrics  # noqa: E402


def run_one(n: int, base_config, uni, sig, args, fh):
    cfg = deepcopy(base_config)
    cfg.setdefault("regime_filter", {})["transition_days"] = n
    print(f"\n{'='*60}\n  transition_days={n}\n{'='*60}")
    engine = BacktestEngine(
        config=cfg, universe_px=uni, signal_px=sig,
        start=args.start, end=args.end, rebal_freq="W-FRI", tx_cost=0.001,
        fred_history=fh,
    )
    result = engine.run()
    m = compute_metrics(result["returns"])
    print(f"  CAGR {m['cagr']:+.1%} | Sharpe {m['sharpe']:.3f} | "
          f"MaxDD {m['max_drawdown']:.2%} | Calmar {m['calmar']:.3f}")

    cm = regime_classification_metrics(
        result["rule_regime"], result["regime"], result["returns"]
    )
    mc = cm.get("miss_cost", {})
    print(f"  위험 미감지: {mc.get('miss_days',0)}/{mc.get('total_days',0)} "
          f"평균 {mc.get('avg_daily_return',0):+.3%}")
    return {"n": n, "metrics": m, "miss": mc}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--end", default="2025-04-30")
    p.add_argument("--ns", default="0,7,14,21")
    args = p.parse_args()
    ns = [int(x) for x in args.ns.split(",")]

    with open(ROOT / "trading" / "config.yaml") as f:
        base_config = yaml.safe_load(f)

    print(f"[1] 데이터 로딩 [{args.start} ~ {args.end}]")
    uni, sig = load_all_prices(config=base_config, start=args.start, end=args.end, use_cache=True)
    fh = fetch_fred_history(args.start, args.end)

    results = [run_one(n, base_config, uni, sig, args, fh) for n in ns]

    print(f"\n{'='*70}\n  비교 요약\n{'='*70}")
    print(f"  {'N':<6}{'Sharpe':>10}{'MaxDD':>10}{'Calmar':>10}{'CAGR':>10}{'miss':>8}")
    print("  " + "-" * 54)
    for r in results:
        m = r["metrics"]
        print(f"  {r['n']:<6}"
              f"{m['sharpe']:>10.3f}"
              f"{m['max_drawdown']:>10.2%}"
              f"{m['calmar']:>10.3f}"
              f"{m['cagr']:>+10.2%}"
              f"{r['miss'].get('miss_days',0):>8}")

    baseline = results[0]
    bm = baseline["metrics"]
    print(f"\n  baseline N={baseline['n']}: Sharpe {bm['sharpe']:.3f}, MaxDD {bm['max_drawdown']:.2%}")
    for r in results[1:]:
        m = r["metrics"]
        dsh = m["sharpe"] - bm["sharpe"]
        dmd = m["max_drawdown"] - bm["max_drawdown"]
        verdict = "✓" if (dsh >= -0.02 and dmd >= -0.01) else "✗"
        print(f"  N={r['n']:<4} {verdict}  ΔSharpe {dsh:+.3f} | ΔMaxDD {dmd:+.2%}")


if __name__ == "__main__":
    main()
