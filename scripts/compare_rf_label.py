"""
RF 라벨링 방식 비교 백테스트 — rule(자기참조) vs forward(N영업일 후 detect_regime).

목적:
  - 외부 비평 #1 검증: RF 학습을 동일 시점 detect_regime() 라벨(자기참조) 대신
    t+N 시점 detect_regime() 라벨(forward-looking)로 바꿨을 때
    백테스트 성과 / 레짐 분류 품질이 어떻게 달라지는지 측정.
  - 채택 시 권장 N 값을 결정한다.

사용:
  python scripts/compare_rf_label.py [--start 2010-01-01] [--end 2025-04-30] \
      [--windows 0,21,63]

의미:
  - rule (forward_window=0): 기존 동작. RF는 룰의 매끄러운 근사기.
  - forward_N: t의 라벨 = t+N 시점의 detect_regime 결과. 자기참조 끊김.
    학습 셋은 마지막 N영업일이 빠진다.
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
    p.add_argument(
        "--windows",
        default="0,21,63",
        help="comma-separated forward windows (0=rule baseline)",
    )
    return p.parse_args()


def main():
    args = parse_args()
    windows = [int(w.strip()) for w in args.windows.split(",") if w.strip()]
    if 0 not in windows:
        windows.insert(0, 0)  # baseline 강제 포함

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

    results: dict[int, tuple] = {}
    for w in windows:
        cfg = deepcopy(base_config)
        cfg.setdefault("hmm", {})["rf_forward_window"] = w
        label = (
            f"BASELINE (rule label, w=0)"
            if w == 0
            else f"FORWARD w={w} (t+{w} detect_regime)"
        )
        results[w] = run_one(label, cfg, universe_px, signal_px, args, fred_history)

    # ── 비교 요약 표 ──────────────────────────────────────────────────────
    print(f"\n{'=' * 60}\n  비교 요약 (rule baseline 대비 Δ)\n{'=' * 60}")
    header = "  {:<22}".format("metric") + "".join(
        f"{('w=' + str(w)):>14}" for w in windows
    )
    print(header)
    print("  " + "-" * (22 + 14 * len(windows)))

    fields = [
        ("CAGR", "cagr", "{:+.2%}"),
        ("Volatility", "volatility", "{:.2%}"),
        ("Sharpe", "sharpe", "{:.3f}"),
        ("MaxDD", "max_drawdown", "{:.2%}"),
        ("Calmar", "calmar", "{:.3f}"),
    ]
    for label, key, fmt in fields:
        row = "  {:<22}".format(label)
        for w in windows:
            _, m, _ = results[w]
            row += f"{fmt.format(m.get(key, 0)):>14}"
        print(row)

    # 분류 메트릭 (rule_regime은 baseline, regime은 ensemble 후 → 직접 비교 의미 제한적)
    has_cls = all(
        results[w][2] and "error" not in results[w][2] for w in windows
    )
    if has_cls:
        print()
        cls_fields = [
            ("MCC", "mcc", "{:+.3f}"),
            ("Macro-F1", "macro_f1", "{:.3f}"),
            ("BalancedAcc", "balanced_accuracy", "{:.3f}"),
            ("Override율", "override_rate", "{:.1%}"),
        ]
        for label, key, fmt in cls_fields:
            row = "  {:<22}".format(label)
            for w in windows:
                _, _, cm = results[w]
                row += f"{fmt.format(cm.get(key, 0)):>14}"
            print(row)

        miss_row = "  {:<22}".format("위험레짐 미감지")
        for w in windows:
            _, _, cm = results[w]
            miss_row += f"{cm['miss_cost']['miss_days']:>14}"
        print(miss_row)

    # ── 권장 판정 ──────────────────────────────────────────────────────────
    print(f"\n  {'─' * 58}")
    baseline = results[0][1]
    best_w = 0
    best_score = (baseline.get("sharpe", 0), -baseline.get("max_drawdown", 0))
    for w in windows:
        if w == 0:
            continue
        m = results[w][1]
        score = (m.get("sharpe", 0), -m.get("max_drawdown", 0))
        if score > best_score:
            best_score = score
            best_w = w

    if best_w == 0:
        print("  ▶ 판정: forward 라벨이 baseline(rule) 대비 우월하지 못함 → 채택 보류")
    else:
        bm = results[best_w][1]
        print(
            f"  ▶ 판정: forward_window={best_w} 권장 "
            f"(Sharpe {bm.get('sharpe',0):.2f}, MaxDD {bm.get('max_drawdown',0):.1%})"
        )
        print(f"    → config.yaml의 hmm.rf_forward_window를 {best_w}로 설정 고려")


if __name__ == "__main__":
    main()
