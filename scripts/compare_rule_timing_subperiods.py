"""
층 2 Step 3 — rule 타이밍 A/B 서브기간 견고성.

Step 2(compare_rule_timing_ab) 결론: rule-timing이 current 대비 Sharpe+0.063·
MaxDD+0.64pp·Calmar+0.109 동시 개선이나, Sharpe 델타가 노이즈 밴드(±0.04)를 약간
넘는 수준이라 단일 구간 in-sample. 여기선 동일 백테스트(3변형 전체기간 1회씩,
HMM 워밍업 일관성 유지)를 돌린 뒤 수익률·레짐을 서브기간으로 잘라 델타 부호가
구간 전반에서 유지되는지(robust) 확인. 코드 변경 없음.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "scripts"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from fetcher import fetch_fred_history  # noqa: E402
from data import load_all_prices  # noqa: E402
from metrics import compute_metrics  # noqa: E402
from analyze_regime_timing import (  # noqa: E402
    DEFENSIVE, EPISODE_MIN_DD, FWD, detect_episodes, fwd_maxdd,
)
from compare_rule_timing_ab import (  # noqa: E402
    START, END, make_engine, timing_aggregate,
)

# (label, start, end)
WINDOWS = [
    ("full      2010-2025", START, END),
    ("H1        2010-2017", "2010-01-01", "2017-12-31"),
    ("H2        2018-2025", "2018-01-01", END),
    ("T1        2010-2014", "2010-01-01", "2014-12-31"),
    ("T2        2015-2019", "2015-01-01", "2019-12-31"),
    ("T3        2020-2025", "2020-01-01", END),
]


def slice_metrics(res, spy, start, end):
    r = res["returns"][start:end]
    reg = res["regime"][start:end]
    m = compute_metrics(r)
    spy_w = spy[start:end]
    episodes = detect_episodes(spy_w, EPISODE_MIN_DD)
    spy_fwd = fwd_maxdd(spy_w, FWD)
    tim = timing_aggregate(reg, episodes, spy_fwd)
    return {
        "Sharpe": m.get("sharpe", 0.0), "MaxDD": m.get("max_drawdown", 0.0),
        "Calmar": m.get("calmar", 0.0), "CAGR": m.get("cagr", 0.0),
        "entry": tim["entry_med"], "n_epi": len(episodes),
    }


def main() -> None:
    with open(ROOT / "trading" / "config.yaml") as f:
        config = yaml.safe_load(f)

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=config, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)
    spy = signal_px["SPY"]

    print("\n전략 실행 중 (전체기간 1회씩)...")
    res = {}
    for mode in ("current", "rule_timing"):
        print(f"  [{mode}]")
        res[mode] = make_engine(config, universe_px, signal_px, fred_history, mode).run()

    spy_aligned = spy.reindex(res["current"].index).ffill()

    print(f"\n{'='*96}")
    print("  서브기간 견고성 — current vs rule-timing (델타 = rule-timing − current)")
    print(f"{'='*96}")
    hdr = (f"  {'window':<20}{'ΔSharpe':>9}{'ΔMaxDD':>9}{'ΔCalmar':>9}{'ΔCAGR':>8}"
           f"{'Δentry':>8}{'n_epi':>7}   sign")
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))

    pos_sharpe = 0
    for label, s, e in WINDOWS:
        c = slice_metrics(res["current"], spy_aligned, s, e)
        rt = slice_metrics(res["rule_timing"], spy_aligned, s, e)
        d_sh = rt["Sharpe"] - c["Sharpe"]
        d_dd = (rt["MaxDD"] - c["MaxDD"]) * 100
        d_ca = rt["Calmar"] - c["Calmar"]
        d_cagr = (rt["CAGR"] - c["CAGR"]) * 100
        d_en = rt["entry"] - c["entry"]
        if d_sh > 0:
            pos_sharpe += 1
        sign = "rule-timing 우위" if (d_sh > 0 and d_dd >= -0.01) else ("혼조" if d_sh > 0 or d_dd > 0 else "current 우위")
        en = f"{d_en:+.0f}d" if not (np.isnan(d_en)) else "—"
        print(f"  {label:<20}{d_sh:>+9.3f}{d_dd:>+8.2f}pp{d_ca:>+9.3f}{d_cagr:>+7.2f}pp"
              f"{en:>8}{c['n_epi']:>7}   {sign}")

    print(f"\n  ΔSharpe>0 구간: {pos_sharpe}/{len(WINDOWS)}  (양수=rule-timing 우위)")
    print("  주의: 서브기간은 동일 전체 백테스트의 슬라이스 — HMM 워밍업·레짐 상태 연속성 유지.")
    print("        Calmar/MaxDD는 짧은 창에서 단일 낙폭에 민감하니 부호 일관성 위주로 해석.")


if __name__ == "__main__":
    main()
