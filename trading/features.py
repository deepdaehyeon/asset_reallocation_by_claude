"""가격 데이터 → 레짐 판단 피처 계산."""
from __future__ import annotations

import numpy as np
import pandas as pd

# HMM 학습에 사용하는 피처 열 (순서 고정)
HMM_FEATURE_COLS = ["momentum_1m", "momentum_3m", "realized_vol", "vix", "credit_signal"]


def compute_features(prices: pd.DataFrame, fred_data: dict | None = None) -> dict:
    """
    레짐 감지에 쓰이는 수치 피처를 계산한다.

    fred_data가 제공되면 credit_signal을 FRED HY 스프레드 기반 값으로 대체한다.
    FRED에서 추가로 제공된 키(hy_spread, curve_10y2y 등)는 결과 dict에 병합된다.

    입력:
        prices: columns에 SPY / ^VIX / TLT / HYG 포함한 종가 DataFrame
        fred_data: fetch_fred_data() 반환값 (없으면 None)

    반환:
        {momentum_1m, momentum_3m, realized_vol, vix, credit_signal, [fred extras...]}
    """
    spy = prices["SPY"].dropna()
    vix = prices["^VIX"].dropna()
    tlt = prices["TLT"].dropna()
    hyg = prices["HYG"].dropna()

    rets = spy.pct_change().dropna()

    def safe_ret(series: pd.Series, window: int) -> float:
        if len(series) <= window:
            return 0.0
        return float(series.iloc[-1] / series.iloc[-window] - 1)

    momentum_1m = safe_ret(spy, 22)
    momentum_3m = safe_ret(spy, 63)
    realized_vol = float(rets.tail(21).std() * np.sqrt(252))
    vix_level = float(vix.iloc[-1]) if len(vix) > 0 else 20.0
    credit_signal = safe_ret(hyg, 22) - safe_ret(tlt, 22)

    features = {
        "momentum_1m": momentum_1m,
        "momentum_3m": momentum_3m,
        "realized_vol": realized_vol,
        "vix": vix_level,
        "credit_signal": credit_signal,
    }

    if fred_data:
        # FRED credit_signal이 있으면 yfinance proxy를 대체
        if "credit_signal" in fred_data:
            features["credit_signal"] = fred_data["credit_signal"]
        # FRED 전용 피처는 키를 그대로 병합 (HMM 학습·출력 용도)
        for key in ("hy_spread", "curve_10y2y"):
            if key in fred_data:
                features[key] = fred_data[key]

    return features


def compute_feature_matrix(prices: pd.DataFrame) -> pd.DataFrame:
    """
    HMM 학습을 위한 일별 피처 행렬을 벡터화 연산으로 계산한다.

    Returns:
        DataFrame with columns = HMM_FEATURE_COLS, index = date
        (최소 65일 warm-up 이후 데이터만 포함)
    """
    spy = prices["SPY"]
    vix = prices["^VIX"]
    hyg = prices["HYG"]
    tlt = prices["TLT"]

    mom_1m = spy.pct_change(22, fill_method=None)
    mom_3m = spy.pct_change(63, fill_method=None)
    rvol = spy.pct_change(fill_method=None).rolling(21).std() * np.sqrt(252)
    credit = hyg.pct_change(22, fill_method=None) - tlt.pct_change(22, fill_method=None)

    matrix = pd.DataFrame(
        {
            "momentum_1m": mom_1m,
            "momentum_3m": mom_3m,
            "realized_vol": rvol,
            "vix": vix,
            "credit_signal": credit,
        }
    ).dropna()

    return matrix[HMM_FEATURE_COLS]
