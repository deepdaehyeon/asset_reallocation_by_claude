"""
실험: 평활 2겹 + 위험 진입 타이밍 통합 격자 — 현행 시스템 + 고정 4지표.

질문(2026-06-13, 충돌 피처쌍 #3·#4):
  #3 평활 2겹 — blend_smoothing_alpha(EWMA 평활)와 confidence_smoothing(신뢰도 가변 평활)이
     같은 파이프라인을 건드린다. 각각의 순효과를 분리한다.
  #4 느린 평활 ↔ 빠른 위험 진입 — regime_timing_source=rule(앙상블보다 +3~5d 빠른 진입)이
     평활(천천히 바꿔라)과 반대 방향. 상쇄되는지 본다.

구조적 사실(engine.py:250,262): confidence_smoothing은 blend_smoothing_alpha>0일 때만 작동.
  → 두 평활은 독립이 아니라 cs가 blend 평활 위에 얹히는 변조. 격자를 이에 맞춰 구성:
  평활 레벨 {없음(α0) / 고정평활(α0.5·cs off) / 가변평활(α0.5·cs on=현행)} × timing {rule, ensemble}.
  = 6셀. 현행 = rule + 가변평활(α0.5·cs on).

고정(규칙5 합의): vol ON(floor 0.65), drift 리밸, DD scaling OFF, core30, 나머지 현행.
  한계: crisis_priority(0.40)가 위기 때 평활을 면제하므로 COVID 구간 평활 효과는 약화됨.
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

# 평활 레벨: (라벨, blend_smoothing_alpha, confidence_smoothing enabled)
SMOOTH_LEVELS = [
    ("평활없음(α0)", 0.0, False),
    ("고정평활(α.5)", 0.5, False),
    ("가변평활(α.5cs)", 0.5, True),   # 현행
]
TIMINGS = ["rule", "ensemble"]   # rule = 현행(빠른 진입)


def run_cell(smooth, timing, base, universe_px, signal_px, fred_history):
    s_label, alpha, cs_on = smooth
    config = copy.deepcopy(base)
    rf = config.setdefault("regime_filter", {})
    rf["blend_smoothing_alpha"] = alpha
    rf["regime_timing_source"] = timing
    if cs_on:
        rf["confidence_smoothing"] = {"enabled": True, "conf_ref": 0.3}
    else:
        rf["confidence_smoothing"] = {"enabled": False}
    rb = config.get("rebalancing", {})
    cur = " ◀현행" if (timing == "rule" and cs_on and alpha == 0.5) else ""
    label = f"{timing[:4]}/{s_label}"
    print(f"  [{label}]{cur}")
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
        "전략": label, "_cur": bool(cur),
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
    print(f"데이터 로딩 [{START} ~ {END}]... (floor 0.65·drift·core30 고정)")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    print("\n전략 실행 중 (6셀 = 평활3 × timing2)...")
    rows = []
    for timing in TIMINGS:
        for smooth in SMOOTH_LEVELS:
            rows.append(run_cell(smooth, timing, base, universe_px, signal_px, fred_history))
    df = pd.DataFrame(rows).set_index("전략")

    print(f"\n{'='*128}")
    print("  평활 2겹 + 위험 진입 타이밍 통합 격자 — 현행(floor0.65·drift·core30) + 고정 4지표")
    print(f"{'='*128}")
    h = (f"  {'전략':>18}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'리밸':>6}"
         f"{'tx':>7}{'COVID':>8}{'Bear22':>8}")
    print(h)
    print("  " + "─" * (len(h) + 6))
    for label, r in df.iterrows():
        mark = " ◀현행" if r["_cur"] else ""
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {label:>18}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}"
              f"{int(r['리밸']):>6}{r['tx']:>7.2%}{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")

    print("\n  판정(CLAUDE.md 규칙4): Martin(1차)·롤링CAGR·Ulcer·회복기간. CAGR/MaxDD/COVID 보조.")
    print("  한계: crisis_priority(0.40)가 위기 때 평활 면제 → COVID 구간 평활 효과 약화. USD 단일통화·in-sample.")
    return df


if __name__ == "__main__":
    main()
