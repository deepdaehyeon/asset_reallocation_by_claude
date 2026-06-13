"""
실험: Goldilocks 금(gold) 비중 축소+재배분 스윕 — 숨은 위기 헤지인가, 진짜 죽은 비중인가?

질문(2026-06-13): #2·#3에서 Goldilocks gold 10%는 동시점 Martin -0.14·CAGR -1.4%로 죽은
  비중(8/11위). 단 Stagflation 원자재처럼 "단독 약함 ≠ 제거"일 수 있다(gold는 위기 분산 헤지).
  Goldilocks gold를 10→6→3→0으로 줄이고 뺀 비중을 equity_etf(Goldilocks 데이터 최강)·현금·
  비례분배로 재배분하면 4지표 + 위기 낙폭(COVID·Bear22)이 어떻게 되나?

핵심 가설: core30(상시 Goldilocks)이 gold를 모든 레짐에서 ~3%(30%×10%) 상시 보유한다.
  gold 축소 = 위기 때 상시 gold 헤지 축소. 위기 낙폭·회복기간이 악화되면 gold는 숨은 헤지(유지),
  악화 안 되면 진짜 죽은 비중(축소). Stagflation 원자재 스윕의 회복기간 역전과 같은 검증.

재배분: 주안 → equity_etf(Goldilocks Martin 1·3위). 비교 → 현금, 나머지 비례분배.

고정(규칙5 합의): vol ON(floor 0.65), drift 리밸, DD scaling OFF, core30 ON. Goldilocks만 토글.
교란(고지 완료): core30이 gold를 상시 보유(전 레짐 영향) / 재배분처 equity는 vol_targeting이
  변동성 클 때 부분 상쇄 / Goldilocks 2210일(전체 58%)이라 전체기간이 크게 움직임(희석 없음).
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


def make_goldilocks(base_targets, gold_w, sink):
    g = copy.deepcopy(base_targets["Goldilocks"])
    freed = g["gold"] - gold_w
    g["gold"] = gold_w
    if freed <= 1e-9:
        return g
    if sink == "equity":
        g["equity_etf"] = g.get("equity_etf", 0.0) + freed
    elif sink == "cash":
        g["cash"] = g.get("cash", 0.0) + freed
    elif sink == "prorata":
        others = {k: v for k, v in g.items() if k != "gold" and v > 0}
        tot = sum(others.values())
        for k, v in others.items():
            g[k] += freed * (v / tot)
    return g


def run_cell(label, gold_w, sink, base, universe_px, signal_px, fred_history):
    config = copy.deepcopy(base)
    config["regime_targets"]["Goldilocks"] = make_goldilocks(
        base["regime_targets"], gold_w, sink)
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
    return {
        "전략": label,
        "r3w": rc3["worst"], "r3m": rc3["median"], "r5w": rc5["worst"],
        "Ulcer": m.get("ulcer", 0.0),
        "rec_dd": rec["maxdd_recovery_days"], "uw_max": rec["max_underwater_days"],
        "Martin": m.get("martin", 0.0),
        "CAGR": m.get("cagr", 0.0), "MaxDD": m.get("max_drawdown", 0.0),
        "tx": float(res["tx_cost"].sum()),
        "COVID": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["COVID 2020"]),
        "Bear22": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["Bear 2022"]),
    }


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    vt = base.get("vol_targeting", {})
    print(f"데이터 로딩 [{START} ~ {END}]... (vol_floor={vt.get('floor')}, drift, core30)")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    cells = [
        ("현행 g10%", 0.10, "equity"),
        ("g6→주식", 0.06, "equity"),
        ("g3→주식", 0.03, "equity"),
        ("g0→주식", 0.0, "equity"),
        ("g0→현금", 0.0, "cash"),
        ("g0→비례분배", 0.0, "prorata"),
    ]

    print("\n전략 실행 중...")
    rows = []
    for label, gw, sink in cells:
        print(f"  [{label}]")
        rows.append(run_cell(label, gw, sink, base, universe_px, signal_px, fred_history))
    df = pd.DataFrame(rows).set_index("전략")

    print(f"\n{'='*128}")
    print(f"  Goldilocks 금 축소+재배분 스윕 — 전체기간 4지표 + 위기 (floor{vt.get('floor')}·drift·core30)")
    print(f"{'='*128}")
    h = (f"  {'전략':>14}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'tx':>7}"
         f"{'COVID':>8}{'Bear22':>8}")
    print(h)
    print("  " + "─" * (len(h) + 4))
    for label, r in df.iterrows():
        mark = " ◀현행" if "현행" in label else ""
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {label:>14}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}"
              f"{r['tx']:>7.2%}{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")

    print("\n  판정(규칙4): Martin(1차)·롤링CAGR·Ulcer·회복기간. COVID·Bear22로 gold 헤지가치 확인.")
    print("  관전: gold 축소+주식 추가가 위기낙폭/회복기간 악화시키면 gold=숨은헤지(유지),")
    print("        악화 없으면 죽은비중(축소). core30이 gold를 상시 보유함에 유의.")
    print("  주의: Goldilocks 58%라 전체기간 크게 움직임. USD단일·in-sample.")
    return df


if __name__ == "__main__":
    main()
