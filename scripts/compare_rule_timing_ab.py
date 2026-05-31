"""
층 2 Step 2 — rule-driven 타이밍 vs 현행(HMM ensemble) A/B.

층 2 진단: confirmed(=_run_drift의 ensemble final)가 rule보다 진입 +3~5d 늦다.
그 래그가 엔드투엔드 성과를 실제로 깎는지 검증.

세 변형 (동일 엔진·config, _get_regime만 상이):
  - current     : ensemble final + HMM blend (현행)
  - rule-timing : final=rule(vol 티어·트리거가 rule 타이밍), blend=HMM 유지 → 타이밍만 격리
  - rule-only   : final=rule + blend=one-hot rule → 타이밍+비중 동시 (HMM 완전 제거)

rule-timing vs current = 순수 타이밍 효과. rule-only vs rule-timing = blend 평활 효과.
코드 변경 없음 (시뮬레이션). 결과는 docs/에 저장.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from fetcher import fetch_fred_history  # noqa: E402
from data import load_all_prices  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from metrics import compute_metrics  # noqa: E402
from regime import REGIMES  # noqa: E402
from analyze_regime_timing import (  # noqa: E402
    DEFENSIVE, EPISODE_MIN_DD, FWD, detect_episodes, fwd_maxdd,
    episode_timing, daily_confusion,
)

START = "2010-01-01"
END = "2025-04-30"
REBAL_FREQ = "W-FRI"
TX_COST = 0.001
CRISIS_WINDOWS = {"COVID 2020": ("2020-02-19", "2020-04-30"),
                  "Bear 2022": ("2022-01-01", "2022-12-31")}


class TimingEngine(BacktestEngine):
    def __init__(self, *a, mode: str = "current", **k):
        self._mode = mode
        super().__init__(*a, **k)

    def _get_regime(self, as_of):
        final, blend, rule, conf, rc, hc = super()._get_regime(as_of)
        if self._mode == "current":
            return final, blend, rule, conf, rc, hc
        if self._mode == "rule_timing":
            return rule, blend, rule, conf, rc, hc
        # rule_only
        onehot = {x: (1.0 if x == rule else 0.0) for x in REGIMES}
        return rule, onehot, rule, rc, rc, 0.0


def make_engine(config, universe_px, signal_px, fred_history, mode):
    return TimingEngine(
        config=config, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=float(config.get("rebalancing", {}).get("drift_threshold", 0.015)),
        cooldown_days=int(config.get("rebalancing", {}).get("min_rebalance_interval_days", 0)),
        fred_history=fred_history, mode=mode,
    )


def crisis_maxdd(returns, start, end):
    r = returns[start:end]
    if r.empty:
        return float("nan")
    eq = (1.0 + r).cumprod()
    return float((eq / eq.cummax() - 1.0).min())


def timing_aggregate(regime: pd.Series, episodes, spy_fwd_dd) -> dict:
    entries, covers = [], []
    for peak, trough, _ in episodes:
        t = episode_timing(regime, peak, trough, DEFENSIVE)
        if t["entry_lag"] is not None:
            entries.append(t["entry_lag"])
        if not np.isnan(t["coverage"]):
            covers.append(t["coverage"])
    rec10 = daily_confusion(regime, spy_fwd_dd, 0.10, DEFENSIVE)["recall"]
    return {
        "entry_med": float(np.median(entries)) if entries else float("nan"),
        "cover_mean": float(np.mean(covers)) if covers else float("nan"),
        "recall10": rec10,
    }


def main() -> None:
    with open(ROOT / "trading" / "config.yaml") as f:
        config = yaml.safe_load(f)

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=config, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    spy = signal_px["SPY"]
    modes = {"current": "current", "rule-timing": "rule_timing", "rule-only": "rule_only"}
    results: Dict[str, pd.DataFrame] = {}
    print("\n전략 실행 중...")
    for label, mode in modes.items():
        print(f"  [{label}]")
        results[label] = make_engine(config, universe_px, signal_px, fred_history, mode).run()

    # 에피소드 (모든 변형 공통: SPY 기준)
    spy_aligned = spy.reindex(results["current"].index).ffill()
    episodes = detect_episodes(spy_aligned, EPISODE_MIN_DD)
    spy_fwd_dd = fwd_maxdd(spy_aligned, FWD)

    rows = []
    for label, res in results.items():
        m = compute_metrics(res["returns"])
        tim = timing_aggregate(res["regime"], episodes, spy_fwd_dd)
        rows.append({
            "variant": label,
            "CAGR": m.get("cagr", 0.0), "Sharpe": m.get("sharpe", 0.0),
            "MaxDD": m.get("max_drawdown", 0.0), "Calmar": m.get("calmar", 0.0),
            "COVID": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["COVID 2020"]),
            "Bear22": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["Bear 2022"]),
            "entry_med": tim["entry_med"], "cover": tim["cover_mean"], "recall10": tim["recall10"],
        })
    df = pd.DataFrame(rows).set_index("variant")

    print(f"\n{'='*100}")
    print("  rule 타이밍 A/B — 3-way (동일 엔진, _get_regime만 상이)")
    print(f"{'='*100}")
    hdr = (f"  {'variant':<14} {'CAGR':>7} {'Sharpe':>7} {'MaxDD':>8} {'Calmar':>7} "
           f"{'COVID':>8} {'Bear22':>8} │ {'진입중앙':>8}{'커버':>7}{'recall10':>9}")
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    for label, row in df.iterrows():
        print(f"  {label:<14} {row['CAGR']:>6.1%} {row['Sharpe']:>7.2f} "
              f"{row['MaxDD']:>7.1%} {row['Calmar']:>7.2f} "
              f"{row['COVID']:>7.1%} {row['Bear22']:>7.1%} │ "
              f"{row['entry_med']:>+7.0f}d{row['cover']:>7.0%}{row['recall10']:>9.0%}")

    cur, rt = df.loc["current"], df.loc["rule-timing"]
    print(f"\n{'='*100}")
    print("  델타 (rule-timing − current) = 순수 타이밍 효과")
    print(f"{'='*100}")
    print(f"  진입래그 중앙값  {rt['entry_med'] - cur['entry_med']:+.0f}d  (음수=빨라짐)")
    print(f"  커버리지        {(rt['cover'] - cur['cover'])*100:+.1f}pp")
    print(f"  recall@10%      {(rt['recall10'] - cur['recall10'])*100:+.1f}pp")
    print(f"  Sharpe          {rt['Sharpe'] - cur['Sharpe']:+.3f}")
    print(f"  MaxDD           {(rt['MaxDD'] - cur['MaxDD'])*100:+.2f}pp  (양수=개선)")
    print(f"  Calmar          {rt['Calmar'] - cur['Calmar']:+.3f}")
    print(f"  COVID DD        {(rt['COVID'] - cur['COVID'])*100:+.2f}pp")
    print(f"  Bear22 DD       {(rt['Bear22'] - cur['Bear22'])*100:+.2f}pp")

    return df


if __name__ == "__main__":
    main()
