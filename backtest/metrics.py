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
    for regime in ["Risk-On", "Neutral", "Risk-Off", "High-Vol"]:
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
