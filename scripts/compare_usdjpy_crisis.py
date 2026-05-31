"""
USDJPY(엔캐리 청산) 신호를 Crisis 트리거에 추가했을 때의 효과 검증.

가설:
  2024-08 엔캐리 청산처럼 BoJ 긴축 + 엔 급등(USDJPY 급락)이 선행하는 유동성
  쇼크는, VIX가 터지기 전에 USDJPY 급락으로 먼저 드러난다. detect_regime의
  Crisis 조건(rvol>0.30 or vix>40)에 "엔 급등 OR" 조건을 더하면 Crisis 진입을
  앞당길 수 있다.

검증 질문 (둘 다 답해야 채택 판단 가능):
  1. 리드타임 — 2024-08 구간에서 엔 트리거가 baseline(VIX/rvol) 대비 Crisis를
     며칠 앞당기는가?
  2. False Crisis — 다른 구간에서 엔 트리거만으로 새로 켜지는 Crisis 일자가
     실제로 위험했는가(forward 수익률 음수), 아니면 헛불(양수)인가?

방법 (코드 변경 없음 — 시뮬레이션):
  - 실제 detect_regime을 그대로 import → 룰 로직 100% 재현, 엔 트리거 ADD만 측정
  - JPY=X를 backtest 캐시 경로로 다운로드해 features에 usdjpy_mom_Nd 주입
  - 변형: (window, threshold) 그리드. USDJPY 급락(=엔 급등) 임계는 음수.

USDJPY(JPY=X) 해석: 엔/달러. 값 하락 = 엔 강세 = 캐리 청산.
  → 청산 신호 = usdjpy_mom_Nd <= threshold (threshold < 0)
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
from regime import detect_regime, _growth_inflation_signals, REGIMES  # noqa: E402
from data import load_all_prices, download_prices  # noqa: E402

USDJPY_TICKER = "JPY=X"

# 엔 모멘텀 윈도우(영업일)와 청산 임계(음수=엔 급등). 그리드 스윕.
YEN_WINDOWS = [5, 10]
YEN_THRESHOLDS = [-0.03, -0.05]

# (A) VIX 종가 Crisis 임계 스윕. baseline=40. rvol 임계는 0.30 고정.
VIX_THRESHOLDS = [40.0, 37.0, 35.0, 32.0, 30.0]
BASE_RVOL_THRESHOLD = 0.30

# 2024-08 캐리 청산 분석 구간
EVENT_START = "2024-07-01"
EVENT_END = "2024-09-30"
EVENT_PEAK = "2024-08-05"  # VIX 인트라데이 ~65, USDJPY ~142 저점


def detect_with_yen(features: dict, yen_mom: float | None, threshold: float) -> str:
    """엔 급등(usdjpy_mom <= threshold)이면 Crisis, 아니면 실제 detect_regime 그대로."""
    if yen_mom is not None and not np.isnan(yen_mom) and yen_mom <= threshold:
        return "Crisis"
    return detect_regime(features)


def detect_with_vix(features: dict, vix_thr: float, rvol_thr: float = BASE_RVOL_THRESHOLD) -> str:
    """
    regime.detect_regime을 Crisis 임계만 파라미터화해 그대로 재현.

    Crisis 외 우선순위 캐스케이드는 detect_regime(regime.py:91-110)과 동일.
    _growth_inflation_signals를 import해 비-Crisis 로직 100% 일치 보장.
    """
    rvol = features["realized_vol"]
    vix = features["vix"]
    if rvol > rvol_thr or vix > vix_thr:
        return "Crisis"
    gb, gB, ir, il = _growth_inflation_signals(features)
    if gB >= 2 and ir >= 1:
        return "Stagflation"
    if gB >= 2:
        return "Slowdown"
    if gb >= 2 and il >= 1:
        return "Goldilocks"
    if gb >= 2 and ir >= 1:
        return "Reflation"
    if gB >= 1:
        return "Slowdown"
    return "Goldilocks"


def build_features_series(
    signal_px: pd.DataFrame,
    usdjpy: pd.Series,
    fred_history: pd.DataFrame,
    start: str,
    end: str,
) -> pd.DataFrame:
    """walk-forward 일별 features + 엔 모멘텀 컬럼(usdjpy_mom_{N})."""
    dates = signal_px.loc[start:end].index

    fh_aligned = (
        fred_history.reindex(dates, method="ffill").ffill(limit=45)
        if fred_history is not None and not fred_history.empty else None
    )

    # 엔 시리즈를 signal 인덱스에 정렬(ffill) 후 윈도우별 모멘텀 사전 계산
    yen = usdjpy.reindex(signal_px.index).ffill()
    yen_mom = {w: yen.pct_change(w) for w in YEN_WINDOWS}

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
        for w in YEN_WINDOWS:
            feat[f"usdjpy_mom_{w}"] = (
                float(yen_mom[w].loc[date]) if date in yen_mom[w].index else np.nan
            )
        feat["usdjpy"] = float(yen.loc[date]) if date in yen.index else np.nan
        feat["date"] = date
        rows.append(feat)
    return pd.DataFrame(rows).set_index("date")


def first_crisis_date(regimes: pd.Series, lo: str, hi: str) -> pd.Timestamp | None:
    """구간 내 첫 Crisis 일자."""
    sub = regimes.loc[lo:hi]
    hits = sub[sub == "Crisis"]
    return hits.index[0] if len(hits) > 0 else None


def forward_max_drawdown(prices: pd.Series, window: int) -> pd.Series:
    """
    각 시점 t에서 향후 window 영업일 내 진입가 대비 최저점 낙폭을 반환한다 (음수).

    fwd_dd[t] = min_{1<=k<=window} (P[t+k] / P[t] - 1)
    point-to-point 수익률과 달리 "그 후 얼마나 깊이 빠졌나"를 측정 →
    de-risking 가치 판정에 적합. Crisis 진입 후 V자 반등해도 낙폭은 포착.
    """
    arr = prices.values.astype(float)
    n = len(arr)
    out = np.full(n, np.nan)
    for i in range(n):
        j = min(i + 1 + window, n)
        if i + 1 < j and arr[i] > 0:
            out[i] = arr[i + 1:j].min() / arr[i] - 1.0
    return pd.Series(out, index=prices.index)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--end", default="2025-04-30")
    p.add_argument("--forward-window", type=int, default=21)
    args = p.parse_args()

    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)

    print(f"[1] 데이터 로딩 [{args.start} ~ {args.end}]")
    universe_px, signal_px = load_all_prices(
        config=cfg, start=args.start, end=args.end, use_cache=True
    )
    usdjpy_df = download_prices([USDJPY_TICKER], start=args.start, end=args.end, use_cache=True)
    if usdjpy_df.empty or USDJPY_TICKER not in usdjpy_df.columns:
        print("    [에러] JPY=X 다운로드 실패 — 중단")
        sys.exit(1)
    usdjpy = usdjpy_df[USDJPY_TICKER].dropna()
    print(f"    USDJPY {len(usdjpy)}일 ({usdjpy.index[0].date()} ~ {usdjpy.index[-1].date()})")

    fred_history = fetch_fred_history(args.start, args.end)
    print(f"    FRED 매크로 {len(fred_history.columns) if not fred_history.empty else 0}개")

    print("[2] features walk-forward 계산 중...")
    fdf = build_features_series(signal_px, usdjpy, fred_history, args.start, args.end)
    print(f"    {len(fdf)}일")

    # forward N일 SPY 수익률 + forward max-drawdown
    spy = signal_px["SPY"].reindex(fdf.index).ffill()
    fdf["fwd_ret"] = spy.pct_change(args.forward_window).shift(-args.forward_window)
    fdf["fwd_dd"] = forward_max_drawdown(spy, args.forward_window)

    # baseline 레짐(엔 트리거 없음)
    fdf["baseline"] = [detect_regime(r.to_dict()) for _, r in fdf.iterrows()]

    fw = args.forward_window
    print(f"\n{'=' * 72}\n  [3] 2024-08 캐리 청산 리드타임 (forward={fw}일)\n{'=' * 72}")
    base_first = first_crisis_date(fdf["baseline"], EVENT_START, EVENT_END)
    print(f"  baseline 첫 Crisis    : {base_first.date() if base_first is not None else '없음'}")
    print(f"  (참고) 시장 피크 일자 : {EVENT_PEAK}\n")

    variants = [(w, t) for w in YEN_WINDOWS for t in YEN_THRESHOLDS]
    leadtime_rows = []
    for w, t in variants:
        col = f"yen_{w}d_{int(t * 100)}pct"
        mom = fdf[f"usdjpy_mom_{w}"]
        fdf[col] = [
            detect_with_yen(r.to_dict(), mom.loc[idx], t)
            for idx, r in fdf.iterrows()
        ]
        v_first = first_crisis_date(fdf[col], EVENT_START, EVENT_END)
        if v_first is not None and base_first is not None:
            lead = (base_first - v_first).days
            lead_str = f"{lead:+d}일 (음수=늦음)"
        elif v_first is not None and base_first is None:
            lead_str = "baseline은 미발동, 변형만 발동"
        else:
            lead_str = "변형 미발동"
        print(f"  {col:<16}: 첫 Crisis {str(v_first.date()) if v_first is not None else '없음':<12} "
              f"리드 {lead_str}")
        leadtime_rows.append({"variant": col, "first_crisis": v_first})

    print(f"\n{'=' * 72}\n  [4] 전체 구간 False Crisis 검증 (forward max-drawdown 기준)\n{'=' * 72}")
    print("  엔 트리거로만 새로 켜진 Crisis 일자 = (변형 Crisis) AND (baseline ≠ Crisis)")
    print(f"  fwd_dd = 향후 {fw}일 내 진입가 대비 최저 낙폭. 깊을수록(음수↑) 진짜 위험 선행.")
    print("  판정 기준: 추가 Crisis 날의 낙폭이 baseline Crisis 날 낙폭에 가까울수록 정당.\n")

    # 야드스틱: baseline Crisis 날 / 전체 날 평균 fwd_dd
    base_crisis_mask = fdf["baseline"] == "Crisis"
    base_crisis_dd = fdf.loc[base_crisis_mask, "fwd_dd"].dropna()
    all_dd = fdf["fwd_dd"].dropna()
    print(f"  [야드스틱] baseline Crisis {int(base_crisis_mask.sum())}일  "
          f"평균 fwd_dd {base_crisis_dd.mean() * 100:+.2f}%  "
          f"중앙값 {base_crisis_dd.median() * 100:+.2f}%")
    print(f"            전체 {len(all_dd)}일      "
          f"평균 fwd_dd {all_dd.mean() * 100:+.2f}%  "
          f"중앙값 {all_dd.median() * 100:+.2f}%\n")

    SEVERE_DD = -0.05  # "진짜 stress" 기준 낙폭
    for w, t in variants:
        col = f"yen_{w}d_{int(t * 100)}pct"
        extra = (fdf[col] == "Crisis") & (fdf["baseline"] != "Crisis")
        n_extra = int(extra.sum())
        if n_extra == 0:
            print(f"  {col:<16}: 추가 Crisis 0일 (baseline과 동일)")
            continue
        dd = fdf.loc[extra, "fwd_dd"].dropna()
        mean_dd = dd.mean() * 100 if len(dd) > 0 else float("nan")
        med_dd = dd.median() * 100 if len(dd) > 0 else float("nan")
        severe = float((dd <= SEVERE_DD).mean()) * 100 if len(dd) > 0 else float("nan")
        print(f"  {col:<16}: 추가 {n_extra:>3}일  "
              f"평균 fwd_dd {mean_dd:+.2f}%  중앙값 {med_dd:+.2f}%  "
              f"낙폭≤-5% 비율 {severe:.0f}%")

    # ── (A) VIX 종가 임계 보정 ────────────────────────────────────────────
    print(f"\n{'=' * 72}\n  [4b] (A) VIX 종가 Crisis 임계 보정\n{'=' * 72}")
    print("  현재 vix>40 종가 임계가 2024-08(VIX 종가 38.6, 인트라데이 ~65)을 놓침.")
    print("  임계를 낮춰 (1) 2024-08을 잡는지 (2) 추가 Crisis 날이 진짜 낙폭을 동반하는지.\n")
    for vt in VIX_THRESHOLDS:
        col = f"vix>{int(vt)}"
        regimes = pd.Series(
            [detect_with_vix(r.to_dict(), vt) for _, r in fdf.iterrows()],
            index=fdf.index,
        )
        v_first = first_crisis_date(regimes, EVENT_START, EVENT_END)
        # baseline(vix>40) 대비 추가 Crisis
        extra = (regimes == "Crisis") & (fdf["baseline"] != "Crisis")
        n_extra = int(extra.sum())
        dd = fdf.loc[extra, "fwd_dd"].dropna()
        if n_extra > 0 and len(dd) > 0:
            mean_dd = dd.mean() * 100
            med_dd = dd.median() * 100
            severe = float((dd <= SEVERE_DD).mean()) * 100
            dd_str = f"평균 fwd_dd {mean_dd:+.2f}%  중앙값 {med_dd:+.2f}%  낙폭≤-5% {severe:.0f}%"
        else:
            dd_str = "추가 Crisis 없음"
        first_str = str(v_first.date()) if v_first is not None else "없음"
        tag = " (baseline)" if vt == 40.0 else ""
        print(f"  {col:<8}{tag:<11}: 2024-08 첫 Crisis {first_str:<12} "
              f"추가 {n_extra:>3}일  {dd_str}")

    print(f"\n{'=' * 72}\n  [5] (참고) baseline 레짐별 forward 수익률·낙폭 분리도\n{'=' * 72}")
    for r in REGIMES:
        mask = fdf["baseline"] == r
        sub_ret = fdf.loc[mask, "fwd_ret"].dropna()
        sub_dd = fdf.loc[mask, "fwd_dd"].dropna()
        if len(sub_ret) == 0:
            continue
        print(f"  {r:<12} n={int(mask.sum()):>5}  "
              f"fwd_ret {sub_ret.mean() * 100:+.2f}%  "
              f"fwd_dd 평균 {sub_dd.mean() * 100:+.2f}% / 중앙값 {sub_dd.median() * 100:+.2f}%")

    # event 구간 USDJPY 궤적 일부 출력 (디버그/검증용)
    print(f"\n{'=' * 72}\n  [6] 2024-08 구간 USDJPY·모멘텀 궤적 (검증용)\n{'=' * 72}")
    ev = fdf.loc[EVENT_START:EVENT_END]
    cols = ["usdjpy"] + [f"usdjpy_mom_{w}" for w in YEN_WINDOWS] + ["vix", "realized_vol", "baseline"]
    cols = [c for c in cols if c in ev.columns]
    with pd.option_context("display.max_rows", 70, "display.width", 120):
        show = ev[cols].copy()
        for w in YEN_WINDOWS:
            c = f"usdjpy_mom_{w}"
            if c in show.columns:
                show[c] = (show[c] * 100).round(2)
        show["usdjpy"] = show["usdjpy"].round(2)
        show["vix"] = show["vix"].round(1)
        show["realized_vol"] = (show["realized_vol"] * 100).round(1)
        print(show.to_string())


if __name__ == "__main__":
    main()
