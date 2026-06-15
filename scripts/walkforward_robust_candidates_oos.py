"""
워크포워드 OOS 검증: MV 최적화기·동시점 진단이 *함께* 가리킨 견고 후보 2건이
  미지 구간(2019~2025)에서 4지표를 개선하는가?

질문(2026-06-15): 사용자 "최적화기 → OOS 검증" 경로. regime_mv_optimizer.py(자산군 MV)와
  동시점 진단([[experiment_2026-06-13_regime_targets_contrast]])이 동시에 가리킨 2건만 검증:
   C1 [Crisis] bond_tips 10%→bond: Crisis tips는 약한 분산자(주식상관 -0.07)인데 bond는
      강한 분산자(-0.44)·연수익 +13% vs tips -2%. → tips 10%를 bond로 이동.
   C2 [Stagflation] commodity 18%→10%: Stag commodity 연수익 -63%·주식상관 +0.22(분산 약함),
      MV가 일관 축소. → 8%p를 bond(Stag 최강 분산자 -0.29·연수익 +25%)로 이동.
  per-regime 비중 미세튜닝은 노이즈([[feedback-regime-targets-no-tuning]])라, 둘 다 OOS에서
  개선 없으면 현행 유지(엔진 흡수 확인). 개선이 견고하면 그 방향만 채택 검토.

방법(규칙4): walkforward_shrink_oos.py 하니스 재사용. 각 config를 2010~2025 1회 실행
  (config 전구간 고정 → 2019~ 수익은 config 선택의 진짜 OOS). 수익을 TRAIN(≤2018)·
  TEST(≥2019)로 슬라이스해 4지표(Martin·Ulcer·회복·롤3y CAGR·MaxDD). drift 모드(라이브 동일).
한계: TEST 6.3년 단일 경로(COVID·Bear22 각 1회). Crisis·Stag은 TEST 내 일부 구간만 발생 →
  엔진(vol targeting·class cap·core30·drift)이 비중차를 흡수하면 효과 미미할 수 있음(그 자체가 결론).
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


def apply_c1(cfg):
    """Crisis: bond_tips 0.10 → bond_krw."""
    cr = cfg["regime_targets"]["Crisis"]
    moved = cr.get("bond_tips", 0.0)
    cr["bond_tips"] = 0.0
    cr["bond_krw"] = round(cr.get("bond_krw", 0.0) + moved, 4)
    return cfg


def apply_c2(cfg):
    """Stagflation: commodity 0.18 → 0.10, 차이를 bond_krw로."""
    st = cfg["regime_targets"]["Stagflation"]
    moved = round(st.get("commodity", 0.0) - 0.10, 4)
    st["commodity"] = 0.10
    st["bond_krw"] = round(st.get("bond_krw", 0.0) + moved, 4)
    return cfg


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}], split={SPLIT}...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    runs = [("현행(baseline)", copy.deepcopy(base))]
    runs.append(("C1 Crisis tips→bond", apply_c1(copy.deepcopy(base))))
    runs.append(("C2 Stag comm 18→10", apply_c2(copy.deepcopy(base))))
    both = apply_c2(apply_c1(copy.deepcopy(base)))
    runs.append(("C1+C2 둘 다", both))

    print("\n실행 중 (각 config 2010~2025 1회, 수익 슬라이스)...")
    train_rows, test_rows = {}, {}
    for label, cfg in runs:
        print(f"  [{label}]")
        tr, te = run_config(cfg, universe_px, signal_px, fred_history)
        train_rows[label] = tr
        test_rows[label] = te

    def print_table(title, rows):
        print(f"\n{'='*104}")
        print(f"  {title}")
        print(f"{'='*104}")
        h = (f"  {'전략':>22}{'Martin':>9}{'CAGR':>9}{'Ulcer':>8}{'최장UW':>8}"
             f"{'롤3y최악':>10}{'MaxDD':>9}{'tx':>8}")
        print(h)
        print("  " + "─" * (len(h)))
        base_m = rows["현행(baseline)"]["Martin"]
        for label, r in rows.items():
            d = r["Martin"] - base_m
            mark = "" if label == "현행(baseline)" else f"  ΔMartin {d:+.2f}"
            print(f"  {label:>22}{r['Martin']:>9.2f}{r['CAGR']:>9.1%}{r['Ulcer']:>8.2f}"
                  f"{int(r['uw_max']):>8}{r['r3w']:>10.1%}{r['MaxDD']:>9.1%}{r['tx']:>8.2%}{mark}")

    print_table("학습창 TRAIN 2010-01 ~ 2018-12 (in-sample)", train_rows)
    print_table(f"검증창 TEST {SPLIT[:7]} ~ {END} (OUT-OF-SAMPLE, COVID+Bear22 포함)", test_rows)

    print("\n  판정:")
    print("  • TEST에서 ΔMartin>0 + 회복기간(UW)·MaxDD 동반 개선이면 그 후보만 채택 검토.")
    print("  • TEST 변화 미미(±0.05 이내)면 엔진 흡수 — 현행 유지([[feedback-regime-targets-no-tuning]]).")
    print("  주의: TEST 6.3년 단일경로. Crisis·Stag은 TEST 내 일부 구간만 발생. drift 모드(라이브 동일).")


if __name__ == "__main__":
    main()
