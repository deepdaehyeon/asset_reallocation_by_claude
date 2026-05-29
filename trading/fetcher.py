"""역사적 시장 데이터 수집 (레짐 신호용)."""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

# FRED 호출 안정화 파라미터
FRED_TIMEOUT_S = 10.0          # 단일 series 호출 timeout
FRED_RETRIES = 1               # 재시도 횟수 (총 시도 = 1 + retries)
FRED_RETRY_BACKOFF_S = 2.0     # 재시도 전 대기
FRED_CACHE_STALE_MAX_H = 24.0  # 캐시 사용 허용 시간 (시간)

_CACHE_DIR = Path(__file__).resolve().parent / ".cache"
_FRED_CACHE_FILE = _CACHE_DIR / "fred_last.json"


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


def _fred_get_series(fred: Any, code: str, **kwargs: Any) -> pd.Series:
    """fred.get_series 호출에 timeout과 재시도를 적용한다.

    fredapi는 자체 timeout이 없어 응답 stall 시 수십 초 매달림 — ThreadPool로 강제 컷.
    실패 시 RuntimeError를 raise하므로 호출 측에서 try/except로 잡는다.
    """
    last_exc: BaseException | None = None
    for attempt in range(1 + FRED_RETRIES):
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            future = ex.submit(fred.get_series, code, **kwargs)
            return future.result(timeout=FRED_TIMEOUT_S)
        except FuturesTimeoutError as e:
            last_exc = TimeoutError(f"{FRED_TIMEOUT_S:.0f}s timeout")
        except Exception as e:
            last_exc = e
        finally:
            # timeout 시 백그라운드 스레드가 계속 돌 수 있음 — 기다리지 않고 즉시 반환
            ex.shutdown(wait=False)
        if attempt < FRED_RETRIES:
            time.sleep(FRED_RETRY_BACKOFF_S)
    raise RuntimeError(f"{type(last_exc).__name__}: {last_exc}") from last_exc


def _save_fred_cache(data: dict) -> None:
    """성공한 FRED 결과를 캐시 파일에 저장 (다음 실패 시 fallback용)."""
    if not data:
        return
    try:
        _CACHE_DIR.mkdir(exist_ok=True)
        payload = {"timestamp": datetime.now().isoformat(), "data": data}
        _FRED_CACHE_FILE.write_text(json.dumps(payload))
    except OSError:
        pass


