"""KIS 기반 멀티 계좌 리밸런싱 실행 레이어."""
import csv
import json
import math
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pykis
import yaml

from portfolio import compute_drift
from messenger import Messenger
from settlement import SettlementTracker
from deposit_log import (
    compute_net_flow,
    fetch_deposit_withdrawal_events,
)

# 시장 코드 → 통화 매핑 (pykis stock.market 값 기준)
MARKET_TO_CURRENCY: Dict[str, str] = {
    "KRX":    "KRW",
    "CRYPTO": "KRW",
    "AMEX":   "USD",
    "NASDAQ": "USD",
    "NYSE":   "USD",
}

# 상태 파일 경로
STATE_FILE = Path(__file__).parent / "state.json"   # 레거시 JSON (읽기 전용 폴백)
STATE_DB   = Path(__file__).parent / "state.db"     # SQLite (primary)

# 주문 로그 CSV
ORDER_LOG_FILE = Path(__file__).parent / "logs" / "orders.csv"
_ORDER_LOG_HEADERS = [
    "datetime", "ticker", "action", "qty", "price", "currency", "amount_krw", "status"
]

def _append_order_log(
    ticker: str,
    action: str,
    qty: int,
    price: float,
    currency: str,
    usd_krw: float,
    status: str,
) -> None:
    """주문 결과를 logs/orders.csv에 누적 기록한다."""
    ORDER_LOG_FILE.parent.mkdir(exist_ok=True)
    amount_krw = qty * price * (usd_krw if currency == "USD" else 1.0)
    row = {
        "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker": ticker,
        "action": action,
        "qty": qty,
        "price": price,
        "currency": currency,
        "amount_krw": round(amount_krw),
        "status": status,
    }
    write_header = not ORDER_LOG_FILE.exists()
    with open(ORDER_LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_ORDER_LOG_HEADERS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _fetch_balance_with_retry(
    client: "pykis.PyKis",
    currency: str,
    acc_name: str,
    max_retries: int = 3,
) -> object:
    """
    KIS 잔고를 currency별 국가 코드로 조회한다. 실패 시 지수 백오프로 재시도.

    country="KR" / "US" 로 분리 호출해 pykis 내부 불필요한 다중 API 호출을 줄인다.
    (country=None 통합 조회는 내부에서 5회 이상 API를 호출하므로 1개 실패 시 전체 실패)

    EGW00133 (접근토큰 1분당 1회) 감지 시 60초 대기 — 일반 backoff(1→2→4s)로는 모두 실패.
    """
    country = "KR" if currency == "KRW" else "US"
    last_err: Exception = RuntimeError("unknown")
    for attempt in range(max_retries):
        try:
            return client.account().balance(country=country)
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                msg = str(e)
                if "EGW00133" in msg or "접근토큰" in msg:
                    wait = 65  # 1분 제한 + 여유
                    print(
                        f"  [재시도] {acc_name} 토큰 1분 제한 EGW00133 "
                        f"({attempt + 1}/{max_retries}), {wait}s 후 재시도"
                    )
                else:
                    wait = 2 ** attempt  # 1s → 2s → 4s
                    print(
                        f"  [재시도] {acc_name} 잔고 조회 실패 "
                        f"({attempt + 1}/{max_retries}), {wait}s 후 재시도: {type(e).__name__}: {e}"
                    )
                time.sleep(wait)
    raise RuntimeError(
        f"{acc_name} 잔고 조회 최종 실패 ({max_retries}회): {last_err}"
    ) from last_err


def _label_ticker(ticker: str, universe: dict) -> str:
    """티커 → 사람이 읽기 쉬운 라벨. 숫자 코드(KRX)는 name을 괄호에 표시."""
    info = universe.get(ticker)
    name = info.get("name") if info else None
    if name and ticker.isdigit():
        return f"{ticker}({name})"
    return ticker


def _looks_like_insufficient_funds(msg: str) -> bool:
    """
    브로커/라이브러리별로 에러 메시지가 다르므로 휴리스틱으로 '현금/매수가능금액 부족'만 판별한다.
    (USD 주문 실패를 전부 합성노출로 처리하면, 유동성/호가/세션 문제까지 잘못 흡수될 수 있음)
    """
    if not msg:
        return False
    m = msg.lower()
    keywords = [
        # Korean
        "예수금", "현금", "잔고", "주문가능", "매수가능", "가용", "증거금", "부족", "초과",
        # English-ish
        "insufficient", "not enough", "insuff", "balance", "fund", "cash",
        "buying power", "orderable", "available",
    ]
    return any(k.lower() in m for k in keywords)


# ── SQLite 상태 관리 ─────────────────────────────────────────────────────────

def _db_init(db_path: Path) -> sqlite3.Connection:
    """DB가 없으면 스키마를 생성하고 연결을 반환한다."""
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")  # 동시 읽기 허용, 쓰기 내구성 향상
    con.execute("""
        CREATE TABLE IF NOT EXISTS state_current (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS state_history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        TEXT    NOT NULL,
            regime    TEXT,
            drawdown  REAL,
            total_krw REAL,
            drift_krw REAL,
            drift_usd REAL,
            snapshot  TEXT    NOT NULL
        )
    """)
    con.commit()
    return con


def load_state() -> dict:
    """SQLite에서 state를 로드한다. DB 없으면 state.json 폴백."""
    if STATE_DB.exists():
        try:
            con = _db_init(STATE_DB)
            rows = con.execute("SELECT key, value FROM state_current").fetchall()
            con.close()
            if rows:
                flat = {k: json.loads(v) for k, v in rows}
                state = flat.get("__root__", {})
                if not isinstance(state, dict):
                    state = {}
                if not isinstance(state.get("peak_krw"), (int, float)):
                    state["peak_krw"] = 0.0
                return state
        except Exception as e:
            print(f"  [경고] state.db 로드 실패 ({e}) → state.json 폴백 시도")

    # JSON 폴백
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [경고] state.json 로드 실패 ({e}) → 기본값 사용")
            return {"peak_krw": 0.0}
        if not isinstance(state.get("peak_krw"), (int, float)):
            state["peak_krw"] = 0.0
        return state

    return {"peak_krw": 0.0}


def save_state(state: dict) -> None:
    """state를 SQLite에 원자적으로 저장하고 history 스냅샷을 남긴다."""
    con = _db_init(STATE_DB)
    try:
        with con:  # BEGIN … COMMIT (예외 시 ROLLBACK)
            con.execute(
                "INSERT OR REPLACE INTO state_current (key, value) VALUES (?, ?)",
                ("__root__", json.dumps(state, ensure_ascii=False)),
            )
            # 레짐·드로우다운 등 핵심 필드만 history로 분리 저장
            con.execute(
                """INSERT INTO state_history
                   (ts, regime, drawdown, total_krw, drift_krw, drift_usd, snapshot)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now().isoformat(),
                    state.get("confirmed_regime"),
                    state.get("last_drawdown"),
                    state.get("last_total_krw"),
                    state.get("last_drift_krw"),
                    state.get("last_drift_usd"),
                    json.dumps(state, ensure_ascii=False),
                ),
            )
    finally:
        con.close()

    # JSON 미러 (사람이 직접 읽을 수 있도록 — 쓰기 실패해도 SQLite가 primary)
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        tmp.replace(STATE_FILE)   # atomic rename
    except OSError:
        pass


def _adjust_tick(price: float, currency: str) -> float:
    """KRX / US 틱 사이즈에 맞게 가격을 내림 처리한다."""
    if currency == "USD":
        return round(price, 2) if price >= 1.0 else round(price, 4)

    if price < 1_000:
        return round(price)
    elif price < 5_000:
        return round(price / 5) * 5
    elif price < 10_000:
        return round(price / 10) * 10
    elif price < 50_000:
        return round(price / 50) * 50
    elif price < 100_000:
        return round(price / 100) * 100
    elif price < 500_000:
        return round(price / 500) * 500
    return round(price / 1_000) * 1_000


class KisRebalancer:
    """
    전체 계좌를 읽어 통합 비중을 계산하고 리밸런싱을 실행한다.

    KRW 계좌(KRW_1, KRW_2 등)는 하나의 풀로 취급하며,
    각 계좌 잔고 비율에 비례해 모든 KRW 종목 주문을 분산한다.
    → 두 계좌가 항상 동일 비중을 유지하므로 exec_account 불일치 문제가 없다.

    USD 계좌는 기존대로 universe의 exec_account를 사용한다.
    """

    def __init__(
        self,
        config: dict,
        auth_path: Optional[Path] = None,
        messenger: Optional[Messenger] = None,
    ):
        self.config = config
        self.universe: Dict[str, dict] = config["universe"]
        fallback = float(config["rebalancing"].get("usd_krw_fallback", 1380.0))
        self.usd_krw: float = self._fetch_usd_krw(fallback)
        self.min_order_krw: float = float(
            config["rebalancing"].get("min_order_krw", 10_000)
        )
        self.messenger = messenger
        auth_path = auth_path or Path(__file__).parent / "auth.yaml"
        self._clients = self._init_clients(auth_path)
        # 유니버스 외 보유 종목: {ticker: {currency, acc_name, amount_krw}}
        self._orphan_holdings: Dict[str, dict] = {}
        # KRW 계좌별 보유액: {acc_name: {ticker: krw_amount}}  — _build_orders 용
        self._krw_acc_holdings: Dict[str, Dict[str, float]] = {}
        # KRW 계좌별 총액: {acc_name: total_krw}  (T+2 보정 포함, 비중 계산용)
        self._krw_acc_totals: Dict[str, float] = {}
        # KRW 계좌별 실제 현금: {acc_name: cash}  (T+2 보정 전, 매수 cap용)
        self._krw_acc_cash: Dict[str, float] = {}
        # 이번 실행에서 주문된 금액(원화 환산) — run.py에서 월간 누적에 합산
        self._last_run_traded_krw: float = 0.0
        # 이번 회차 매도 성공 금액 (acc_name → 누적 KRW). _fetch_krw_orderable fallback에서
        # _krw_acc_cash에 더해 매도대금 보정용. rebalance() 시작 시 reset.
        self._recent_sell_proceeds_krw: Dict[str, float] = {}
        # KIS rate limit 예방용 주문 간 throttle (초). 0이면 비활성.
        self.order_throttle_s: float = float(
            config.get("rebalancing", {}).get("order_throttle_s", 0.25)
        )

    @staticmethod
    def _fetch_usd_krw(fallback: float) -> float:
        """실시간 USD/KRW 환율 조회. state.json 캐시가 1시간 이내이면 재사용."""
        state = load_state()
        cached_rate = state.get("usd_krw_rate")
        cached_at = state.get("usd_krw_at")
        if cached_rate and cached_at:
            try:
                age = (datetime.now() - datetime.fromisoformat(cached_at)).total_seconds()
                if age < 3600:
                    print(f"    USD/KRW: {cached_rate:,.1f} (캐시, {age/60:.0f}분 전)")
                    return float(cached_rate)
            except Exception:
                pass

        try:
            from fetcher import fetch_usd_krw
            rate = fetch_usd_krw(fallback)
            print(f"    USD/KRW: {rate:,.1f} (실시간)")
            state["usd_krw_rate"] = rate
            state["usd_krw_at"] = datetime.now().isoformat()
            save_state(state)
            return rate
        except Exception:
            print(f"    USD/KRW: {fallback:,.1f} (폴백)")
            return fallback

    # ──────────────────────────────────────────────
    # 초기화
    # ──────────────────────────────────────────────

    def _init_clients(self, auth_path: Path) -> Dict[str, pykis.PyKis]:
        """config의 accounts 정의를 기반으로 pykis 클라이언트를 생성한다.

        acc_no가 같으면 currency에 관계없이 동일 인스턴스를 공유한다.
        → 동일 계좌(KRW_1·USD 모두 64378890-01)는 토큰을 하나만 유지해 EGW00133 방지.
        """
        with open(auth_path) as f:
            auth = yaml.safe_load(f)

        clients: Dict[str, pykis.PyKis] = {}
        seen_acc: Dict[str, pykis.PyKis] = {}  # acc_no → client

        for acc_name, acc_cfg in self.config["accounts"].items():
            acc_no = acc_cfg["acc_no"]
            if acc_no not in seen_acc:
                creds = auth[acc_no]
                seen_acc[acc_no] = pykis.PyKis(
                    id=creds["id"],
                    appkey=creds["appkey"],
                    secretkey=creds["secretkey"],
                    account=acc_no,
                    keep_token=True,
                )
            clients[acc_name] = seen_acc[acc_no]

        return clients

    # ──────────────────────────────────────────────
    # peak 보정 — explicit 입출금 이벤트 우선, 휴리스틱 fallback
    # ──────────────────────────────────────────────

    def _correct_peak_for_io(
        self,
        peak: float,
        prev_total: float,
        prev_total_at: Optional[str],
        total_all_krw: float,
        current_principal_krw: Optional[float] = None,
        state_snapshot: Optional[dict] = None,
    ) -> Tuple[float, Optional[str]]:
        """
        직전 실행 이후 발생한 입출금을 peak에 반영한다.

        조회 우선순위:
          1. trading/logs/deposits.csv (explicit, 결정적)
          2. pykis account().profits() 역산: 입출금 = Δprincipal - 실현손익
          3. 휴리스틱: |Δ|>10% AND age<30h → 입출금 추정

        Returns
        -------
        (new_peak, processed_through_iso)
          processed_through_iso: KIS profits 백엔드를 사용한 경우 마지막 처리일(YYYY-MM-DD).
                                  호출자가 state에 캐시하면 같은 날 중복 호출 시 매도손익 이중 계산을 막는다.
        """
        if peak <= 0 or prev_total <= 0 or not prev_total_at:
            return peak, None

        # since 시각 파싱 — 실패하면 보정 스킵
        try:
            since_dt = datetime.fromisoformat(prev_total_at)
        except (ValueError, TypeError):
            return peak, None

        events, source = fetch_deposit_withdrawal_events(
            since=since_dt,
            pykis_clients=getattr(self, "_clients", None),
            state_snapshot=state_snapshot,
            current_principal_krw=current_principal_krw,
        )

        if events is not None:
            net_flow = compute_net_flow(events)
            processed_through = (
                datetime.now().date().isoformat() if source == "kis_profits" else None
            )

            if net_flow == 0.0:
                return peak, processed_through

            new_peak = peak + net_flow
            if new_peak <= 0:
                print(
                    f"  [peak 보정/{source}] net_flow {net_flow:+,.0f}원 적용 시 peak<0"
                    f" — total_all_krw({total_all_krw:,.0f}원)로 리셋"
                )
                return float(total_all_krw), processed_through

            n_dep = sum(1 for e in events if e.kind == "deposit")
            n_wd = len(events) - n_dep
            print(
                f"  [peak 보정/{source}] {len(events)}건"
                f" (입금 {n_dep} / 출금 {n_wd}) net {net_flow:+,.0f}원"
                f" → peak {peak:,.0f}→{new_peak:,.0f}원"
            )
            return new_peak, processed_through

        # 휴리스틱 fallback
        try:
            age_h = (
                datetime.now() - datetime.fromisoformat(prev_total_at)
            ).total_seconds() / 3600
        except (ValueError, TypeError):
            age_h = float("inf")

        rel_change = (total_all_krw - prev_total) / prev_total
        if age_h <= 30:
            if abs(rel_change) > 0.10:
                new_peak = peak * (1 + rel_change)
                print(
                    f"  [peak 보정/휴리스틱] 자산 {prev_total:,.0f}→{total_all_krw:,.0f}원"
                    f" ({rel_change:+.1%}, 직전 {age_h:.1f}h전)"
                    f" → 입출금 추정, peak {peak:,.0f}→{new_peak:,.0f}원"
                )
                print(
                    "  [참고] 명시적 입출금 로그 사용 권장:"
                    " trading/logs/deposits.csv (ts,acc_name,amount_krw,kind,note)"
                )
                return new_peak, None
        elif abs(rel_change) > 0.10:
            print(
                f"  [peak 보정 스킵] 직전 자산 기록이 {age_h:.0f}h 전 (>30h)"
                f" — 시장 변동 가능성, 입출금 보정 건너뜀"
            )

        return peak, None

    # ──────────────────────────────────────────────
    # 포트폴리오 상태 조회
    # ──────────────────────────────────────────────

    def get_portfolio_state(self) -> Tuple[float, float, float, Dict[str, float], float]:
        """
        전 계좌를 합산하여 (total_krw, total_usd_krw, total_krw_only, 현재비중, 드로우다운) 반환.

        total_krw      : 유니버스 기준 전체 (USD+KRW 합산, KRW 환산)
        total_usd_krw  : USD 계좌 총액 (KRW 환산)
        total_krw_only : KRW 계좌 총액
        현재비중       : {ticker: fraction of total_krw}  — drift·출력 기준
        드로우다운     : 직전 고점 대비 낙폭 (0 이하 실수)
        """
        holdings_krw: Dict[str, float] = {}  # ticker → KRW 환산 금액 (전 계좌 합산)
        cash_by_currency: Dict[str, float] = {"KRW": 0.0, "USD": 0.0}
        krw_acc_holdings: Dict[str, Dict[str, float]] = {}  # acc_name → {ticker: krw}
        krw_acc_cash: Dict[str, float] = {}  # acc_name → cash
        purchase_amount_krw_total: float = 0.0  # 매입금액 합 (KRW 환산) — KIS profits 백엔드용

        processed_acc: set = set()
        # (acc_no, currency) → balance  — currency별 국가 분리 조회로 API 호출 최소화
        balance_cache: Dict[tuple, object] = {}
        for acc_name, acc_cfg in self.config["accounts"].items():
            acc_no = acc_cfg["acc_no"]
            currency = acc_cfg["currency"]
            client = self._clients[acc_name]

            # 동일 acc_no + currency 조합은 한 번만 처리
            key = (acc_no, currency)
            if key in processed_acc:
                continue
            processed_acc.add(key)

            # 재시도 포함 잔고 조회 (country별 분리 — 통합 조회는 내부 API 5회)
            if key not in balance_cache:
                balance_cache[key] = _fetch_balance_with_retry(client, currency, acc_name)
            balance = balance_cache[key]
            acc_stock_holdings: Dict[str, float] = {}

            for stock in balance.stocks:
                ticker = stock.symbol
                mkt_currency = MARKET_TO_CURRENCY.get(stock.market)
                if mkt_currency is None:
                    print(
                        f"  [경고] {ticker}: 알 수 없는 market 코드 '{stock.market}'"
                        f" → 통화 분류 불가, 스킵"
                    )
                    continue
                if mkt_currency != currency:
                    continue
                try:
                    amt = float(stock.current_amount)
                except Exception as e:
                    print(f"  [경고] {ticker} 평가금액 변환 실패: {e} — 0 처리")
                    amt = 0.0
                krw_amt = amt * self.usd_krw if currency == "USD" else amt
                holdings_krw[ticker] = holdings_krw.get(ticker, 0.0) + krw_amt

                # 매입금액(cost basis) — KIS profits 역산 백엔드용
                try:
                    purch = float(stock.purchase_amount)
                except Exception:
                    purch = 0.0
                purchase_amount_krw_total += (
                    purch * self.usd_krw if currency == "USD" else purch
                )

                if currency == "KRW":
                    acc_stock_holdings[ticker] = acc_stock_holdings.get(ticker, 0.0) + krw_amt

                # 유니버스 외 종목 기록 (acc_name 포함)
                if ticker not in self.universe and krw_amt > 0:
                    self._orphan_holdings[ticker] = {
                        "currency":   currency,
                        "acc_name":   acc_name,
                        "amount_krw": krw_amt,
                    }

            deposit = balance.deposits.get(currency)
            if deposit is None:
                print(f"  [경고] {acc_name} 예수금 미반환 (currency={currency}) — 현금 0 처리")
                cash = 0.0
            else:
                try:
                    if currency == "KRW":
                        # KIS의 dnca_tot_amt(pykis deposit.amount)는 당일 매도대금 미반영.
                        # T+2 결제 대기 자금까지 포함하는 prvs_rcdl_excc_amt를 raw에서 추출.
                        # 미가용 시 dnca_tot_amt로 폴백.
                        cash = float(deposit.amount)
                        try:
                            raw = balance.raw()
                            out2 = raw.get("output2") if raw else None
                            if isinstance(out2, list) and out2:
                                out2 = out2[0]
                            if isinstance(out2, dict):
                                prvs = out2.get("prvs_rcdl_excc_amt")
                                if prvs not in (None, ""):
                                    cash = float(prvs)
                        except Exception as e:
                            print(f"  [경고] {acc_name} prvs_rcdl_excc_amt 추출 실패: {e} — dnca_tot_amt 사용")
                    else:
                        # USD: pykis deposit.amount = frcr_dncl_amt_2 (예수금)는 매수증거금 포함.
                        # 매수 결제 대기분이 stocks 평가에도 잡혀 있어 이중계산.
                        # frcr_drwg_psbl_amt_1 (출금가능금액, 매수증거금 차감)을 사용.
                        cash_usd = float(deposit.withdrawable_amount)  # = frcr_drwg_psbl_amt_1
                        # 폴백: withdrawable_amount이 0 또는 비정상이면 deposit.amount 사용
                        if cash_usd <= 0:
                            cash_usd = float(deposit.amount)
                        cash = cash_usd * self.usd_krw
                except Exception as e:
                    print(f"  [경고] {acc_name} 예수금 변환 실패: {e} — 0 처리")
                    cash = 0.0
            cash_by_currency[currency] = cash_by_currency.get(currency, 0.0) + cash

            if currency == "KRW":
                krw_acc_holdings[acc_name] = acc_stock_holdings
                krw_acc_cash[acc_name] = cash

        # KRW 계좌별 총액 저장 (주식 + 현금) — orphan은 제외 (target 비중 왜곡 방지)
        self._krw_acc_holdings = krw_acc_holdings
        self._krw_acc_totals = {
            acc: sum(
                v for t, v in krw_acc_holdings.get(acc, {}).items()
                if t in self.universe
            ) + krw_acc_cash.get(acc, 0.0)
            for acc in krw_acc_holdings
        }
        self._krw_acc_cash = krw_acc_cash  # T+2 보정 전 실제 현금 (매수 cap용)

        # 유니버스 외 보유 종목 분리 및 안내
        universe_krw = {t: v for t, v in holdings_krw.items() if t in self.universe}
        orphan_krw = {t: v for t, v in holdings_krw.items() if t not in self.universe}

        if orphan_krw:
            total_all = sum(holdings_krw.values()) + sum(cash_by_currency.values())
            print("  [정리 예정] 유니버스 외 보유 종목 (리밸런싱 시 자동 전량 매도):")
            for t, v in orphan_krw.items():
                print(f"    {t}: {v:,.0f} KRW ({v/total_all*100:.1f}%)")

        # 계좌별 분리 계산
        usd_holdings = sum(
            v for t, v in universe_krw.items()
            if self.universe[t]["currency"] == "USD"
        )
        krw_holdings = sum(
            v for t, v in universe_krw.items()
            if self.universe[t]["currency"] == "KRW"
        )
        total_usd_krw = usd_holdings + cash_by_currency.get("USD", 0.0)
        total_krw_only = krw_holdings + cash_by_currency.get("KRW", 0.0)

        universe_total_krw = total_usd_krw + total_krw_only

        if universe_total_krw == 0:
            return 0.0, 0.0, 0.0, {}, 0.0

        # 현재 비중 = 전체 대비 (drift·출력용)
        current_weights = {t: v / universe_total_krw for t, v in universe_krw.items()}

        # 드로우다운: 전체 자산(orphan 포함) 기준
        # KRW deposit.amount=dnca_tot_amt(매도 즉시 반영)이므로 T+2 보정 불필요
        state = load_state()
        total_all_krw = sum(holdings_krw.values()) + sum(cash_by_currency.values())
        peak = state.get("peak_krw", 0.0)

        prev_total = float(state.get("last_total_all_krw", 0.0))
        prev_total_at = state.get("last_total_all_krw_at")
        current_principal_krw = purchase_amount_krw_total + sum(cash_by_currency.values())
        peak, kis_profits_processed_through = self._correct_peak_for_io(
            peak=peak,
            prev_total=prev_total,
            prev_total_at=prev_total_at,
            total_all_krw=total_all_krw,
            current_principal_krw=current_principal_krw,
            state_snapshot=state,
        )

        peak = max(peak, total_all_krw)
        self._peak_krw = peak
        self._last_total_all_krw = total_all_krw
        self._last_principal_krw = current_principal_krw
        self._kis_profits_processed_through = kis_profits_processed_through
        drawdown = (total_all_krw / peak - 1.0) if peak > 0 else 0.0

        return universe_total_krw, total_usd_krw, total_krw_only, current_weights, drawdown

    # ──────────────────────────────────────────────
    # 리밸런싱 실행
    # ──────────────────────────────────────────────

    def rebalance(
        self,
        current_weights: Dict[str, float],
        target_usd: Dict[str, float],
        target_krw: Dict[str, float],
        total_usd_krw: float,
        total_krw_only: float,
        threshold: float,
        tracker: Optional[SettlementTracker] = None,
        side: str = "all",
        force_full_rebalance: bool = False,
    ) -> Tuple[List[str], List[dict]]:
        """
        리밸런싱을 실행한다.

        side: "all" | "krw" | "usd"
          - "krw" / "usd": 해당 계좌 종목만 주문 생성 (monitor에서 트리거 확정 후 호출)
          - threshold=0.0 으로 호출하면 drift 재확인 없이 바로 실행

        force_full_rebalance: True면 per_ticker_drift_threshold를 무시하고 min_order_krw 이상
        모든 차이를 주문 생성한다. drift/regime_change/drawdown_emergency 트리거가 발동된 회차에서
        portfolio가 의도된 비중으로 수렴하도록 보장 (단일 종목만 거래되어 편향되는 현상 방지).

        버퍼 잔여분 내 KRW 매수는 즉시 실행하고, 초과분은 deferred_buys로 반환한다.
        USD 계좌는 버퍼 로직 미적용 (USD 현금으로 직접 집행).

        Returns:
            (order_log, deferred_buys)
        """
        total_value_krw = total_usd_krw + total_krw_only

        # threshold > 0 이면 drift 재확인 (monitor 없이 직접 호출 시 안전장치)
        if threshold > 0:
            from portfolio import merge_to_total_weights
            merged_target = merge_to_total_weights(target_usd, target_krw, total_usd_krw, total_krw_only)
            drift = compute_drift(current_weights, merged_target)
            print(f"총 drift: {drift*100:.1f}%  (임계값: {threshold*100:.0f}%)")
            if drift < threshold:
                print("→ 리밸런싱 불필요")
                return [], []

        if force_full_rebalance:
            print("  [강제 평준화] per_ticker_drift_threshold 무시 — 모든 차이 주문 생성")

        all_orders = self._build_orders(
            current_weights, target_usd, target_krw, total_usd_krw, total_krw_only,
            force_full_rebalance=force_full_rebalance,
        )

        # 단일 실행 회전율 상한 체크 (매수+매도 합산 / 포트폴리오 총액)
        max_run = float(self.config.get("rebalancing", {}).get("max_run_turnover", 0.0))
        if total_value_krw > 0 and max_run > 0:
            total_order_krw = sum(abs(a) for _, _, a, _ in all_orders)
            run_rate = total_order_krw / total_value_krw
            if run_rate > max_run:
                print(
                    f"  [경고] 단일 실행 회전율 초과: {run_rate:.1%} > {max_run:.1%} "
                    f"(주문 {total_order_krw:,.0f}원 / 포트폴리오 {total_value_krw:,.0f}원) → 실행 차단"
                )
                return [], []

        # side 필터: 해당 계좌 종목만
        if side == "krw":
            all_orders = [(t, c, a, acc) for t, c, a, acc in all_orders if c == "KRW"]
        elif side == "usd":
            all_orders = [(t, c, a, acc) for t, c, a, acc in all_orders if c == "USD"]

        # 월간 누적 회전율 상한 체크 (side 필터 이후 — 실제 집행 예정 금액 기준)
        side_order_krw = sum(abs(a) for _, _, a, _ in all_orders)
        max_monthly = float(self.config.get("rebalancing", {}).get("max_monthly_turnover", 0.0))
        if max_monthly > 0 and total_value_krw > 0:
            current_ym = datetime.now().strftime("%Y-%m")
            _s = load_state()
            if _s.get("monthly_ym") != current_ym:
                monthly_traded = 0.0
            else:
                monthly_traded = float(_s.get("monthly_traded_krw", 0.0))
            monthly_rate = (monthly_traded + side_order_krw) / total_value_krw
            if monthly_rate > max_monthly:
                print(
                    f"  [경고] 월간 누적 회전율 초과: 누적 {monthly_traded/total_value_krw:.1%}"
                    f" + 이번 {side_order_krw/total_value_krw:.1%}"
                    f" = {monthly_rate:.1%} > {max_monthly:.1%} → 실행 차단"
                )
                return [], []

        # 실제 체결된 의도 금액만 누적 (cap 보호 측면에선 보수적인 의도금액보다
        # 약간 작아지지만, 실패한 주문이 회전율에 잡히는 왜곡 제거).
        actual_traded_krw = 0.0

        sell_orders = [(t, c, a, acc) for t, c, a, acc in all_orders if a < 0]
        buy_orders  = [(t, c, a, acc) for t, c, a, acc in all_orders if a > 0]
        sell_cnt, buy_cnt = len(sell_orders), len(buy_orders)
        side_label = f" [{side.upper()}]" if side != "all" else ""
        print(f"→{side_label} 실행 {len(all_orders)}건 (매도 {sell_cnt}, 매수 {buy_cnt})")

        order_log: List[str] = []
        failed_buys: List[dict] = []

        # 이번 회차 매도 추적기 reset — _fetch_krw_orderable fallback 보정용
        self._recent_sell_proceeds_krw = {}

        # Phase 1: 매도 먼저 실행 — KIS는 체결 즉시 주문가능금액에 반영
        for i, (ticker, currency, amount_diff_krw, acc_name) in enumerate(sell_orders):
            if i > 0 and self.order_throttle_s > 0:
                time.sleep(self.order_throttle_s)
            result = self._execute_order(ticker, currency, amount_diff_krw, acc_name)
            if result:
                order_log.append(result)
                # 성공 결과는 "매수 .../매도 ..." 접두, 실패는 "[timeout]/[오류]" 접두
                if not result.startswith("["):
                    actual_traded_krw += abs(amount_diff_krw)
                    # KRW 매도 성공분을 acc별로 누적 — orderable API 실패 시 fallback 보정
                    if currency == "KRW":
                        self._recent_sell_proceeds_krw[acc_name] = (
                            self._recent_sell_proceeds_krw.get(acc_name, 0.0)
                            + abs(amount_diff_krw)
                        )

        # Phase 2: KRW 주문가능금액 조회 (매도 완료 후 → 당일 매도대금 반영)
        krw_buys_by_acc: Dict[str, List[Tuple[str, float]]] = {}
        for t, c, a, acc in buy_orders:
            if c == "KRW":
                krw_buys_by_acc.setdefault(acc, []).append((t, a))

        scaled_buy_orders: List[Tuple[str, str, float, str]] = []
        for acc_name, buys in krw_buys_by_acc.items():
            ref_ticker = buys[0][0]
            orderable = self._fetch_krw_orderable(acc_name, ref_ticker)
            total_buy = sum(d for _, d in buys)
            if orderable > 0 and total_buy > orderable:
                scale = orderable / total_buy
                print(f"  [주문가능 cap] {acc_name}: {total_buy:,.0f}원 → {orderable:,.0f}원 ({scale:.1%})")
                buys = [(t, d * scale) for t, d in buys]
            for t, d in buys:
                if d >= self.min_order_krw:
                    scaled_buy_orders.append((t, "KRW", d, acc_name))

        for t, c, a, acc in buy_orders:
            if c == "USD":
                scaled_buy_orders.append((t, c, a, acc))

        # Phase 3: 매수 실행
        for i, (ticker, currency, amount_diff_krw, acc_name) in enumerate(scaled_buy_orders):
            if i > 0 and self.order_throttle_s > 0:
                time.sleep(self.order_throttle_s)
            result = self._execute_order(ticker, currency, amount_diff_krw, acc_name)
            if result:
                order_log.append(result)
                if not result.startswith("["):
                    actual_traded_krw += abs(amount_diff_krw)
                is_funds_error = result.startswith("[오류]") and _looks_like_insufficient_funds(result)
                is_timeout = result.startswith("[timeout]")
                if is_funds_error or is_timeout:
                    failed_buys.append({
                        "ticker": ticker,
                        "amount_krw": abs(amount_diff_krw),
                        "currency": currency,
                    })

        # 체결분 기준으로 monthly_traded_krw 누적 — 실패한 주문은 회전율에 포함 안 됨
        self._last_run_traded_krw = actual_traded_krw

        if failed_buys:
            cnt_krw = sum(1 for d in failed_buys if d["currency"] == "KRW")
            cnt_usd = sum(1 for d in failed_buys if d["currency"] == "USD")
            parts = [f"{c} {n}건" for c, n in [("KRW", cnt_krw), ("USD", cnt_usd)] if n > 0]
            print(f"    [매수 실패] {', '.join(parts)} → 다음 실행 시 합성 노출로 대체")
        return order_log, failed_buys

    def _build_orders(
        self,
        current: Dict[str, float],
        target_usd: Dict[str, float],
        target_krw: Dict[str, float],
        total_usd_krw: float,
        total_krw_only: float,
        force_full_rebalance: bool = False,
    ) -> List[Tuple[str, str, float, str]]:
        """
        (ticker, currency, amount_diff_krw, acc_name) 주문 목록 생성.

        USD 종목: target_usd[t] × total_usd_krw, exec_account 계좌로 실행.
        KRW 종목: 각 KRW 계좌의 잔고 비율에 비례해 분산 주문 생성.
          → KRW_1·KRW_2가 항상 동일 비중을 유지한다.

        per_ticker_drift_threshold: 개별 종목의 계좌 내 이탈이
        이 값 미만이면 거래 제외 (불필요한 소규모 거래 방지).
        USD는 USD 계좌 총액, KRW는 해당 KRW 계좌 총액을 기준으로 비교.

        force_full_rebalance=True면 per_ticker_drift_threshold를 0으로 간주 — min_order_krw 이상
        모든 차이를 주문에 포함. drift 트리거 발동 시 단일 종목 편향 방지용.
        """
        total_krw = total_usd_krw + total_krw_only
        per_ticker_thr = 0.0 if force_full_rebalance else float(
            self.config["rebalancing"].get("per_ticker_drift_threshold", 0.0)
        )
        orders: List[Tuple[str, str, float, str]] = []
        # KRW 매수 후보: 계좌별로 수집 — 한도 체크는 매도 후 rebalance()에서 주문가능금액 기준 수행
        krw_buy_candidates: Dict[str, List[Tuple[str, float]]] = {}

        for ticker, meta in self.universe.items():
            currency = meta["currency"]

            if currency == "USD":
                current_amt = current.get(ticker, 0.0) * total_krw
                target_amt = target_usd.get(ticker, 0.0) * total_usd_krw
                diff = target_amt - current_amt
                diff_frac = abs(diff) / total_usd_krw if total_usd_krw > 0 else 0.0
                if abs(diff) >= self.min_order_krw and (
                    per_ticker_thr <= 0 or diff_frac >= per_ticker_thr
                ):
                    orders.append((ticker, "USD", diff, meta["exec_account"]))
            else:
                # KRW: 계좌별로 별도 주문 생성 (동일 비중 유지)
                target_w = target_krw.get(ticker, 0.0)
                for acc_name, acc_total in self._krw_acc_totals.items():
                    if acc_total <= 0:
                        continue
                    acc_current = self._krw_acc_holdings.get(acc_name, {}).get(ticker, 0.0)
                    acc_target = target_w * acc_total
                    diff = acc_target - acc_current
                    diff_frac = abs(diff) / acc_total if acc_total > 0 else 0.0
                    if abs(diff) >= self.min_order_krw and (
                        per_ticker_thr <= 0 or diff_frac >= per_ticker_thr
                    ):
                        if diff < 0:
                            orders.append((ticker, "KRW", diff, acc_name))
                        else:
                            krw_buy_candidates.setdefault(acc_name, []).append((ticker, diff))

        for acc_name, buys in krw_buy_candidates.items():
            for ticker, diff in buys:
                if diff >= self.min_order_krw:
                    orders.append((ticker, "KRW", diff, acc_name))

        return orders

    # ──────────────────────────────────────────────
    # 단일 종목 주문
    # ──────────────────────────────────────────────

    def _get_client(self, ticker: str, acc_name: Optional[str] = None) -> pykis.PyKis:
        if acc_name is None:
            acc_name = self.universe[ticker]["exec_account"]
        return self._clients[acc_name]

    def _get_price(self, stock, action: str, currency: str) -> float:
        try:
            ob = stock.orderbook()
            price = (
                float(ob.ask_price.price)
                if action == "buy"
                else float(ob.bid_price.price)
            )
        except Exception:
            quote = stock.quote()
            price = float(quote.high if action == "buy" else quote.low)
        return _adjust_tick(price, currency)

    def _get_held_qty(self, client: pykis.PyKis, ticker: str, currency: str, price: float, acc_name: str = "?") -> int:
        """매도 직전 실제 보유 수량을 조회한다. 조회 실패 시 RuntimeError를 발생시킨다."""
        try:
            for s in _fetch_balance_with_retry(client, currency, acc_name).stocks:
                if s.symbol != ticker:
                    continue
                # 같은 symbol이라도 다른 시장(KRX vs NASDAQ 등)이면 스킵
                if MARKET_TO_CURRENCY.get(s.market, "KRW") != currency:
                    continue
                # orderable = 매도가능수량 (잠긴 주식·미결제 수량 제외) — 가장 안전한 기준
                orderable_val = getattr(s, "orderable", None)
                if orderable_val is not None:
                    return int(float(orderable_val))
                qty_val = getattr(s, "qty", None)
                if qty_val is not None:
                    return int(float(qty_val))
                # orderable/qty 없으면 평가금액(native currency) ÷ 현재가(native currency)로 추정
                # current_amount는 USD 종목이면 USD, KRX 종목이면 KRW — price도 같은 단위
                return math.floor(float(s.current_amount) / price) if price > 0 else 0
            return 0  # 잔고에 없음
        except Exception as e:
            raise RuntimeError(f"{ticker} 보유 수량 조회 실패: {e}") from e

    def _fetch_krw_orderable(self, acc_name: str, ref_ticker: str) -> float:
        """KRX 계좌의 주문가능금액을 KIS API로 조회한다 (매도 직후 호출 — 당일 매도대금 포함).

        KIS는 매도 체결 즉시 max_buy_qty에 반영하므로 T+2 현금 입금 전이라도 정확한 한도를 반환한다.
        조회 실패 시 _krw_acc_cash (T+2 미결제 제외 현금) 폴백.

        cap 결정: max_buy_qty × price를 신뢰한다.
        - oa.amount(ord_psbl_cash)는 당일 매도대금을 반영하지 않아 매도 직후 매수에서 비현실적으로 작음.
        - oa.quantity(max_buy_qty)는 KIS가 수수료 + 결제대기 자금까지 모두 반영해 자체 계산한 권위값.
        - 따라서 cash와의 min 비교는 매도 직후 케이스에서 cap을 잘못 줄임 (실제 버그 사례 2026-05-27).
        - 가격은 _execute_order와 동일한 ask(_get_price)를 사용 — 종가로 산정하면 ask 프리미엄만큼
          필요 현금을 과소평가해 마지막 잔여 매수(469830 버퍼)가 '주문가능금액 초과'로 거부됨 (2026-06-08).
        """
        client = self._clients[acc_name]
        try:
            stock = client.stock(ref_ticker)
            try:
                price = self._get_price(stock, "buy", "KRW")
                if price <= 0:
                    price = 1_000.0
            except Exception:
                price = 1_000.0
            oa = client.account().orderable_amount("KRX", ref_ticker, price=price)
            cash = float(oa.amount)          # ord_psbl_cash (참고용, cap에는 사용 안 함)
            max_qty = int(oa.quantity)        # max_buy_qty — 수수료 + 매도대금 포함 KIS 계산
            qty_based = float(max_qty) * price
            # 98%만 사용 — 다종목 바스켓의 수수료·세금·ask 슬리피지·정수주 반올림 여유분 확보
            effective = qty_based * 0.98
            print(
                f"    [주문가능금액] {acc_name}: {effective:,.0f}원"
                f" (max_qty={max_qty}×{price:.0f}={qty_based:,.0f}, ask·98% 적용,"
                f" 참고 ord_psbl_cash={cash:,.0f})"
            )
            return effective
        except Exception as e:
            # 매도 직후 KIS API가 실패하면 _krw_acc_cash만으로는 매도대금이 빠져 cap을
            # 잘못 줄임 (2026-05-27 실제 버그 사례). 이번 회차 매도 성공분을 더해 보정.
            base_cash = self._krw_acc_cash.get(acc_name, float("inf"))
            sell_credit = self._recent_sell_proceeds_krw.get(acc_name, 0.0)
            # 수수료·슬리피지 여유분 2% 차감
            fallback = (base_cash + sell_credit) * 0.98 if base_cash != float("inf") else base_cash
            sell_note = f" + 이번 매도 {sell_credit:,.0f}" if sell_credit > 0 else ""
            print(
                f"  [경고] {acc_name} 주문가능금액 조회 실패: {type(e).__name__}: {e}"
                f" → fallback {fallback:,.0f}원 (현금 {base_cash:,.0f}{sell_note}, ×0.98)"
            )
            return fallback

    def _wait_for_fill(
        self,
        order,
        reorder: Callable[[float], object],
        ticker: str,
        action: str,
        qty: int,
        price: float,
        currency: str,
        max_retries: int = 10,
        retry_interval: int = 100,
    ) -> Tuple[bool, float]:
        """미체결 주문 대기 루프. retry_interval초마다 가격 조정 후 재주문, max_retries회 초과 시 취소 후 타임아웃.

        재주문 전 이전 주문을 반드시 취소한다.
        취소하지 않으면 여러 주문이 동시에 시장에 열려 있다가 다음 날 일괄 체결될 수 있다.
        """
        rate = 1.001 if action == "buy" else 0.999
        cnt = 0
        retries = 0

        def _try_cancel(o) -> None:
            try:
                o.cancel()
            except Exception as ce:
                print(f"  [경고] {ticker} 주문 취소 실패: {ce}")

        while order.pending:
            time.sleep(1)
            cnt += 1
            if cnt % retry_interval == 0:
                _try_cancel(order)
                price = _adjust_tick(price * rate, currency)
                order = reorder(price)
                retries += 1
                if retries >= max_retries:
                    _try_cancel(order)
                    print(f"  [timeout] {ticker}: 주문 시간 초과 ({max_retries}회 재시도)")
                    _append_order_log(ticker, action, qty, price, currency, self.usd_krw, "timeout")
                    return False, price
        return True, price

    def sell_orphans(self, side: str, tracker: Optional[SettlementTracker] = None) -> List[str]:
        """
        유니버스에 없는 보유 종목을 전량 매도한다.

        get_portfolio_state() 호출 후 채워진 _orphan_holdings를 사용.
        side: "all" | "krw" | "usd"
        """
        targets = {
            t: info for t, info in self._orphan_holdings.items()
            if side == "all"
            or (side == "krw" and info["currency"] == "KRW")
            or (side == "usd" and info["currency"] == "USD")
        }
        if not targets:
            return []

        # 계좌별로 잔고를 재조회해 현재 수량을 확보한다
        # (portfolio_state 수집 시점과 매도 시점 사이의 가격 변화로 인한 수량 오차 방지)
        live_qtys: Dict[str, int] = {}
        fetched_keys: set = set()
        for info in targets.values():
            acc_name, currency = info["acc_name"], info["currency"]
            key = (acc_name, currency)
            if key in fetched_keys:
                continue
            fetched_keys.add(key)
            client = self._clients[acc_name]
            try:
                for s in _fetch_balance_with_retry(client, currency, acc_name).stocks:
                    mkt_cur = MARKET_TO_CURRENCY.get(s.market, "KRW")
                    if mkt_cur != currency or s.symbol in self.universe:
                        continue
                    orderable_val = getattr(s, "orderable", None)
                    qty_val = orderable_val if orderable_val is not None else getattr(s, "qty", None)
                    if qty_val is not None:
                        live_qtys[s.symbol] = int(float(qty_val))
            except Exception as e:
                print(f"    [경고] {acc_name} 잔고 재조회 최종 실패: {e}")

        results: List[str] = []
        for ticker, info in targets.items():
            currency = info["currency"]
            client = self._clients[info["acc_name"]]

            qty = live_qtys.get(ticker, 0)
            if qty > 0:
                # 정확한 수량으로 전량 매도
                result = self._execute_exact_sell(ticker, currency, qty, client)
            else:
                # qty API 미지원 시 보유 평가금액으로 추정 (소액이면 생략)
                amount_krw = info["amount_krw"]
                if amount_krw < self.min_order_krw:
                    print(f"  [skip] {ticker}: 소액 ({amount_krw:,.0f}원)")
                    continue
                result = self._execute_order(ticker, currency, -amount_krw, info["acc_name"])

            if result:
                results.append(result)

        return results

    def _execute_exact_sell(
        self,
        ticker: str,
        currency: str,
        qty: int,
        client: "pykis.PyKis",
    ) -> Optional[str]:
        """지정 수량을 정확히 전량 매도한다 (유니버스 외 종목 정리 전용)."""
        try:
            stock = client.stock(ticker)
            price = self._get_price(stock, "sell", currency)
            if price <= 0:
                print(f"  [skip] {ticker}: 가격 조회 실패")
                return None

            print(f"  sell {_label_ticker(ticker, self.universe)} {qty}주 @ {price:,.2f} {currency}  [유니버스 외 정리]")
            order = stock.sell(qty=qty, price=price)
            filled, price = self._wait_for_fill(
                order, lambda p: stock.sell(qty=qty, price=p),
                ticker, "sell", qty, price, currency,
            )
            if not filled:
                return f"[timeout] 매도 {ticker} {qty}주"

            _append_order_log(ticker, "sell", qty, price, currency, self.usd_krw, "ok")
            return f"매도 {_label_ticker(ticker, self.universe)} {qty}주 @ {price:,.2f} {currency} [정리]"

        except Exception as e:
            print(f"  [error] {ticker}: {e}")
            _append_order_log(ticker, "sell", qty, 0.0, currency, self.usd_krw, f"error:{e}")
            if self.messenger:
                self.messenger.send_order_error(ticker, e)
            return f"[오류] {ticker}: {e}"

    def _execute_order(
        self,
        ticker: str,
        currency: str,
        amount_diff_krw: float,
        acc_name: Optional[str] = None,
    ) -> Optional[str]:
        """지정 종목을 KRW 환산 금액 기준으로 매수/매도한다. 결과 문자열을 반환한다."""
        action = "buy" if amount_diff_krw > 0 else "sell"
        amount_local = (
            abs(amount_diff_krw) / self.usd_krw
            if currency == "USD"
            else abs(amount_diff_krw)
        )
        qty, price = 0, 0.0

        try:
            client = self._get_client(ticker, acc_name)
            stock = client.stock(ticker)
            price = self._get_price(stock, action, currency)

            if price <= 0:
                print(f"  [skip] {ticker}: 가격 조회 실패")
                return None

            qty = math.floor(amount_local / price)
            if qty <= 0:
                print(f"  [skip] {ticker}: 수량 0")
                return None

            # 매도 시 실제 보유 수량으로 상한 설정
            # 동시 실행이나 이전 주문의 부분 체결로 실제보다 많은 수량을 매도하는 것을 방지한다.
            if action == "sell":
                held_qty = self._get_held_qty(client, ticker, currency, price, acc_name=acc_name or ticker)
                if held_qty == 0:
                    print(f"  [skip] {ticker}: 실보유 수량 없음")
                    return None
                if qty > held_qty:
                    print(f"  [경고] {ticker}: 주문 수량 {qty}주 → 실보유 {held_qty}주로 조정")
                    qty = held_qty

            print(f"  {action} {_label_ticker(ticker, self.universe)} {qty}주 @ {price:,.2f} {currency}")

            order_fn = getattr(stock, action)
            order = order_fn(qty=qty, price=price)
            filled, price = self._wait_for_fill(
                order, lambda p: order_fn(qty=qty, price=p),
                ticker, action, qty, price, currency,
            )
            if not filled:
                return f"[timeout] {action} {ticker} {qty}주"

            label = "매수" if action == "buy" else "매도"
            _append_order_log(ticker, action, qty, price, currency, self.usd_krw, "ok")
            return f"{label} {_label_ticker(ticker, self.universe)} {qty}주 @ {price:,.2f} {currency}"

        except Exception as e:
            print(f"  [error] {ticker}: {e}")
            _append_order_log(ticker, action, qty, price, currency, self.usd_krw, f"error:{e}")
            if self.messenger:
                self.messenger.send_order_error(ticker, e)
            return f"[오류] {ticker}: {e}"
