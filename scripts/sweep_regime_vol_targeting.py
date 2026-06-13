"""
실험: 레짐별 vol targeting 사다리의 가치 검증 — 현행 시스템 + 고정 4지표.

질문(2026-06-13): 레짐별 target_vol 사다리(G0.13/R0.11/S0.09/St0.08/C0.06)가
  단일 flat target_vol을 4지표로 이기는가? 이 사다리값은 손으로 설정된 후 검증된 적이 없다.

가설: floor 0.80 + core30 조합에서 사다리 효과가 muted일 것.
  - floor 0.80 → port_vol>~16%면 전 레짐이 동일하게 floor → 차등 소멸(진짜 위기 vol 30%+).
  - regime_targets가 이미 위기에 equity를 깎음 → 사다리 방어쪽 끝은 redundant.
  - core30이 vol targeting을 satellite 70%에만 작용시킴(실효 최대 축소 14%).

변형(같은 엔진·기간, config만 토글):
  [floor 0.80]
    flat0.08 / flat0.10 / flat0.12 : regime_target_vol 제거 → 단일 target_vol
    ladder현행                      : G0.13/R0.11/S0.09/St0.08/C0.06 ◀현행
    ladder상단완화                  : G0.15/R0.13/나머지 동일 (상단이 실제 레버인지)
  [floor 0.65 — remetric에서 4지표 우위였던 값과 교차]
    ladder현행@0.65 / flat0.10@0.65
  [참고] vol OFF

라이브/엔진 변경 없음(진단). 결과는 docs/에 저장.
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

LADDER = {"Goldilocks": 0.13, "Reflation": 0.11, "Slowdown": 0.09,
          "Stagflation": 0.08, "Crisis": 0.06}
LADDER_LOOSE = {**LADDER, "Goldilocks": 0.15, "Reflation": 0.13}

# (label, enabled, regime_target_vol(None=flat), target_vol, floor)
VARIANTS = [
    ("flat0.08 f80",      True,  {},            0.08, 0.80),
    ("flat0.10 f80",      True,  {},            0.10, 0.80),
    ("flat0.12 f80",      True,  {},            0.12, 0.80),
    ("ladder현행 f80",    True,  LADDER,        0.10, 0.80),   # ◀현행
    ("ladder상단↑ f80",   True,  LADDER_LOOSE,  0.10, 0.80),
    ("ladder현행 f65",    True,  LADDER,        0.10, 0.65),
    ("flat0.10 f65",      True,  {},            0.10, 0.65),
    ("vol OFF",           False, {},            0.10, 0.80),
]


def run_cell(spec, config, universe_px, signal_px, fred_history):
    label, enabled, rtv, tv, floor = spec
    rb = config.get("rebalancing", {})
    vt = config.setdefault("vol_targeting", {})
    vt["enabled"] = enabled
    vt["target_vol"] = float(tv)
    vt["regime_target_vol"] = dict(rtv)
    vt["floor"] = float(floor)
    print(f"  [{label}]")
    res = BacktestEngine(
        config=config, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=float(rb.get("drift_threshold", 0.015)),
        cooldown_days=int(rb.get("min_rebalance_interval_days", 0)),
        fred_history=fred_history,
    ).run()
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
    cs = base.get("core_satellite", {})
    print(f"데이터 로딩 [{START} ~ {END}]... "
          f"(core_satellite enabled={cs.get('enabled')} ratio={cs.get('core_ratio')}, "
          f"rf_weight={base['hmm'].get('rf_weight')})")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    print("\n전략 실행 중...")
    rows = [run_cell(s, copy.deepcopy(base), universe_px, signal_px, fred_history) for s in VARIANTS]
    df = pd.DataFrame(rows).set_index("전략")

    print(f"\n{'='*124}")
    print(f"  레짐별 vol targeting 사다리 검증 — 현행 시스템(core30) + 고정 4지표")
    print(f"{'='*124}")
    h = (f"  {'전략':>16}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'리밸':>6}"
         f"{'tx':>7}{'COVID':>8}{'Bear22':>8}")
    print(h)
    print("  " + "─" * (len(h) + 6))
    for label, r in df.iterrows():
        mark = " ◀현행" if "현행 f80" in label else ""
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {label:>16}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}"
              f"{int(r['리밸']):>6}{r['tx']:>7.2%}{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")

    print("\n  판정(CLAUDE.md 규칙4): Martin(1차)·롤링CAGR·Ulcer·회복기간. CAGR/MaxDD/COVID 보조.")
    print("  주의: USD 단일통화 백테스트 — 라이브 합성 순환매·실제 회전 미반영. in-sample.")
    return df


if __name__ == "__main__":
    main()
