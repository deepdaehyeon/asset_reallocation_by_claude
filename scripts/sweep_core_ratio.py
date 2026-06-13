"""
실험: 코어(고정 Goldilocks) 비중 스윕 0 / 30 / 50 / 70% — 현행 시스템 + 고정 4지표.

질문(2026-06-13): core+satellite에서 코어 30%는 정적 Goldilocks(vol 면제 회복 앵커)다.
  코어 비중을 0(코어 없음)·30(현행)·50·70%로 바꾸면 4지표가 어떻게 움직이나?
  코어↑ = vol 면제·정적 Goldilocks 질량↑ = 회복 참여↑·엔진 반응↓.

고정(규칙5 합의): vol ON(floor 0.65), drift 리밸(config 0.015), DD scaling OFF, 나머지 현행.
  core_ratio만 토글. 라이브/엔진/config 변경 없음(진단).
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

# core_ratio 값 (0 = 코어 없음)
RATIOS = [0.0, 0.30, 0.50, 0.70]


def run_cell(ratio, base, universe_px, signal_px, fred_history):
    config = copy.deepcopy(base)
    cs = config.setdefault("core_satellite", {})
    if ratio <= 0:
        cs["enabled"] = False
    else:
        cs["enabled"] = True
        cs["core_ratio"] = ratio
    rb = config.get("rebalancing", {})
    label = f"core {int(ratio*100)}%" + ("(현행)" if abs(ratio - 0.30) < 1e-9 else "")
    print(f"  [{label}]")
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
        "리밸": int(res["rebalanced"].sum()), "tx": float(res["tx_cost"].sum()),
        "COVID": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["COVID 2020"]),
        "Bear22": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["Bear 2022"]),
    }


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    vt = base.get("vol_targeting", {})
    print(f"데이터 로딩 [{START} ~ {END}]... (vol_floor={vt.get('floor')}, drift 리밸)")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    print("\n전략 실행 중...")
    rows = [run_cell(x, base, universe_px, signal_px, fred_history) for x in RATIOS]
    df = pd.DataFrame(rows).set_index("전략")

    print(f"\n{'='*124}")
    print(f"  코어 비중 스윕 (0/30/50/70%) — 현행 시스템(floor{vt.get('floor')}·drift) + 고정 4지표")
    print(f"{'='*124}")
    h = (f"  {'전략':>14}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'리밸':>6}"
         f"{'tx':>7}{'COVID':>8}{'Bear22':>8}")
    print(h)
    print("  " + "─" * (len(h) + 6))
    for label, r in df.iterrows():
        mark = " ◀현행" if "현행" in label else ""
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {label:>14}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}"
              f"{int(r['리밸']):>6}{r['tx']:>7.2%}{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")

    print("\n  판정(CLAUDE.md 규칙4): Martin(1차)·롤링CAGR·Ulcer·회복기간. CAGR/MaxDD/COVID 보조.")
    print("  주의: USD 단일통화 백테스트 — 라이브 합성 순환매·실제 회전 미반영. in-sample.")
    return df


if __name__ == "__main__":
    main()
