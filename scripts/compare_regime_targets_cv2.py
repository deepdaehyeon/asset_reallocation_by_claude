"""
층 1 Step 3 — C-v2 후보 검증.

층 1 진단 결론: C안(커밋 6d86b79)이 forward-21일 Sharpe(V-shape 편향)로 추가한
risk/trend 비중이 위험레짐 일별 방어를 약화시켰고, 현재 엔진에선 Sharpe도 악화.
단 C안의 "제거"(Crisis/Slowdown MF)는 타당 → 전면 revert가 아닌 선별 조정.

C-v2 = current에서 V-shape 기반 추가만 타겟 리버트, MF 제거는 유지,
       freed 비중을 contemp 우위 방어자산으로 환원:
  - Crisis      : equity_etf 10→0, equity_factor 6→3, bond_tips 10→5
                  → cash 28→34, bond_usd 10→16, bond_krw 10→16
  - Stagflation : equity_factor 8→3 → bond_krw 0→5
  - Reflation   : MF 12→5 → commodity 16→20, bond_tips 5→8
  - Slowdown    : 유지 (C안 조정 타당)

3-way 비교: pre-C / current / C-v2. 동일 엔진·동일 나머지 config.
코드 변경 없음 (시뮬레이션). 결과는 docs/에 저장.
"""
from __future__ import annotations

import subprocess
import sys
import warnings
from copy import deepcopy
from pathlib import Path
from typing import Dict

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

CONFIG_PATH = ROOT / "trading" / "config.yaml"
PRE_C_REF = "6d86b79^:trading/config.yaml"
START = "2010-01-01"
END = "2025-04-30"
REBAL_FREQ = "W-FRI"
TX_COST = 0.001

CRISIS_WINDOWS = {
    "COVID 2020": ("2020-02-19", "2020-04-30"),
    "Bear 2022": ("2022-01-01", "2022-12-31"),
}


def load_pre_c_targets() -> dict:
    blob = subprocess.check_output(["git", "show", PRE_C_REF], cwd=ROOT, text=True)
    return yaml.safe_load(blob)["regime_targets"]


def build_cv2_targets(current: dict) -> dict:
    """current regime_targets에 타겟 리버트 적용 → C-v2. 각 레짐 합계 1.0 검증."""
    t = deepcopy(current)

    # Crisis: V-shape equity/tips 회수 → 방어자산 환원
    c = t["Crisis"]
    c["equity_etf"] = 0.00      # 0.10 → 0 (forward #2 / contemp #6 +0.06 무방어)
    c["equity_factor"] = 0.03   # 0.06 → 0.03
    c["bond_tips"] = 0.05       # 0.10 → 0.05 (forward #3 / contemp #8)
    c["cash"] = 0.34            # 0.28 → 0.34 (contemp #1)
    c["bond_usd"] = 0.16        # 0.10 → 0.16 (contemp #2)
    c["bond_krw"] = 0.16        # 0.10 → 0.16 (contemp #3)

    # Stagflation: equity_factor 회수 → bond_krw 추가
    s = t["Stagflation"]
    s["equity_factor"] = 0.03   # 0.08 → 0.03 (forward #3 / contemp #11 -2.37)
    s["bond_krw"] = 0.05        # 0.00 → 0.05 (contemp #3 +2.65 저비중 방어)

    # Reflation: MF 회수 → commodity/bond_tips (contemp 상위) 보강
    r = t["Reflation"]
    r["managed_futures"] = 0.05  # 0.12 → 0.05 (forward #1 / contemp #10)
    r["commodity"] = 0.20        # 0.16 → 0.20 (contemp #1 +2.85)
    r["bond_tips"] = 0.08        # 0.05 → 0.08 (contemp #2 +2.09)

    for regime, w in t.items():
        if regime == "Transition":
            continue
        total = sum(w.values())
        assert abs(total - 1.0) < 1e-9, f"{regime} 합계 {total} ≠ 1.0"
    return t


def _drift(config):
    return float(config.get("rebalancing", {}).get("drift_threshold", 0.015))


def _cooldown(config):
    return int(config.get("rebalancing", {}).get("min_rebalance_interval_days", 0))


def make_engine(config, universe_px, signal_px, fred_history):
    return BacktestEngine(
        config=config, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=_drift(config), cooldown_days=_cooldown(config),
        fred_history=fred_history,
    )


