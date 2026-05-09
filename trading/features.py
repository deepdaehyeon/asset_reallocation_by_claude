"""가격 데이터 → 레짐 판단 피처 계산."""
from __future__ import annotations

import numpy as np
import pandas as pd

# ── 피처 컬럼 정의 ────────────────────────────────────────────────────────────
# HMM/RF 학습에 사용할 가격 파생 피처 (signal_px만으로 계산 가능)
PRICE_FEATURE_COLS: list[str] = [
    "momentum_1m",      # SPY 1개월 모멘텀
    "momentum_3m",      # SPY 3개월 모멘텀
    "realized_vol",     # SPY 21일 실현변동성 (연환산)
    "vix",              # VIX 수준
    "credit_signal",    # HYG-TLT 스프레드 모멘텀 (신용 프록시)
    "dxy_mom_1m",       # 달러 인덱스 1M 모멘텀
    "commodity_mom_1m", # 원자재(DJP) 1M 모멘텀
]

# FRED API로만 계산 가능한 매크로 피처
MACRO_FEATURE_COLS: list[str] = [
    "cpi_yoy",          # CPI 전년비 (%)
    "cpi_mom_zscore",   # CPI MoM 3년 Z-score
    "unrate_chg_3m",    # 실업률 3M 변화
    "breakeven_5y",     # 5년 기대인플레이션 (BEI)
    "m2_yoy",           # M2 공급 전년비 (%)
    "fed_bs_yoy",       # Fed 자산규모 전년비 (QE/QT 신호)
    "hy_spread_zscore", # HY 스프레드 3년 Z-score
    "curve_10y2y",      # 장단기 금리차
]

# 하위 호환 alias — 이전 코드에서 HMM_FEATURE_COLS를 직접 참조하는 경우
HMM_FEATURE_COLS: list[str] = PRICE_FEATURE_COLS


def get_active_feature_cols(feature_matrix: pd.DataFrame) -> list[str]:
    """feature_matrix에 실제 존재하는 피처 열만 순서 보존하여 반환."""
    desired = PRICE_FEATURE_COLS + MACRO_FEATURE_COLS
    return [c for c in desired if c in feature_matrix.columns]


# ── 내부 헬퍼 ────────────────────────────────────────────────────────────────

