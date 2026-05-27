"""
레짐 분류기 관측·검증 도구 (외부 비평 #4, #6 처리).

#6 관측 가능성 — 대리 지표
  (a) 레짐별 forward N일 평균 수익률·변동성·Sharpe (분리도)
  (b) 전환 시점 ±N일 누적 수익률 (전환 적시성: 선행/후행)
  (c) whipsaw 빈도 (단기간 내 동일 레짐 복귀)

#4 신뢰도 캘리브레이션
  (d) confidence bin별 분류 정확도 (rule_regime을 정답으로 — 자기참조 한계 있음)
  (e) confidence threshold 민감도 (threshold 변화 시 fallback 비율·전체 성과)

사용:
  python scripts/regime_diagnostics.py [--start 2010-01-01] [--end 2025-04-30] \
      [--forward-window 21] [--out docs/regime_diagnostics_<date>.json]

출력:
  콘솔: ASCII 표
  파일: JSON dump (옵션)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from fetcher import fetch_fred_history  # noqa: E402
from data import load_all_prices  # noqa: E402
from engine import BacktestEngine  # noqa: E402


REGIMES = ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis"]


# ── #6 (a) 레짐별 forward return ────────────────────────────────────────────

def regime_forward_return_separation(result: pd.DataFrame, forward_window: int) -> pd.DataFrame:
    """레짐별 forward N일 누적 수익률 통계 (분리도)."""
    df = result.copy()
    df["fwd_return"] = df["value"].pct_change(forward_window).shift(-forward_window)
    rows = []
    for r in REGIMES:
        mask = df["regime"] == r
        sub = df.loc[mask, "fwd_return"].dropna()
        if len(sub) == 0:
            continue
        rows.append({
            "regime": r,
            "n_days": int(mask.sum()),
            "fwd_mean_pct": float(sub.mean() * 100),
            "fwd_std_pct": float(sub.std() * 100),
            "sharpe_annualized": (
                float(sub.mean() / sub.std() * np.sqrt(252 / forward_window))
                if sub.std() > 0 else float("nan")
            ),
            "win_rate_pct": float((sub > 0).mean() * 100),
        })
    return pd.DataFrame(rows)


# ── #6 (b) 전환 적시성 ────────────────────────────────────────────────────────

def transition_timeliness(result: pd.DataFrame, window: int = 21) -> pd.DataFrame:
    """전환 시점 ±window 일 누적 수익률 (선행/후행 판단)."""
    df = result.copy()
    df["regime_prev"] = df["regime"].shift(1)
    transitions = df[(df["regime"] != df["regime_prev"]) & df["regime_prev"].notna()]

    rows = []
    for date, row in transitions.iterrows():
        idx = df.index.get_loc(date)
        if idx < window or idx + window >= len(df):
            continue
        pre = df["value"].iloc[idx - window] / df["value"].iloc[idx - 2 * window] - 1 if idx >= 2 * window else np.nan
        # 전환 직전 window일 / 직후 window일 수익률
        ret_pre = df["value"].iloc[idx] / df["value"].iloc[idx - window] - 1
        ret_post = df["value"].iloc[idx + window] / df["value"].iloc[idx] - 1
        rows.append({
            "date": date,
            "from": row["regime_prev"],
            "to": row["regime"],
            "ret_pre_pct": float(ret_pre * 100),
            "ret_post_pct": float(ret_post * 100),
        })

    if not rows:
        return pd.DataFrame()
    tdf = pd.DataFrame(rows)
    # 전환 종류별 평균
    summary = (
        tdf.groupby(["from", "to"])
        .agg(n=("date", "size"),
             ret_pre_pct=("ret_pre_pct", "mean"),
             ret_post_pct=("ret_post_pct", "mean"))
        .reset_index()
    )
    return summary, tdf


# ── #6 (c) whipsaw 빈도 ─────────────────────────────────────────────────────

def whipsaw_frequency(result: pd.DataFrame, window: int = 21) -> dict:
    """N영업일 내 직전 레짐으로 복귀한 전환의 비율."""
    df = result.copy()
    df["regime_prev"] = df["regime"].shift(1)
    transitions = df[(df["regime"] != df["regime_prev"]) & df["regime_prev"].notna()]
    total = len(transitions)
    if total == 0:
        return {"total_transitions": 0, "whipsaw_count": 0, "whipsaw_rate_pct": 0.0}

    whipsaw = 0
    trans_records = transitions.reset_index()[["date", "regime", "regime_prev"]].to_dict("records")
    for i, t in enumerate(trans_records[:-1]):
        for nxt in trans_records[i + 1:]:
            days_elapsed = (nxt["date"] - t["date"]).days
            if days_elapsed > window * 1.5:  # 영업일 → 달력일 환산 보수적
                break
            # 다음 전환이 원래 레짐으로 돌아왔으면 whipsaw
            if nxt["regime"] == t["regime_prev"]:
                whipsaw += 1
                break

    return {
        "total_transitions": int(total),
        "whipsaw_count": int(whipsaw),
        "whipsaw_rate_pct": round(whipsaw / total * 100, 1),
        "window_days": window,
    }


# ── #4 (d) confidence 캘리브레이션 (reliability) ────────────────────────────

def confidence_calibration(result: pd.DataFrame, n_bins: int = 10) -> pd.DataFrame:
    """
    confidence bin별 ensemble↔rule 일치율 (rule_regime을 정답으로 가정).

    한계: rule_regime이 정답이라는 가정 자체가 자기참조. 그래도 confidence가
    높을수록 일치율도 높아져야 한다는 단조성은 유효한 점검.
    """
    df = result.copy()
    df = df[df["combined_conf"].notna()].copy()
    df["correct"] = (df["regime"] == df["rule_regime"]).astype(int)

    df["bin"] = pd.cut(df["combined_conf"], bins=n_bins, include_lowest=True)
    grouped = df.groupby("bin", observed=True).agg(
        n_days=("correct", "size"),
        accuracy_pct=("correct", lambda x: float(x.mean() * 100)),
        conf_mean=("combined_conf", "mean"),
    ).reset_index()
    grouped["bin"] = grouped["bin"].astype(str)
    return grouped


# ── #4 (e) confidence threshold 민감도 ──────────────────────────────────────

def confidence_threshold_sensitivity(result: pd.DataFrame,
                                     thresholds: list[float]) -> pd.DataFrame:
    """threshold별 (fallback 비율, fallback 후 portfolio Sharpe·MaxDD).

    fallback 정책: 라이브와 동일 — confidence < threshold면 직전 확정 레짐 유지.
    """
    rows = []
    for thr in thresholds:
        df = result.copy()
        # 시뮬레이션: confidence가 threshold 미만이면 직전 confirmed regime 유지
        sim_regime = []
        last_confirmed = None
        for _, r in df.iterrows():
            conf = r["combined_conf"] if pd.notna(r["combined_conf"]) else 0.0
            if conf >= thr or last_confirmed is None:
                last_confirmed = r["regime"]
            sim_regime.append(last_confirmed)
        df["sim_regime"] = sim_regime
        fallback_rate = (df["sim_regime"] != df["regime"]).mean()

        # 시뮬레이션 portfolio 성과는 이번 분석 범위 밖 (블렌딩 비중 재계산 필요)
        # 대신 sim_regime이 rule과 얼마나 다른지만 측정
        match_with_rule = (df["sim_regime"] == df["rule_regime"]).mean()

        rows.append({
            "threshold": thr,
            "fallback_rate_pct": round(fallback_rate * 100, 1),
            "sim_match_rule_pct": round(match_with_rule * 100, 1),
        })
    return pd.DataFrame(rows)


# ── 메인 ────────────────────────────────────────────────────────────────────

def _table(df: pd.DataFrame) -> str:
    """간단 ASCII 표 (소수 포맷 보정 후 to_string)."""
    if df is None or df.empty:
        return "  (no data)"
    return df.to_string(index=False, float_format=lambda v: f"{v:>10.3f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--end", default="2025-04-30")
    p.add_argument("--forward-window", type=int, default=21)
    p.add_argument("--out", default=None,
                   help="JSON dump 경로 (예: docs/regime_diagnostics_20260527.json)")
    args = p.parse_args()

    config_path = ROOT / "trading" / "config.yaml"
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    print(f"[1] 백테스트 실행  [{args.start} ~ {args.end}]")
    universe_px, signal_px = load_all_prices(
        config=cfg, start=args.start, end=args.end, use_cache=True
    )
    fred_history = fetch_fred_history(args.start, args.end)
    if not fred_history.empty:
        print(f"    FRED 매크로 ({len(fred_history.columns)}개) 포함")

    engine = BacktestEngine(
        config=cfg, universe_px=universe_px, signal_px=signal_px,
        start=args.start, end=args.end, rebal_freq="W-FRI", tx_cost=0.001,
        fred_history=fred_history,
    )
    result = engine.run()
    print(f"    백테스트 완료: {len(result)}일, confidence 평균 {result['combined_conf'].mean():.3f}")

    fw = args.forward_window
    dump: dict = {"period": f"{args.start} ~ {args.end}", "forward_window_days": fw}

    print(f"\n{'=' * 70}")
    print(f"  #6 (a) 레짐별 forward {fw}일 수익률 분리도")
    print(f"{'=' * 70}")
    sep = regime_forward_return_separation(result, fw)
    print(_table(sep))
    dump["forward_return_separation"] = sep.to_dict("records")

    print(f"\n{'=' * 70}")
    print(f"  #6 (b) 전환 적시성 (±{fw}일 누적 수익률, 전환 종류별 평균)")
    print(f"{'=' * 70}")
    tres = transition_timeliness(result, fw)
    if isinstance(tres, tuple):
        summary, _detail = tres
        print(_table(summary))
        dump["transition_timeliness"] = summary.to_dict("records")
    else:
        print("  (전환 없음)")
        dump["transition_timeliness"] = []

    print(f"\n{'=' * 70}")
    print(f"  #6 (c) whipsaw 빈도 (window={fw}일 내 직전 레짐 복귀)")
    print(f"{'=' * 70}")
    ws = whipsaw_frequency(result, fw)
    for k, v in ws.items():
        print(f"  {k:<22}: {v}")
    dump["whipsaw"] = ws

    print(f"\n{'=' * 70}")
    print(f"  #4 (d) confidence 캘리브레이션 (10 bins, rule_regime 정답 가정)")
    print(f"{'=' * 70}")
    cal = confidence_calibration(result, n_bins=10)
    print(_table(cal))
    # 단조성 점검
    if len(cal) >= 3:
        accs = cal["accuracy_pct"].values
        confs = cal["conf_mean"].values
        # Spearman rank correlation
        from scipy.stats import spearmanr
        rho, _pv = spearmanr(confs, accs)
        print(f"\n  단조성 (Spearman ρ, conf vs accuracy): {rho:+.3f}  "
              f"({'단조 증가 — 캘리브레이션 OK' if rho >= 0.6 else '비단조 — 산식 재검토 필요'})")
        dump["calibration_spearman_rho"] = float(rho)
    dump["calibration"] = cal.to_dict("records")

    print(f"\n{'=' * 70}")
    print(f"  #4 (e) confidence threshold 민감도")
    print(f"{'=' * 70}")
    sens = confidence_threshold_sensitivity(result, [0.10, 0.20, 0.30, 0.40, 0.50, 0.60])
    print(_table(sens))
    dump["threshold_sensitivity"] = sens.to_dict("records")

    # 메타
    dump["regime_distribution"] = {
        r: int((result["regime"] == r).sum()) for r in REGIMES
    }

    if args.out:
        out_path = ROOT / args.out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(dump, f, indent=2, default=str)
        print(f"\n  JSON 저장: {out_path}")


if __name__ == "__main__":
    main()
