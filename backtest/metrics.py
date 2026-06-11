"""백테스트 성과 지표 계산."""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


def compute_metrics(
    returns: pd.Series,
    risk_free: float = 0.04,
) -> Dict[str, float]:
    """
    일별 수익률에서 핵심 성과 지표를 계산한다.
    risk_free: 연환산 무위험 수익률 (기본 4%)
    """
    returns = returns.dropna()
    if len(returns) < 10 or returns.std() == 0:
        return {}

    ann = 252
    n_years = len(returns) / ann
    total_return = float((1 + returns).prod() - 1)
    cagr = float((1 + total_return) ** (1 / n_years) - 1) if n_years > 0 else 0.0

    vol = float(returns.std() * np.sqrt(ann))
    sharpe = (cagr - risk_free) / vol if vol > 0 else 0.0

    cum = (1 + returns).cumprod()
    rolling_max = cum.cummax()
    dd_series = (cum - rolling_max) / rolling_max
    max_dd = float(dd_series.min())

    calmar = cagr / abs(max_dd) if max_dd < 0 else 0.0
    win_rate = float((returns > 0).mean())

    # Ulcer Index: 낙폭의 깊이+지속을 함께 반영(RMS of drawdown%). MaxDD가 한 순간만
    # 보는 것과 달리, 오래·깊게 물려있을수록 커진다 → 장기보유자 체감 고통에 가깝다.
    ui = float(np.sqrt((dd_series.mul(100) ** 2).mean()))
    # Martin ratio(=Ulcer Performance Index): Calmar의 Ulcer 버전. 초과수익/Ulcer.
    martin = (cagr - risk_free) / (ui / 100) if ui > 0 else 0.0

    return {
        "total_return": round(total_return, 4),
        "cagr":         round(cagr, 4),
        "volatility":   round(vol, 4),
        "sharpe":       round(sharpe, 3),
        "max_drawdown": round(max_dd, 4),
        "calmar":       round(calmar, 3),
        "ulcer":        round(ui, 3),
        "martin":       round(martin, 3),
        "win_rate":     round(win_rate, 3),
        "n_days":       len(returns),
    }


def ulcer_index(returns: pd.Series) -> float:
    """Ulcer Index: sqrt(mean(drawdown%^2)). 낙폭 깊이+지속 동시 반영, 낮을수록 좋음."""
    returns = returns.dropna()
    if len(returns) < 2:
        return 0.0
    dd = drawdown_series(returns).mul(100)
    return float(np.sqrt((dd ** 2).mean()))


def recovery_duration(returns: pd.Series) -> Dict[str, float]:
    """
    낙폭에서 회복까지 걸린 기간(달력일) 분석 — 장기보유자가 '물려있는 시간'.

    Returns
    -------
    dict:
      max_underwater_days  : 가장 길게 직전 고점 아래 머문 기간(달력일)
      maxdd_recovery_days  : 최대 낙폭 저점에서 직전 고점 회복까지 걸린 기간
                             (기간 내 미회복이면 -1 = 아직 물려있음)
      currently_underwater_days : series 끝 시점에 직전 고점 아래 머문 기간(0이면 신고가)
    """
    returns = returns.dropna()
    if len(returns) < 2:
        return {"max_underwater_days": 0, "maxdd_recovery_days": -1,
                "currently_underwater_days": 0}

    cum = (1 + returns).cumprod()
    idx = cum.index
    peak_val = cum.iloc[0]
    peak_date = idx[0]
    max_uw = 0
    for date, v in cum.items():
        if v >= peak_val:
            uw = (date - peak_date).days
            if uw > max_uw:
                max_uw = uw
            peak_val = v
            peak_date = date
    trailing_uw = (idx[-1] - peak_date).days
    if trailing_uw > max_uw:
        max_uw = trailing_uw

    # 최대 낙폭 저점 → 회복까지
    dd = drawdown_series(returns)
    trough_date = dd.idxmin()
    peak_before = cum.loc[:trough_date].idxmax()
    peak_level = cum.loc[peak_before]
    after = cum.loc[trough_date:]
    recovered = after[after >= peak_level]
    if len(recovered) > 0:
        maxdd_rec = (recovered.index[0] - trough_date).days
    else:
        maxdd_rec = -1  # 기간 내 미회복

    return {
        "max_underwater_days": int(max_uw),
        "maxdd_recovery_days": int(maxdd_rec),
        "currently_underwater_days": int(trailing_uw),
    }


def rolling_cagr(returns: pd.Series, years: float = 3.0) -> Dict[str, float]:
    """
    롤링 CAGR 분포 — 진입시점에 따라 보유기간(years) 동안 받았을 연환산 수익률.
    장기보유자의 실제 경험 분포(최악/중앙/최선 + 마이너스 마감 비율).
    """
    returns = returns.dropna()
    window = int(round(years * 252))
    if len(returns) <= window:
        return {"years": years, "worst": 0.0, "median": 0.0, "best": 0.0,
                "pct_negative": 0.0, "n_windows": 0}

    cum = (1 + returns).cumprod()
    ratio = (cum / cum.shift(window)).dropna()
    cagr = ratio ** (1.0 / years) - 1.0
    return {
        "years":        years,
        "worst":        round(float(cagr.min()), 4),
        "median":       round(float(cagr.median()), 4),
        "best":         round(float(cagr.max()), 4),
        "pct_negative": round(float((cagr < 0).mean()), 4),
        "n_windows":    int(len(cagr)),
    }


