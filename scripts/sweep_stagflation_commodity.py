"""
실험: Stagflation 원자재(commodity) 비중 축소 + 재배분 스윕 — 현행 시스템 + 고정 4지표.

질문(2026-06-13): #2·#3에서 Stagflation의 commodity 18%는 동시점 Martin -2.88·CAGR -48.6%로
  최하위권(forward 착시였음). rule이 Stagflation을 인플레 고점 뒤 성장쇼크 저점에서 늦게
  라벨하기 때문(#1). 그 구간에 실제 작동하는 인플레 헤지는 gold·TIPS다. commodity를
  18→12→6→0으로 줄이고 뺀 비중을 재배분하면 4지표(전체기간 + Stagflation 구간)가 어떻게 되나?

재배분 설계(사용자와 논의 후):
  - 주안(권고): freed → TIPS:gold = 50:50 (그 구간 작동하는 인플레 헤지, gold 집중 회피)
  - 중립 기준1: freed → cash (보수적 디리스크)
  - 중립 기준2: freed → 나머지 Stagflation 자산 비례분배 (commodity 외 전부 pro-rata)

고정(규칙5 합의): vol ON(floor 0.65), drift 리밸(config 0.015), DD scaling OFF, core30 ON.
  regime_targets.Stagflation만 토글. 라이브/엔진/config 변경 없음(진단).

교란(규칙5 사용자 고지 완료):
  - core30이 Stagflation 비중을 70%에만 적용 + 코어(Goldilocks)에 commodity 5% → 효과 30% 희석.
  - dynamic_class_caps(VIX>30 commodity 50%↓)가 2022 일부 날 commodity를 이미 축소.
  - Stagflation 163일(전체 4%) → 전체기간 4지표는 거의 안 움직임 → Stagflation 구간 지표 병행.
  - vol_targeting은 equity만 축소(commodity 비대상) → commodity엔 교란 아님.
"""
from __future__ import annotations

import copy
import sys
import warnings
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "scripts"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from data import load_all_prices  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
from metrics import compute_metrics, rolling_cagr, recovery_duration  # noqa: E402
from compare_rule_timing_ab import START, END, REBAL_FREQ, TX_COST, crisis_maxdd  # noqa: E402

CRISIS_WINDOWS = {"COVID 2020": ("2020-02-19", "2020-04-30"),
                  "Bear 2022": ("2022-01-01", "2022-12-31")}


def make_stagflation(base_targets, commodity_w, sink):
    """Stagflation 비중 dict 생성: commodity를 commodity_w로 낮추고 freed를 sink로 재배분."""
    sf = copy.deepcopy(base_targets["Stagflation"])
    freed = sf["commodity"] - commodity_w
    sf["commodity"] = commodity_w
    if freed <= 1e-9:
        return sf
    if sink == "tips_gold":
        sf["bond_tips"] = sf.get("bond_tips", 0.0) + freed * 0.5
        sf["gold"] = sf.get("gold", 0.0) + freed * 0.5
    elif sink == "cash":
        sf["cash"] = sf.get("cash", 0.0) + freed
    elif sink == "prorata":
        # commodity 제외 나머지 자산군에 현재 비중 비례 분배
        others = {k: v for k, v in sf.items() if k != "commodity" and v > 0}
        tot = sum(others.values())
        for k, v in others.items():
            sf[k] += freed * (v / tot)
    return sf


