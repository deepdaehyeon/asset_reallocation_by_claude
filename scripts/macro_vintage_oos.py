"""
처방 (b): 매크로 라벨 point-in-time 검증 — vintage(첫 발표값) vs 최신 수정값.

질문(2026-06-14): 비판 리뷰 처방 (b)="라벨을 point-in-time으로 검증". 현재 백테스트는
  FRED 최신 *수정값*을 쓴다(fetch_fred_history). publication lag는 적용돼 있으나(비평 #3),
  그건 "언제 알았나"(timing)만 보정하고 *값 자체*는 나중에 수정된 최종값 → 그 당시엔
  몰랐던 정보가 레짐 라벨에 새어든다(미래 누수). 진짜 point-in-time = 그 시점 첫 발표값(vintage).

방법(누수 격리):
  - REVISABLE = {CPIAUCSL, UNRATE, M2SL, WALCL, NFCI}만 ALFRED get_series_all_releases로
    각 reference date의 *첫 발표값*(min realtime_start)을 재구성 → fetch_fred_history의
    동일 변환(YoY·3m변화·z-score)·동일 pub-lag를 그대로 타게 monkeypatch.
  - 시장기반 일별(T5YIE·BAA10Y·T10Y2Y)은 사실상 무수정 → 그대로(원본).
  - core30·vol·hmm·blend·평활 전부 현행 ON 고정. **데이터 값만** 바꿔 vintage 효과 격리.

판정(규칙4):
  - TRAIN/TEST(split 2019) 4지표가 vintage에서 거의 안 변하면 → 수정 누수 무시 가능,
    백테스트·walk-forward 결론 유지.
  - TEST Martin이 vintage에서 *유의하게 하락*하면 → 백테스트가 수정 정보로 OOS를 과대평가,
    진짜 라이브 OOS는 더 낮음(주의 필요).
  - 레짐 라벨 일치율(일별 acting regime)로 "라벨이 point-in-time에서 실제로 달라지나" 직접 측정.
"""
from __future__ import annotations

import copy
import sys
import warnings
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "scripts"))
load_dotenv(ROOT / ".env")

warnings.filterwarnings("ignore", message="Model is not converging.*")

import fetcher  # noqa: E402
from data import load_all_prices  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from fetcher import fetch_fred_history, _get_fred_client  # noqa: E402
from metrics import compute_metrics, rolling_cagr, recovery_duration  # noqa: E402
from compare_rule_timing_ab import START, END, REBAL_FREQ, TX_COST  # noqa: E402

SPLIT = "2019-01-01"
# NFCI 제외: get_series_all_releases가 10만행 상한에 걸려 1976년까지만 반환(주별 전체이력
# 수정으로 vintage 폭증) → 백테스트 구간 미포함. NFCI는 양쪽 다 최신값으로 고정해 4대
# 경제지표만 깨끗이 vintage 비교(NFCI 수정효과는 미검증, 한계로 명시).
REVISABLE = {"CPIAUCSL", "UNRATE", "M2SL", "WALCL"}

_orig_get_series = fetcher._fred_get_series
_release_cache: dict[str, pd.Series] = {}


def _first_release_series(fred, code: str) -> pd.Series:
    """ALFRED 전체 릴리즈에서 각 reference date의 첫 발표값을 복원."""
    ar = fred.get_series_all_releases(code)
    ar = ar.copy()
    ar["value"] = pd.to_numeric(ar["value"], errors="coerce")
    ar = ar.dropna(subset=["value"])
    fr = ar.sort_values("realtime_start").groupby("date")["value"].first()
    fr.index = pd.to_datetime(fr.index)
    return fr.sort_index()


def _vintage_get_series(fred, code: str, **kwargs):
    """REVISABLE은 첫 발표값(vintage)으로, 나머지는 원본(최신값)으로."""
    if code not in REVISABLE:
        return _orig_get_series(fred, code, **kwargs)
    if code not in _release_cache:
        _release_cache[code] = _first_release_series(fred, code)
    s = _release_cache[code]
    os_ = kwargs.get("observation_start")
    oe_ = kwargs.get("observation_end")
    if os_:
        s = s.loc[str(os_):]
    if oe_:
        s = s.loc[:str(oe_)]
    return s.copy()


def build_engine(cfg, universe_px, signal_px, fred_history):
    rb = cfg.get("rebalancing", {})
    return BacktestEngine(
        config=cfg, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=float(rb.get("drift_threshold", 0.015)),
        cooldown_days=int(rb.get("min_rebalance_interval_days", 0)),
        fred_history=fred_history,
    )


def slice_metrics(returns, lo, hi):
    r = returns.loc[lo:hi].dropna()
    m = compute_metrics(r)
    rc3 = rolling_cagr(r, years=3.0)
    rec = recovery_duration(r)
    return {
        "Martin": m.get("martin", 0.0), "CAGR": m.get("cagr", 0.0),
        "Ulcer": m.get("ulcer", 0.0), "MaxDD": m.get("max_drawdown", 0.0),
        "uw_max": rec["max_underwater_days"], "r3w": rc3["worst"],
    }


