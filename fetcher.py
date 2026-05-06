"""역사적 시장 데이터 수집 (레짐 신호용)."""
from __future__ import annotations

import os

import pandas as pd
import yfinance as yf


def fetch_signal_prices(tickers: list[str], lookback_days: int = 130) -> pd.DataFrame:
    """
    레짐 감지에 필요한 가격 히스토리를 yfinance로 수집한다.

    Returns:
        종목별 조정 종가 DataFrame (columns = tickers)
    """
    df = yf.download(
        tickers,
        period=f"{lookback_days}d",
        interval="1d",
        auto_adjust=True,
        progress=False,
    )

    if isinstance(df.columns, pd.MultiIndex):
        prices = df["Close"]
    else:
        prices = df[["Close"]]
        prices.columns = tickers

    return prices.dropna(how="all")


def fetch_usd_krw(fallback: float = 1380.0) -> float:
    """
    yfinance로 실시간 USD/KRW 환율을 조회한다.

    조회 실패 시 fallback 값을 반환한다.
    """
    try:
        hist = yf.Ticker("KRW=X").history(period="5d")
        if not hist.empty:
            rate = float(hist["Close"].iloc[-1])
            if 900 < rate < 2000:   # 비정상값 필터
                return rate
    except Exception:
        pass
    return fallback


def fetch_fred_data() -> dict:
    """
    FRED API로 HY 스프레드와 10Y-2Y 금리 커브를 조회한다.

    환경변수 FRED_API_KEY가 없거나 fredapi 미설치 시 빈 dict 반환.
    반환 키:
        hy_spread      float  ICE BofA US HY OAS (%)
        curve_10y2y    float  10년-2년 국채 금리 차 (%)
        credit_signal  float  HY 스프레드 1M 변화의 역수 (rule-based 스케일 환산)
    """
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        return {}

    try:
        from fredapi import Fred
    except ImportError:
        return {}

    try:
        fred = Fred(api_key=api_key)

        # ICE BofA US High Yield OAS (%)
        hy = fred.get_series("BAMLH0A0HYM2").dropna()
        # 10Y - 2Y Treasury spread (%)
        curve = fred.get_series("T10Y2Y").dropna()

        result: dict = {}

        if len(hy) > 0:
            result["hy_spread"] = float(hy.iloc[-1])

        if len(hy) >= 22:
            # HY 스프레드 1M 변화 → credit_signal 스케일로 환산
            # 스프레드 상승 = 위험회피 = 음수 / 하락 = 위험선호 = 양수
            spread_chg = float(hy.iloc[-1] - hy.iloc[-22])
            result["credit_signal"] = -spread_chg / 20.0  # 약 ±0.03 스케일

        if len(curve) > 0:
            result["curve_10y2y"] = float(curve.iloc[-1])

        return result

    except Exception as e:
        print(f"    [FRED] 조회 실패 ({type(e).__name__}): {e}")
        return {}