def _load_fred_cache_if_fresh() -> dict:
    """캐시가 STALE_MAX_H 이내면 반환, 아니면 빈 dict.

    사용 시 호출자가 stale 사용 사실을 로그로 표시한다.
    """
    if not _FRED_CACHE_FILE.exists():
        return {}
    try:
        payload = json.loads(_FRED_CACHE_FILE.read_text())
        ts = datetime.fromisoformat(payload["timestamp"])
        age_h = (datetime.now() - ts).total_seconds() / 3600
        if age_h <= FRED_CACHE_STALE_MAX_H:
            print(f"    [FRED] stale cache 사용 ({age_h:.1f}h 전 저장)")
            return dict(payload.get("data", {}))
        print(f"    [FRED] cache 만료 ({age_h:.1f}h > {FRED_CACHE_STALE_MAX_H:.0f}h)")
    except (OSError, ValueError, KeyError):
        pass
    return {}


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
        # API 키 없어도 캐시는 사용 가능 — 매크로 피처 fallback
        return _load_fred_cache_if_fresh()

    result: dict = {}
    failures: list[str] = []

    # ── 신용 / 금리 (일별) ─────────────────────────────────────────────────
    # ICE BAMLH0A0HYM2(HY OAS) 라이선스 회수 → BAA10Y로 대체 (의미는 credit spread proxy)
    try:
        hy = _fred_get_series(fred, "BAA10Y").dropna()
        if len(hy) > 0:
            result["hy_spread"] = float(hy.iloc[-1])
        if len(hy) >= 63:
            result["hy_spread_zscore"] = float(_zscore_series(hy).iloc[-1])
    except Exception as e:
        failures.append(f"BAA10Y({type(e).__name__})")

    try:
        curve = _fred_get_series(fred, "T10Y2Y").dropna()
        if len(curve) > 0:
            result["curve_10y2y"] = float(curve.iloc[-1])
    except Exception as e:
        failures.append(f"T10Y2Y({type(e).__name__})")

    # credit_signal은 fetch_fred_data에서 제외 — compute_features의 가격기반 신호 사용
    # (가격기반 HYG-TLT 모멘텀은 ±0.1 범위, FRED 기반은 ±0.25 범위로 임계값 0.01과 스케일 불일치)

    # ── 기대 인플레이션 (일별) ─────────────────────────────────────────────
    try:
        bei = _fred_get_series(fred, "T5YIE").dropna()
        if len(bei) > 0:
            result["breakeven_5y"] = float(bei.iloc[-1])
    except Exception as e:
        failures.append(f"T5YIE({type(e).__name__})")

    # ── CPI (월별) ────────────────────────────────────────────────────────
    try:
        cpi = _fred_get_series(fred, "CPIAUCSL").dropna()
        if len(cpi) >= 13:
            yoy = (cpi / cpi.shift(12) - 1) * 100
            result["cpi_yoy"] = float(yoy.dropna().iloc[-1])
        if len(cpi) >= 18:
            mom = cpi.pct_change()
            result["cpi_mom_zscore"] = float(
                _zscore_series(mom, window=36).dropna().iloc[-1]
            )
    except Exception as e:
        failures.append(f"CPIAUCSL({type(e).__name__})")

    # ── 실업률 (월별) ─────────────────────────────────────────────────────
    try:
        unrate = _fred_get_series(fred, "UNRATE").dropna()
        if len(unrate) >= 4:
            result["unrate_chg_3m"] = float(unrate.iloc[-1] - unrate.iloc[-4])
    except Exception as e:
        failures.append(f"UNRATE({type(e).__name__})")

    # ── M2 공급 (월별) ────────────────────────────────────────────────────
    try:
        m2 = _fred_get_series(fred, "M2SL").dropna()
        if len(m2) >= 13:
            m2_yoy = (m2 / m2.shift(12) - 1) * 100
            result["m2_yoy"] = float(m2_yoy.dropna().iloc[-1])
    except Exception as e:
        failures.append(f"M2SL({type(e).__name__})")

    # ── Fed 자산규모 (주별) ───────────────────────────────────────────────
    try:
        bs = _fred_get_series(fred, "WALCL").dropna()
        if len(bs) >= 53:
            bs_yoy = (bs / bs.shift(52) - 1) * 100
            result["fed_bs_yoy"] = float(bs_yoy.dropna().iloc[-1])
    except Exception as e:
        failures.append(f"WALCL({type(e).__name__})")

    # ── NFCI (ChicagoFed 금융여건, 주별) ──────────────────────────────────
    try:
        nfci = _fred_get_series(fred, "NFCI").dropna()
        if len(nfci) > 0:
            result["nfci"] = float(nfci.iloc[-1])
    except Exception as e:
        failures.append(f"NFCI({type(e).__name__})")

    if failures:
        print(f"    [FRED] 일부 series 실패: {', '.join(failures)}")

    if result:
        _save_fred_cache(result)
        return result

    # 모든 series 실패 → 캐시 fallback
    print("    [FRED] 모든 series 실패 — 캐시 fallback 시도")
    return _load_fred_cache_if_fresh()


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
        # ICE BAMLH0A0HYM2(HY OAS) 라이선스 회수 → Moody's BAA - 10Y 국채 spread 대체.
        # 변수명 hy_spread는 호환 유지하되 의미는 "credit spread proxy(BAA10Y)"로 재해석.
        # 스케일: BAA10Y 평균 ~2.5%, std ~0.8%, GFC peak 6%, COVID 4% (HY 대비 압축됨).
        "BAA10Y":         "hy_raw",
        "T10Y2Y":         "curve_10y2y",
        # NFCI: Chicago Fed National Financial Conditions Index (주별, 매주 수요일 발표).
        # 음수=loose(평상), 양수=tight(위험). 평균 ~-0.3, std ~0.6, GFC peak 3.06.
        "NFCI":           "nfci",
    }

    raw: dict[str, pd.Series] = {}
    for code, alias in series_map.items():
        try:
            s = _fred_get_series(fred, code, observation_start=fetch_start, observation_end=end)
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
    #
    # Publication lag 적용 (외부 비평 #3):
    #   FRED의 reference date 기준 index에 발표 시차만큼 영업일을 더해
    #   "as_of 시점 d에서 사용 가능한 값 = d 이전에 발표된 가장 최근 값"이 되도록 한다.
    #   각 시리즈의 표준 발표 캘린더 기반 보수적 추정.
    from pandas.tseries.offsets import BDay
    PUB_LAG = {
        "cpi_yoy":          30,   # CPI: 다음달 중순 발표 → ~30 BDay
        "cpi_mom_zscore":   30,
        "unrate_chg_3m":    25,   # UNRATE: 다음달 첫 금요일 → ~25 BDay
        "m2_yoy":           30,   # M2SL: 다음달 4째 화요일 → ~30 BDay
        "fed_bs_yoy":        7,   # WALCL: 다음주 목요일 → ~7 BDay
        "breakeven_5y":      1,   # T5YIE: 일별, 마감 후 → 다음 영업일
        "hy_spread":         1,   # 일별
        "hy_spread_zscore":  1,
        "curve_10y2y":       1,   # T10Y2Y: 일별
        "nfci":              7,   # NFCI: 주별, 다음 수요일 발표 → ~7 BDay
    }

    def _publish(s: pd.Series, key: str) -> pd.Series:
        """시리즈 index에 publication lag(영업일)를 더해 발표 가용일로 변환."""
        lag = PUB_LAG.get(key, 0)
        if lag <= 0:
            return s
        shifted = s.set_axis(s.index + BDay(lag))
        # 원본이 calendar day 기반(예: weekend 포함 daily series)인 경우
        # BDay shift 시 동일 영업일로 중복될 수 있다 → 마지막 값(=더 최근 reference)만 유지.
        if shifted.index.has_duplicates:
            shifted = shifted[~shifted.index.duplicated(keep="last")]
        return shifted

    # CPI (monthly)
    if "cpi_raw" in raw:
        cpi_m = raw["cpi_raw"]  # 월별 원본
        yoy_m = (cpi_m / cpi_m.shift(12) - 1) * 100  # 12개월 YoY
        mom_z_m = _zscore_series(cpi_m.pct_change(), window=36)  # 36개월 z-score
        result["cpi_yoy"] = _publish(yoy_m, "cpi_yoy").reindex(idx, method="ffill", limit=45)
        result["cpi_mom_zscore"] = _publish(mom_z_m, "cpi_mom_zscore").reindex(idx, method="ffill", limit=45)

    # 실업률 (monthly)
    if "unrate_raw" in raw:
        ur_m = raw["unrate_raw"]
        chg_m = ur_m - ur_m.shift(3)   # 3개월 변화
        result["unrate_chg_3m"] = _publish(chg_m, "unrate_chg_3m").reindex(idx, method="ffill", limit=45)

    # Breakeven (daily)
    if "breakeven_5y" in raw:
        result["breakeven_5y"] = _publish(raw["breakeven_5y"], "breakeven_5y").reindex(idx, method="ffill", limit=5)

    # M2 (monthly)
    if "m2_raw" in raw:
        m2_m = raw["m2_raw"]
        yoy_m = (m2_m / m2_m.shift(12) - 1) * 100  # 12개월 YoY
        result["m2_yoy"] = _publish(yoy_m, "m2_yoy").reindex(idx, method="ffill", limit=45)

    # Fed 자산규모 (weekly)
    if "fed_bs_raw" in raw:
        bs_w = raw["fed_bs_raw"]
        yoy_w = (bs_w / bs_w.shift(52) - 1) * 100   # 52주 YoY
        result["fed_bs_yoy"] = _publish(yoy_w, "fed_bs_yoy").reindex(idx, method="ffill", limit=10)

    # HY 스프레드 (daily, z-score는 영업일 756)
    if "hy_raw" in raw:
        hy_d = raw["hy_raw"]
        result["hy_spread"] = _publish(hy_d, "hy_spread").reindex(idx, method="ffill", limit=3)
        result["hy_spread_zscore"] = _publish(_zscore_series(hy_d), "hy_spread_zscore").reindex(idx, method="ffill", limit=3)

    # NFCI (weekly, ChicagoFed financial conditions index)
    if "nfci" in raw:
        result["nfci"] = _publish(raw["nfci"], "nfci").reindex(idx, method="ffill", limit=10)

    # 장단기 금리차 (daily)
    if "curve_10y2y" in raw:
        result["curve_10y2y"] = (
            _publish(raw["curve_10y2y"], "curve_10y2y").reindex(idx, method="ffill", limit=3)
        )

    # warm-up 구간 제거 → start 이후만 반환
    result = result.loc[start:end]

    # NaN이 과반인 열 제거
    result = result.loc[:, result.isna().mean() < 0.5]

    return result