def _zscore(s: pd.Series, window: int = 756) -> pd.Series:
    """rolling Z-score. NaN이 전체의 50% 초과하면 0 반환."""
    mean = s.rolling(window, min_periods=window // 2).mean()
    std  = s.rolling(window, min_periods=window // 2).std()
    z = (s - mean) / std.replace(0, np.nan)
    return z.fillna(0.0)


def _safe_mom(series: pd.Series, window: int) -> float:
    """window일 전 대비 수익률. 데이터 부족 시 0 반환."""
    if len(series) <= window:
        return 0.0
    return float(series.iloc[-1] / series.iloc[-window] - 1)


# ── 단일 시점 피처 계산 (live trading) ───────────────────────────────────────

def compute_features(prices: pd.DataFrame, fred_data: dict | None = None) -> dict:
    """
    레짐 감지에 쓰이는 수치 피처를 계산한다.

    prices    : columns에 SPY / ^VIX / TLT / HYG / [DX-Y.NYB] / [DJP] 포함한 종가 DataFrame
    fred_data : fetch_fred_data() 반환값 (없으면 None)

    반환: PRICE_FEATURE_COLS + (fred_data 제공 시) MACRO_FEATURE_COLS 일부
    """
    spy = prices["SPY"].dropna()
    vix = prices["^VIX"].dropna()
    tlt = prices["TLT"].dropna()
    hyg = prices["HYG"].dropna()

    rets = spy.pct_change().dropna()

    momentum_1m = _safe_mom(spy, 22)
    momentum_3m = _safe_mom(spy, 63)
    realized_vol = float(rets.tail(21).std() * np.sqrt(252))
    vix_level   = float(vix.iloc[-1]) if len(vix) > 0 else 20.0
    credit_signal = _safe_mom(hyg, 22) - _safe_mom(tlt, 22)

    features: dict = {
        "momentum_1m":  momentum_1m,
        "momentum_3m":  momentum_3m,
        "realized_vol": realized_vol,
        "vix":          vix_level,
        "credit_signal": credit_signal,
    }

    # 달러 인덱스 모멘텀
    if "DX-Y.NYB" in prices.columns:
        dxy = prices["DX-Y.NYB"].dropna()
        features["dxy_mom_1m"] = _safe_mom(dxy, 22)

    # 원자재 모멘텀
    if "DJP" in prices.columns:
        djp = prices["DJP"].dropna()
        features["commodity_mom_1m"] = _safe_mom(djp, 22)

    if fred_data:
        # FRED credit_signal 우선 사용
        if "credit_signal" in fred_data:
            features["credit_signal"] = fred_data["credit_signal"]
        # FRED 피처 병합
        for key in (
            "hy_spread", "hy_spread_zscore", "curve_10y2y",
            "cpi_yoy", "cpi_mom_zscore",
            "unrate_chg_3m",
            "breakeven_5y",
            "m2_yoy", "fed_bs_yoy",
        ):
            if key in fred_data:
                features[key] = fred_data[key]

    return features


# ── 히스토리 피처 행렬 계산 (backtest / HMM 학습) ────────────────────────────

def compute_rolling_correlation(prices: pd.DataFrame, window: int = 60) -> float:
    """
    주요 자산 간 평균 롤링 상관계수를 계산한다.

    window: 롤링 기간 (영업일). 0.8 초과 시 포지션 축소 경고 기준.
    반환: 자산 쌍 평균 상관계수 (데이터 부족 시 0.0)
    """
    candidates = [c for c in ["SPY", "TLT", "HYG", "GLD", "DJP"] if c in prices.columns]
    if len(candidates) < 2:
        return 0.0
    rets = prices[candidates].pct_change(fill_method=None).dropna()
    if len(rets) < window:
        return 0.0
    corr = rets.tail(window).corr()
    n = len(candidates)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    if not pairs:
        return 0.0
    return float(sum(corr.iloc[i, j] for i, j in pairs) / len(pairs))


def compute_feature_matrix(
    prices: pd.DataFrame,
    fred_history: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    HMM/RF 학습을 위한 일별 피처 행렬을 계산한다.

    prices       : SPY / ^VIX / TLT / HYG / [DX-Y.NYB] / [DJP] 종가 DataFrame
    fred_history : fetch_fred_history() 반환 DataFrame (없으면 가격 파생 피처만 사용)

    Returns:
        DataFrame with columns ⊆ PRICE_FEATURE_COLS + MACRO_FEATURE_COLS, index = date
        (최소 65일 warm-up 이후 데이터만 포함)
    """
    spy = prices["SPY"]
    vix = prices["^VIX"]
    hyg = prices["HYG"]
    tlt = prices["TLT"]

    mom_1m  = spy.pct_change(22, fill_method=None)
    mom_3m  = spy.pct_change(63, fill_method=None)
    rvol    = spy.pct_change(fill_method=None).rolling(21).std() * np.sqrt(252)
    credit  = hyg.pct_change(22, fill_method=None) - tlt.pct_change(22, fill_method=None)

    data: dict[str, pd.Series] = {
        "momentum_1m":  mom_1m,
        "momentum_3m":  mom_3m,
        "realized_vol": rvol,
        "vix":          vix,
        "credit_signal": credit,
    }

    # 달러 인덱스 1M 모멘텀
    if "DX-Y.NYB" in prices.columns:
        dxy = prices["DX-Y.NYB"]
        data["dxy_mom_1m"] = dxy.pct_change(22, fill_method=None)

    # 원자재 1M 모멘텀
    if "DJP" in prices.columns:
        djp = prices["DJP"]
        data["commodity_mom_1m"] = djp.pct_change(22, fill_method=None)

    matrix = pd.DataFrame(data).dropna()

    # FRED 히스토리 있으면 매크로 피처 합류
    if fred_history is not None and not fred_history.empty:
        fred_aligned = (
            fred_history
            .reindex(matrix.index, method="ffill")   # 월/주간 → 일별 forward-fill
            .ffill(limit=45)                          # 최대 45 영업일(~2개월) 스탈 허용
        )
        for col in fred_history.columns:
            if col in fred_aligned.columns:
                matrix[col] = fred_aligned[col]

        # NaN이 과반인 FRED 열은 제거 (데이터 시작 전 구간)
        threshold = 0.5
        matrix = matrix.loc[:, matrix.isna().mean() < threshold]
        matrix = matrix.dropna()

    return matrix
