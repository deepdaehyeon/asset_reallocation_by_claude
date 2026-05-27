"""
C안 Phase 1 — forward return quantile binning 라벨 정의·분포·분리도 검증.

목표:
  - 새 quantile 기반 라벨 시리즈를 생성하고
  - 기존 detect_regime 라벨과 분포·전환 패턴·분리도를 비교한다.
  - 코드 변경 없이 시뮬레이션만. Phase 2 진행 여부 결정.

옵션 2 매핑 (5개 레짐 이름 유지):
  - Crisis      : forward 변동성 top 10%
  - Stagflation : forward 수익률 bottom 30% + 변동성 ≥ median
  - Slowdown    : forward 수익률 bottom 30% + 변동성 < median
  - Reflation   : forward 수익률 top 30% + 변동성 ≥ median
  - Goldilocks  : forward 수익률 top 30% + 변동성 < median
  - 나머지 ~40% : 가장 가까운 bin에 (Manhattan 거리, 수익률·변동성 z-score)

forward window: 21영업일 (1개월).
quantile threshold: 전체 데이터에 대해 한 번 추정 (in-sample, Phase 1 sanity check).
   ※ Phase 1b (별도 PR)에서 walk-forward로 학습/추론 분리해 진짜 예측력 검증 예정.

비교 지표:
  - 레짐 분포
  - 전환 횟수 + whipsaw 비율 (21일 내 직전 레짐 복귀)
  - 레짐별 평균 forward 21일 수익률 (분리도)

사용:
  python scripts/compare_quantile_regimes.py [--start 2010-01-01] [--end 2025-04-30]
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from features import compute_features  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
from data import load_all_prices  # noqa: E402
from regime import detect_regime  # noqa: E402


REGIMES = ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis"]
FORWARD_WINDOW = 21
EWMA_LAMBDA = 0.94


# ── 라벨 생성 ────────────────────────────────────────────────────────────────

def _ewma_fwd_vol(spy: pd.Series, window: int) -> pd.Series:
    """t 시점 기준 t+1..t+window 구간의 EWMA 연환산 변동성."""
    rets = spy.pct_change()
    # forward window의 수익률 분산을 EWMA로 (간단히 std로 근사)
    fwd_vol = []
    arr = rets.values
    for t in range(len(rets)):
        if t + window >= len(rets):
            fwd_vol.append(np.nan)
            continue
        win = arr[t + 1: t + 1 + window]
        win = win[~np.isnan(win)]
        if len(win) < window // 2:
            fwd_vol.append(np.nan)
            continue
        fwd_vol.append(float(np.std(win, ddof=1) * np.sqrt(252)))
    return pd.Series(fwd_vol, index=rets.index)


def _assign_quantile_label(ret_z: float, vol_z: float,
                            ret_hi: float, ret_lo: float,
                            vol_hi: float, vol_med: float) -> str:
    """5개 레짐 매핑."""
    # 1. Crisis: 변동성이 극단적
    if vol_z >= vol_hi:
        return "Crisis"
    # 2. 코어 4개 quadrant (수익률 top/bottom 30% × 변동성 high/low)
    if ret_z >= ret_hi and vol_z >= vol_med:
        return "Reflation"
    if ret_z >= ret_hi and vol_z < vol_med:
        return "Goldilocks"
    if ret_z <= ret_lo and vol_z >= vol_med:
        return "Stagflation"
    if ret_z <= ret_lo and vol_z < vol_med:
        return "Slowdown"
    # 3. 중간 30~70%: 가장 가까운 코어 레짐에 할당 (Manhattan distance)
    candidates = {
        "Goldilocks":  (ret_hi, 0.0),
        "Reflation":   (ret_hi, vol_med),
        "Slowdown":    (ret_lo, 0.0),
        "Stagflation": (ret_lo, vol_med),
    }
    best = min(
        candidates.items(),
        key=lambda kv: abs(ret_z - kv[1][0]) + abs(vol_z - kv[1][1])
    )
    return best[0]


def build_quantile_labels(spy: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    """forward (수익률, 변동성) → quantile threshold → 5개 레짐 라벨."""
    spy_aligned = spy.reindex(dates).ffill()
    # forward 21일 수익률
    fwd_ret = spy_aligned.pct_change(FORWARD_WINDOW).shift(-FORWARD_WINDOW)
    # forward 21일 실현 변동성
    fwd_vol = _ewma_fwd_vol(spy_aligned, FORWARD_WINDOW)
    fwd_vol.index = spy_aligned.index

    df = pd.DataFrame({"ret": fwd_ret, "vol": fwd_vol}).dropna()
    if df.empty:
        return pd.Series(dtype=object, index=dates)

    # quantile threshold (전체 분포 기준)
    ret_hi = float(np.percentile(df["ret"], 70))   # top 30%
    ret_lo = float(np.percentile(df["ret"], 30))   # bottom 30%
    vol_hi = float(np.percentile(df["vol"], 90))   # top 10% → Crisis
    vol_med = float(np.percentile(df["vol"], 50))  # median

    print(f"    quantile thresholds: ret_hi={ret_hi:+.3%}  ret_lo={ret_lo:+.3%}  "
          f"vol_hi={vol_hi:.1%}  vol_med={vol_med:.1%}")

    labels = df.apply(
        lambda r: _assign_quantile_label(r["ret"], r["vol"],
                                         ret_hi, ret_lo, vol_hi, vol_med),
        axis=1
    )
    return labels.reindex(dates)


# ── features walk-forward (기존 detect_regime 라벨 생성용) ──────────────────

def build_features_series(signal_px: pd.DataFrame,
                          fred_history: pd.DataFrame,
                          dates: pd.DatetimeIndex) -> pd.DataFrame:
    fh_aligned = (
        fred_history.reindex(dates, method="ffill").ffill(limit=45)
        if fred_history is not None and not fred_history.empty else None
    )
    rows = []
    for date in dates:
        sig = signal_px.loc[:date].tail(70)
        if len(sig) < 30:
            continue
        try:
            feat = compute_features(sig)
        except Exception:
            continue
        if fh_aligned is not None and date in fh_aligned.index:
            row = fh_aligned.loc[date]
            for c in fh_aligned.columns:
                v = row[c]
                if not pd.isna(v):
                    feat[c] = float(v)
        feat["date"] = date
        rows.append(feat)
    return pd.DataFrame(rows).set_index("date")


# ── 진단 ────────────────────────────────────────────────────────────────────

def _whipsaw_pct(series: pd.Series, window: int = 21) -> tuple[int, int, float]:
    s = series.dropna()
    prev = s.shift(1)
    trans = s[(s != prev) & prev.notna()]
    total = len(trans)
    if total == 0:
        return 0, 0, 0.0
    records = list(zip(trans.index, trans.values, prev.loc[trans.index].values))
    whip = 0
    for i, (d, to, fr) in enumerate(records[:-1]):
        for nxt_d, nxt_to, _ in records[i + 1:]:
            if (nxt_d - d).days > window * 1.5:
                break
            if nxt_to == fr:
                whip += 1
                break
    return total, whip, round(whip / total * 100, 1)


def _summary(name: str, labels: pd.Series, fwd_ret: pd.Series) -> None:
    print(f"\n  ── {name} ──")
    aligned = pd.DataFrame({"label": labels, "fwd": fwd_ret}).dropna()
    # 분포
    counts = aligned["label"].value_counts()
    total = int(counts.sum())
    dist = "  ".join(f"{r}:{int(counts.get(r, 0))}({counts.get(r,0)/total:.0%})"
                     for r in REGIMES)
    print(f"    분포 (n={total}): {dist}")

    # 분리도
    print("    regime별 평균 forward 21일 수익률 (Sharpe):")
    for r in REGIMES:
        sub = aligned[aligned["label"] == r]["fwd"]
        if len(sub) == 0:
            continue
        sh = sub.mean() / sub.std() * np.sqrt(252 / FORWARD_WINDOW) if sub.std() > 0 else 0
        print(f"      {r:<12} mean={sub.mean()*100:+.2f}%  Sharpe={sh:+.2f}  n={len(sub)}")

    # 전환·whipsaw
    total_t, whip, whip_pct = _whipsaw_pct(labels)
    print(f"    전환 {total_t}회 | whipsaw {whip}회 ({whip_pct}%)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--end", default="2025-04-30")
    args = p.parse_args()

    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)

    print(f"[1] 데이터 로딩 [{args.start} ~ {args.end}]")
    universe_px, signal_px = load_all_prices(
        config=cfg, start=args.start, end=args.end, use_cache=True
    )
    fred_history = fetch_fred_history(args.start, args.end)
    print(f"    FRED 매크로 {len(fred_history.columns) if not fred_history.empty else 0}개")

    dates = signal_px.loc[args.start:args.end].index

    print("\n[2] quantile 라벨 생성 (in-sample threshold)")
    spy = signal_px["SPY"]
    q_labels = build_quantile_labels(spy, dates)

    print("\n[3] 기존 detect_regime 라벨 생성 (walk-forward features)")
    fdf = build_features_series(signal_px, fred_history, dates)
    rule_labels = pd.Series(
        [detect_regime(r.to_dict()) for _, r in fdf.iterrows()],
        index=fdf.index, name="rule"
    )

    # forward return (분리도 측정용, 둘 다 같은 시계열 사용)
    spy_d = spy.reindex(dates).ffill()
    fwd_ret = spy_d.pct_change(FORWARD_WINDOW).shift(-FORWARD_WINDOW)

    print(f"\n[4] 분포·분리도·전환 비교")
    _summary("baseline (현재 detect_regime)", rule_labels, fwd_ret)
    _summary("quantile (Phase 1 in-sample)", q_labels, fwd_ret)

    # ── 종합 비교 ──────────────────────────────────────────────────────────
    print(f"\n{'=' * 70}\n  Goldilocks 분리도 회복 여부\n{'=' * 70}")
    for name, labels in [("rule_baseline", rule_labels), ("quantile", q_labels)]:
        aligned = pd.DataFrame({"label": labels, "fwd": fwd_ret}).dropna()
        means = {r: aligned[aligned["label"] == r]["fwd"].mean() * 100
                 for r in REGIMES if (aligned["label"] == r).any()}
        if "Goldilocks" not in means:
            continue
        gold = means["Goldilocks"]
        others_mean = np.mean([v for k, v in means.items() if k != "Goldilocks"])
        rank = sorted(means.items(), key=lambda x: -x[1])
        gold_rank = next(i + 1 for i, (k, _) in enumerate(rank) if k == "Goldilocks")
        print(f"  {name:<16}: Goldilocks {gold:+.2f}%  vs 평균 {others_mean:+.2f}%  "
              f"Δ {gold - others_mean:+.2f}pp  순위 {gold_rank}/{len(means)}")


if __name__ == "__main__":
    main()