def regime_breakdown(
    returns: pd.Series,
    regimes: pd.Series,
) -> pd.DataFrame:
    """레짐별 성과 분해."""
    rows = []
    for regime in ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis"]:
        mask = regimes == regime
        r = returns[mask]
        if len(r) < 10:
            continue
        m = compute_metrics(r)
        m["regime"] = regime
        m["days"] = len(r)
        m["pct_time"] = round(float(mask.mean()), 3)
        rows.append(m)
    return pd.DataFrame(rows).set_index("regime") if rows else pd.DataFrame()


def drawdown_series(returns: pd.Series) -> pd.Series:
    """일별 낙폭 시리즈 반환 (0 이하)."""
    cum = (1 + returns).cumprod()
    return (cum - cum.cummax()) / cum.cummax()


def regime_classification_metrics(
    rule_regimes: pd.Series,
    ensemble_regimes: pd.Series,
    returns: pd.Series,
) -> dict:
    """
    레짐 분류 품질 지표.

    rule_regimes    : 규칙 기반 레짐 (pseudo ground truth)
    ensemble_regimes: HMM 앙상블 레짐 (predicted)
    returns         : 일별 포트폴리오 수익률

    Returns
    -------
    dict with keys:
      mcc               : Matthews Correlation Coefficient [-1, 1]
      macro_f1          : 클래스별 F1 단순 평균 (불균형 무관)
      balanced_accuracy : 클래스별 accuracy 단순 평균
      per_class         : 레짐별 precision / recall / f1 / support
      override_rate     : HMM이 규칙 기반을 덮어쓴 비율
      miss_cost         : 위험 레짐을 Goldilocks로 오판했을 때 평균 일별 수익률
    """
    try:
        from sklearn.metrics import (
            matthews_corrcoef,
            f1_score,
            precision_recall_fscore_support,
            balanced_accuracy_score,
        )
    except ImportError:
        return {"error": "scikit-learn 미설치"}

    labels = ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis"]

    aligned = pd.DataFrame({
        "rule":     rule_regimes,
        "ensemble": ensemble_regimes,
        "returns":  returns,
    }).dropna()

    if len(aligned) < 10:
        return {}

    y_true = aligned["rule"].values
    y_pred = aligned["ensemble"].values

    mcc = float(matthews_corrcoef(y_true, y_pred))
    macro_f1 = float(f1_score(y_true, y_pred, average="macro", labels=labels, zero_division=0))
    bal_acc = float(balanced_accuracy_score(y_true, y_pred))

    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, zero_division=0
    )
    per_class = {
        label: {
            "precision": round(float(precision[i]), 3),
            "recall":    round(float(recall[i]), 3),
            "f1":        round(float(f1[i]), 3),
            "support":   int(support[i]),
        }
        for i, label in enumerate(labels)
    }

    # HMM이 규칙 기반과 다른 날 비율
    override_mask = aligned["rule"] != aligned["ensemble"]
    override_rate = float(override_mask.mean())

    # 위험 레짐(Crisis / Stagflation)을 Goldilocks로 오판한 날의 포트폴리오 수익
    rare = {"Crisis", "Stagflation"}
    miss_mask = aligned["rule"].isin(rare) & (aligned["ensemble"] == "Goldilocks")
    miss_returns = aligned.loc[miss_mask, "returns"]
    miss_cost = {
        "avg_daily_return": round(float(miss_returns.mean()), 5) if len(miss_returns) > 0 else 0.0,
        "miss_days":        int(miss_mask.sum()),
        "total_days":       int(aligned["rule"].isin(rare).sum()),
    }

    return {
        "mcc":               round(mcc, 3),
        "macro_f1":          round(macro_f1, 3),
        "balanced_accuracy": round(bal_acc, 3),
        "override_rate":     round(override_rate, 3),
        "per_class":         per_class,
        "miss_cost":         miss_cost,
    }


def crisis_analysis(
    returns: pd.Series,
    periods: Optional[Dict[str, tuple]] = None,
) -> pd.DataFrame:
    """주요 위기 구간별 성과 분석."""
    if periods is None:
        periods = {
            "GFC (2008-2009)":    ("2008-01-01", "2009-03-31"),
            "COVID Crash (2020)": ("2020-02-19", "2020-04-30"),
            "Bear 2022":          ("2022-01-01", "2022-12-31"),
            "SVB (2023 Q1)":      ("2023-03-01", "2023-05-31"),
        }

    rows = []
    for name, (start, end) in periods.items():
        slc = returns[start:end]
        if len(slc) < 5:
            continue
        m = compute_metrics(slc)
        if not m:
            continue
        m["period"] = name
        rows.append(m)

    return pd.DataFrame(rows).set_index("period") if rows else pd.DataFrame()
