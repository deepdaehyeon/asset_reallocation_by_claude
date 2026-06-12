"""
drift_threshold sweep — stabilize_mapping 적용 후 재탐색.

배경: 기존 drift 임계 선택(1.5%)은 label-switching 정렬(stabilize_mapping) 도입 전 결과.
정렬로 가짜 회전이 줄었으므로 최적 임계가 이동했을 수 있다. 현행 config(stabilize on,
cooldown 0, timing rule) 위에서 drift_threshold만 토글해 재탐색한다.

측정: 위험조정 지표 + 리밸 횟수·누적 tx 비용 + 위기 낙폭(COVID/Bear22).
코드 변경 없음 (시뮬레이션). 결과는 docs/에 저장.
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
from metrics import compute_metrics, rolling_cagr, recovery_duration  # noqa: E402
from compare_rule_timing_ab import START, END, REBAL_FREQ, TX_COST, crisis_maxdd  # noqa: E402

CRISIS_WINDOWS = {"COVID 2020": ("2020-02-19", "2020-04-30"),
                  "Bear 2022": ("2022-01-01", "2022-12-31")}
GRID = [0.005, 0.010, 0.015, 0.020, 0.030, 0.050, 0.080]


def run_cell(dt, config, universe_px, signal_px, fred_history):
    rb = config.get("rebalancing", {})
    print(f"  [drift={dt:.1%}] stabilize={config['hmm'].get('stabilize_mapping')}")
    res = BacktestEngine(
        config=config, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=dt,
        cooldown_days=int(rb.get("min_rebalance_interval_days", 0)),
        fred_history=fred_history,
    ).run()
    r = res["returns"].dropna()
    m = compute_metrics(r)
    rc3 = rolling_cagr(r, years=3.0)
    rc5 = rolling_cagr(r, years=5.0)
    rec = recovery_duration(r)
    return {
        "drift": dt,
        "CAGR": m.get("cagr", 0.0), "MaxDD": m.get("max_drawdown", 0.0),
        "Ulcer": m.get("ulcer", 0.0), "Martin": m.get("martin", 0.0),
        "r3w": rc3["worst"], "r3m": rc3["median"], "r5w": rc5["worst"],
        "rec_dd": rec["maxdd_recovery_days"], "uw_max": rec["max_underwater_days"],
        "리밸": int(res["rebalanced"].sum()), "tx누적": float(res["tx_cost"].sum()),
        "COVID": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["COVID 2020"]),
        "Bear22": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["Bear 2022"]),
    }


def main() -> None:
    from fetcher import fetch_fred_history
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]... "
          f"(timing={base.get('regime_filter', {}).get('regime_timing_source')}, "
          f"stabilize={base.get('hmm', {}).get('stabilize_mapping')}, "
          f"db={base.get('hmm', {}).get('mapping_deadband')})")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    cur = float(base.get("rebalancing", {}).get("drift_threshold", 0.015))
    print("\n전략 실행 중...")
    rows = [run_cell(dt, copy.deepcopy(base), universe_px, signal_px, fred_history) for dt in GRID]
    df = pd.DataFrame(rows).set_index("drift")

    print(f"\n{'='*116}")
    print(f"  drift_threshold sweep — 고정 4지표(롤링CAGR·Ulcer·회복기간·Martin), "
          f"stabilize on db={base['hmm'].get('mapping_deadband')} — 현행={cur:.1%}")
    print(f"{'='*116}")
    hdr = (f"  {'drift':>7}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
           f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'리밸':>7}{'tx':>7}{'│MaxDD':>8}")
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    best = df["Martin"].idxmax()
    for dt, r in df.iterrows():
        mark = " ◀현행" if abs(dt - cur) < 1e-9 else ""
        if dt == best:
            mark += " ★Martin최대"
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {dt:>6.1%}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{int(r['리밸']):>7}{r['tx누적']:>7.2%}"
              f"{r['MaxDD']:>8.1%}{mark}")
    print("  Martin 1차 판정, 동률·근소차는 회복기간·롤3y최악으로. tx·리밸은 회전비용 보조참고.")
    return df


if __name__ == "__main__":
    main()
