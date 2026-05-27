"""
blend EWMA 평활 비교 백테스트 — 외부 비평 #6-c (whipsaw 억제).

new_blend = α · prev_blend + (1-α) · raw_blend

평가:
  - raw whipsaw 비율 (21일 내 직전 레짐 복귀)
  - 전환 횟수 (총)
  - 백테스트 Sharpe / MaxDD / Calmar
  - 위험 레짐(Slowdown/Stagflation/Crisis) 진입 적시성·일수

사용:
  python scripts/compare_blend_smoothing.py [--alphas 0,0.5,0.7,0.9]
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
from metrics import compute_metrics  # noqa: E402

RISKY_REGIMES = {"Slowdown", "Stagflation", "Crisis"}


def _whipsaw_stats(df: pd.DataFrame, window: int = 21) -> dict:
    s = df["regime"]
    prev = s.shift(1)
    trans = df[(s != prev) & prev.notna()]
    total = len(trans)
    if total == 0:
        return {"transitions": 0, "whipsaw": 0, "whipsaw_pct": 0.0}

    trans_records = trans.reset_index()[["date", "regime"]].to_dict("records")
    trans_records = [
        {"date": r["date"], "to": r["regime"], "from": p}
        for r, p in zip(trans_records, prev.loc[trans.index].values)
    ]
    whip = 0
    for i, t in enumerate(trans_records[:-1]):
        for nxt in trans_records[i + 1:]:
            days = (nxt["date"] - t["date"]).days
            if days > window * 1.5:
                break
            if nxt["to"] == t["from"]:
                whip += 1
                break
    return {
        "transitions": int(total),
        "whipsaw": int(whip),
        "whipsaw_pct": round(whip / total * 100, 1),
    }


def run_one(alpha: float, base_config: dict, universe_px, signal_px, args, fred_history):
    cfg = deepcopy(base_config)
    cfg.setdefault("regime_filter", {})["blend_smoothing_alpha"] = alpha
    print(f"\n{'=' * 60}\n  α = {alpha}\n{'=' * 60}")
    engine = BacktestEngine(
        config=cfg, universe_px=universe_px, signal_px=signal_px,
        start=args.start, end=args.end, rebal_freq="W-FRI", tx_cost=0.001,
        fred_history=fred_history,
    )
    result = engine.run()
    m = compute_metrics(result["returns"])
    ws = _whipsaw_stats(result, args.window)
    risky_days = int(result["regime"].isin(RISKY_REGIMES).sum())
    crisis_days = int((result["regime"] == "Crisis").sum())

    print(
        f"  CAGR {m.get('cagr',0):+.1%} | "
        f"Sharpe {m.get('sharpe',0):.2f} | "
        f"MaxDD {m.get('max_drawdown',0):.1%} | "
        f"Calmar {m.get('calmar',0):.2f}"
    )
    print(
        f"  전환 {ws['transitions']}회 | "
        f"whipsaw {ws['whipsaw']}회 ({ws['whipsaw_pct']}%) | "
        f"위험 레짐 {risky_days}일 (Crisis {crisis_days})"
    )
    return {"alpha": alpha, "metrics": m, "whipsaw": ws,
            "risky_days": risky_days, "crisis_days": crisis_days}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--end", default="2025-04-30")
    p.add_argument("--alphas", default="0,0.5,0.7,0.9")
    p.add_argument("--window", type=int, default=21)
    args = p.parse_args()
    alphas = [float(a) for a in args.alphas.split(",")]

    with open(ROOT / "trading" / "config.yaml") as f:
        base_config = yaml.safe_load(f)

    print(f"[1] 데이터 로딩  [{args.start} ~ {args.end}]")
    universe_px, signal_px = load_all_prices(
        config=base_config, start=args.start, end=args.end, use_cache=True
    )
    fred_history = fetch_fred_history(args.start, args.end)

    results = []
    for a in alphas:
        results.append(run_one(a, base_config, universe_px, signal_px, args, fred_history))

    # ── 요약 표 ──────────────────────────────────────────────────────────
    print(f"\n{'=' * 80}\n  비교 요약\n{'=' * 80}")
    print(f"  {'α':<6}{'Sharpe':>10}{'MaxDD':>10}{'Calmar':>10}"
          f"{'trans':>10}{'whipsaw':>14}{'risky_d':>10}{'Crisis_d':>10}")
    print("  " + "-" * 78)
    baseline = next(r for r in results if r["alpha"] == 0)
    for r in results:
        m = r["metrics"]
        ws = r["whipsaw"]
        whip_cell = f"{ws['whipsaw']} ({ws['whipsaw_pct']}%)"
        print(
            f"  {r['alpha']:<6}"
            f"{m.get('sharpe',0):>10.3f}"
            f"{m.get('max_drawdown',0):>10.2%}"
            f"{m.get('calmar',0):>10.3f}"
            f"{ws['transitions']:>10}"
            f"{whip_cell:>14}"
            f"{r['risky_days']:>10}"
            f"{r['crisis_days']:>10}"
        )

    print(f"\n  {'─' * 78}")
    bm = baseline["metrics"]
    bws = baseline["whipsaw"]
    print(f"  baseline α=0: Sharpe {bm.get('sharpe',0):.2f}, MaxDD {bm.get('max_drawdown',0):.1%}, "
          f"whipsaw {bws['whipsaw_pct']}%, Crisis {baseline['crisis_days']}일")

    for r in results:
        if r["alpha"] == 0:
            continue
        Δsharpe = r["metrics"].get("sharpe", 0) - bm.get("sharpe", 0)
        Δmdd = r["metrics"].get("max_drawdown", 0) - bm.get("max_drawdown", 0)
        Δwhip = r["whipsaw"]["whipsaw_pct"] - bws["whipsaw_pct"]
        Δcrisis = r["crisis_days"] - baseline["crisis_days"]
        # 채택 기준: whipsaw 감소 + Sharpe 동등 이상 + MaxDD 악화 ≤2pp + Crisis 일수 큰 감소 없음(≤30% 감소)
        ok_whip = Δwhip < 0
        ok_sharpe = Δsharpe >= -0.05
        ok_mdd = Δmdd >= -0.02
        ok_crisis = (
            baseline["crisis_days"] == 0
            or r["crisis_days"] >= baseline["crisis_days"] * 0.7
        )
        verdict = "✓" if (ok_whip and ok_sharpe and ok_mdd and ok_crisis) else "✗"
        print(
            f"  α={r['alpha']:<4} {verdict} "
            f"Δwhipsaw {Δwhip:+.1f}pp | ΔSharpe {Δsharpe:+.3f} | "
            f"ΔMaxDD {Δmdd:+.2%} | ΔCrisis {Δcrisis:+d}일"
        )


if __name__ == "__main__":
    main()
