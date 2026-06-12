"""
risk_hardening 재검증 — 현행 베이스라인 위에서 '추가 하드닝'이 밥값을 하는가.

배경: 원 실험(2026-05-28)은 drawdown_scaling이 살아있고 floor 0.65이던 시절 결과.
이후 b923dd2에서 drawdown scaling 제거 + floor 0.50을 채택해 베이스라인이 이동했다.
따라서 "현행 베이스라인보다 더 조이면(또는 dd를 되살리면) 나은가?"를 고정 4지표
(롤링CAGR·Ulcer·회복기간·Martin)로 재검증한다. 코드 변경 없음 (시뮬레이션).

시나리오 (현행 config 위 토글):
  baseline    : 현행 (floor 0.50, target_vol Goldilocks 0.13~Crisis 0.06, dd OFF)
  vol_tight   : regime_target_vol 전체 ×0.80 (더 타이트)
  floor_0.35  : equity 최대 65% 축소 허용
  dd_reenable : drawdown_scaling ON + 임계 일찍(-7/-15/-22%)
  all3        : 위 셋 결합
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
from metrics import compute_metrics, rolling_cagr, recovery_duration  # noqa: E402
from compare_rule_timing_ab import START, END, REBAL_FREQ, TX_COST, crisis_maxdd  # noqa: E402

CRISIS_WINDOWS = {"COVID 2020": ("2020-02-19", "2020-04-30"),
                  "Bear 2022": ("2022-01-01", "2022-12-31")}


def apply_scenario(base: dict, name: str) -> dict:
    cfg = copy.deepcopy(base)
    vt = cfg.setdefault("vol_targeting", {})
    risk = cfg.setdefault("risk", {})
    if name in ("vol_tight", "all3"):
        rtv = base.get("vol_targeting", {}).get("regime_target_vol", {})
        vt["regime_target_vol"] = {k: round(v * 0.80, 4) for k, v in rtv.items()}
    if name in ("floor_0.35", "all3"):
        vt["floor"] = 0.35
    if name in ("dd_reenable", "all3"):
        risk["drawdown_scaling_enabled"] = True
        risk.setdefault("drawdown_thresholds", {}).update(
            {"mild": -0.07, "moderate": -0.15, "severe": -0.22}
        )
    return cfg


def run_cell(name, base, universe_px, signal_px, fred_history):
    cfg = apply_scenario(base, name)
    rb = cfg.get("rebalancing", {})
    print(f"  [{name}] floor={cfg['vol_targeting'].get('floor')} "
          f"dd={cfg['risk'].get('drawdown_scaling_enabled')}")
    res = BacktestEngine(
        config=cfg, universe_px=universe_px, signal_px=signal_px,
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
        "scenario": name,
        "CAGR": m.get("cagr", 0.0), "MaxDD": m.get("max_drawdown", 0.0),
        "Ulcer": m.get("ulcer", 0.0), "Martin": m.get("martin", 0.0),
        "r3w": rc3["worst"], "r3m": rc3["median"], "r5w": rc5["worst"],
        "rec_dd": rec["maxdd_recovery_days"], "uw_max": rec["max_underwater_days"],
        "COVID": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["COVID 2020"]),
        "Bear22": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["Bear 2022"]),
    }


def main() -> None:
    from fetcher import fetch_fred_history
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]... (현행 floor={base['vol_targeting'].get('floor')}, "
          f"dd={base['risk'].get('drawdown_scaling_enabled')})")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    scenarios = ["baseline", "vol_tight", "floor_0.35", "dd_reenable", "all3"]
    print("\n전략 실행 중...")
    rows = [run_cell(s, base, universe_px, signal_px, fred_history) for s in scenarios]
    df = pd.DataFrame(rows).set_index("scenario")

    print(f"\n{'='*112}")
    print("  risk_hardening 재검증 — 고정 4지표(롤링CAGR·Ulcer·회복기간·Martin), 현행 베이스라인 위 토글")
    print(f"{'='*112}")
    hdr = (f"  {'scenario':<12}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
           f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'COVID':>8}{'Bear22':>8}")
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    best = df["Martin"].idxmax()
    for name, r in df.iterrows():
        mark = " ◀베이스" if name == "baseline" else ""
        if name == best:
            mark += " ★Martin최대"
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {name:<12}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}"
              f"{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")

    print("\n  Martin 1차 판정. baseline이 최대면 '추가 하드닝 net negative' 재확인.")
    print("  회복일·최장UW가 하드닝으로 줄면 방어 가치 인정. COVID/Bear22는 위기 방어 보조참고.")
    return df


if __name__ == "__main__":
    main()
