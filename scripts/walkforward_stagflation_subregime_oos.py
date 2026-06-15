"""
워크포워드 OOS: 스태그 하위국면 분기 — 긴축형(실질금리↑)에서 금·tips 축소.

질문(2026-06-15): stagflation_realrate_split.py 진단 — 실질금리 3개월 변화가 스태그를 가른다.
  실질금리 하락(디스인플레형) 시 금 Martin+25.25·tips+22.71·bond+77.11 전부 영웅,
  상승(긴축형) 시 금 −0.96·tips −0.24 부호역전·bond +3.79 급약화·cash 강건. 처방:
  긴축형 스태그에서만 금·tips→채권/cash. 디스인플레형은 현행 유지(엔진의 base Stagflation).
방법: engine.py stagflation_subregime 오버레이(되돌림 가능). enabled 시 스태그 슬롯이
  real_rate_chg_3m≥0인 날 tightening_targets로 교체. drift 모드·4지표(규칙4).
제약: 합 1.00 유지. 디스인플레형(실질금리↓)은 base 그대로라 단일변수 비교.
한계: 스태그 소표본을 또 분할(긴축 114·디스인플레 49일), TEST 2022 집중. 동시점·프록시.
  교란(규칙5): core30이 70%로 희석·vol targeting 0.08 흡수 가능 → 씻겨나갈 위험 있음.
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


def _tightening(base_stag, gold, tips, comm, to_bond, to_cash):
    """base Stagflation을 복사해 긴축형 변형 생성. 합 1.00 유지."""
    t = dict(base_stag)
    t["gold"] = gold
    t["bond_tips"] = tips
    t["commodity"] = comm
    t["bond_krw"] = round(base_stag["bond_krw"] + to_bond, 4)
    t["cash"] = round(base_stag["cash"] + to_cash, 4)
    return t


def with_subregime(cfg, tight):
    cfg["stagflation_subregime"] = {
        "enabled": True,
        "split_feature": "real_rate_chg_3m",
        "threshold": 0.0,
        "tightening_targets": tight,
    }
    return cfg


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    stag = base["regime_targets"]["Stagflation"]

    # ST1: 금 18→10·tips 8→4 (−0.12) → bond +0.08·cash +0.04. 진단 직격(금·tips만).
    st1 = _tightening(stag, gold=0.10, tips=0.04, comm=stag["commodity"], to_bond=0.08, to_cash=0.04)
    # ST2: ST1 + 긴축형서도 약체인 commodity 18→14(−0.04) → cash +0.04 추가.
    st2 = _tightening(stag, gold=0.10, tips=0.04, comm=0.14, to_bond=0.08, to_cash=0.08)

    for nm, t in [("ST1", st1), ("ST2", st2)]:
        s = round(sum(t.values()), 4)
        assert abs(s - 1.0) < 1e-6, f"{nm} 합 {s} ≠ 1.0"

    print(f"데이터 로딩 [{START} ~ {END}], split={SPLIT}...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    runs = [
        ("현행(baseline)", copy.deepcopy(base)),
        ("ST1 긴축형 금·tips→bond/cash", with_subregime(copy.deepcopy(base), st1)),
        ("ST2 ST1+comm→cash", with_subregime(copy.deepcopy(base), st2)),
    ]

    print("\n실행 중 (각 config 2010~2025 1회, 수익 슬라이스)...")
    train_rows, test_rows = {}, {}
    for label, cfg in runs:
        print(f"  [{label}]")
        tr, te = run_config(cfg, universe_px, signal_px, fred_history)
        train_rows[label] = tr
        test_rows[label] = te

    def print_table(title, rows):
        print(f"\n{'='*112}")
        print(f"  {title}")
        print(f"{'='*112}")
        h = (f"  {'전략':>28}{'Martin':>9}{'CAGR':>9}{'Ulcer':>8}{'최장UW':>8}"
             f"{'롤3y최악':>10}{'MaxDD':>9}{'tx':>8}")
        print(h)
        print("  " + "─" * (len(h)))
        base_m = rows["현행(baseline)"]["Martin"]
        for label, r in rows.items():
            d = r["Martin"] - base_m
            mark = "" if label == "현행(baseline)" else f"  ΔMartin {d:+.2f}"
            print(f"  {label:>28}{r['Martin']:>9.2f}{r['CAGR']:>9.1%}{r['Ulcer']:>8.2f}"
                  f"{int(r['uw_max']):>8}{r['r3w']:>10.1%}{r['MaxDD']:>9.1%}{r['tx']:>8.2%}{mark}")

    print_table("학습창 TRAIN 2010-01 ~ 2018-12 (in-sample)", train_rows)
    print_table(f"검증창 TEST {SPLIT[:7]} ~ {END} (OUT-OF-SAMPLE)", test_rows)

    print("\n  판정: TEST ΔMartin>0 + 회복(UW)·MaxDD 동반개선이면 채택검토. 미미하면 엔진흡수→현행유지.")
    print("  주의: 긴축형 분기만 변경(디스인플레형 base). drift 모드. 라이브는 확인 후.")


if __name__ == "__main__":
    main()
