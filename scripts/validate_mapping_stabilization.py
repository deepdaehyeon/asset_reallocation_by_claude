"""
HMM label-switching 정렬(stabilize_mapping) 검증 — off vs on A/B.

배경: HMM을 매 실행 재학습 → 비지도 state→regime 매핑이 불안정(label-switching).
같은 데이터에도 Goldilocks↔Slowdown 라벨이 뒤집혀 blend 비중이 흔들리고 왕복 회전 발생.
처방(approach 2): 재학습 후 새 군집을 직전 실행(anchor) 군집에 1:1 매칭(Hungarian),
정규화 거리 ≤ deadband면 직전 라벨 물려받음 → 가짜 플립 차단.

이 스크립트: 동일 엔진·config, hmm.stabilize_mapping만 토글.
측정: 위험조정 지표(불변/개선이어야 안전) + 리밸 횟수·누적 tx 비용(회전 감소 기대).
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
from metrics import compute_metrics  # noqa: E402
from compare_rule_timing_ab import START, END, REBAL_FREQ, TX_COST, crisis_maxdd  # noqa: E402

CRISIS_WINDOWS = {"COVID 2020": ("2020-02-19", "2020-04-30"),
                  "Bear 2022": ("2022-01-01", "2022-12-31")}


def make_engine(config, universe_px, signal_px, fred_history):
    rb = config.get("rebalancing", {})
    return BacktestEngine(
        config=config, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=float(rb.get("drift_threshold", 0.015)),
        cooldown_days=int(rb.get("min_rebalance_interval_days", 0)),
        fred_history=fred_history,
    )


def run_cell(label, config, universe_px, signal_px, fred_history):
    print(f"  [{label}] stabilize_mapping={config['hmm'].get('stabilize_mapping')}")
    res = make_engine(config, universe_px, signal_px, fred_history).run()
    m = compute_metrics(res["returns"])
    return {
        "cell": label,
        "CAGR": m.get("cagr", 0.0),
        "Sharpe": m.get("sharpe", 0.0),
        "MaxDD": m.get("max_drawdown", 0.0),
        "Calmar": m.get("calmar", 0.0),
        "리밸": int(res["rebalanced"].sum()),
        "tx누적": float(res["tx_cost"].sum()),
        "COVID": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["COVID 2020"]),
        "Bear22": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["Bear 2022"]),
    }


def main() -> None:
    from fetcher import fetch_fred_history
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]... "
          f"(timing_source={base.get('regime_filter', {}).get('regime_timing_source')})")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    off = copy.deepcopy(base); off["hmm"]["stabilize_mapping"] = False

    print("\n전략 실행 중...")
    rows = [run_cell("off (현행)", off, universe_px, signal_px, fred_history)]
    for db in (0.3, 0.5, 0.75, 1.0):
        cfg = copy.deepcopy(base)
        cfg["hmm"]["stabilize_mapping"] = True
        cfg["hmm"]["mapping_deadband"] = db
        rows.append(run_cell(f"on db={db}", cfg, universe_px, signal_px, fred_history))
    df = pd.DataFrame(rows).set_index("cell")

    print(f"\n{'='*92}")
    print("  HMM 매핑 정렬(stabilize_mapping) off vs on")
    print(f"{'='*92}")
    hdr = (f"  {'cell':<16}{'CAGR':>7}{'Sharpe':>8}{'MaxDD':>9}{'Calmar':>8}"
           f"{'리밸':>7}{'tx누적':>9}{'COVID':>9}{'Bear22':>9}")
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    for label, r in df.iterrows():
        print(f"  {label:<16}{r['CAGR']:>6.1%}{r['Sharpe']:>8.2f}{r['MaxDD']:>8.1%}"
              f"{r['Calmar']:>8.2f}{int(r['리밸']):>7}{r['tx누적']:>8.2%}"
              f"{r['COVID']:>8.1%}{r['Bear22']:>8.1%}")

    o = df.loc["off (현행)"]
    print(f"\n  델타(on−off):")
    for label in df.index[1:]:
        n = df.loc[label]
        print(f"    {label:<10} ΔSharpe {n['Sharpe']-o['Sharpe']:+.3f} | "
              f"ΔMaxDD {(n['MaxDD']-o['MaxDD'])*100:+.2f}pp | "
              f"ΔCalmar {n['Calmar']-o['Calmar']:+.3f} | "
              f"리밸 {int(o['리밸'])}→{int(n['리밸'])} | "
              f"ΔCOVID {(n['COVID']-o['COVID'])*100:+.2f}pp | "
              f"ΔBear22 {(n['Bear22']-o['Bear22'])*100:+.2f}pp")
    return df


if __name__ == "__main__":
    main()
