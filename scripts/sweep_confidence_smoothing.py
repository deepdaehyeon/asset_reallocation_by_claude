"""
confidence_smoothing sweep — 신뢰도 가변 blend 평활 검증.

배경: 저신뢰 레짐 전환 시 새 blend 채택을 늦춰 가짜 회전을 억제하는 장치.
    eff_α = 1 - (1 - blend_smoothing_alpha) · clip(conf / conf_ref, 0, 1)
    Crisis(raw_blend[Crisis] ≥ crisis_priority_threshold)는 면제 — 위기 빠른 진입 보존.

목표: off(고정-α) 대비 (1) 회전/비용 감소, (2) 위험조정 지표 비열위,
    (3) 핵심으로 COVID/Bear22 방어 반응이 죽지 않는지 확인.

코드 변경 없음 (시뮬레이션). 현행 config 위에서 confidence_smoothing만 토글한다.
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
# None = off (고정-α 기준선), 나머지는 conf_ref 값
GRID = [None, 0.2, 0.3, 0.4, 0.5]


def _apply(config: dict, conf_ref):
    rf = config.setdefault("regime_filter", {})
    if conf_ref is None:
        rf["confidence_smoothing"] = {"enabled": False}
    else:
        rf["confidence_smoothing"] = {"enabled": True, "conf_ref": float(conf_ref)}
    return config


def run_cell(conf_ref, config, universe_px, signal_px, fred_history):
    rb = config.get("rebalancing", {})
    label = "off" if conf_ref is None else f"on/ref={conf_ref}"
    print(f"  [{label}]")
    res = BacktestEngine(
        config=config, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=float(rb.get("drift_threshold", 0.015)),
        cooldown_days=int(rb.get("min_rebalance_interval_days", 0)),
        fred_history=fred_history,
    ).run()
    m = compute_metrics(res["returns"])
    return {
        "cell": label,
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
    rf = base.get("regime_filter", {})
    print(f"데이터 로딩 [{START} ~ {END}]... "
          f"(α={rf.get('blend_smoothing_alpha')}, "
          f"timing={rf.get('regime_timing_source')}, "
          f"conf_method={rf.get('confidence_method')}, "
          f"crisis_prio={base.get('hmm', {}).get('crisis_priority_threshold')})")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    print("\n전략 실행 중...")
    rows = [
        run_cell(cr, _apply(copy.deepcopy(base), cr), universe_px, signal_px, fred_history)
        for cr in GRID
    ]
    df = pd.DataFrame(rows).set_index("cell")

    print(f"\n{'='*94}")
    print(f"  confidence_smoothing sweep (α={rf.get('blend_smoothing_alpha')}) — off=고정-α 기준선")
    print(f"{'='*94}")
    hdr = (f"  {'cell':>11}{'CAGR':>7}{'Sharpe':>8}{'MaxDD':>9}{'Calmar':>8}"
           f"{'리밸':>7}{'tx누적':>9}{'COVID':>9}{'Bear22':>9}")
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    for cell, r in df.iterrows():
        mark = " ◀ 기준선" if cell == "off" else ""
        print(f"  {cell:>11}{r['CAGR']:>7.1%}{r['Sharpe']:>8.2f}{r['MaxDD']:>8.1%}"
              f"{r['Calmar']:>8.2f}{int(r['리밸']):>7}{r['tx누적']:>8.2%}"
              f"{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")
    return df


if __name__ == "__main__":
    main()
