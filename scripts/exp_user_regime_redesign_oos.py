"""
사용자 레짐별 비중 재설계 — 워크포워드 OOS 비교 (라이브 미반영, 비교용).

질문(2026-06-15, 사용자): 발생확률(Goldi 59%·Slow 25%)을 고려해 레짐별 핵심 비중을 직접
  재설계. 적은 자산은 그 %로 고정, 나머지는 드롭한 자산들의 현재 비중을 비례 유지해 채움
  (option 3). 현행 대비 4지표(Martin·CAGR·Ulcer·회복기간) TRAIN/TEST 비교.

방법:
  - USER = 사용자가 적은 레짐별 핵심 비중(소수). L=합, R=1-L.
  - 나머지 R은 현재 config에서 '사용자가 안 적은' 자산들의 현재 비중을 R/S로 스케일해 채움.
  - Transition은 사용자 미지정 → 현행 유지.
  - 엔진 전체 ON(vol targeting·core30·blend·drift) → 실운영 조건서 비중만 교체한 공정 비교.
  - run_config(walkforward_shrink_oos): config 고정 2010~2025 1회 → 2019+ 진짜 OOS.

한계(규칙5): core30이 자산 30%를 Goldilocks로 고정(비중 변경 일부 무력화), vol targeting이
  고변동시 비중 재조정 → 비중 효과 희석/흡수 가능. 단일 경로·USD단일. 라이브 반영은 확인 후.
"""
from __future__ import annotations

import copy
import sys
import warnings
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "scripts"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from data import load_all_prices  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
from walkforward_shrink_oos import run_config, SPLIT  # noqa: E402
from compare_rule_timing_ab import START, END  # noqa: E402

USER = {
    "Goldilocks": {"equity_etf": 0.40, "equity_factor": 0.10, "gold": 0.05, "cash": 0.05},
    "Reflation": {"commodity": 0.15, "equity_factor": 0.12, "equity_sector": 0.10,
                  "managed_futures": 0.05},
    "Slowdown": {"bond_krw": 0.25, "gold": 0.10, "bond_tips": 0.10},
    "Stagflation": {"gold": 0.14, "bond_krw": 0.14, "bond_tips": 0.10, "commodity": 0.12},
    "Crisis": {"bond_krw": 0.25, "cash": 0.20, "gold": 0.10},
}


def build_new_targets(base_targets):
    out = {}
    for rg, cur in base_targets.items():
        if rg not in USER:
            out[rg] = dict(cur)
            continue
        listed = USER[rg]
        R = 1.0 - sum(listed.values())
        unlisted = {k: v for k, v in cur.items() if k not in listed and v > 0}
        S = sum(unlisted.values())
        new = dict(listed)
        if S > 0 and R > 0:
            for k, v in unlisted.items():
                new[k] = v * R / S
        out[rg] = new
    return out


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)

    new_cfg = copy.deepcopy(base)
    new_cfg["regime_targets"] = build_new_targets(base["regime_targets"])

    print("신규 regime_targets (option 3: 적은 값 고정 + 드롭자산 현재비중 비례):")
    for rg in ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis"]:
        items = sorted(new_cfg["regime_targets"][rg].items(), key=lambda x: -x[1])
        s = "  ".join(f"{k}={v:.1%}" for k, v in items if v > 0.001)
        print(f"  [{rg}] {s}")

    print(f"\n데이터 로딩 [{START} ~ {END}], split={SPLIT}...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    runs = [("현행", base), ("신규(사용자 재설계)", new_cfg)]
    train_rows, test_rows = {}, {}
    print("\n실행 중 (각 config 2010~2025 1회)...")
    for label, cfg in runs:
        print(f"  [{label}]")
        tr, te = run_config(cfg, universe_px, signal_px, fred_history)
        train_rows[label] = tr
        test_rows[label] = te

    def table(title, rows):
        print(f"\n{'='*92}\n  {title}\n{'='*92}")
        h = (f"  {'설정':>18}{'Martin':>9}{'CAGR':>9}{'Ulcer':>8}{'최장UW':>8}"
             f"{'롤3y최악':>10}{'MaxDD':>9}{'tx':>8}")
        print(h); print("  " + "─" * (len(h)))
        bm = rows["현행"]["Martin"]
        for label, r in rows.items():
            d = r["Martin"] - bm
            mark = "" if label == "현행" else f"  Δ{d:+.2f}"
            print(f"  {label:>18}{r['Martin']:>9.2f}{r['CAGR']:>9.1%}{r['Ulcer']:>8.2f}"
                  f"{int(r['uw_max']):>8}{r['r3w']:>10.1%}{r['MaxDD']:>9.1%}{r['tx']:>8.2%}{mark}")

    table("학습창 TRAIN 2010-01~2018-12 (in-sample)", train_rows)
    table(f"검증창 TEST {SPLIT[:7]}~{END} (OUT-OF-SAMPLE)", test_rows)

    print("\n  판정(규칙4): TEST Martin·Ulcer·회복기간으로 판단. 현행 대비 개선이면 라이브 검토.")
    print("  주의: core30·vol targeting 흡수·단일경로·USD단일. 라이브 반영은 확인 후.")


if __name__ == "__main__":
    main()