def run_cell(label, commodity_w, sink, base, universe_px, signal_px, fred_history):
    config = copy.deepcopy(base)
    config["regime_targets"]["Stagflation"] = make_stagflation(
        base["regime_targets"], commodity_w, sink)
    rb = config.get("rebalancing", {})
    eng = BacktestEngine(
        config=config, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=float(rb.get("drift_threshold", 0.015)),
        cooldown_days=int(rb.get("min_rebalance_interval_days", 0)),
        fred_history=fred_history,
    )
    res = eng.run()
    r = res["returns"].dropna()
    m = compute_metrics(r)
    rc3 = rolling_cagr(r, years=3.0)
    rc5 = rolling_cagr(r, years=5.0)
    rec = recovery_duration(r)

    # Stagflation 구간 자체 지표 (acting regime == Stagflation 인 날의 수익만)
    sf_mask = (res["regime"] == "Stagflation").reindex(r.index, fill_value=False)
    sf_r = r[sf_mask]
    sf_m = compute_metrics(sf_r) if len(sf_r) >= 10 else {}

    return {
        "전략": label,
        "r3w": rc3["worst"], "r3m": rc3["median"], "r5w": rc5["worst"],
        "Ulcer": m.get("ulcer", 0.0),
        "rec_dd": rec["maxdd_recovery_days"], "uw_max": rec["max_underwater_days"],
        "Martin": m.get("martin", 0.0),
        "CAGR": m.get("cagr", 0.0), "MaxDD": m.get("max_drawdown", 0.0),
        "tx": float(res["tx_cost"].sum()),
        "Bear22": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["Bear 2022"]),
        # Stagflation 구간
        "sf_n": len(sf_r), "sf_cagr": sf_m.get("cagr", float("nan")),
        "sf_martin": sf_m.get("martin", float("nan")),
        "sf_maxdd": sf_m.get("max_drawdown", float("nan")),
    }


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    vt = base.get("vol_targeting", {})
    print(f"데이터 로딩 [{START} ~ {END}]... (vol_floor={vt.get('floor')}, drift 리밸, core30)")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    cur_c = base["regime_targets"]["Stagflation"]["commodity"]
    cells = [
        ("현행 c18%", 0.18, "tips_gold"),
        ("c12→TIPS+금", 0.12, "tips_gold"),
        ("c6→TIPS+금", 0.06, "tips_gold"),
        ("c0→TIPS+금", 0.0, "tips_gold"),
        ("c0→현금", 0.0, "cash"),
        ("c0→비례분배", 0.0, "prorata"),
    ]

    print("\n전략 실행 중...")
    rows = []
    for label, cw, sink in cells:
        print(f"  [{label}]")
        rows.append(run_cell(label, cw, sink, base, universe_px, signal_px, fred_history))
    df = pd.DataFrame(rows).set_index("전략")

    print(f"\n{'='*128}")
    print(f"  Stagflation 원자재 축소+재배분 스윕 — 전체기간 4지표 (floor{vt.get('floor')}·drift·core30)")
    print(f"{'='*128}")
    h = (f"  {'전략':>14}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'tx':>7}{'Bear22':>8}")
    print(h)
    print("  " + "─" * (len(h) + 4))
    for label, r in df.iterrows():
        mark = " ◀현행" if "현행" in label else ""
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {label:>14}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}"
              f"{r['tx']:>7.2%}{r['Bear22']:>8.1%}{mark}")

    print(f"\n  ── Stagflation 구간 자체 지표 (acting regime=Stagflation 인 날만) ──")
    print(f"  {'전략':>14}{'sf일수':>8}{'sf_CAGR':>10}{'sf_Martin':>11}{'sf_MaxDD':>10}")
    print("  " + "─" * 56)
    for label, r in df.iterrows():
        mark = " ◀현행" if "현행" in label else ""
        print(f"  {label:>14}{int(r['sf_n']):>8}{r['sf_cagr']:>10.1%}"
              f"{r['sf_martin']:>11.2f}{r['sf_maxdd']:>10.1%}{mark}")

    print("\n  판정(규칙4): Martin(1차)·롤링CAGR·Ulcer·회복기간. Stagflation 163일(전체4%)이라")
    print("  전체기간 변화는 작음 → sf구간 지표로 방향 확인. 단 sf구간은 비연속·소표본.")
    print("  주의: core30 30% 희석, VIX캡 일부 작동, USD단일·in-sample.")
    return df


if __name__ == "__main__":
    main()
