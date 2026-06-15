"""
워크포워드 OOS: 긴축형 스태그(실질금리↑)에서 vol 목표를 더 조이는 레버.

질문(2026-06-15): 스태그 하위국면 분기를 *비중*으로 처방하니 엔진이 흡수
  (experiment_2026-06-15_stagflation_subregime_oos: ST1 −0.03·ST2 −0.14). 그러나
  vol targeting은 SL2처럼 엔진이 흡수하지 않는 실제 레버([[feedback-regime-timing-lever]],
  voltarget-blend-defense-engine). 그래서 실질금리를 비중 스위치가 아니라 **긴축형 스태그
  에서 vol 목표(현재 0.08)를 더 조이는** 레버로 쓴다. 디스인플레형은 0.08 유지.
방법: engine.py stagflation_subregime 오버레이의 tightening_vol(가중치 불변, vol만 교체).
  real_rate_chg_3m≥0인 날 Stagflation vol 목표를 VL값으로. drift 모드·4지표(규칙4).
변형: VL06(0.08→0.06, Crisis급), VL05(0.05), VL04(0.04, 최강 방어).
한계: 스태그 소표본·TEST 2022 집중. 동시점·프록시. core30 70% 희석(코어는 vol 면제)으로
  레버 효과가 위성 70%에 한정. 라이브는 확인 후.
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


def with_voltighten(cfg, vol):
    cfg["stagflation_subregime"] = {
        "enabled": True,
        "split_feature": "real_rate_chg_3m",
        "threshold": 0.0,
        "tightening_vol": vol,
    }
    return cfg


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)

    print(f"데이터 로딩 [{START} ~ {END}], split={SPLIT}...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    runs = [
        ("현행(baseline, vol 0.08)", copy.deepcopy(base)),
        ("VL06 긴축형 vol→0.06", with_voltighten(copy.deepcopy(base), 0.06)),
        ("VL05 긴축형 vol→0.05", with_voltighten(copy.deepcopy(base), 0.05)),
        ("VL04 긴축형 vol→0.04", with_voltighten(copy.deepcopy(base), 0.04)),
    ]

    print("\n실행 중 (각 config 2010~2025 1회, 수익 슬라이스)...")
    train_rows, test_rows = {}, {}
    for label, cfg in runs:
        print(f"  [{label}]")
        tr, te = run_config(cfg, universe_px, signal_px, fred_history)
        train_rows[label] = tr
        test_rows[label] = te

    def print_table(title, rows):
        print(f"\n{'='*110}")
        print(f"  {title}")
        print(f"{'='*110}")
        h = (f"  {'전략':>26}{'Martin':>9}{'CAGR':>9}{'Ulcer':>8}{'최장UW':>8}"
             f"{'롤3y최악':>10}{'MaxDD':>9}{'tx':>8}")
        print(h)
        print("  " + "─" * (len(h)))
        base_m = rows["현행(baseline, vol 0.08)"]["Martin"]
        for label, r in rows.items():
            d = r["Martin"] - base_m
            mark = "" if "baseline" in label else f"  ΔMartin {d:+.2f}"
            print(f"  {label:>26}{r['Martin']:>9.2f}{r['CAGR']:>9.1%}{r['Ulcer']:>8.2f}"
                  f"{int(r['uw_max']):>8}{r['r3w']:>10.1%}{r['MaxDD']:>9.1%}{r['tx']:>8.2%}{mark}")

    print_table("학습창 TRAIN 2010-01 ~ 2018-12 (in-sample)", train_rows)
    print_table(f"검증창 TEST {SPLIT[:7]} ~ {END} (OUT-OF-SAMPLE)", test_rows)

    print("\n  판정: TEST ΔMartin>0 + 회복(UW)·MaxDD 동반개선이면 채택검토. vol은 흡수되지 않는 레버라")
    print("        비중 처방(ST1/ST2 흡수)과 달리 효과 가능성↑. 미미하면 현행 0.08 유지.")
    print("  주의: 긴축형(실질금리↑)만 조임, 디스인플레형 0.08 유지. drift 모드. 라이브는 확인 후.")


if __name__ == "__main__":
    main()
