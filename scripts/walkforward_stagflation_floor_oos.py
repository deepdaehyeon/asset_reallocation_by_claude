"""
워크포워드 OOS: 긴축형 스태그(실질금리↑)에서 vol floor를 낮춰 주식을 더 깊이 축소.

질문(2026-06-15): vol 목표만 0.08→0.04로 조이니 정확히 0 변화
  (walkforward_stagflation_voltighten_oos). 진단 결과 긴축형일의 50%는 실현변동성>12.3%라
  이미 floor(0.65, 주식 최대 35% 축소)에 클램프 → 목표를 낮춰도 무효. **진짜 묶는 건 floor.**
  그래서 긴축형 스태그에서만 floor를 0.65→0.50/0.40/0.30으로 낮춰 주식을 더 깊이(50/60/70%)
  줄이도록 한다. 디스인플레형은 0.65 유지.
방법: engine.py stagflation_subregime의 tightening_floor(목표 0.08 유지, floor만 교체).
  floor는 전역값이라 acting_regime=="Stagflation"일 때만 적용(게이팅). drift·4지표(규칙4).
변형: FL50(0.50), FL40(0.40), FL30(0.30).
한계: 스태그 소표본·TEST 2022 집중. core30 70% 희석(코어 vol 면제). 라이브는 확인 후.
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


def with_floor(cfg, floor):
    cfg["stagflation_subregime"] = {
        "enabled": True,
        "split_feature": "real_rate_chg_3m",
        "threshold": 0.0,
        "tightening_floor": floor,
    }
    return cfg


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)

    print(f"데이터 로딩 [{START} ~ {END}], split={SPLIT}...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    runs = [
        ("현행(baseline, floor 0.65)", copy.deepcopy(base)),
        ("FL50 긴축형 floor→0.50", with_floor(copy.deepcopy(base), 0.50)),
        ("FL40 긴축형 floor→0.40", with_floor(copy.deepcopy(base), 0.40)),
        ("FL30 긴축형 floor→0.30", with_floor(copy.deepcopy(base), 0.30)),
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
        base_m = rows["현행(baseline, floor 0.65)"]["Martin"]
        for label, r in rows.items():
            d = r["Martin"] - base_m
            mark = "" if "baseline" in label else f"  ΔMartin {d:+.2f}"
            print(f"  {label:>26}{r['Martin']:>9.2f}{r['CAGR']:>9.1%}{r['Ulcer']:>8.2f}"
                  f"{int(r['uw_max']):>8}{r['r3w']:>10.1%}{r['MaxDD']:>9.1%}{r['tx']:>8.2%}{mark}")

    print_table("학습창 TRAIN 2010-01 ~ 2018-12 (in-sample)", train_rows)
    print_table(f"검증창 TEST {SPLIT[:7]} ~ {END} (OUT-OF-SAMPLE)", test_rows)

    print("\n  판정: TEST ΔMartin>0 + 회복(UW)·MaxDD 동반개선이면 채택검토. floor가 진짜 묶는 레버라면")
    print("        target과 달리 효과가 나타나야 함. 미미하면 스태그는 vol로도 개선 불가 결론.")
    print("  주의: 긴축형(실질금리↑)·acting=Stagflation에만 floor 인하. drift 모드. 라이브는 확인 후.")


if __name__ == "__main__":
    main()
