"""
R3 — whipsaw 억제 레이어 단순화 스윕.

라이브 run.py에만 존재하던 트리거/히스테리시스 레이어를 엔진(_run_triggered)에 이식해
2×2×2 그리드로 비교한다:
  rc (regime_change_trigger) : 확정 레짐 변경 시 drift 밴드 우회 강제 리밸런스  {on, off}
  cf (confirmation_count)    : raw 레짐 N회 연속 확정                          {2, 1}
  fb (confidence fallback)   : combined_conf < threshold → 이전 확정 유지       {on, off}

기준점(base*): rc=on, cf=2, fb=on = 현재 config.

가설:
  H1 — rc_off: regime_changed 강제 트리거는 drift와 redundant → 제거해도 무손실?
  H2 — fb_off: confidence fallback 제거 → 라벨 안정화 손실?
  H3 — cf1   : confirmation 1회로 완화 → whipsaw 증가?

측정: Sharpe / MaxDD / Calmar / Vol / CAGR + 리밸런스 횟수(whipsaw 프록시) + 누적 tx_cost.

사용:
  python scripts/compare_r3_whipsaw.py [--start 2010-01-01] [--end 2025-04-30]
"""
from __future__ import annotations

import argparse
import sys
import warnings
from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from fetcher import fetch_fred_history  # noqa: E402
from data import load_all_prices  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from metrics import compute_metrics  # noqa: E402


# (label, regime_change_trigger, confirmation_count, fallback_on)
VARIANTS = [
    ("base*",          True,  2, True),   # 현재 config
    ("rc_off",         False, 2, True),   # H1
    ("cf1",            True,  1, True),   # H3
    ("fb_off",         True,  2, False),  # H2
    ("rc_off+cf1",     False, 1, True),
    ("rc_off+fb_off",  False, 2, False),
    ("cf1+fb_off",     True,  1, False),
    ("all_off",        False, 1, False),  # 모든 레이어 최소
]


def run_one(label, rc, cf, fb, base_config, universe_px, signal_px, args, fred_history):
    cfg = deepcopy(base_config)
    rf = cfg.setdefault("regime_filter", {})
    rb = cfg.setdefault("rebalancing", {})
    rf["confirmation_count"] = cf
    base_thr = base_config.get("regime_filter", {}).get("confidence_threshold", 0.20)
    rf["confidence_threshold"] = base_thr if fb else 0.0
    rb["regime_change_trigger"] = rc

    print(f"\n{'=' * 60}\n  {label}  (rc={rc}, confirm={cf}, fallback={fb})\n{'=' * 60}")
    engine = BacktestEngine(
        config=cfg, universe_px=universe_px, signal_px=signal_px,
        start=args.start, end=args.end, rebal_freq="W-FRI", tx_cost=0.001,
        fred_history=fred_history, trigger_mode=True,
    )
    result = engine.run()
    m = compute_metrics(result["returns"])
    n_rebal = int(result["rebalanced"].sum())
    tx_total = float(result["tx_cost"].sum())
    print(
        f"  CAGR {m.get('cagr',0):+.2%} | Vol {m.get('volatility',0):.2%} | "
        f"Sharpe {m.get('sharpe',0):.3f} | MaxDD {m.get('max_drawdown',0):.2%} | "
        f"Calmar {m.get('calmar',0):.3f} | 리밸 {n_rebal}회 | tx {tx_total:.2%}"
    )
    return {"label": label, "metrics": m, "n_rebal": n_rebal, "tx_total": tx_total}


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

    res = {}
    for label, rc, cf, fb in VARIANTS:
        res[label] = run_one(label, rc, cf, fb, base_config, universe_px, signal_px, args, fred_history)

    print(f"\n{'=' * 86}\n  비교 요약 (R3 whipsaw 레이어 그리드)\n{'=' * 86}")
    print(f"  {'variant':<16}{'Sharpe':>9}{'MaxDD':>9}{'Calmar':>9}{'Vol':>9}{'CAGR':>9}{'리밸':>7}{'tx':>8}")
    print("  " + "-" * 76)
    for label, _, _, _ in VARIANTS:
        r = res[label]
        m = r["metrics"]
        print(
            f"  {label:<16}"
            f"{m.get('sharpe',0):>9.3f}"
            f"{m.get('max_drawdown',0):>9.2%}"
            f"{m.get('calmar',0):>9.3f}"
            f"{m.get('volatility',0):>9.2%}"
            f"{m.get('cagr',0):>9.2%}"
            f"{r['n_rebal']:>7}"
            f"{r['tx_total']:>8.2%}"
        )

    base = res["base*"]
    bm = base["metrics"]
    print(f"\n  {'─' * 84}")
    print(f"  base* 기준: Sharpe {bm.get('sharpe',0):.3f}, MaxDD {bm.get('max_drawdown',0):.2%}, "
          f"Calmar {bm.get('calmar',0):.3f}, 리밸 {base['n_rebal']}회, tx {base['tx_total']:.2%}")
    print("  해석: base* 대비 Sharpe·MaxDD 거의 동일하면서 리밸/tx가 줄면 redundant 레이어로 판단.")


if __name__ == "__main__":
    main()