def run_once(cfg, universe_px, signal_px, fred_history):
    res = build_engine(cfg, universe_px, signal_px, fred_history).run()
    ret = res["returns"]
    out = {
        "train": slice_metrics(ret, START, "2018-12-31"),
        "test": slice_metrics(ret, SPLIT, END),
        "regime": res["regime"], "rule_regime": res["rule_regime"],
    }
    out["train"]["tx"] = float(res["tx_cost"].loc[START:"2018-12-31"].sum())
    out["test"]["tx"] = float(res["tx_cost"].loc[SPLIT:END].sum())
    return out


def main():
    import yaml
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)

    if _get_fred_client() is None:
        print("FRED_API_KEY 없음 — 중단 (vintage 비교 불가).")
        return

    print(f"데이터 로딩 [{START} ~ {END}], split={SPLIT}...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)

    # 1) 베이스라인: 최신 수정값 (현행)
    print("\n[1/2] 베이스라인 FRED (최신 수정값) 로딩·실행...")
    fred_revised = fetch_fred_history(START, END)
    rev = run_once(copy.deepcopy(base), universe_px, signal_px, fred_revised)

    # 2) vintage: 첫 발표값 (point-in-time)
    print("[2/2] vintage FRED (첫 발표값) 재구성·실행...")
    fetcher._fred_get_series = _vintage_get_series
    try:
        fred_vintage = fetch_fred_history(START, END)
    finally:
        fetcher._fred_get_series = _orig_get_series
    vin = run_once(copy.deepcopy(base), universe_px, signal_px, fred_vintage)

    # 데이터 차이 진단
    print("\n  vintage vs 수정값 — 컬럼별 평균 절대차(공통 구간):")
    common = fred_revised.index.intersection(fred_vintage.index)
    for col in fred_revised.columns:
        if col in fred_vintage.columns:
            d = (fred_revised.loc[common, col] - fred_vintage.loc[common, col]).abs().mean()
            print(f"    {col:>18}: {d:.4f}")

    def print_table(title, key):
        print(f"\n{'='*96}")
        print(f"  {title}")
        print(f"{'='*96}")
        h = (f"  {'데이터':>16}{'Martin':>9}{'CAGR':>9}{'Ulcer':>8}{'최장UW':>8}"
             f"{'롤3y최악':>10}{'MaxDD':>9}{'tx':>8}")
        print(h)
        print("  " + "─" * len(h))
        for label, run in [("최신수정(현행)", rev), ("vintage(첫발표)", vin)]:
            r = run[key]
            print(f"  {label:>16}{r['Martin']:>9.2f}{r['CAGR']:>9.1%}{r['Ulcer']:>8.2f}"
                  f"{int(r['uw_max']):>8}{r['r3w']:>10.1%}{r['MaxDD']:>9.1%}{r['tx']:>8.2%}")
        dm_tr = vin[key]["Martin"] - rev[key]["Martin"]
        print(f"  → ΔMartin(vintage−현행): {dm_tr:+.2f}")

    print_table("학습창 TRAIN 2010-01 ~ 2018-12 (in-sample)", "train")
    print_table(f"검증창 TEST {SPLIT[:7]} ~ {END} (OUT-OF-SAMPLE, COVID+Bear22 포함)", "test")

    # 레짐 라벨 일치율 (acting regime = rule_regime, timing_source=rule)
    ra = rev["rule_regime"].reindex(vin["rule_regime"].index)
    va = vin["rule_regime"]
    both = pd.concat([ra, va], axis=1, keys=["rev", "vin"]).dropna()
    agree_all = (both["rev"] == both["vin"]).mean()
    test_mask = both.index >= SPLIT
    agree_test = (both.loc[test_mask, "rev"] == both.loc[test_mask, "vin"]).mean()
    print(f"\n  레짐 라벨(acting=rule) 일치율: 전체 {agree_all:.1%} · 검증창 {agree_test:.1%}")
    diff = both[both["rev"] != both["vin"]]
    if len(diff):
        print(f"  불일치 {len(diff)}일 — 전환 예시(최근 5):")
        for dt, row in diff.tail(5).iterrows():
            print(f"    {dt.date()}: 현행={row['rev']:>11} vs vintage={row['vin']:>11}")

    print("\n  판정:")
    print("  • 4지표·라벨이 거의 동일 → 수정 누수 무시 가능, 백테스트·walk-forward 결론 유지.")
    print("  • TEST Martin이 vintage에서 유의 하락 → 백테스트가 OOS 과대평가(라이브는 더 낮음).")
    print("  주의: vintage=첫발표 근사(이후 소수정 미반영). 시장 일별지표는 무수정. USD단일.")


if __name__ == "__main__":
    main()
