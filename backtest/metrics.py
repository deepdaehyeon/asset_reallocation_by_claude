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

    return {
        "total_return": round(total_return, 4),
        "cagr":         round(cagr, 4),
        "volatility":   round(vol, 4),
        "sharpe":       round(sharpe, 3),
        "max_drawdown": round(max_dd, 4),
        "calmar":       round(calmar, 3),
        "win_rate":     round(win_rate, 3),
        "n_days":       len(returns),
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
