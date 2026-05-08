"""백테스트용 역사 데이터 다운로드 및 캐시 관리."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

import pandas as pd
import yfinance as yf

CACHE_DIR = Path(__file__).parent / ".cache"

# 한국 ETF 상장 이력이 짧아 동일 기초지수 추종 미국 ETF로 대체
# 백테스트는 USD 기준 수익률로 계산 (환율 효과 미반영)
PROXY_MAP: Dict[str, str] = {
    "379800": "SPY",   # KODEX S&P500 → SPY
    "379810": "QQQ",   # KODEX 나스닥100 → QQQ
    "305080": "IEF",   # TIGER 미국채10년 → IEF (동일 기초자산)
    "411060": "GLD",   # ACE KRX금현물 → GLD
    "469830": "BIL",   # SOL 초단기채 → BIL (1-3M T-Bill)
}

# 실제 상장 전 구간은 NaN → 해당 티커 비중을 available 자산에 비례 재배분
TICKER_INCEPTION: Dict[str, str] = {
    "DBMF": "2019-05-10",
    "PLTR": "2020-09-30",
    "DBC":  "2006-02-03",
    "GLD":  "2004-11-18",
    "BIL":  "2007-05-25",
}

SIGNAL_TICKERS = ["SPY", "^VIX", "TLT", "HYG", "DX-Y.NYB", "DJP"]
# DX-Y.NYB: 달러 인덱스(DXY)  /  DJP: Bloomberg Commodity Index ETN (2006~)


def _cache_path(ticker: str) -> Path:
    CACHE_DIR.mkdir(exist_ok=True)
    return CACHE_DIR / f"{ticker.replace('^', '_')}.parquet"


def download_prices(
    tickers: list[str],
    start: str,
    end: str,
    use_cache: bool = True,
) -> pd.DataFrame:
    """조정 종가를 다운로드하고 캐시에 저장한다."""
    frames: Dict[str, pd.Series] = {}

    for ticker in tickers:
        cache = _cache_path(ticker)
        if use_cache and cache.exists():
            cached = pd.read_parquet(cache)["Close"]
            cached.index = pd.to_datetime(cached.index).tz_localize(None)
            if (str(cached.index.min().date()) <= start
                    and str(cached.index.max().date()) >= end):
                frames[ticker] = cached[start:end]
                continue

        data = yf.download(
            ticker, start=start, end=end, auto_adjust=True, progress=False
        )
        if data.empty:
            print(f"  [경고] {ticker}: 데이터 없음")
            continue

        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.squeeze()
        close.index = pd.to_datetime(close.index).tz_localize(None)

        if use_cache:
            close.to_frame(name="Close").to_parquet(cache)

        frames[ticker] = close[start:end]

    if not frames:
        return pd.DataFrame()

    df = pd.DataFrame(frames)
    df.index = pd.to_datetime(df.index)
    return df.ffill()


def get_universe_tickers(config: dict) -> Dict[str, str]:
    """config universe에서 {원본티커: 다운로드티커} 매핑 반환."""
    return {
        ticker: PROXY_MAP.get(ticker, ticker)
        for ticker in config["universe"]
    }


def load_all_prices(
    config: dict,
    start: str,
    end: str,
    use_cache: bool = True,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    유니버스 가격 + 신호 가격을 로드한다.

    Returns:
        (universe_prices, signal_prices)
        universe_prices: 원본 티커명 컬럼 (프록시 적용 후 다운로드)
        signal_prices:   SPY / ^VIX / TLT / HYG
    """
    ticker_map = get_universe_tickers(config)
    download_set = list(set(ticker_map.values()) | set(SIGNAL_TICKERS))

    raw = download_prices(download_set, start=start, end=end, use_cache=use_cache)

    universe_cols = {
        orig: raw[proxy]
        for orig, proxy in ticker_map.items()
        if proxy in raw.columns
    }
    universe_prices = pd.DataFrame(universe_cols).dropna(how="all")

    signal_prices = raw[[t for t in SIGNAL_TICKERS if t in raw.columns]]

    return universe_prices, signal_prices
