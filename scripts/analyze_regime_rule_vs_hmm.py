"""
룰 레짐 vs HMM 레짐 — '매일 포지션이 바뀐다'의 진짜 원인 분해.

질문(2026-06-17, 사용자): "지금까지 룰이랑 hmm이 예측한 레짐을 분석해줘. 진짜 심각하게.
  레짐 체인지 때문에 포지션을 매일마다 바꾸고 있는 것 같아."

핵심 프레이밍(config 확인 결과):
  - regime_filter.regime_timing_source = "rule"  → 라이브가 쓰는 '이산(discrete) 최종 레짐'은
    안정적인 룰 레짐이다. HMM 앙상블 argmax가 아니다.
  - regime_filter.blend_smoothing_alpha = 0.5     → 비중을 만드는 블렌딩 확률은
    (HMM 0.6 + RF 0.4)를 EWMA(α=0.5)로 평활한 '연속 확률 벡터'다.
  - 즉 비중은 blend_regime_targets(평활 블렌드)에서 나온다. 이산 레짐 '전환'이 아니라
    *블렌딩 확률의 연속 표류(drift)*가 비중을 매일 움직일 수 있다 — 이 가설을 데이터로 검증.

방법(라이브 일일 케이던스 재현):
  매 거래일마다 _get_regime(date)를 호출(= _prev_blend 평활 체인을 일일로 전진, 라이브와 동일).
  네 가지 레짐 계열을 동시 포착:
    rule        : detect_regime (룰)
    raw_hmm     : 평활 전 (HMM0.6+RF0.4) argmax — apply_blend_smoothing 첫 인자 가로채기
    smoothed    : 평활 후 블렌드 argmax (라이브 비중의 실제 입력)
    final       : 라이브 최종 이산 레짐 (= rule, timing_source 때문)
  그리고 매일 목표비중 _target_weights를 계산해 일일 L1 표류를 측정.

산출:
  - 계열별 연간 전환 횟수 / 평균 지속일(run-length) / 점유율
  - 일일 ||Δsmoothed_blend||_1 / ||Δtarget_weights||_1 평균·분포
  - 목표비중이 단독으로 drift_threshold(1.5%)를 넘긴 날의 비율
  - 그 '리밸 유발일' 중 이산 최종레짐 전환이 있던 비율 vs 블렌드 표류만으로 넘긴 비율
    → 사용자 가설(이산 레짐 전환이 매일 포지션을 바꾼다) 검증/반증
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "scripts"))

import warnings
warnings.filterwarnings("ignore", message="Model is not converging.*")

from data import load_all_prices  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
from features import compute_features  # noqa: E402
import engine as engine_mod  # noqa: E402
from ablation_regime_stack import build_engine  # noqa: E402
from compare_rule_timing_ab import START, END  # noqa: E402

REGIMES = ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis", "Transition"]


def argmax_regime(d):
    if not d:
        return None
    return max(d.items(), key=lambda kv: kv[1])[0]


def series_stats(labels):
    """계열의 전환 횟수, 평균 run-length, 점유율."""
    labels = [x for x in labels if x is not None]
    n = len(labels)
    if n == 0:
        return {}, 0, 0.0
    flips = sum(1 for a, b in zip(labels, labels[1:]) if a != b)
    runs = []
    cur = 1
    for a, b in zip(labels, labels[1:]):
        if a == b:
            cur += 1
        else:
            runs.append(cur)
            cur = 1
    runs.append(cur)
    occ = pd.Series(labels).value_counts(normalize=True).to_dict()
    return occ, flips, float(np.mean(runs))


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)

    start = sys.argv[1] if len(sys.argv) > 1 else START
    end = sys.argv[2] if len(sys.argv) > 2 else END
    drift_thr = float(base.get("rebalancing", {}).get("drift_threshold", 0.015))

    print(f"데이터 로딩 [{start} ~ {end}]...", flush=True)
    universe_px, signal_px = load_all_prices(config=base, start=start, end=end, use_cache=True)
    fred_history = fetch_fred_history(start, end)

    eng = build_engine(base, universe_px, signal_px, fred_history)

    # 평활 전 raw 블렌드(HMM0.6+RF0.4) 가로채기.
    captured = {"raw": None}
    orig_smooth = engine_mod.apply_blend_smoothing

    def spy_smooth(blend, prev, alpha, **kw):
        captured["raw"] = dict(blend)
        return orig_smooth(blend, prev, alpha, **kw)

    engine_mod.apply_blend_smoothing = spy_smooth

    px = universe_px.copy()
    dates = px.index
    rows = []
    prev_blend_vec = None
    prev_tw = None

    print(f"일일 레짐/비중 계산 ({len(dates)}일)...", flush=True)
    for i, date in enumerate(dates):
        captured["raw"] = None
        try:
            final, blend, rule_regime, conf, rc, hc = eng._get_regime(date)
        except Exception:
            continue

        raw_hmm = argmax_regime(captured["raw"]) if captured["raw"] is not None else None
        smoothed = argmax_regime(blend)

        sig = signal_px[:date].tail(65)
        feat = compute_features(sig) if len(sig) >= 30 else {}
        rv = feat.get("realized_vol", 0.15)
        vix = feat.get("vix", 0.0)
        try:
            tw = eng._target_weights(
                blend, rv, 1.0, regime=final, vix=vix,
                signal_px_slice=sig, universe_px_slice=px[:date].tail(65),
            )
        except Exception:
            tw = {}

        blend_vec = np.array([blend.get(r, 0.0) for r in REGIMES])
        d_blend = float(np.abs(blend_vec - prev_blend_vec).sum()) if prev_blend_vec is not None else np.nan
        if prev_tw is not None and tw:
            keys = set(prev_tw) | set(tw)
            d_tw = float(sum(abs(tw.get(k, 0.0) - prev_tw.get(k, 0.0)) for k in keys))
        else:
            d_tw = np.nan

        regime_changed = (len(rows) > 0 and final != rows[-1]["final"])
        rows.append({
            "date": date, "rule": rule_regime, "raw_hmm": raw_hmm,
            "smoothed": smoothed, "final": final,
            "d_blend": d_blend, "d_tw": d_tw, "regime_changed": regime_changed,
        })
        prev_blend_vec = blend_vec
        prev_tw = tw
        if (i + 1) % 200 == 0:
            print(f"  ... {i+1}/{len(dates)} ({date.date()})", flush=True)

    engine_mod.apply_blend_smoothing = orig_smooth

    df = pd.DataFrame(rows).set_index("date")
    if df.empty:
        print("데이터 없음.")
        return

    span_years = (df.index[-1] - df.index[0]).days / 365.25
    print("\n" + "=" * 96)
    print(f"  룰 vs HMM 레짐 분해 — {df.index[0].date()} ~ {df.index[-1].date()} "
          f"({len(df)} 거래일, {span_years:.1f}년)")
    print("=" * 96)

    print(f"\n  {'계열':<26}{'전환/년':>10}{'평균지속일':>12}{'점유율 상위3':>0}")
    print("  " + "-" * 90)
    for col, name in [("rule", "rule (룰=라이브 최종이산)"),
                      ("raw_hmm", "raw_hmm (평활 전 argmax)"),
                      ("smoothed", "smoothed (평활 후 argmax)"),
                      ("final", "final (라이브 최종)")]:
        occ, flips, mean_run = series_stats(df[col].tolist())
        per_yr = flips / span_years if span_years > 0 else 0
        top3 = ", ".join(f"{k} {v:.0%}" for k, v in
                         sorted(occ.items(), key=lambda x: -x[1])[:3])
        print(f"  {name:<26}{per_yr:>10.1f}{mean_run:>12.1f}   {top3}")

    db = df["d_blend"].dropna()
    dt = df["d_tw"].dropna()
    print("\n  일일 변동(L1):")
    print(f"    평활 블렌드 ||Δ||₁  평균 {db.mean():.4f}  중앙 {db.median():.4f}  "
          f"95p {db.quantile(0.95):.4f}  최대 {db.max():.4f}")
    print(f"    목표비중   ||Δ||₁  평균 {dt.mean():.4f}  중앙 {dt.median():.4f}  "
          f"95p {dt.quantile(0.95):.4f}  최대 {dt.max():.4f}")

    over = dt > drift_thr
    n_over = int(over.sum())
    print(f"\n  목표비중이 단독으로 drift_threshold({drift_thr:.1%})를 넘긴 날: "
          f"{n_over}/{len(dt)} ({n_over/len(dt):.1%})")

    # 그 리밸 유발일 중 이산 최종레짐 전환이 있던 비율
    over_idx = dt[over].index
    chg = df.loc[over_idx, "regime_changed"]
    n_chg = int(chg.sum())
    print(f"    그 중 이산 최종레짐(rule) 전환이 '같은 날' 있던 날: "
          f"{n_chg}/{n_over} ({(n_chg/n_over if n_over else 0):.1%})")
    print(f"    → 나머지 {n_over - n_chg}/{n_over} "
          f"({(1 - n_chg/n_over if n_over else 0):.1%})는 이산 레짐 불변, "
          f"블렌드 표류만으로 리밸 유발")

    # 이산 최종레짐 전환일의 비중변동 vs 비전환일
    chg_days = df["regime_changed"]
    print(f"\n  이산 최종레짐 전환일 수: {int(chg_days.sum())}/{len(df)} "
          f"({chg_days.sum()/len(df):.1%}) — 연 {chg_days.sum()/span_years:.1f}회")
    if chg_days.sum() > 0:
        print(f"    전환일 평균 ||Δtarget||₁ = {df.loc[chg_days, 'd_tw'].dropna().mean():.4f}")
    print(f"    비전환일 평균 ||Δtarget||₁ = {df.loc[~chg_days, 'd_tw'].dropna().mean():.4f}")

    out = ROOT / "docs" / "_regime_rule_vs_hmm_daily.csv"
    df.to_csv(out)
    print(f"\n  일별 시계열 저장: {out.relative_to(ROOT)}")
    return df


if __name__ == "__main__":
    main()
