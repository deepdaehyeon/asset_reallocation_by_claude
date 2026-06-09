"""
rf_weight sweep — 포화된 HMM 견제(레버 B).

배경: GaussianHMM 사후확률이 0/100으로 포화돼 blend=0.6·HMM+0.4·RF에서 단일 HMM이
    캘리브레이션된 RF를 압도(2026-06-08~09 Slowdown 휘프소). RF 가중을 올리면 포화 HMM의
    노이즈성 플립 영향이 줄어든다. min_covar floor(레버 A)는 회전·위험조정 성과에서
    효과 없음으로 기각(docs/experiment_2026-06-09_hmm_min_covar.md). 여기선 직접 RF 가중을
    높여 같은 목표(휘프소↓·위기방어 보존)를 위험조정 성과 손실 없이 달성하는지 본다.

코드 변경 없음 (시뮬레이션). 현행 config 위에서 hmm.rf_weight만 토글.
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
GRID = [0.40, 0.50, 0.60, 0.70, 0.80]


def run_cell(w, config, universe_px, signal_px, fred_history):
    rb = config.get("rebalancing", {})
    config.setdefault("hmm", {})["rf_weight"] = float(w)
    print(f"  [rf_weight={w}]")
    res = BacktestEngine(
        config=config, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=float(rb.get("drift_threshold", 0.015)),
        cooldown_days=int(rb.get("min_rebalance_interval_days", 0)),
        fred_history=fred_history,
    ).run()
    m = compute_metrics(res["returns"])
    return {
        "rf_weight": w,
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
    cur = float(base.get("hmm", {}).get("rf_weight", 0.40))
    print(f"데이터 로딩 [{START} ~ {END}]... "
          f"(현행 rf_weight={cur}, "
          f"conf_smoothing={base['regime_filter'].get('confidence_smoothing')}, "
          f"min_covar={base['hmm'].get('min_covar')})")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    print("\n전략 실행 중...")
    rows = [run_cell(w, copy.deepcopy(base), universe_px, signal_px, fred_history) for w in GRID]
    df = pd.DataFrame(rows).set_index("rf_weight")

    print(f"\n{'='*96}")
    print(f"  HMM rf_weight sweep — 현행={cur}")
    print(f"{'='*96}")
    hdr = (f"  {'rf_weight':>10}{'CAGR':>7}{'Sharpe':>8}{'MaxDD':>9}{'Calmar':>8}"
           f"{'리밸':>7}{'tx누적':>9}{'COVID':>9}{'Bear22':>9}")
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    for w, r in df.iterrows():
        mark = " ◀ 현행" if abs(w - cur) < 1e-12 else ""
        print(f"  {w:>10}{r['CAGR']:>7.1%}{r['Sharpe']:>8.2f}{r['MaxDD']:>8.1%}"
              f"{r['Calmar']:>8.2f}{int(r['리밸']):>7}{r['tx누적']:>8.2%}"
              f"{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")
    return df


if __name__ == "__main__":
    main()
