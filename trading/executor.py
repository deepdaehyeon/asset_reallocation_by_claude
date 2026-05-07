"""KIS 기반 멀티 계좌 리밸런싱 실행 레이어."""
import csv
import json
import math
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

# 드로우다운 추적용 상태 파일
STATE_FILE = Path(__file__).parent / "state.json"

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


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"peak_krw": 0.0}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


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
    전체 계좌를 읽어 통합 비중을 계산하고,
    config의 exec_account 지정에 따라 주문을 분배한다.
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

    @staticmethod
    def _fetch_usd_krw(fallback: float) -> float:
        """실시간 USD/KRW 환율을 조회한다. 실패 시 fallback 사용."""
        try:
            from fetcher import fetch_usd_krw
            rate = fetch_usd_krw(fallback)
            print(f"    USD/KRW: {rate:,.1f} (실시간)")
            return rate
        except Exception:
            print(f"    USD/KRW: {fallback:,.1f} (폴백)")
            return fallback

    # ──────────────────────────────────────────────
    # 초기화
    # ──────────────────────────────────────────────

    def _init_clients(self, auth_path: Path) -> Dict[str, pykis.PyKis]:
        """config의 accounts 정의를 기반으로 pykis 클라이언트를 생성한다."""
        with open(auth_path) as f:
            auth = yaml.safe_load(f)

        clients: Dict[str, pykis.PyKis] = {}
        seen_acc: Dict[str, pykis.PyKis] = {}  # acc_no → 이미 생성된 클라이언트 재사용

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
    # 포트폴리오 상태 조회
    # ──────────────────────────────────────────────

    def _get_cash_krw(self, client: pykis.PyKis, currency: str) -> float:
        """orderable_amount 프록시로 현금 잔고를 조회한다 (asset_allocator 방식)."""
        proxy = "379800" if currency == "KRW" else "QQQ"
        try:
            amount = float(client.stock(proxy).orderable_amount(price=1).amount)
            return amount * self.usd_krw if currency == "USD" else amount
        except Exception:
            return 0.0

    def get_portfolio_state(self) -> Tuple[float, float, float, Dict[str, float], float]:
        """
        전 계좌를 합산하여 (total_krw, total_usd_krw, total_krw_only, 현재비중, 드로우다운) 반환.

        total_krw      : 유니버스 기준 전체 (USD+KRW 합산, KRW 환산)
        total_usd_krw  : USD 계좌 총액 (KRW 환산)
        total_krw_only : KRW 계좌 총액
        현재비중       : {ticker: fraction of total_krw}  — drift·출력 기준
        드로우다운     : 직전 고점 대비 낙폭 (0 이하 실수)
        """
        holdings_krw: Dict[str, float] = {}  # ticker → KRW 환산 금액
        cash_by_currency: Dict[str, float] = {"KRW": 0.0, "USD": 0.0}

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
            for stock in balance.stocks:
                ticker = stock.symbol
                mkt_currency = MARKET_TO_CURRENCY.get(stock.market, "KRW")
                if mkt_currency != currency:
                    continue
                amt = float(stock.amount)
                krw_amt = amt * self.usd_krw if currency == "USD" else amt
                holdings_krw[ticker] = holdings_krw.get(ticker, 0.0) + krw_amt

                # 유니버스 외 종목 기록 (acc_name 포함)
                if ticker not in self.universe and krw_amt > 0:
                    self._orphan_holdings[ticker] = {
                        "currency":   currency,
                        "acc_name":   acc_name,
                        "amount_krw": krw_amt,
                    }

            cash_by_currency[currency] = (
                cash_by_currency.get(currency, 0.0)
                + self._get_cash_krw(client, currency)
            )

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

        # 드로우다운은 전체 자산(orphan 포함)으로 계산
        total_all_krw = sum(holdings_krw.values()) + sum(cash_by_currency.values())
        state = load_state()
        peak = max(state.get("peak_krw", 0.0), total_all_krw)
        state["peak_krw"] = peak
        save_state(state)
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

        # side 필터: 해당 계좌 종목만
        if side == "krw":
            all_orders = [(t, c, a) for t, c, a in all_orders if c == "KRW"]
        elif side == "usd":
            all_orders = [(t, c, a) for t, c, a in all_orders if c == "USD"]

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

        # 매도 우선 정렬
        immediate.sort(key=lambda x: x[2])

        sell_cnt = sum(1 for _, _, a in immediate if a < 0)
        buy_cnt = sum(1 for _, _, a in immediate if a > 0)
        side_label = f" [{side.upper()}]" if side != "all" else ""
        print(f"→{side_label} 즉시 실행 {len(immediate)}건 (매도 {sell_cnt}, 매수 {buy_cnt}), 지연 매수 {len(deferred)}건")

        order_log: List[str] = []
        for ticker, currency, amount_diff_krw in immediate:
            result = self._execute_order(ticker, currency, amount_diff_krw)
            if result:
                order_log.append(result)
                if tracker and amount_diff_krw < 0:
                    tracker.record_sell(ticker, abs(amount_diff_krw), currency)

        return order_log, deferred

    def _split_buy_orders(
        self,
        orders: List[Tuple[str, str, float]],
        current_weights: Dict[str, float],
        total_krw: float,
        buffer_tickers: List[str],
    ) -> Tuple[List[Tuple[str, str, float]], List[dict]]:
        """
        매수 주문을 버퍼 여유 내 즉시 실행과 지연 실행으로 분류한다.

        현재 버퍼 자산(469830·SHY) 평가금액을 즉시 가용 예산으로 사용.
        큰 매수부터 greedy하게 할당한다.
        """
        sells = [(t, c, a) for t, c, a in orders if a < 0]
        buys = [(t, c, a) for t, c, a in orders if a > 0]

        # 현재 버퍼 가용액 (KRW 환산)
        buffer_available = sum(current_weights.get(bt, 0.0) for bt in buffer_tickers) * total_krw

        immediate = list(sells)
        deferred: List[dict] = []
        used = 0.0

        for ticker, currency, amount in sorted(buys, key=lambda x: -abs(x[2])):
            buy_krw = abs(amount)
            if used + buy_krw <= buffer_available:
                immediate.append((ticker, currency, amount))
                used += buy_krw
            else:
                deferred.append({"ticker": ticker, "amount_krw": buy_krw, "currency": currency})

        if deferred:
            print(f"    버퍼 가용액: {buffer_available:,.0f}원 / 전체 매수: {sum(abs(a) for _,_,a in buys):,.0f}원")

        return immediate, deferred

    def _build_orders(
        self,
        current: Dict[str, float],
        target_usd: Dict[str, float],
        target_krw: Dict[str, float],
        total_usd_krw: float,
        total_krw_only: float,
    ) -> List[Tuple[str, str, float]]:
        """
        (ticker, currency, amount_diff_krw) 주문 목록 생성.

        각 종목의 목표금액은 계좌별 총액 기준:
          USD 종목 → target_usd[t] × total_usd_krw
          KRW 종목 → target_krw[t] × total_krw_only

        per_ticker_drift_threshold: 개별 종목의 전체 포트폴리오 대비 이탈이
        이 값 미만이면 거래 제외 (불필요한 소규모 거래 방지).
        """
        total_krw = total_usd_krw + total_krw_only
        per_ticker_thr = float(
            self.config["rebalancing"].get("per_ticker_drift_threshold", 0.0)
        )
        orders = []
        for ticker, meta in self.universe.items():
            current_amt = current.get(ticker, 0.0) * total_krw
            if meta["currency"] == "USD":
                target_amt = target_usd.get(ticker, 0.0) * total_usd_krw
            else:
                target_amt = target_krw.get(ticker, 0.0) * total_krw_only
            diff = target_amt - current_amt
            diff_frac = abs(diff) / total_krw if total_krw > 0 else 0.0
            if abs(diff) >= self.min_order_krw and (
                per_ticker_thr <= 0 or diff_frac >= per_ticker_thr
            ):
                orders.append((ticker, meta["currency"], diff))
        return orders

    # ──────────────────────────────────────────────
    # 단일 종목 주문
    # ──────────────────────────────────────────────

    def _get_client(self, ticker: str) -> pykis.PyKis:
        exec_account = self.universe[ticker]["exec_account"]
        return self._clients[exec_account]

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

    def _wait_for_fill(
        self,
        order,
        reorder: Callable[[float], object],
        ticker: str,
        action: str,
        qty: int,
        price: float,
        currency: str,
    ) -> Tuple[bool, float]:
        """미체결 주문 대기 루프. 100초마다 가격 조정 재주문, 1000초 초과 시 타임아웃."""
        rate = 1.001 if action == "buy" else 0.999
        cnt = 0
        while order.pending:
            time.sleep(1)
            cnt += 1
            if cnt % 100 == 0:
                price = _adjust_tick(price * rate, currency)
                order = reorder(price)
            if cnt >= 1000:
                print(f"  [timeout] {ticker}: 주문 시간 초과")
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
                result = self._execute_order(ticker, currency, -amount_krw, client=client)

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
        client: Optional["pykis.PyKis"] = None,
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
            if client is None:
                client = self._get_client(ticker)
            stock = client.stock(ticker)
            price = self._get_price(stock, action, currency)

            if price <= 0:
                print(f"  [skip] {ticker}: 가격 조회 실패")
                return None

            qty = math.floor(amount_local / price)
            if qty <= 0:
                print(f"  [skip] {ticker}: 수량 0")
                return None

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
