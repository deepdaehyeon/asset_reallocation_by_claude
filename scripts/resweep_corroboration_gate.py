"""
재실험: 비-Crisis 디리스크 코로보레이션 게이트 (레버 C) — 현행 시스템 + 고정 4지표.

왜 재실험인가 (2026-06-13):
  원 실험(2026-06-09)은 core 없는 baseline·vol floor 0.65·Sharpe/Calmar 기준이었다.
  그 이후 라이브 시스템이 크게 바뀌었다:
    - core30 라이브 적용(core_satellite enabled, core_ratio 0.30) — 자산 30%를 Goldilocks 고정
    - vol_targeting.floor 0.50→0.80
    - 채권 KRW 통합(bond_usd→bond_krw), 에너지/TIPS KRW화
  결정적으로 게이트는 blend에 적용되는데(engine.py:240), 그 뒤 core_satellite(engine.py:333)가
  30%를 Goldilocks로 고정하므로 **게이트 효과가 satellite 70%에만 작용 → 원 실험보다 희석**된다.
  또 평가 기준이 CLAUDE.md 규칙4로 고정(롤링CAGR·Ulcer·회복기간·Martin)됐으므로 그 기준으로 재판정한다.

설계:
  - config.yaml을 그대로 로드 → 현행 라이브 시스템(core30·floor0.80·KRW통합) 반영.
  - gamma ∈ {0.0(현행 off), 0.25, 0.50, 0.75, 1.0} 스윕. gamma>0이면 게이트 enabled.
  - 각 셀에 대해 고정 4지표 + 보조(CAGR·MaxDD·COVID·Bear22·churn) 산출.
  - 코드/라이브 변경 없음(진단). 결과는 docs/에 저장.
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
    r = res["returns"].dropna()
    m = compute_metrics(r)
    rc3 = rolling_cagr(r, years=3.0)
    rc5 = rolling_cagr(r, years=5.0)
    rec = recovery_duration(r)
    return {
        "gamma": gamma,
        # 고정 4지표
        "r3w": rc3["worst"], "r3m": rc3["median"], "r5w": rc5["worst"],
        "Ulcer": m.get("ulcer", 0.0),
        "rec_dd": rec["maxdd_recovery_days"], "uw_max": rec["max_underwater_days"],
        "Martin": m.get("martin", 0.0),
        # 보조 참고
        "CAGR": m.get("cagr", 0.0), "MaxDD": m.get("max_drawdown", 0.0),
        "리밸": int(res["rebalanced"].sum()), "tx": float(res["tx_cost"].sum()),
        "COVID": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["COVID 2020"]),
        "Bear22": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["Bear 2022"]),
    }


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    cs = base.get("core_satellite", {})
    vt = base.get("vol_targeting", {})
    print(f"데이터 로딩 [{START} ~ {END}]... "
          f"(core_satellite enabled={cs.get('enabled')} ratio={cs.get('core_ratio')} "
          f"regime={cs.get('core_regime')}, vol_floor={vt.get('floor')}, "
          f"rf_weight={base['hmm'].get('rf_weight')})")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    print("\n전략 실행 중...")
    rows = [run_cell(g, copy.deepcopy(base), universe_px, signal_px, fred_history) for g in GRID]
    df = pd.DataFrame(rows).set_index("gamma")

    print(f"\n{'='*120}")
    print(f"  코로보레이션 게이트 재스윕 — 현행 시스템(core30·floor0.80) + 고정 4지표 — gamma 0.0=현행(off)")
    print(f"{'='*120}")
    h = (f"  {'gamma':>7}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'리밸':>6}"
         f"{'tx':>7}{'COVID':>8}{'Bear22':>8}")
    print(h)
    print("  " + "─" * (len(h) + 6))
    for g, r in df.iterrows():
        mark = " ◀현행" if abs(g) < 1e-12 else ""
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {g:>7}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}"
              f"{int(r['리밸']):>6}{r['tx']:>7.2%}{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")

    print("\n  판정 기준(CLAUDE.md 규칙4): Martin(1차)·롤링CAGR·Ulcer·회복기간. CAGR/MaxDD/COVID는 보조.")
    print("  주의: 백테스트는 USD 단일통화 — 라이브 USD합성 순환매·회전 미반영.")
    return df


if __name__ == "__main__":
    main()
