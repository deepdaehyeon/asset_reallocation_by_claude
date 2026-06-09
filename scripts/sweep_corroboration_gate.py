"""
비-Crisis 디리스크 코로보레이션 게이트 sweep (레버 C).

배경: 포화된 HMM이 단독으로 방어(Slowdown/Stagflation) 쪽으로 blend를 끌어내릴 때,
    매크로(rule_regime)가 위험선호면 디리스크를 코로보레이션 없는 것으로 보고 방어 질량을
    gamma만큼 회수해 rule_regime으로 재배분한다(2026-06-08~09 Slowdown 휘프소 사건이 동기).
    Crisis 질량은 절대 줄이지 않고, blend[Crisis]>=crisis_priority_threshold면 게이트 전체 면제.

레버 A(min_covar)·B(rf_weight)는 회전을 못 줄이거나 위기방어를 악화시켜 기각.
C는 "위험 아닐 때만, 매크로가 동의하지 않는 방어만" 표적 감쇠한다.

gamma=0.0이 baseline(게이트 off). 코드 경로는 config로 토글(기본 off, 라이브 불변).
결과는 docs/에 저장.
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
GRID = [0.0, 0.25, 0.50, 0.75, 1.0]


def run_cell(gamma, config, universe_px, signal_px, fred_history):
    rb = config.get("rebalancing", {})
    cg = config.setdefault("regime_filter", {}).setdefault("corroboration_gate", {})
    cg["enabled"] = gamma > 0
    cg["gamma"] = float(gamma)
    print(f"  [gamma={gamma}]")
    res = BacktestEngine(
        config=config, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=float(rb.get("drift_threshold", 0.015)),
        cooldown_days=int(rb.get("min_rebalance_interval_days", 0)),
        fred_history=fred_history,
    ).run()
    m = compute_metrics(res["returns"])
    return {
        "gamma": gamma,
        "CAGR": m.get("cagr", 0.0), "Sharpe": m.get("sharpe", 0.0),
        "MaxDD": m.get("max_drawdown", 0.0), "Calmar": m.get("calmar", 0.0),
        "리밸": int(res["rebalanced"].sum()), "tx누적": float(res["tx_cost"].sum()),
        "COVID": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["COVID 2020"]),
        "Bear22": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["Bear 2022"]),
    }


def main() -> None:
    from fetcher import fetch_fred_history
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]... "
          f"(rf_weight={base['hmm'].get('rf_weight')}, "
          f"conf_smoothing={base['regime_filter'].get('confidence_smoothing')}, "
          f"crisis_thr={base['hmm'].get('crisis_priority_threshold')})")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    print("\n전략 실행 중...")
    rows = [run_cell(g, copy.deepcopy(base), universe_px, signal_px, fred_history) for g in GRID]
    df = pd.DataFrame(rows).set_index("gamma")

    print(f"\n{'='*96}")
    print(f"  비-Crisis 디리스크 코로보레이션 게이트 sweep — gamma=0.0=현행(off)")
    print(f"{'='*96}")
    hdr = (f"  {'gamma':>8}{'CAGR':>7}{'Sharpe':>8}{'MaxDD':>9}{'Calmar':>8}"
           f"{'리밸':>7}{'tx누적':>9}{'COVID':>9}{'Bear22':>9}")
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    for g, r in df.iterrows():
        mark = " ◀ 현행" if abs(g) < 1e-12 else ""
        print(f"  {g:>8}{r['CAGR']:>7.1%}{r['Sharpe']:>8.2f}{r['MaxDD']:>8.1%}"
              f"{r['Calmar']:>8.2f}{int(r['리밸']):>7}{r['tx누적']:>8.2%}"
              f"{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")
    return df


if __name__ == "__main__":
    main()
