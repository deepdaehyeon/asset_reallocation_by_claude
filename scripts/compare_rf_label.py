"""
RF 라벨링 방식 비교 백테스트 — 5개 시나리오 (외부 비평 #1 옵션 1·2 검증).

시나리오:
  rule            forward_window=0                      (기존 baseline)
  forward_rule_21 forward_window=21, mode=rule_at_future (옵션 1, N=21)
  forward_rule_63 forward_window=63, mode=rule_at_future (옵션 1, N=63)
  forward_q_21    forward_window=21, mode=quantile       (옵션 2, N=21)
  forward_q_63    forward_window=63, mode=quantile       (옵션 2, N=63)

옵션 1: t의 라벨로 t+N 시점의 detect_regime() — 룰의 임계는 그대로
옵션 2: t의 라벨로 t+N 시점의 (momentum_1m, realized_vol) 분위 매핑 — 룰 임계 회피

FRED_API_KEY 환경변수가 잡혀 있으면 매크로 피처 포함, 없으면 가격 파생 7개만 사용.

사용:
  python scripts/compare_rf_label.py [--start 2010-01-01] [--end 2025-04-30]
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings
from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from fetcher import fetch_fred_history  # noqa: E402  (.env 자동 로드 포함)
from data import load_all_prices  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from metrics import compute_metrics, regime_classification_metrics  # noqa: E402


SCENARIOS = [
    # (label, forward_window, label_mode)
    ("rule",            0,  "rule_at_future"),
    ("forward_rule_21", 21, "rule_at_future"),
    ("forward_q_21",    21, "quantile"),
    ("forward_qv2_21",  21, "forward_quantile_v2"),
    ("forward_qv2_63",  63, "forward_quantile_v2"),
]


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

    fred_key_set = bool(os.environ.get("FRED_API_KEY"))
    fred_history = fetch_fred_history(args.start, args.end)
    if not fred_history.empty:
        print(f"  FRED 매크로 피처 ({len(fred_history.columns)}개): {list(fred_history.columns)}")
    else:
        hint = "" if fred_key_set else " (FRED_API_KEY 미설정)"
        print(f"  FRED 매크로 없음 — 가격 파생 피처만 사용{hint}")

    results: dict[str, tuple] = {}
    for label, fw, mode in SCENARIOS:
        cfg = deepcopy(base_config)
        h = cfg.setdefault("hmm", {})
        h["rf_forward_window"] = fw
        h["rf_label_mode"] = mode
        scenario_title = f"{label}  [forward_window={fw}, label_mode={mode}]"
        results[label] = run_one(scenario_title, cfg, universe_px, signal_px, args, fred_history)

    # ── 비교 요약 표 ──────────────────────────────────────────────────────
    fred_tag = " (FRED 포함)" if not fred_history.empty else " (FRED 미사용)"
    print(f"\n{'=' * 80}\n  비교 요약{fred_tag} — rule baseline 기준\n{'=' * 80}")

    labels = [s[0] for s in SCENARIOS]
    header = "  {:<22}".format("metric") + "".join(f"{lbl:>14}" for lbl in labels)
    print(header)
    print("  " + "-" * (22 + 14 * len(labels)))

    fields = [
        ("CAGR", "cagr", "{:+.2%}"),
        ("Volatility", "volatility", "{:.2%}"),
        ("Sharpe", "sharpe", "{:.3f}"),
        ("MaxDD", "max_drawdown", "{:.2%}"),
        ("Calmar", "calmar", "{:.3f}"),
    ]
    for fld_label, key, fmt in fields:
        row = "  {:<22}".format(fld_label)
        for lbl in labels:
            _, m, _ = results[lbl]
            row += f"{fmt.format(m.get(key, 0)):>14}"
        print(row)

    # 분류 metric (rule_regime 기준 — 옵션 2처럼 자기참조 끊긴 모델은 점수 자동 하락 주의)
    has_cls = all(
        results[lbl][2] and "error" not in results[lbl][2] for lbl in labels
    )
    if has_cls:
        print()
        cls_fields = [
            ("MCC", "mcc", "{:+.3f}"),
            ("Macro-F1", "macro_f1", "{:.3f}"),
            ("BalancedAcc", "balanced_accuracy", "{:.3f}"),
            ("Override율", "override_rate", "{:.1%}"),
        ]
        for fld_label, key, fmt in cls_fields:
            row = "  {:<22}".format(fld_label)
            for lbl in labels:
                _, _, cm = results[lbl]
                row += f"{fmt.format(cm.get(key, 0)):>14}"
            print(row)

        miss_row = "  {:<22}".format("위험레짐 미감지")
        for lbl in labels:
            _, _, cm = results[lbl]
            miss_row += f"{cm['miss_cost']['miss_days']:>14}"
        print(miss_row)

    # ── 권장 판정 (백테스트 metric만, 분류 metric은 자기참조 한계로 제외) ─────
    print(f"\n  {'─' * 78}")
    baseline_m = results["rule"][1]
    bm_sharpe = baseline_m.get("sharpe", 0)
    bm_mdd = baseline_m.get("max_drawdown", 0)

    best_label = "rule"
    best_score = (bm_sharpe, -bm_mdd)
    for lbl in labels:
        if lbl == "rule":
            continue
        m = results[lbl][1]
        score = (m.get("sharpe", 0), -m.get("max_drawdown", 0))
        if score > best_score:
            best_score = score
            best_label = lbl

    if best_label == "rule":
        print("  ▶ 판정: rule baseline 이 우월 — forward 라벨 채택 보류")
    else:
        bm = results[best_label][1]
        print(
            f"  ▶ 판정: {best_label} 권장 "
            f"(Sharpe {bm.get('sharpe',0):.2f} vs baseline {bm_sharpe:.2f}, "
            f"MaxDD {bm.get('max_drawdown',0):.1%} vs {bm_mdd:.1%})"
        )


if __name__ == "__main__":
    main()
