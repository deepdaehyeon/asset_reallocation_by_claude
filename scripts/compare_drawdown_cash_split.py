"""
드로우다운 축소분 재배치 종목 비교 백테스트 — 큐 항목 C 검증.

문제: 드로우다운 시 equity 축소분이 전량 469830(SOL 초단기채)으로 수렴.
      305080(미국채10Y, deflationary 위기 시 flight-to-safety 상승)은 미사용.

비교 변형 (risk.drawdown_cash_split):
  baseline    469830 100%                 (기존)
  split_5050  469830 50% / 305080 50%
  split_7030  469830 70% / 305080 30%

드로우다운은 포트폴리오 기준이라 전략 MaxDD(~-10%)에서만 mild 임계가 걸림.
발동 빈도(DD ≤ -10% 일수)도 함께 보고해 효과 크기를 가늠한다.

사용:
  python scripts/compare_drawdown_cash_split.py [--start 2010-01-01] [--end 2025-04-30]
"""
from __future__ import annotations

import argparse
import sys
import warnings
from copy import deepcopy
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from fetcher import fetch_fred_history  # noqa: E402
from data import load_all_prices  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from metrics import compute_metrics  # noqa: E402


VARIANTS = [
    ("baseline",   {"469830": 1.0}),
    ("split_5050", {"469830": 0.5, "305080": 0.5}),
    ("split_7030", {"469830": 0.7, "305080": 0.3}),
]


def _dd_trigger_days(returns) -> tuple[int, int, int, int]:
    """포트폴리오 드로우다운 곡선에서 mild/moderate/severe 발동 일수."""
    r = returns.dropna()
    equity = (1.0 + r).cumprod()
    dd = equity / equity.cummax() - 1.0
    total = len(dd)
    mild = int((dd <= -0.10).sum())
    moderate = int((dd <= -0.20).sum())
    severe = int((dd <= -0.30).sum())
    return total, mild, moderate, severe


def run_one(label, split, base_config, universe_px, signal_px, args, fred_history):
    cfg = deepcopy(base_config)
    cfg.setdefault("risk", {})["drawdown_cash_split"] = split
    print(f"\n{'=' * 60}\n  {label}  {split}\n{'=' * 60}")
    engine = BacktestEngine(
        config=cfg, universe_px=universe_px, signal_px=signal_px,
        start=args.start, end=args.end, rebal_freq="W-FRI", tx_cost=0.001,
        fred_history=fred_history,
    )
    result = engine.run()
    m = compute_metrics(result["returns"])
    total, mild, moderate, severe = _dd_trigger_days(result["returns"])
    print(
        f"  CAGR {m.get('cagr',0):+.2%} | Vol {m.get('volatility',0):.2%} | "
        f"Sharpe {m.get('sharpe',0):.3f} | MaxDD {m.get('max_drawdown',0):.2%} | "
        f"Calmar {m.get('calmar',0):.3f}"
    )
    print(
        f"  드로우다운 발동 일수: mild(≤-10%) {mild}/{total} ({mild/total:.1%}) | "
        f"moderate(≤-20%) {moderate} | severe(≤-30%) {severe}"
    )
    return {"label": label, "metrics": m, "trigger": (total, mild, moderate, severe)}


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
    for label, split in VARIANTS:
        results.append(run_one(label, split, base_config, universe_px, signal_px, args, fred_history))

    print(f"\n{'=' * 72}\n  비교 요약\n{'=' * 72}")
    print(f"  {'variant':<12}{'Sharpe':>10}{'MaxDD':>10}{'Calmar':>10}{'CAGR':>10}{'mild발동':>12}")
    print("  " + "-" * 64)
    for r in results:
        m = r["metrics"]
        total, mild, _, _ = r["trigger"]
        print(
            f"  {r['label']:<12}"
            f"{m.get('sharpe',0):>10.3f}"
            f"{m.get('max_drawdown',0):>10.2%}"
            f"{m.get('calmar',0):>10.3f}"
            f"{m.get('cagr',0):>10.2%}"
            f"{mild/total:>11.1%}"
        )

    base = results[0]["metrics"]
    print(f"\n  {'─' * 70}")
    for r in results[1:]:
        m = r["metrics"]
        ds = m.get("sharpe", 0) - base.get("sharpe", 0)
        dd = m.get("max_drawdown", 0) - base.get("max_drawdown", 0)
        dc = m.get("calmar", 0) - base.get("calmar", 0)
        print(
            f"  {r['label']:<12} vs baseline: "
            f"ΔSharpe {ds:+.3f} | ΔMaxDD {dd:+.2%} | ΔCalmar {dc:+.3f}"
        )


if __name__ == "__main__":
    main()