def crisis_maxdd(returns, start, end):
    r = returns[start:end]
    if r.empty:
        return float("nan")
    eq = (1.0 + r).cumprod()
    return float((eq / eq.cummax() - 1.0).min())


def risk_defense(result) -> Dict[str, float]:
    out = {}
    for r in ("Crisis", "Stagflation", "Slowdown"):
        mask = result["regime"] == r
        out[r] = float(result.loc[mask, "returns"].mean()) if mask.any() else float("nan")
    return out


def main() -> None:
    with open(CONFIG_PATH) as f:
        current = yaml.safe_load(f)

    pre_c = deepcopy(current)
    pre_c["regime_targets"] = load_pre_c_targets()
    cv2 = deepcopy(current)
    cv2["regime_targets"] = build_cv2_targets(current["regime_targets"])

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(
        config=current, start=START, end=END, use_cache=True
    )
    fred_history = fetch_fred_history(START, END)
    print(f"  유니버스 {len(universe_px.columns)}종목 / {len(universe_px)}거래일")

    variants = {
        "pre-C": pre_c,
        "current (C안)": current,
        "C-v2": cv2,
    }
    results = {}
    print("\n전략 실행 중...")
    for label, cfg in variants.items():
        print(f"  [{label}]")
        results[label] = make_engine(cfg, universe_px, signal_px, fred_history).run()

    rows = []
    for label, res in results.items():
        m = compute_metrics(res["returns"])
        d = risk_defense(res)
        rows.append({
            "variant": label,
            "CAGR": m.get("cagr", 0.0), "Sharpe": m.get("sharpe", 0.0),
            "MaxDD": m.get("max_drawdown", 0.0), "Calmar": m.get("calmar", 0.0),
            "COVID": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["COVID 2020"]),
            "Bear22": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["Bear 2022"]),
            "Crisis_d": d["Crisis"], "Stagfl_d": d["Stagflation"], "Slowdn_d": d["Slowdown"],
        })
    df = pd.DataFrame(rows).set_index("variant")

    print(f"\n{'='*96}")
    print("  C-v2 검증 — 3-way (동일 엔진, regime_targets만 상이)")
    print(f"{'='*96}")
    hdr = (f"  {'variant':<16} {'CAGR':>7} {'Sharpe':>7} {'MaxDD':>8} {'Calmar':>7} "
           f"{'COVID':>8} {'Bear22':>8}")
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    for label, row in df.iterrows():
        print(f"  {label:<16} {row['CAGR']:>6.1%} {row['Sharpe']:>7.2f} "
              f"{row['MaxDD']:>7.1%} {row['Calmar']:>7.2f} "
              f"{row['COVID']:>7.1%} {row['Bear22']:>7.1%}")

    print("\n  위험레짐 체류일 일평균 수익률 (방어, 높을수록 좋음):")
    print(f"  {'variant':<16} {'Crisis':>10} {'Stagflation':>12} {'Slowdown':>10}")
    print("  " + "─" * 50)
    for label, row in df.iterrows():
        print(f"  {label:<16} {row['Crisis_d']:>+9.3%} "
              f"{row['Stagfl_d']:>+11.3%} {row['Slowdn_d']:>+9.3%}")

    cur, v2 = df.loc["current (C안)"], df.loc["C-v2"]
    print(f"\n{'='*96}")
    print("  델타 (C-v2 − current)")
    print(f"{'='*96}")
    print(f"  Sharpe   {v2['Sharpe'] - cur['Sharpe']:+.3f}")
    print(f"  MaxDD    {(v2['MaxDD'] - cur['MaxDD'])*100:+.2f}pp  (양수=개선)")
    print(f"  Calmar   {v2['Calmar'] - cur['Calmar']:+.3f}")
    print(f"  CAGR     {(v2['CAGR'] - cur['CAGR'])*100:+.2f}pp")
    print(f"  Crisis 일방어   {(v2['Crisis_d'] - cur['Crisis_d'])*100:+.3f}pp")
    print(f"  Stagfl 일방어   {(v2['Stagfl_d'] - cur['Stagfl_d'])*100:+.3f}pp")

    return df


if __name__ == "__main__":
    main()
