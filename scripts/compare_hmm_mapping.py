"""
비지도 HMM 매핑 vs legacy detect_regime 매핑 비교 백테스트.

목적:
  - 두 매핑 방식의 백테스트 성과(CAGR/Sharpe/MaxDD)와 레짐 분류 품질(MCC/Macro-F1) 비교
  - 새 매핑이 성능 저하를 일으키면 적용 보류 결정

사용: python scripts/compare_hmm_mapping.py [--start 2010-01-01] [--end 2025-04-30]
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


def _fmt(m: dict) -> str:
    return (
        f"CAGR {m.get('cagr', 0):+.1%} | "
        f"Vol {m.get('volatility', 0):.1%} | "
        f"Sharpe {m.get('sharpe', 0):.2f} | "
        f"MaxDD {m.get('max_drawdown', 0):.1%} | "
        f"Calmar {m.get('calmar', 0):.2f}"
    )


def run_one(label: str, config: dict, universe_px, signal_px, args, fred_history):
    print(f"\n{'=' * 60}\n  {label}\n{'=' * 60}")
    engine = BacktestEngine(
        config=config,
        universe_px=universe_px,
        signal_px=signal_px,
        start=args.start,
        end=args.end,
        rebal_freq="W-FRI",
        tx_cost=args.tx_cost,
        fred_history=fred_history,
    )
    result = engine.run()
    m = compute_metrics(result["returns"])
    print(f"  {_fmt(m)}")
    cm = regime_classification_metrics(
        result["rule_regime"], result["regime"], result["returns"]
    )
    if cm and "error" not in cm:
        print(
            f"  MCC {cm['mcc']:+.3f} | Macro-F1 {cm['macro_f1']:.3f} | "
            f"BalAcc {cm['balanced_accuracy']:.3f} | Override {cm['override_rate']:.1%}"
        )
        mc = cm["miss_cost"]
        if mc["total_days"] > 0:
            print(
                f"  위험 레짐 미감지: {mc['miss_days']}/{mc['total_days']} "
                f"({mc['miss_days']/mc['total_days']:.0%}) "
                f"평균 일수익 {mc['avg_daily_return']:+.3%}"
            )
    counts = result["regime"].value_counts()
    print("  레짐 분포: " + "  ".join(f"{r}:{c}일" for r, c in counts.items()))
    return result, m, cm


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--end", default="2025-04-30")
    p.add_argument("--tx-cost", type=float, default=0.001)
    return p.parse_args()


def main():
    args = parse_args()

    config_path = ROOT / "trading" / "config.yaml"
    with open(config_path) as f:
        base_config = yaml.safe_load(f)

    print(f"데이터 로딩  [{args.start} ~ {args.end}]")
    universe_px, signal_px = load_all_prices(
        config=base_config, start=args.start, end=args.end, use_cache=True
    )
    fred_history = fetch_fred_history(args.start, args.end)
    if not fred_history.empty:
        print(f"  FRED 매크로 피처: {list(fred_history.columns)}")
    else:
        print("  FRED 매크로 없음 — 가격 파생 피처만 사용")

    cfg_legacy = deepcopy(base_config)
    cfg_legacy.setdefault("hmm", {})["unsupervised_mapping"] = False

    cfg_new = deepcopy(base_config)
    cfg_new.setdefault("hmm", {})["unsupervised_mapping"] = True

    r_legacy, m_legacy, cm_legacy = run_one(
        "LEGACY (detect_regime 다수결 매핑)",
        cfg_legacy, universe_px, signal_px, args, fred_history,
    )
    r_new, m_new, cm_new = run_one(
        "NEW (비지도 state-feature 매핑)",
        cfg_new, universe_px, signal_px, args, fred_history,
    )

    print(f"\n{'=' * 60}\n  비교 요약\n{'=' * 60}")
    print(f"  {'metric':<22}{'legacy':>14}{'new':>14}{'Δ':>12}")
    print(f"  {'-' * 60}")
    fields = [
        ("CAGR", "cagr", "{:+.2%}"),
        ("Volatility", "volatility", "{:.2%}"),
        ("Sharpe", "sharpe", "{:.3f}"),
        ("MaxDD", "max_drawdown", "{:.2%}"),
        ("Calmar", "calmar", "{:.3f}"),
    ]
    for label, key, fmt in fields:
        a = m_legacy.get(key, 0.0)
        b = m_new.get(key, 0.0)
        diff = b - a
        print(f"  {label:<22}{fmt.format(a):>14}{fmt.format(b):>14}{diff:>+12.4f}")

    if cm_legacy and cm_new and "error" not in cm_legacy and "error" not in cm_new:
        print()
        cls_fields = [
            ("MCC", "mcc", "{:+.3f}"),
            ("Macro-F1", "macro_f1", "{:.3f}"),
            ("BalancedAcc", "balanced_accuracy", "{:.3f}"),
            ("Override율", "override_rate", "{:.1%}"),
        ]
        for label, key, fmt in cls_fields:
            a = cm_legacy.get(key, 0.0)
            b = cm_new.get(key, 0.0)
            diff = b - a
            print(f"  {label:<22}{fmt.format(a):>14}{fmt.format(b):>14}{diff:>+12.4f}")

        # 위험 레짐 미감지 비교
        a_mc = cm_legacy["miss_cost"]
        b_mc = cm_new["miss_cost"]
        print(
            f"  {'위험레짐 미감지':<22}"
            f"{a_mc['miss_days']:>14}{b_mc['miss_days']:>14}"
            f"{b_mc['miss_days'] - a_mc['miss_days']:>+12d}"
        )

    # 판정
    print(f"\n  {'─' * 58}")
    sharpe_diff = m_new.get("sharpe", 0) - m_legacy.get("sharpe", 0)
    dd_diff = m_new.get("max_drawdown", 0) - m_legacy.get("max_drawdown", 0)
    mcc_diff = (cm_new.get("mcc", 0) - cm_legacy.get("mcc", 0)) if cm_new and cm_legacy else 0
    f1_diff = (cm_new.get("macro_f1", 0) - cm_legacy.get("macro_f1", 0)) if cm_new and cm_legacy else 0

    print(
        f"  Δ Sharpe {sharpe_diff:+.3f}  ΔMaxDD {dd_diff:+.2%}  "
        f"ΔMCC {mcc_diff:+.3f}  ΔMacro-F1 {f1_diff:+.3f}"
    )

    # 채택 기준: Sharpe 저하 ≤0.10 + MaxDD 악화 ≤2pp + MCC/F1 둘 다 -0.10 이상
    sharpe_ok = sharpe_diff >= -0.10
    dd_ok = dd_diff >= -0.02
    cls_ok = mcc_diff >= -0.10 and f1_diff >= -0.10
    if sharpe_ok and dd_ok and cls_ok:
        print("  ▶ 판정: 채택 가능 — new 매핑이 성능 저하 없이 작동")
    else:
        reasons = []
        if not sharpe_ok:
            reasons.append(f"Sharpe 저하 {sharpe_diff:+.3f}")
        if not dd_ok:
            reasons.append(f"MaxDD 악화 {dd_diff:+.2%}")
        if not cls_ok:
            reasons.append(f"분류 품질 저하 (ΔMCC {mcc_diff:+.3f} / ΔF1 {f1_diff:+.3f})")
        print(f"  ▶ 판정: 롤백 권고 — {' / '.join(reasons)}")


if __name__ == "__main__":
    main()
