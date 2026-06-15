"""
워크포워드 OOS: Slowdown·Crisis 비중조정 후보 — 에피소드 진단 기반.

질문(2026-06-15): regime_episode_split 결과 — Slowdown은 방어3종(bond·gold·tips) 양 시대 양수
  (안정), Crisis는 bond·gold 강건하나 eqETF는 +39%(2011)→−23%(2020+) 역전. 진단상:
   SL1 [Slowdown] eqETF 15→10 → bond 27→32 (약체 주식↓·안정 채권↑)
   SL2 [Slowdown] commodity 5→0 → gold 14→18(+4)·bond 27→28(+1) (약체 원자재 제거)
   CR1 [Crisis] eqFac 6→2 + commodity 5→2 → bond 20→27 (신뢰성 약체 위성↓·강건 채권↑)
   CR2 [Crisis] eqETF 10→6 → bond 20→24 (에피소드-불안정 V자 베팅 축소 — TEST서 −1.77이라 도움 가설)
방법(규칙4): walkforward_shrink_oos 하니스. TRAIN/TEST 4지표. drift 모드.
제약: gold cap 18, eqFac cap 10, 나머지 행선지 bond/cash 무캡. 라이브는 확인 후.
한계: Crisis는 TEST에 COVID·2022·2025 집중. 동시점·프록시·in-sample.
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


def sl1(cfg):
    s = cfg["regime_targets"]["Slowdown"]
    s["equity_etf"] = 0.10
    s["bond_krw"] = round(s["bond_krw"] + 0.05, 4)
    return cfg


def sl2(cfg):
    s = cfg["regime_targets"]["Slowdown"]
    s["commodity"] = 0.0
    s["gold"] = 0.18
    s["bond_krw"] = round(s["bond_krw"] + 0.01, 4)
    return cfg


def cr1(cfg):
    c = cfg["regime_targets"]["Crisis"]
    c["equity_factor"] = 0.02
    c["commodity"] = 0.02
    c["bond_krw"] = round(c["bond_krw"] + 0.07, 4)
    return cfg


def cr2(cfg):
    c = cfg["regime_targets"]["Crisis"]
    c["equity_etf"] = 0.06
    c["bond_krw"] = round(c["bond_krw"] + 0.04, 4)
    return cfg


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}], split={SPLIT}...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    runs = [
        ("현행(baseline)", copy.deepcopy(base)),
        ("SL1 둔화 eqETF→bond", sl1(copy.deepcopy(base))),
        ("SL2 둔화 comm→gold", sl2(copy.deepcopy(base))),
        ("CR1 위기 위성→bond", cr1(copy.deepcopy(base))),
        ("CR2 위기 eqETF→bond", cr2(copy.deepcopy(base))),
    ]

    print("\n실행 중 (각 config 2010~2025 1회, 수익 슬라이스)...")
    train_rows, test_rows = {}, {}
    for label, cfg in runs:
        print(f"  [{label}]")
        tr, te = run_config(cfg, universe_px, signal_px, fred_history)
        train_rows[label] = tr
        test_rows[label] = te

    def print_table(title, rows):
        print(f"\n{'='*108}")
        print(f"  {title}")
        print(f"{'='*108}")
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
    print_table(f"검증창 TEST {SPLIT[:7]} ~ {END} (OUT-OF-SAMPLE)", test_rows)

    print("\n  판정: TEST ΔMartin>0 + 회복(UW)·MaxDD 동반개선이면 채택검토. 미미하면 엔진흡수→현행유지.")
    print("  주의: drift 모드(라이브 동일). Slowdown 방어3종 안정·Crisis bond/gold 안정. 라이브는 확인 후.")


if __name__ == "__main__":
    main()
