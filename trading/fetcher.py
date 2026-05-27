"""역사적 시장 데이터 수집 (레짐 신호용)."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf


def _load_env_from_file() -> None:
    """프로젝트 루트의 .env를 한 번 파싱해 os.environ에 주입 (이미 있는 키는 보존)."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip("'\"")
            if k and v and os.environ.get(k) is None:
                os.environ[k] = v
    except OSError:
        pass


_load_env_from_file()


def fetch_signal_prices(tickers: list[str], lookback_days: int = 130) -> pd.DataFrame:
    """
    레짐 감지에 필요한 가격 히스토리를 yfinance로 수집한다.

    일부 ticker가 누락·부족해도 가용한 컬럼만으로 DataFrame 반환 (경고 출력).
    compute_features에서 필수 ticker(SPY)만 검증하고 나머지는 fallback 처리.

    Returns:
        종목별 조정 종가 DataFrame (columns ⊆ tickers)

    Raises:
        RuntimeError: yfinance API 호출 실패 또는 빈 응답
    """
    try:
        df = yf.download(
            tickers,
            period=f"{lookback_days}d",
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
    except Exception as e:
        raise RuntimeError(
            f"yfinance 일괄 다운로드 실패: {type(e).__name__}: {e}"
        ) from e

    if df is None or df.empty:
        raise RuntimeError(f"yfinance 빈 응답 (요청 ticker: {tickers})")

    if isinstance(df.columns, pd.MultiIndex):
        prices = df["Close"].copy()
    else:
        # 단일 ticker일 때 yfinance는 OHLCV 단순 컬럼 반환
        prices = df[["Close"]].copy()
        if len(tickers) == 1:
            prices.columns = tickers

    prices = prices.dropna(how="all")

    # 누락·데이터 부족 ticker 명시적 경고
    min_required = max(int(lookback_days * 0.5), 30)
    issues: list[str] = []
    for t in tickers:
        if t not in prices.columns:
            issues.append(f"{t}:컬럼없음")
            continue
        n = prices[t].dropna().shape[0]
        if n < min_required:
            issues.append(f"{t}:{n}/{min_required}일")
    if issues:
        print(f"    [yfinance 품질 경고] {' | '.join(issues)}")

    return prices


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


# ── FRED 조회 유틸 ────────────────────────────────────────────────────────────

def _get_fred_client():
    """FRED 클라이언트 반환. API 키 없거나 fredapi 미설치 시 None."""
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        return None
    try:
        from fredapi import Fred
        return Fred(api_key=api_key)
    except ImportError:
        return None


def _zscore_series(s: pd.Series, window: int = 756) -> pd.Series:
    mean = s.rolling(window, min_periods=window // 2).mean()
    std  = s.rolling(window, min_periods=window // 2).std()
    return ((s - mean) / std.replace(0, np.nan)).fillna(0.0)


def fetch_fred_data() -> dict:
    """
    FRED API로 현재 시점의 매크로 피처를 조회한다.

    환경변수 FRED_API_KEY가 없거나 fredapi 미설치 시 빈 dict 반환.

    반환 키 (credit_signal은 더 이상 반환하지 않음 — 가격기반과 스케일이 달라 임계값 불일치):
        hy_spread          float  ICE BofA US HY OAS (%)
        hy_spread_zscore   float  HY 스프레드 3년 Z-score
        curve_10y2y        float  10년-2년 국채 금리 차 (%)
        cpi_yoy            float  CPI 전년비 (%)
        cpi_mom_zscore     float  CPI MoM 3년 Z-score
        unrate_chg_3m      float  실업률 3개월 변화
        breakeven_5y       float  5년 기대인플레이션 (BEI, %)
        m2_yoy             float  M2 공급 전년비 (%)
        fed_bs_yoy         float  Fed 자산규모 전년비 (%)
    """
    fred = _get_fred_client()
    if fred is None:
        return {}

    result: dict = {}

    try:
        # ── 신용 / 금리 (일별) ─────────────────────────────────────────────
        hy = fred.get_series("BAMLH0A0HYM2").dropna()
        curve = fred.get_series("T10Y2Y").dropna()

        if len(hy) > 0:
            result["hy_spread"] = float(hy.iloc[-1])
        if len(hy) >= 63:
            result["hy_spread_zscore"] = float(_zscore_series(hy).iloc[-1])
        if len(curve) > 0:
            result["curve_10y2y"] = float(curve.iloc[-1])
        # credit_signal은 fetch_fred_data에서 제외 — compute_features의 가격기반 신호 사용
        # (가격기반 HYG-TLT 모멘텀은 ±0.1 범위, FRED 기반은 ±0.25 범위로 임계값 0.01과 스케일 불일치)

        # ── 기대 인플레이션 (일별) ─────────────────────────────────────────
        try:
            bei = fred.get_series("T5YIE").dropna()
            if len(bei) > 0:
                result["breakeven_5y"] = float(bei.iloc[-1])
        except Exception:
            pass

        # ── CPI (월별) ────────────────────────────────────────────────────
        try:
            cpi = fred.get_series("CPIAUCSL").dropna()
            if len(cpi) >= 13:
                yoy = (cpi / cpi.shift(12) - 1) * 100
                result["cpi_yoy"] = float(yoy.dropna().iloc[-1])
            if len(cpi) >= 18:   # 36개월 윈도우의 min_periods=18
                mom = cpi.pct_change()
                # 월별 시리즈는 36개월 윈도우 사용 (영업일 756은 월별 데이터엔 부적합)
                result["cpi_mom_zscore"] = float(
                    _zscore_series(mom, window=36).dropna().iloc[-1]
                )
        except Exception:
            pass

        # ── 실업률 (월별) ─────────────────────────────────────────────────
        try:
            unrate = fred.get_series("UNRATE").dropna()
            if len(unrate) >= 4:
                result["unrate_chg_3m"] = float(unrate.iloc[-1] - unrate.iloc[-4])
        except Exception:
            pass

        # ── M2 공급 (월별) ────────────────────────────────────────────────
        try:
            m2 = fred.get_series("M2SL").dropna()
            if len(m2) >= 13:
                m2_yoy = (m2 / m2.shift(12) - 1) * 100
                result["m2_yoy"] = float(m2_yoy.dropna().iloc[-1])
        except Exception:
            pass

        # ── Fed 자산규모 (주별) ───────────────────────────────────────────
        try:
            bs = fred.get_series("WALCL").dropna()
            if len(bs) >= 53:
                bs_yoy = (bs / bs.shift(52) - 1) * 100
                result["fed_bs_yoy"] = float(bs_yoy.dropna().iloc[-1])
        except Exception:
            pass

    except Exception as e:
        print(f"    [FRED] 조회 실패 ({type(e).__name__}): {e}")

    return result


def fetch_fred_history(start: str, end: str) -> pd.DataFrame:
    """
    백테스트용 FRED 매크로 피처 히스토리를 반환한다.

    start/end 보다 3년 앞서 다운로드해 Z-score 계산 warm-up을 확보하고,
    최종 결과는 [start, end] 구간만 반환한다.

    환경변수 FRED_API_KEY가 없으면 빈 DataFrame 반환.

    반환 컬럼 (일별 인덱스, 월별·주별 시리즈는 forward-fill 적용):
        cpi_yoy, cpi_mom_zscore, unrate_chg_3m, breakeven_5y,
        m2_yoy, fed_bs_yoy, hy_spread, hy_spread_zscore, curve_10y2y
    """
    fred = _get_fred_client()
    if fred is None:
        return pd.DataFrame()

    # Z-score warm-up용 3년 추가 이력
    fetch_start = str(int(start[:4]) - 3) + start[4:]

    series_map = {
        "CPIAUCSL":       "cpi_raw",
        "UNRATE":         "unrate_raw",
        "T5YIE":          "breakeven_5y",
        "M2SL":           "m2_raw",
        "WALCL":          "fed_bs_raw",
        "BAMLH0A0HYM2":   "hy_raw",
        "T10Y2Y":         "curve_10y2y",
    }

    raw: dict[str, pd.Series] = {}
    for code, alias in series_map.items():
        try:
            s = fred.get_series(code, observation_start=fetch_start, observation_end=end)
            s = s.dropna()
            # 빈 시리즈/datetime 아닌 index는 스킵 (ICE 라이선스 회수 등으로 빈 응답이 올 수 있음)
            if len(s) == 0 or not pd.api.types.is_datetime64_any_dtype(s.index):
                print(f"    [FRED history] {code}: 빈 응답 — 스킵")
                continue
            raw[alias] = s
        except Exception as e:
            print(f"    [FRED history] {code} 조회 실패: {e}")

    if not raw:
        return pd.DataFrame()

    # 일별 인덱스 생성 (거래일 기준)
    idx = pd.date_range(start=fetch_start, end=end, freq="B")
    result = pd.DataFrame(index=idx)

    # ── 변환 계산 ──────────────────────────────────────────────────────────
    # 원칙: 시리즈는 native 빈도(월/주/일)에서 변환·z-score 계산 후 일별로 reindex.
    # daily ffill된 시리즈에 shift(252)·pct_change()를 적용하면 의미 왜곡.

    # CPI (monthly)
    if "cpi_raw" in raw:
        cpi_m = raw["cpi_raw"]  # 월별 원본
        yoy_m = (cpi_m / cpi_m.shift(12) - 1) * 100  # 12개월 YoY
        mom_z_m = _zscore_series(cpi_m.pct_change(), window=36)  # 36개월 z-score
        result["cpi_yoy"] = yoy_m.reindex(idx, method="ffill", limit=45)
        result["cpi_mom_zscore"] = mom_z_m.reindex(idx, method="ffill", limit=45)

    # 실업률 (monthly)
    if "unrate_raw" in raw:
        ur_m = raw["unrate_raw"]
        chg_m = ur_m - ur_m.shift(3)   # 3개월 변화
        result["unrate_chg_3m"] = chg_m.reindex(idx, method="ffill", limit=45)

    # Breakeven (daily)
    if "breakeven_5y" in raw:
        result["breakeven_5y"] = raw["breakeven_5y"].reindex(idx, method="ffill", limit=5)

    # M2 (monthly)
    if "m2_raw" in raw:
        m2_m = raw["m2_raw"]
        yoy_m = (m2_m / m2_m.shift(12) - 1) * 100  # 12개월 YoY
        result["m2_yoy"] = yoy_m.reindex(idx, method="ffill", limit=45)

    # Fed 자산규모 (weekly)
    if "fed_bs_raw" in raw:
        bs_w = raw["fed_bs_raw"]
        yoy_w = (bs_w / bs_w.shift(52) - 1) * 100   # 52주 YoY
        result["fed_bs_yoy"] = yoy_w.reindex(idx, method="ffill", limit=10)

    # HY 스프레드 (daily, z-score는 영업일 756)
    if "hy_raw" in raw:
        hy_d = raw["hy_raw"]
        result["hy_spread"] = hy_d.reindex(idx, method="ffill", limit=3)
        result["hy_spread_zscore"] = _zscore_series(hy_d).reindex(idx, method="ffill", limit=3)

    # 장단기 금리차 (daily)
    if "curve_10y2y" in raw:
        result["curve_10y2y"] = (
            raw["curve_10y2y"].reindex(idx, method="ffill", limit=3)
        )

    # warm-up 구간 제거 → start 이후만 반환
    result = result.loc[start:end]

    # NaN이 과반인 열 제거
    result = result.loc[:, result.isna().mean() < 0.5]

    return result
