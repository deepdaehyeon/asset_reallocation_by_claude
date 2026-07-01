"""
소프트 룰 A/B — 룰의 하드 임계(momentum>2% 등 계단식)를 로지스틱 램프로 부드럽게
바꿔 blend의 RF(룰 근사기) 자리에 주입하고, 하드 대비 4지표를 비교한다.

질문(2026-07-01, 사용자): detect_regime의 딱딱한 경계선(예: momentum 2%)을 소프트하게
  바꾸면 4지표가 개선되나? blend_probs=(1-w)·HMM + w·RULE에서 RULE을 다음으로 스윕:
    - baseline_RF : 현행 (RF = 하드 룰의 지도학습 근사기)
    - soft scale 0.0 : analytic 하드 룰 (nesting 앵커 — RF근사 vs 정확한 하드 룰 분리)
    - soft scale 0.5 / 1.0 / 2.0 : 점점 더 부드럽게

범위 합의: core30 OFF(순수 신호 — rf_hmm_weight_sweep와 동일 기준)와 ON(라이브 현실)
  둘 다. feature_smoothing 현행 ON. drift 리밸·tx·USD단일·2010~2025. rf_weight=0.70 고정.
  판정은 규칙4 4지표(롤링CAGR·Ulcer·회복기간·Martin). 소프트 폭 미세조정은 지양하고
  구간 추세로 판단([[feedback-regime-targets-no-tuning]]).
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "scripts"))

import warnings
warnings.filterwarnings("ignore", message="Model is not converging.*")

from data import load_all_prices  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
from ablation_regime_stack import build_engine, metrics_row  # noqa: E402
from compare_rule_timing_ab import START, END  # noqa: E402

# (label, soft_rule_scale)  — None = 현행 RF
VARIANTS = [
    ("baseline_RF",   None),
    ("soft_s0(hard)", 0.0),
    ("soft_s0.5",     0.5),
    ("soft_s1.0",     1.0),
    ("soft_s2.0",     2.0),
]


# 라이브 운영점: config.yaml의 rf_weight(2026-06-17 0.40→0.70).
RF_WEIGHT = 0.70


def make_cfg(base, soft_scale, core30):
    cfg = copy.deepcopy(base)
    cfg.setdefault("core_satellite", {})["enabled"] = bool(core30)
    cfg.setdefault("hmm", {})["rf_enabled"] = True
    cfg["hmm"]["rf_weight"] = RF_WEIGHT
    if soft_scale is None:
        cfg["hmm"].pop("soft_rule_scale", None)
    else:
        cfg["hmm"]["soft_rule_scale"] = float(soft_scale)
    return cfg


def run_block(base, universe_px, signal_px, fred_history, core30):
    rows = []
    for label, scale in VARIANTS:
        print(f"[core30={'ON' if core30 else 'OFF'}] [{label}] 실행 중...", flush=True)
        res = build_engine(
            make_cfg(base, scale, core30), universe_px, signal_px, fred_history
        ).run()
        rows.append(metrics_row(label, res))
    return pd.DataFrame(rows).set_index("전략")


def print_table(df, title):
    print(f"\n{'='*132}")
    print(f"  {title}")
    print(f"{'='*132}")
    h = (f"  {'전략':>14}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'tx':>7}"
         f"{'COVID':>8}{'Bear22':>8}")
    print(h)
    print("  " + "─" * (len(h)))
    for label, r in df.iterrows():
        mark = " ◀base" if label == "baseline_RF" else ""
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        # r3w/r3m/r5w/CAGR/MaxDD/COVID/Bear22는 소수(fraction) → ×100로 % 표기.
        print(f"  {label:>14}{r['r3w']*100:>9.1f}{r['r3m']*100:>9.1f}"
              f"{r['r5w']*100:>9.1f}{r['Ulcer']:>8.2f}{rec:>8}"
              f"{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']*100:>8.1f}"
              f"{r['MaxDD']*100:>8.1f}{r['tx']:>7.2f}{r['COVID']*100:>8.1f}{r['Bear22']*100:>8.1f}{mark}")


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]...", flush=True)
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    df_off = run_block(base, universe_px, signal_px, fred_history, core30=False)
    print_table(df_off, "소프트 룰 A/B — core30 OFF(순수 신호)·평활 ON·drift·tx·USD단일·2010~2025 (rf_weight=0.70)")
    df_on = run_block(base, universe_px, signal_px, fred_history, core30=True)
    print_table(df_on, "소프트 룰 A/B — core30 ON(라이브 현실)·평활 ON·drift·tx·USD단일·2010~2025 (rf_weight=0.70)")


if __name__ == "__main__":
    main()
