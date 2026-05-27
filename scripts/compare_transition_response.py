"""
Transition 후행 손실 완화 비교 백테스트 — 진단 결과 후속 작업.

4개 시나리오:
  baseline           : 현재 (override 0.60, no crisis priority, predict_proba)
  override_50        : override threshold 0.60 → 0.50
  +crisis_prio_30    : 위 + Crisis 우선 (blend[Crisis] ≥ 0.30이면 즉시 Crisis)
  +forward_hmm       : 위 + HMM 1-step ahead transition (use_forward_hmm)

평가:
  - Sharpe / MaxDD / Calmar
  - 위험 레짐(Slowdown/Stag/Crisis) 미감지 일수
  - Crisis 진입 적시성 (전환 전·후 21일 수익률)
  - 위험 레짐 일수 (총)
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

RISKY_REGIMES = {"Slowdown", "Stagflation", "Crisis"}


SCENARIOS = [
    # (label, override_thr, crisis_prio, use_forward_hmm)
    ("baseline",         0.60, None,  False),
    ("override_50",      0.50, None,  False),
    ("+crisis_prio_30",  0.50, 0.30,  False),
    ("+forward_hmm",     0.50, 0.30,  True),
]


def _fmt(m: dict) -> str:
    return (
        f"CAGR {m.get('cagr',0):+.1%} | Sharpe {m.get('sharpe',0):.2f} | "
        f"MaxDD {m.get('max_drawdown',0):.1%} | Calmar {m.get('calmar',0):.2f}"
    )


def _crisis_transition_quality(result: pd.DataFrame, window: int = 21) -> dict:
    """Crisis 진입 전·후 21일 SPY 수익률 평균 (후행성 측정)."""
    s = result["regime"]
    prev = s.shift(1)
    entries = result[(s == "Crisis") & (prev != "Crisis") & prev.notna()]
    n = len(entries)
    if n == 0:
        return {"n_crisis_entries": 0}
    pre, post = [], []
    for date in entries.index:
        idx = result.index.get_loc(date)
        if idx < window:
            continue
        if idx + window >= len(result):
            continue
        pre.append(result["value"].iloc[idx] / result["value"].iloc[idx - window] - 1)
        post.append(result["value"].iloc[idx + window] / result["value"].iloc[idx] - 1)
    return {
        "n_crisis_entries": n,
        "pre_mean_pct": round(sum(pre) / len(pre) * 100, 2) if pre else 0.0,
        "post_mean_pct": round(sum(post) / len(post) * 100, 2) if post else 0.0,
    }


def run_one(label, override_thr, crisis_prio, use_forward_hmm,
            base_config, universe_px, signal_px, args, fred_history):
    cfg = deepcopy(base_config)
    h = cfg.setdefault("hmm", {})
    h["override_threshold"] = override_thr
    h["crisis_priority_threshold"] = crisis_prio
    h["use_forward_hmm"] = use_forward_hmm

    print(f"\n{'=' * 60}\n  {label}  "
          f"(override={override_thr}, crisis_prio={crisis_prio}, "
          f"forward_hmm={use_forward_hmm})\n{'=' * 60}")

    engine = BacktestEngine(
        config=cfg, universe_px=universe_px, signal_px=signal_px,
        start=args.start, end=args.end, rebal_freq="W-FRI", tx_cost=0.001,
        fred_history=fred_history,
    )
    result = engine.run()
    m = compute_metrics(result["returns"])
    print(f"  {_fmt(m)}")

    cm = regime_classification_metrics(
        result["rule_regime"], result["regime"], result["returns"]
    )
    mc = cm["miss_cost"] if cm and "miss_cost" in cm else {}
    risky_d = int(result["regime"].isin(RISKY_REGIMES).sum())
    crisis_d = int((result["regime"] == "Crisis").sum())
    print(
        f"  위험 레짐 미감지: {mc.get('miss_days', 0)}/{mc.get('total_days', 0)} "
        f"평균 일수익 {mc.get('avg_daily_return', 0):+.3%} | "
        f"위험 일수 {risky_d}일 (Crisis {crisis_d})"
    )
    ct = _crisis_transition_quality(result)
    if ct["n_crisis_entries"] > 0:
        print(
            f"  Crisis 전환 적시성: 진입 전 {ct.get('pre_mean_pct',0):+.2f}%  "
            f"진입 후 {ct.get('post_mean_pct',0):+.2f}%  (n={ct['n_crisis_entries']})"
        )
    return {
        "label": label, "metrics": m, "miss_cost": mc,
        "risky_d": risky_d, "crisis_d": crisis_d,
        "crisis_transition": ct,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--end", default="2025-04-30")
    args = p.parse_args()

    with open(ROOT / "trading" / "config.yaml") as f:
        base_config = yaml.safe_load(f)

    print(f"[1] 데이터 로딩 [{args.start} ~ {args.end}]")
    universe_px, signal_px = load_all_prices(
        config=base_config, start=args.start, end=args.end, use_cache=True
    )
    fred_history = fetch_fred_history(args.start, args.end)

    results = []
    for label, ot, cp, fhmm in SCENARIOS:
        results.append(run_one(label, ot, cp, fhmm, base_config,
                               universe_px, signal_px, args, fred_history))

    # ── 요약 ─────────────────────────────────────────────────────────────
    print(f"\n{'=' * 80}\n  비교 요약\n{'=' * 80}")
    print(f"  {'scenario':<22}{'Sharpe':>10}{'MaxDD':>10}{'Calmar':>10}"
          f"{'miss':>8}{'crisis_d':>10}{'crisis_post':>14}")
    print("  " + "-" * 78)
    baseline = results[0]
    for r in results:
        m = r["metrics"]
        mc = r["miss_cost"]
        post = r["crisis_transition"].get("post_mean_pct", 0.0)
        print(
            f"  {r['label']:<22}"
            f"{m.get('sharpe',0):>10.3f}"
            f"{m.get('max_drawdown',0):>10.2%}"
            f"{m.get('calmar',0):>10.3f}"
            f"{mc.get('miss_days', 0):>8}"
            f"{r['crisis_d']:>10}"
            f"{post:>+14.2f}"
        )

    # ── baseline 대비 채택 안내 ──────────────────────────────────────────
    print(f"\n  {'─' * 78}")
    bm = baseline["metrics"]
    bmc = baseline["miss_cost"]
    print(f"  baseline: Sharpe {bm.get('sharpe',0):.2f}  MaxDD {bm.get('max_drawdown',0):.1%}  "
          f"위험 미감지 {bmc.get('miss_days',0)}일")
    for r in results[1:]:
        m = r["metrics"]
        mc = r["miss_cost"]
        Δsharpe = m.get("sharpe", 0) - bm.get("sharpe", 0)
        Δmdd = m.get("max_drawdown", 0) - bm.get("max_drawdown", 0)
        Δmiss = mc.get("miss_days", 0) - bmc.get("miss_days", 0)
        ok_sharpe = Δsharpe >= -0.03
        ok_mdd = Δmdd >= -0.01
        ok_miss = Δmiss <= 0
        verdict = "✓" if (ok_sharpe and ok_mdd and ok_miss) else "✗"
        print(
            f"  {r['label']:<22} {verdict}  "
            f"ΔSharpe {Δsharpe:+.3f} | ΔMaxDD {Δmdd:+.2%} | Δmiss {Δmiss:+d}일"
        )


if __name__ == "__main__":
    main()
