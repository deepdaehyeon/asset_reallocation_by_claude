"""역사적 시장 데이터 수집 (레짐 신호용)."""
import yfinance as yf
import pandas as pd


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

    # 단일 종목이면 MultiIndex가 아니므로 통일
    if isinstance(df.columns, pd.MultiIndex):
        prices = df["Close"]
    else:
        prices = df[["Close"]]
        prices.columns = tickers

    return prices.dropna(how="all")
