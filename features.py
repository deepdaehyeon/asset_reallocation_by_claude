"""가격 데이터 → 레짐 판단 피처 계산."""
import numpy as np
import pandas as pd


def compute_features(prices: pd.DataFrame) -> dict:
    """
    레짐 감지에 쓰이는 수치 피처를 계산한다.

    입력:
        prices: columns에 SPY / ^VIX / TLT / HYG 포함한 종가 DataFrame

    반환:
        {
            momentum_1m: float,   SPY 1개월 수익률
            momentum_3m: float,   SPY 3개월 수익률
            realized_vol: float,  SPY 21일 실현변동성 (연환산)
            vix: float,           VIX 최신값
            credit_signal: float, HYG - TLT 1개월 수익률 차 (양수 = 위험선호)
        }
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

    # HYG가 TLT보다 강하면 신용 환경 우호적 (Risk-On 신호)
    credit_signal = safe_ret(hyg, 22) - safe_ret(tlt, 22)

    return {
        "momentum_1m": momentum_1m,
        "momentum_3m": momentum_3m,
        "realized_vol": realized_vol,
        "vix": vix_level,
        "credit_signal": credit_signal,
    }
