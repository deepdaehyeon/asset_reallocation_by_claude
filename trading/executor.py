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
        # KRW 계좌별 총액: {acc_name: total_krw}
        self._krw_acc_totals: Dict[str, float] = {}
        # 이번 실행에서 주문된 금액(원화 환산) — run.py에서 월간 누적에 합산
        self._last_run_traded_krw: float = 0.0

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

        acc_no가 같더라도 currency가 다르면 별도 인스턴스를 생성한다.
        → KRW_1(KRW)과 USD(USD)는 동일 acc_no여도 독립 클라이언트를 가진다.
        """
        with open(auth_path) as f:
            auth = yaml.safe_load(f)

        clients: Dict[str, pykis.PyKis] = {}
        seen_acc: Dict[tuple, pykis.PyKis] = {}  # (acc_no, currency) → client

        for acc_name, acc_cfg in self.config["accounts"].items():
            acc_no = acc_cfg["acc_no"]
            currency = acc_cfg["currency"]
            key = (acc_no, currency)
            if key not in seen_acc:
                creds = auth[acc_no]
                seen_acc[key] = pykis.PyKis(
                    id=creds["id"],
                    appkey=creds["appkey"],
                    secretkey=creds["secretkey"],
                    account=acc_no,
                    keep_token=True,
                )
            clients[acc_name] = seen_acc[key]

        return clients

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

        processed_acc: set = set()
        for acc_name, acc_cfg in self.config["accounts"].items():
            acc_no = acc_cfg["acc_no"]
            currency = acc_cfg["currency"]
            client = self._clients[acc_name]

            # 동일 acc_no + currency 조합은 한 번만 조회
            key = (acc_no, currency)
            if key in processed_acc:
                continue
            processed_acc.add(key)

            balance = client.account().balance()
            acc_stock_holdings: Dict[str, float] = {}

            for stock in balance.stocks:
                ticker = stock.symbol
                mkt_currency = MARKET_TO_CURRENCY.get(stock.market, "KRW")
                if mkt_currency != currency:
                    continue
                amt = float(stock.current_amount)
                krw_amt = amt * self.usd_krw if currency == "USD" else amt
                holdings_krw[ticker] = holdings_krw.get(ticker, 0.0) + krw_amt

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
                raise RuntimeError(f"예수금 조회 실패 ({acc_name}/{currency}): deposit is None")
            cash = float(deposit.amount) * self.usd_krw if currency == "USD" else float(deposit.amount)
            cash_by_currency[currency] = cash_by_currency.get(currency, 0.0) + cash

            if currency == "KRW":
                krw_acc_holdings[acc_name] = acc_stock_holdings
                krw_acc_cash[acc_name] = cash

        # KRW 계좌별 총액 저장 (주식 + 현금)
        self._krw_acc_holdings = krw_acc_holdings
        self._krw_acc_totals = {
            acc: sum(krw_acc_holdings.get(acc, {}).values()) + krw_acc_cash.get(acc, 0.0)
            for acc in krw_acc_holdings
        }

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

        # T+2 미결제 매도대금 보정
        # 체결된 매도 주식은 이미 잔고에서 빠졌지만 현금은 결제일까지 미입금.
        # 보정하지 않으면 총자산이 낮게 잡혀 드로우다운·목표비중이 왜곡된다.
        state = load_state()
        _tmp_tracker = SettlementTracker(state)
        pending_krw = _tmp_tracker.pending_krw("KRW")
        pending_usd = _tmp_tracker.pending_krw("USD")

        if pending_krw > 0:
            total_krw_only += pending_krw
            # KRW 계좌별 총액도 보정 (비율 유지로 비례 배분)
            if self._krw_acc_totals:
                krw_before = sum(self._krw_acc_totals.values())
                if krw_before > 0:
                    for acc in self._krw_acc_totals:
                        self._krw_acc_totals[acc] += pending_krw * (self._krw_acc_totals[acc] / krw_before)

        if pending_usd > 0:
            total_usd_krw += pending_usd

        universe_total_krw = total_usd_krw + total_krw_only

        if universe_total_krw == 0:
            return 0.0, 0.0, 0.0, {}, 0.0

        # 현재 비중 = 전체 대비 (drift·출력용)
        current_weights = {t: v / universe_total_krw for t, v in universe_krw.items()}

        # 드로우다운은 전체 자산(orphan 포함, 미결제 보정)으로 계산
        total_all_krw = sum(holdings_krw.values()) + sum(cash_by_currency.values())
        total_all_krw_adj = total_all_krw + pending_krw + pending_usd
        if pending_krw + pending_usd > 0:
            print(f"    T+2 미결제 보정: +{pending_krw + pending_usd:,.0f}원 → 실질 총자산 {total_all_krw_adj:,.0f}원")

        peak = max(state.get("peak_krw", 0.0), total_all_krw_adj)
        self._peak_krw = peak  # 호출 측(run.py)이 state에 저장
        drawdown = (total_all_krw_adj / peak - 1.0) if peak > 0 else 0.0

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
    ) -> Tuple[List[str], List[dict]]:
        """
        리밸런싱을 실행한다.

        side: "all" | "krw" | "usd"
          - "krw" / "usd": 해당 계좌 종목만 주문 생성 (monitor에서 트리거 확정 후 호출)
          - threshold=0.0 으로 호출하면 drift 재확인 없이 바로 실행

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

        all_orders = self._build_orders(current_weights, target_usd, target_krw, total_usd_krw, total_krw_only)

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

        self._last_run_traded_krw = side_order_krw

        buffer_tickers = self.config.get("settlement", {}).get("buffer_tickers", [])
        # USD 단독 실행 시 KRW 버퍼 로직 불필요
        if side == "usd":
            immediate, deferred = all_orders, []
        elif tracker and buffer_tickers:
            immediate, deferred = self._split_buy_orders(
                all_orders, current_weights, total_value_krw, buffer_tickers
            )
        else:
            immediate, deferred = all_orders, []

        # 매도 우선 정렬 (amount_diff_krw 기준 — 음수가 앞)
        immediate.sort(key=lambda x: x[2])

        sell_cnt = sum(1 for _, _, a, _ in immediate if a < 0)
        buy_cnt = sum(1 for _, _, a, _ in immediate if a > 0)
        side_label = f" [{side.upper()}]" if side != "all" else ""
        print(f"→{side_label} 즉시 실행 {len(immediate)}건 (매도 {sell_cnt}, 매수 {buy_cnt}), 지연 매수 {len(deferred)}건")

        order_log: List[str] = []
        failed_usd_buys: List[dict] = []
        for ticker, currency, amount_diff_krw, acc_name in immediate:
            result = self._execute_order(ticker, currency, amount_diff_krw, acc_name)
            if result:
                order_log.append(result)
                if tracker and amount_diff_krw < 0:
                    tracker.record_sell(ticker, abs(amount_diff_krw), currency)
                # USD 매수 실패 중 "현금/매수가능금액 부족"으로 보이는 케이스만 deferred로 기록
                # (timeout/기타 오류는 유동성/세션/가격갱신 문제일 수 있어 합성노출로 자동 흡수하지 않음)
                if amount_diff_krw > 0 and currency == "USD" and result.startswith("[오류]"):
                    if _looks_like_insufficient_funds(result):
                        failed_usd_buys.append(
                            {
                                "ticker": ticker,
                                "amount_krw": abs(amount_diff_krw),
                                "currency": "USD",
                            }
                        )
            else:
                # result가 None이면 'skip'일 수 있어(수량 0 등) 기본적으로 deferred로 기록하지 않는다.
                pass

        # 기존 버퍼 초과로 인한 deferred + USD 매수 실패분 deferred를 합쳐 반환
        if failed_usd_buys:
            print(f"    [USD 실패] 매수 {len(failed_usd_buys)}건 → 다음 KRW 실행에서 합성 노출로 대체")
        return order_log, deferred + failed_usd_buys

    def _split_buy_orders(
        self,
        orders: List[Tuple[str, str, float, str]],
        current_weights: Dict[str, float],
        total_krw: float,
        buffer_tickers: List[str],
    ) -> Tuple[List[Tuple[str, str, float, str]], List[dict]]:
        """
        매수 주문을 버퍼 여유 내 즉시 실행과 지연 실행으로 분류한다.

        버퍼(469830) 가용액은 계좌별로 독립 계산한다.
        계좌 A의 버퍼는 계좌 B의 매수에 쓸 수 없다 (계좌 간 현금 이동 없음).
        큰 매수부터 greedy하게 할당한다.
        """
        sells = [(t, c, a, acc) for t, c, a, acc in orders if a < 0]
        buys = [(t, c, a, acc) for t, c, a, acc in orders if a > 0]

        # 계좌별 버퍼 가용액: 각 계좌 내 buffer_tickers의 실제 보유금액
        acc_buffer: Dict[str, float] = {
            acc: sum(self._krw_acc_holdings.get(acc, {}).get(bt, 0.0) for bt in buffer_tickers)
            for acc in self._krw_acc_totals
        }

        immediate = list(sells)
        deferred: List[dict] = []
        acc_used: Dict[str, float] = {}

        for ticker, currency, amount, acc_name in sorted(buys, key=lambda x: -abs(x[2])):
            buy_krw = abs(amount)
            buf_available = acc_buffer.get(acc_name, 0.0)
            used = acc_used.get(acc_name, 0.0)
            if used + buy_krw <= buf_available:
                immediate.append((ticker, currency, amount, acc_name))
                acc_used[acc_name] = used + buy_krw
            else:
                deferred.append({"ticker": ticker, "amount_krw": buy_krw, "currency": currency})

        if deferred:
            total_buf = sum(acc_buffer.values())
            total_buy = sum(abs(a) for _, _, a, _ in buys)
            print(f"    버퍼 가용액: {total_buf:,.0f}원 / 전체 매수: {total_buy:,.0f}원")
            for acc, buf in acc_buffer.items():
                used = acc_used.get(acc, 0.0)
                print(f"      {acc}: 버퍼 {buf:,.0f}원 (사용 {used:,.0f}원)")

        return immediate, deferred

    def _build_orders(
        self,
        current: Dict[str, float],
        target_usd: Dict[str, float],
        target_krw: Dict[str, float],
        total_usd_krw: float,
        total_krw_only: float,
    ) -> List[Tuple[str, str, float, str]]:
        """
        (ticker, currency, amount_diff_krw, acc_name) 주문 목록 생성.

        USD 종목: target_usd[t] × total_usd_krw, exec_account 계좌로 실행.
        KRW 종목: 각 KRW 계좌의 잔고 비율에 비례해 분산 주문 생성.
          → KRW_1·KRW_2가 항상 동일 비중을 유지한다.

        per_ticker_drift_threshold: 개별 종목의 전체 포트폴리오 대비 이탈이
        이 값 미만이면 거래 제외 (불필요한 소규모 거래 방지).
        """
        total_krw = total_usd_krw + total_krw_only
        per_ticker_thr = float(
            self.config["rebalancing"].get("per_ticker_drift_threshold", 0.0)
        )
        orders: List[Tuple[str, str, float, str]] = []

        for ticker, meta in self.universe.items():
            currency = meta["currency"]

            if currency == "USD":
                current_amt = current.get(ticker, 0.0) * total_krw
                target_amt = target_usd.get(ticker, 0.0) * total_usd_krw
                diff = target_amt - current_amt
                diff_frac = abs(diff) / total_krw if total_krw > 0 else 0.0
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
                    diff_frac = abs(diff) / total_krw if total_krw > 0 else 0.0
                    if abs(diff) >= self.min_order_krw and (
                        per_ticker_thr <= 0 or diff_frac >= per_ticker_thr
                    ):
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

    def _get_held_qty(self, client: pykis.PyKis, ticker: str, currency: str, price: float) -> int:
        """매도 직전 실제 보유 수량을 조회한다. 조회 실패 시 RuntimeError를 발생시킨다."""
        try:
            for s in client.account().balance().stocks:
                if s.symbol != ticker:
                    continue
                # orderable = 매도가능수량 (잠긴 주식·미결제 수량 제외) — 가장 안전한 기준
                orderable_val = getattr(s, "orderable", None)
                if orderable_val is not None:
                    return int(float(orderable_val))
                qty_val = getattr(s, "qty", None)
                if qty_val is not None:
                    return int(float(qty_val))
                # orderable/qty 필드가 없으면 평가금액 ÷ 현재가로 추정
                amt = float(s.current_amount)
                if currency == "USD":
                    amt /= self.usd_krw
                return math.floor(amt / price) if price > 0 else 0
            return 0  # 잔고에 없음
        except Exception as e:
            raise RuntimeError(f"{ticker} 보유 수량 조회 실패: {e}") from e

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

    def sell_orphans(self, side: str) -> List[str]:
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
                for s in client.account().balance().stocks:
                    mkt_cur = MARKET_TO_CURRENCY.get(s.market, "KRW")
                    if mkt_cur != currency or s.symbol in self.universe:
                        continue
                    qty_val = getattr(s, "qty", None)
                    if qty_val is not None:
                        live_qtys[s.symbol] = int(qty_val)
            except Exception as e:
                print(f"    [경고] {acc_name} 잔고 재조회 실패: {e}")

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

            print(f"  sell {ticker} {qty}주 @ {price:,.2f} {currency}  [유니버스 외 정리]")
            order = stock.sell(qty=qty, price=price)
            filled, price = self._wait_for_fill(
                order, lambda p: stock.sell(qty=qty, price=p),
                ticker, "sell", qty, price, currency,
            )
            if not filled:
                return f"[timeout] 매도 {ticker} {qty}주"

            _append_order_log(ticker, "sell", qty, price, currency, self.usd_krw, "ok")
            return f"매도 {ticker} {qty}주 @ {price:,.2f} {currency} [정리]"

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
                held_qty = self._get_held_qty(client, ticker, currency, price)
                if held_qty == 0:
                    print(f"  [skip] {ticker}: 실보유 수량 없음")
                    return None
                if qty > held_qty:
                    print(f"  [경고] {ticker}: 주문 수량 {qty}주 → 실보유 {held_qty}주로 조정")
                    qty = held_qty

            print(f"  {action} {ticker} {qty}주 @ {price:,.2f} {currency}")

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
            return f"{label} {ticker} {qty}주 @ {price:,.2f} {currency}"

        except Exception as e:
            print(f"  [error] {ticker}: {e}")
            _append_order_log(ticker, action, qty, price, currency, self.usd_krw, f"error:{e}")
            if self.messenger:
                self.messenger.send_order_error(ticker, e)
            return f"[오류] {ticker}: {e}"
