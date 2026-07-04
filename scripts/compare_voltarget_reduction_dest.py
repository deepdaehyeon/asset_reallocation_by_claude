"""
vol targeting 축소분 행선지 A/B/C 비교 — 규칙4 4지표.

질문(2026-07-04, 사용자): vol targeting이 equity를 깎으면 축소분이 전량 cash(469830)로
  간다. 다른 방어자산으로 분배하는 게 나은가? de-risk 효과↓ vs 방어수익↑ 트레이드오프를
  백테스트로 판정.

변형 (config.vol_targeting.reduction_dest):
  A_cash       cash 100%                                  (현행)
  B_defensive  bond_krw·bond_tips·gold·cash 현재비중 비례
  C_nonequity  위 + commodity·managed_futures 비례        (더 공격적)

범위 합의(규칙5): 나머지 전부 현행 고정(drift 5%·floor 0.65·core30·blend·평활),
  분배처만 토글. drift 리밸 모드·2010~2025. 판정=규칙4 4지표(롤링CAGR·Ulcer·회복기간·Martin).

사용: python scripts/compare_voltarget_reduction_dest.py
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

VARIANTS = [
    ("A_cash",      "cash"),        # 현행 baseline
    ("B_defensive", "defensive"),   # bond/gold/cash 비례
    ("C_nonequity", "nonequity"),   # + commodity/MF 비례
]


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)

    print(f"[1] 데이터 로딩 [{START} ~ {END}]", flush=True)
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    # reduction_dest는 레짐 계산에 영향 없음(비중 후처리) → 레짐 경로를 1회만 캐싱해
    # 세 변형에서 재사용(HMM/RF 재학습을 변형마다 반복하지 않음). 비중은 _target_weights가
    # 변형별 config(reduction_dest 포함)로 재계산되므로 A/B/C가 정확히 갈린다.
    print("\n[2] 레짐 경로 캐싱 중 (HMM 1회 학습)...", flush=True)
    probe = build_engine(copy.deepcopy(base), universe_px, signal_px, fred_history)
    regime_cache = probe.precompute_regime_path()

    rows = []
    for label, dest in VARIANTS:
        print(f"\n[3] 백테스트: {label} (reduction_dest={dest})  [캐시 재사용]", flush=True)
        cfg = copy.deepcopy(base)
        cfg.setdefault("vol_targeting", {})["reduction_dest"] = dest
        eng = build_engine(cfg, universe_px, signal_px, fred_history)
        res = eng.run(regime_cache=regime_cache)
        rows.append(metrics_row(label, res))

    df = pd.DataFrame(rows).set_index("전략")
    cols = ["Martin", "r3w", "r3m", "r5w", "Ulcer", "rec_dd", "uw_max",
            "CAGR", "MaxDD", "COVID", "Bear22", "tx"]
    df = df[cols]

    print("\n" + "=" * 96)
    print("  vol targeting 축소분 행선지 A/B/C — 규칙4 4지표 (Martin·롤링CAGR·Ulcer·회복기간 우선)")
    print("=" * 96)
    fmt = {"Martin": "{:.3f}", "r3w": "{:.2%}", "r3m": "{:.2%}", "r5w": "{:.2%}",
           "Ulcer": "{:.2f}", "rec_dd": "{:.0f}", "uw_max": "{:.0f}",
           "CAGR": "{:.2%}", "MaxDD": "{:.2%}", "COVID": "{:.2%}", "Bear22": "{:.2%}",
           "tx": "{:.2%}"}
    print(df.to_string(formatters={k: v.format for k, v in fmt.items()}))

    base_row = df.loc["A_cash"]
    print("\n  vs A_cash (Δ):")
    for lbl in ["B_defensive", "C_nonequity"]:
        r = df.loc[lbl]
        print(
            f"    {lbl:<14} ΔMartin {r['Martin']-base_row['Martin']:+.3f} | "
            f"Δ롤3y최악 {r['r3w']-base_row['r3w']:+.2%} | ΔUlcer {r['Ulcer']-base_row['Ulcer']:+.2f} | "
            f"Δ회복 {r['rec_dd']-base_row['rec_dd']:+.0f}d | ΔMaxDD {r['MaxDD']-base_row['MaxDD']:+.2%} | "
            f"ΔCOVID {r['COVID']-base_row['COVID']:+.2%}"
        )

    out = ROOT / "docs" / "_voltarget_reduction_dest_abc.csv"
    df.to_csv(out)
    print(f"\n  저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
