"""
실험: 채권 KRW 통합 — bond_usd(IEF/SHY)를 bond_krw(305080)로 합쳐 합성 제거.

배경: 라이브에서 305080은 자기 목표치 + USD계좌 부족분 잔차를 함께 떠안아
    0%↔11%로 출렁이며 왕복 거래(낭비)를 유발(2026-06-11 회전율 분석).
    근본 해결: 채권 노출을 처음부터 KRW종목으로 확정 → bond_krw_extra 합성 경로 제거.

변경: 각 레짐에서 bond_krw += bond_usd, bond_usd = 0 (bond_tips/VTIP은 유지).

백테스트 한계(중요):
  - data.py가 305080→IEF로 프록시하므로, 백테스트상 실제 차이는
    bond_usd의 SHY(1-3년, 라우팅 0.42)가 IEF(10년)로 바뀌어 듀레이션이 길어지는 것뿐.
  - 환율 효과 미반영(USD 단일 통화) → IEF↔305080 환/헤지 차이는 백테스트가 못 잡음.
  → 따라서 이 실험은 "듀레이션 연장이 방어/수익을 해치지 않는가"의 동등성 검증이고,
    운영상 합성 제거 효과(왕복 감소)는 라이브에서만 실현된다.

코드 변경 없음(config는 메모리에서만 변형). 결과는 docs/에 저장.
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
from engine import BacktestEngine  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
from prototype_forward_regime_predictability import run_engine, bt_row  # noqa: E402
from compare_rule_timing_ab import START, END  # noqa: E402


def fold_bond_usd_into_krw(config: dict) -> dict:
    """각 레짐에서 bond_usd 비중을 bond_krw로 이전하고 bond_usd=0."""
    cfg = copy.deepcopy(config)
    moved = {}
    for regime, tgt in cfg["regime_targets"].items():
        bu = tgt.get("bond_usd", 0.0)
        if bu > 0:
            tgt["bond_krw"] = tgt.get("bond_krw", 0.0) + bu
            tgt["bond_usd"] = 0.0
            moved[regime] = bu
    return cfg, moved


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    variant, moved = fold_bond_usd_into_krw(base)

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    print("\n레짐별 bond_usd → bond_krw 이전량:")
    for r, v in moved.items():
        nb = variant["regime_targets"][r]["bond_krw"]
        print(f"  {r:<12} bond_usd {v:.2f} → bond_krw (합산 {nb:.2f})")

    print("\nbaseline(현행) 백테스트...")
    base_res = bt_row("baseline(현행)", run_engine(BacktestEngine, universe_px, signal_px, fred_history, base))
    print("variant(채권 KRW 통합) 백테스트...")
    var_res = bt_row("채권KRW통합", run_engine(BacktestEngine, universe_px, signal_px, fred_history, variant))

    print(f"\n{'='*94}\n  채권 KRW 통합 — 동등성 검증 (전체 기간 {START}~{END})\n{'='*94}")
    hdr = (f"  {'전략':>16}{'CAGR':>8}{'Sharpe':>8}{'MaxDD':>9}{'Calmar':>8}"
           f"{'리밸':>7}{'tx누적':>9}{'COVID':>9}{'Bear22':>9}")
    print(hdr)
    print("  " + "─" * (len(hdr) + 4))
    for r in (base_res, var_res):
        print(f"  {r['전략']:>16}{r['CAGR']:>8.1%}{r['Sharpe']:>8.2f}{r['MaxDD']:>8.1%}"
              f"{r['Calmar']:>8.2f}{int(r['리밸']):>7}{r['tx누적']:>8.2%}"
              f"{r['COVID']:>8.1%}{r['Bear22']:>8.1%}")

    print("\n  주의: 백테스트는 305080→IEF 프록시·환율 미반영이라 합성 제거 효과는 못 보이고,")
    print("        SHY(1-3년)→IEF(10년) 듀레이션 연장 효과만 반영된다.")


if __name__ == "__main__":
    main()
