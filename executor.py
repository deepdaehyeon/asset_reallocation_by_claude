"""KIS 기반 멀티 계좌 리밸런싱 실행 레이어."""
import json
import math
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pykis
import yaml

from portfolio import compute_drift
from messenger import Messenger

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


def _load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"peak_krw": 0.0}


def _save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


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
        self.usd_krw: float = float(
            config["rebalancing"].get("usd_krw_fallback", 1380.0)
        )
        self.min_order_krw: float = float(
            config["rebalancing"].get("min_order_krw", 10_000)
        )
        self.messenger = messenger
        auth_path = auth_path or Path(__file__).parent / "auth.yaml"
        self._clients = self._init_clients(auth_path)

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

    def get_portfolio_state(self) -> Tuple[float, Dict[str, float], float]:
        """
        전 계좌를 합산하여 (총자산_KRW, 현재비중, 드로우다운) 를 반환한다.

        현재비중: {ticker: fraction}  — 유니버스 기준 합산으로 정규화
        총자산_KRW: 유니버스 외 보유(정리 예정) 종목 포함 전체 평가금액
        드로우다운: 직전 고점 대비 낙폭 (0 이하 실수)
        """
        holdings_krw: Dict[str, float] = {}  # ticker → KRW 환산 금액
        total_cash_krw = 0.0

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

            # balance.deposits는 통화명 문자열 리스트 — orderable_amount로 현금 조회
            total_cash_krw += self._get_cash_krw(client, currency)

        # 유니버스 외 보유 종목(IAU·TSLY 등) 분리 및 경고
        universe_krw = {t: v for t, v in holdings_krw.items() if t in self.universe}
        orphan_krw = {t: v for t, v in holdings_krw.items() if t not in self.universe}

        if orphan_krw:
            total_all = sum(holdings_krw.values()) + total_cash_krw
            print("  [경고] 유니버스 외 보유 종목 (수동 정리 필요):")
            for t, v in orphan_krw.items():
                print(f"    {t}: {v:,.0f} KRW ({v/total_all*100:.1f}%)")

        # 비중 계산은 유니버스 + 현금 기준 (orphan 제외하여 drift 왜곡 방지)
        universe_total_krw = sum(universe_krw.values()) + total_cash_krw
        if universe_total_krw == 0:
            return 0.0, {}, 0.0

        current_weights = {t: v / universe_total_krw for t, v in universe_krw.items()}

        # 드로우다운은 전체 자산(orphan 포함)으로 계산
        total_krw = sum(holdings_krw.values()) + total_cash_krw
        state = _load_state()
        peak = max(state["peak_krw"], total_krw)
        state["peak_krw"] = peak
        _save_state(state)
        drawdown = (total_krw / peak - 1.0) if peak > 0 else 0.0

        # 반환값 total은 universe_total_krw — _build_orders의 주문 금액 계산 기준
        return universe_total_krw, current_weights, drawdown

    # ──────────────────────────────────────────────
    # 리밸런싱 실행
    # ──────────────────────────────────────────────

    def rebalance(
        self,
        current_weights: Dict[str, float],
        target_weights: Dict[str, float],
        total_value_krw: float,
        threshold: float,
    ) -> List[str]:
        """
        drift가 threshold를 초과할 때만 리밸런싱을 실행한다.
        sell 먼저, buy 나중 순서로 주문한다.
        주문 결과 문자열 리스트를 반환한다.
        """
        drift = compute_drift(current_weights, target_weights)
        print(f"총 drift: {drift*100:.1f}%  (임계값: {threshold*100:.0f}%)")

        if drift < threshold:
            print("→ 리밸런싱 불필요")
            return []

        orders = self._build_orders(current_weights, target_weights, total_value_krw)
        # 매도 우선 (amount < 0 이 앞에 오도록 정렬)
        orders.sort(key=lambda x: x[2])

        print(f"→ 주문 {len(orders)}건 실행")
        order_log: List[str] = []
        for ticker, currency, amount_diff_krw in orders:
            result = self._execute_order(ticker, currency, amount_diff_krw)
            if result:
                order_log.append(result)
        return order_log

    def _build_orders(
        self,
        current: Dict[str, float],
        target: Dict[str, float],
        total_krw: float,
    ) -> List[Tuple[str, str, float]]:
        """(ticker, currency, amount_diff_krw) 주문 목록을 생성한다."""
        orders = []
        for ticker, meta in self.universe.items():
            current_amt = current.get(ticker, 0.0) * total_krw
            target_amt = target.get(ticker, 0.0) * total_krw
            diff = target_amt - current_amt
            if abs(diff) >= self.min_order_krw:
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

    def _execute_order(
        self,
        ticker: str,
        currency: str,
        amount_diff_krw: float,
    ) -> Optional[str]:
        """지정 종목을 KRW 환산 금액 기준으로 매수/매도한다. 결과 문자열을 반환한다."""
        action = "buy" if amount_diff_krw > 0 else "sell"
        amount_local = (
            abs(amount_diff_krw) / self.usd_krw
            if currency == "USD"
            else abs(amount_diff_krw)
        )

        try:
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

            # 미체결 시 가격 조정 (asset_allocator 방식 동일)
            rate = 1.001 if action == "buy" else 0.999
            cnt = 0
            while order.pending:
                time.sleep(1)
                cnt += 1
                if cnt % 100 == 0:
                    price = _adjust_tick(price * rate, currency)
                    order = order_fn(qty=qty, price=price)
                if cnt >= 1000:
                    print(f"  [timeout] {ticker}: 주문 시간 초과")
                    return f"[timeout] {action} {ticker} {qty}주"

            label = "매수" if action == "buy" else "매도"
            return f"{label} {ticker} {qty}주 @ {price:,.2f} {currency}"

        except Exception as e:
            print(f"  [error] {ticker}: {e}")
            if self.messenger:
                self.messenger.send_order_error(ticker, e)
            return f"[오류] {ticker}: {e}"
