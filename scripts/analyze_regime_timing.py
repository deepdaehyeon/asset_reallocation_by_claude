"""
층 2 — 위험레짐 진입/이탈 적시성(timing) 검증.

층 0·1 결론: 레짐 시스템의 가치는 거친 risk-on/off **스위칭**(+ vol targeting)에
있다. 그렇다면 핵심은 그 스위칭이 **실제 시장 스트레스 대비 제때** 일어나는가다.

기존 도구(regime_classification_metrics)는 "룰을 정답으로 둔 HMM vs 룰" 비교라
적시성을 못 본다. 여기선 **실현 SPY 드로우다운**을 정답으로 삼아:
  1. SPY 드로우다운 ≥8% 에피소드를 자동 탐지(peak→trough)
  2. 각 에피소드에서 방어레짐(Crisis/Stagflation/Slowdown) 진입/이탈 래그 + 커버리지
  3. 전체 기간 일별 정밀도/리콜 (방어레짐 vs forward-21일 SPY 드로우다운 정답)
  4. 확정(confirmed) vs 룰(rule) 레짐 — confirmation 히스테리시스가 래그를 더하는지

음수 래그 = 선행(좋음), 양수 = 후행. 코드 변경 없음. 결과는 docs/에 저장.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import List, Tuple

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

START = "2010-01-01"
END = "2025-04-30"
REBAL_FREQ = "W-FRI"
TX_COST = 0.001

DEFENSIVE = {"Crisis", "Stagflation", "Slowdown"}
EPISODE_MIN_DD = 0.08   # ≥8% 하락만 에피소드로
FWD = 21                # 일별 정답: forward 21일 SPY max-drawdown
GT_THRESHOLDS = [0.05, 0.10, 0.15]


def make_engine(config, universe_px, signal_px, fred_history):
    return BacktestEngine(
        config=config, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=float(config.get("rebalancing", {}).get("drift_threshold", 0.015)),
        cooldown_days=int(config.get("rebalancing", {}).get("min_rebalance_interval_days", 0)),
        fred_history=fred_history,
    )


def detect_episodes(spy: pd.Series, min_dd: float) -> List[Tuple[pd.Timestamp, pd.Timestamp, float]]:
    """SPY 드로우다운 ≥min_dd 에피소드 (peak_date, trough_date, trough_dd) 자동 탐지."""
    dd = spy / spy.cummax() - 1.0
    in_dd = dd < -1e-9
    # 신고가(dd==0) 회복 시점마다 그룹 증가 → 연속 드로우다운 구간 분리
    group = (~in_dd).cumsum()
    episodes = []
    for _, seg in dd[in_dd].groupby(group[in_dd]):
        if seg.min() > -min_dd:
            continue
        run_start = seg.index[0]
        pos = dd.index.get_loc(run_start)
        peak_date = dd.index[pos - 1] if pos > 0 else run_start  # 직전 신고가일
        trough_date = seg.idxmin()
        episodes.append((peak_date, trough_date, float(seg.min())))
    return episodes


def fwd_maxdd(spy: pd.Series, window: int) -> pd.Series:
    arr = spy.values
    n = len(arr)
    out = np.full(n, np.nan)
    for i in range(n):
        j = min(i + 1 + window, n)
        seg = arr[i + 1:j]
        if len(seg) and arr[i] > 0:
            out[i] = seg.min() / arr[i] - 1.0
    return pd.Series(out, index=spy.index)


def episode_timing(regime: pd.Series, peak: pd.Timestamp, trough: pd.Timestamp,
                   defensive: set) -> dict:
    is_def = regime.isin(defensive)
    decline = regime[(regime.index >= peak) & (regime.index <= trough)]
    # 진입: peak~trough 사이 첫 방어일 (peak 10일 전부터 허용 — 선행 포착)
    win = regime[(regime.index >= peak - pd.Timedelta(days=14)) & (regime.index <= trough)]
    def_days = win[win.isin(defensive)]
    entry_lag = (def_days.index[0] - peak).days if len(def_days) else None
    coverage = float(decline.isin(defensive).mean()) if len(decline) else float("nan")
    # 이탈: trough 이후 첫 비방어일 (90일 내)
    after = regime[(regime.index > trough) & (regime.index <= trough + pd.Timedelta(days=120))]
    nondef = after[~after.isin(defensive)]
    exit_lag = (nondef.index[0] - trough).days if len(nondef) else None
    return {"entry_lag": entry_lag, "coverage": coverage, "exit_lag": exit_lag}


def daily_confusion(regime: pd.Series, spy_fwd_dd: pd.Series, thr: float, defensive: set) -> dict:
    df = pd.DataFrame({"def": regime.isin(defensive), "dd": spy_fwd_dd}).dropna()
    stress = df["dd"] <= -thr
    defen = df["def"]
    tp = int((defen & stress).sum())
    fp = int((defen & ~stress).sum())
    fn = int((~defen & stress).sum())
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    return {"thr": thr, "n_stress": int(stress.sum()), "precision": precision, "recall": recall}


def main() -> None:
    with open(ROOT / "trading" / "config.yaml") as f:
        config = yaml.safe_load(f)

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=config, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    print("전체 백테스트 실행 (current config)...")
    result = make_engine(config, universe_px, signal_px, fred_history).run()
    confirmed = result["regime"]
    rule = result["rule_regime"]

    spy = signal_px["SPY"].reindex(result.index).ffill()
    spy_fwd_dd = fwd_maxdd(spy, FWD)

    # ── 1. 에피소드별 타이밍 ──────────────────────────────────────────────────
    episodes = detect_episodes(spy, EPISODE_MIN_DD)
    print(f"\n{'='*92}")
    print(f"  1. SPY 드로우다운 ≥{EPISODE_MIN_DD:.0%} 에피소드별 방어레짐 타이밍 (음수 래그=선행)")
    print(f"{'='*92}")
    hdr = (f"  {'peak':<12}{'trough':<12}{'dd':>7}  │ "
           f"{'confirmed':^28} │ {'rule':^28}")
    print(hdr)
    print(f"  {'':<12}{'':<12}{'':>7}  │ {'entry':>7}{'cover':>9}{'exit':>10} │ "
          f"{'entry':>7}{'cover':>9}{'exit':>10}")
    print("  " + "─" * (len(hdr) + 6))

    agg = {"confirmed": [], "rule": []}
    for peak, trough, dd in episodes:
        ct = episode_timing(confirmed, peak, trough, DEFENSIVE)
        rt = episode_timing(rule, peak, trough, DEFENSIVE)
        agg["confirmed"].append(ct)
        agg["rule"].append(rt)

        def fmt(t):
            e = f"{t['entry_lag']:+d}d" if t["entry_lag"] is not None else "miss"
            x = f"{t['exit_lag']:+d}d" if t["exit_lag"] is not None else "—"
            return f"{e:>7}{t['coverage']:>8.0%}{x:>10}"
        print(f"  {peak.date()!s:<12}{trough.date()!s:<12}{dd:>+6.0%}  │ "
              f"{fmt(ct)} │ {fmt(rt)}")

    # 집계
    def summarize(items):
        entries = [t["entry_lag"] for t in items if t["entry_lag"] is not None]
        covers = [t["coverage"] for t in items if not np.isnan(t["coverage"])]
        exits = [t["exit_lag"] for t in items if t["exit_lag"] is not None]
        misses = sum(1 for t in items if t["entry_lag"] is None)
        return entries, covers, exits, misses

    print(f"\n  집계 (n={len(episodes)} 에피소드):")
    for key in ("confirmed", "rule"):
        entries, covers, exits, misses = summarize(agg[key])
        print(f"    [{key}] 진입래그 중앙값 {np.median(entries):+.0f}d "
              f"(평균 {np.mean(entries):+.1f}d) | 커버리지 평균 {np.mean(covers):.0%} | "
              f"이탈래그 중앙값 {np.median(exits):+.0f}d | 미진입 {misses}건")

    # ── 2. 일별 정밀도/리콜 ───────────────────────────────────────────────────
    print(f"\n{'='*92}")
    print(f"  2. 일별 정밀도/리콜 — 방어레짐 vs forward-{FWD}일 SPY 드로우다운 정답")
    print(f"{'='*92}")
    print(f"  {'정답 임계':>10}{'스트레스일':>10}{'precision':>12}{'recall':>10}  해석")
    print("  " + "─" * 70)
    for thr in GT_THRESHOLDS:
        c = daily_confusion(confirmed, spy_fwd_dd, thr, DEFENSIVE)
        note = ""
        if thr == 0.10:
            note = "← 진짜 위기 기준"
        print(f"  {'≥'+format(thr,'.0%'):>10}{c['n_stress']:>10}"
              f"{c['precision']:>12.0%}{c['recall']:>10.0%}  {note}")

    print("\n  (precision=방어전환이 실제 스트레스로 이어진 비율, recall=스트레스를 방어로 잡은 비율)")
    print("  주의: detect_regime은 후행적 — 음수 진입래그(선행)는 드물 것으로 예상.")


if __name__ == "__main__":
    main()
