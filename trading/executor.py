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
    DEFAULT_LOG_PATH as DEPOSIT_LOG_PATH,
    compute_net_flow,
    fetch_deposit_withdrawal_events,
)

# 입출금 자동감지 이상 경고 임계값 — 감지된 입출금으로 설명 안 되는 자산 변동이
# 이 비율을 넘으면 조용히 넘기지 않고 로그에 경고를 남긴다.
# 2026-08-18: kis_profits 역산이 실제 입출금을 net_flow≈0으로 놓친 사례(사용자 신고로 발견,
# 자동감지 자체는 침묵) → 감지 실패를 "알파로 착시"가 아니라 눈에 띄게 만드는 안전장치.
IO_ANOMALY_ALERT_THRESHOLD = 0.05  # 5%p

# 설명 안 되는 감소(출금 추정) 자동감지 결과를 적는 검토용 파일. deposits.csv와 달리
# fetch_deposit_withdrawal_events가 읽지 않으므로 낙폭(drawdown) 계산에 자동 반영되지
# 않는다 — 진짜 폭락을 출금으로 오인해 낙폭을 지워버리는 위험을 피하기 위함.
# 사람이 확인 후 진짜 출금이면 trading/logs/deposits.csv로 직접 옮긴다.
PENDING_WITHDRAWAL_REVIEW_PATH = Path(__file__).parent / "logs" / "deposits_pending_review.csv"

# 시장 코드 → 통화 매핑 (pykis stock.market 값 기준)
MARKET_TO_CURRENCY: Dict[str, str] = {
    "KRX":    "KRW",
    "CRYPTO": "KRW",
    "AMEX":   "USD",
    "NASDAQ": "USD",
    "NYSE":   "USD",
}


def _compute_usd_nav_cash(
    withdrawable_amount: object,
    deposit_amount: object,
    raw_balance: object,
) -> Tuple[float, float, float]:
    """USD NAV 현금을 ``기준 예수금 + 미결제 매도 - 미결제 매수``로 계산한다.

    KIS 해외 체결기준현재잔고는 당일 매수 종목을 ``stocks``에 즉시 포함하지만,
    ``frcr_drwg_psbl_amt_1``에는 당일 미결제 매수·매도가 모두 반영되지 않을 수 있다.
    따라서 총자산(NAV)에서는 gross 매도액만 더하지 말고 미결제 순매매만 반영해야 한다.

    Returns
    -------
    (cash_usd, unsettled_sell_usd, unsettled_buy_usd)
    """
    cash_usd = float(withdrawable_amount)
    if cash_usd <= 0:
        cash_usd = float(deposit_amount)

    if not isinstance(raw_balance, dict):
        return cash_usd, 0.0, 0.0

    out2 = raw_balance.get("output2")
    if isinstance(out2, list):
        out2 = out2[0] if out2 else None
    if not isinstance(out2, dict):
        return cash_usd, 0.0, 0.0

    def _nonnegative_amount(key: str) -> float:
        value = out2.get(key)
        if value in (None, ""):
            return 0.0
        amount = float(value)
        if not math.isfinite(amount) or amount < 0:
            raise ValueError(f"{key} 비정상 값: {value}")
        return amount

    unsettled_sell_usd = _nonnegative_amount("frcr_sll_amt_smtl")
    unsettled_buy_usd = _nonnegative_amount("frcr_buy_amt_smtl")
    cash_usd += unsettled_sell_usd - unsettled_buy_usd
    return cash_usd, unsettled_sell_usd, unsettled_buy_usd


# 상태 파일 경로
STATE_FILE = Path(__file__).parent / "state.json"   # 레거시 JSON (읽기 전용 폴백)
STATE_DB   = Path(__file__).parent / "state.db"     # SQLite (primary)

# 주문 로그 CSV
ORDER_LOG_FILE = Path(__file__).parent / "logs" / "orders.csv"
_ORDER_LOG_HEADERS = [
    "datetime", "ticker", "action", "qty", "price", "currency", "amount_krw", "status"
]

_IO_EVENT_CSV_HEADERS = ["ts", "acc_name", "amount_krw", "kind", "note", "id"]


def _append_io_event_csv(
    path: Path,
    ts: datetime,
    acc_name: str,
    amount_krw: float,
    kind: str,
    note: str,
) -> None:
    """입출금 이벤트를 deposits.csv 형식(ts,acc_name,amount_krw,kind,note,id)으로 append.

    deposit_log.read_events_from_csv가 읽는 형식과 동일하게 맞춘다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    row = {
        "ts": ts.isoformat(timespec="seconds"),
        "acc_name": acc_name,
        "amount_krw": f"{amount_krw:.0f}",
        "kind": kind,
        "note": note,
        "id": "",
    }
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_IO_EVENT_CSV_HEADERS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


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
                elif "EGW00215" in msg or "초당 거래건수" in msg:
                    wait = 2 ** (attempt + 1)  # 2s → 4s: 원장 endpoint는 일반 호출보다 보수적
                    print(
                        f"  [재시도] {acc_name} 원장 초당 제한 EGW00215 "
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


def _net_equivalent_orders(
    orders: List[Tuple[str, str, float, str]],
    groups: Optional[list],
    min_order_krw: float,
) -> List[Tuple[str, str, float, str]]:
    """같은 equivalence group 내 상쇄되는 매수/매도를 상계해 통화 왕복(wash) 매매를 제거한다.

    orders: [(ticker, currency, diff_krw, acc_name)] — diff>0 매수, <0 매도 (모두 KRW 환산).
    같은 그룹(예: QQQ↔379810 나스닥)의 순매수·순매도 중 겹치는 부분(min)을 양쪽에서 비례
    축소한다. 그룹 순노출 변화(net)는 보존하고 상쇄분만 제거 → **매매를 줄이기만** 하므로 안전
    (경제적으로 동일한 자산을 통화만 바꿔 팔고 되사는 것을 방지). side 필터 전에 호출해야
    USD 매수와 KRW 매도가 서로 상계된다. groups 없으면 원본 그대로 반환.
    """
    if not groups:
        return orders
    t2g: dict = {}
    for i, g in enumerate(groups):
        for t in g:
            t2g[t] = i
    result: List[Tuple[str, str, float, str]] = []
    by_group: dict = {}
    for o in orders:
        gid = t2g.get(o[0])
        if gid is None:
            result.append(o)          # 그룹 밖은 그대로 통과
        else:
            by_group.setdefault(gid, []).append(o)
    for gos in by_group.values():
        total_buy = sum(o[2] for o in gos if o[2] > 0)
        total_sell = -sum(o[2] for o in gos if o[2] < 0)
        offset = min(total_buy, total_sell)
        if offset <= 0:               # 한 방향뿐이면 상쇄 없음
            result.extend(gos)
            continue
        for (tk, cur, diff, acc) in gos:
            if diff > 0 and total_buy > 0:
                new_diff = diff * (1.0 - offset / total_buy)
            elif diff < 0 and total_sell > 0:
                new_diff = diff * (1.0 - offset / total_sell)
            else:
                new_diff = diff
            if abs(new_diff) >= min_order_krw:
                result.append((tk, cur, new_diff, acc))
    return result


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
        # 유동성 얇은 종목별 주문 분할·재시도 설정 {ticker: {max_order_krw, max_retries, price_chase, retry_interval_s}}
        self.illiquid_cfg: Dict[str, dict] = config["rebalancing"].get(
            "illiquid_order_handling", {}
        )
        # 리밸런싱 대상에서 제외할 KRW 계좌 = "없는 계좌"로 취급한다.
        # 주문 생성·비중·drift는 물론 성과 지표(총자산·peak·드로우다운·알파·원금)에서도
        # 전부 제외된다(get_portfolio_state). 세금 등으로 동결한 계좌의 잔고가 성과에
        # 섞이면 입금이 알파로 둔갑하고, 굴리지 않는 현금이 수익률 분모에 들어간다.
        # 2026-08-01 성과 제외로 전환 — 그 전에는 비중·drift에서만 제외했다.
        self.excluded_krw_accounts: set = set(
            config["rebalancing"].get("excluded_krw_accounts", [])
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
        # 포트폴리오 조회에서 확보한 매도가능수량 캐시. 종목마다 잔고 API를 다시
        # 호출해 EGW00215를 유발하던 패턴을 없애고, 체결 시 로컬에서 차감한다.
        self._held_qty_cache: Dict[Tuple[str, str, str], int] = {}
        self._last_order_unfilled: Optional[dict] = None
        self._last_order_filled_amount_krw: float = 0.0
        # KIS rate limit 예방용 주문 간 throttle (초). 0이면 비활성.
        self.order_throttle_s: float = float(
            config.get("rebalancing", {}).get("order_throttle_s", 0.25)
        )
        reb_cfg = config.get("rebalancing", {})
        self.order_timeout_s: float = float(reb_cfg.get("order_timeout_s", 180.0))
        self.order_retry_interval_s: float = float(reb_cfg.get("order_retry_interval_s", 30.0))
        self.order_poll_interval_s: float = float(reb_cfg.get("order_poll_interval_s", 2.0))
        self.order_max_retries: int = int(reb_cfg.get("order_max_retries", 4))

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
        self._last_net_flow_krw = 0.0  # 벤치마크 알파용 — 이번 실행 감지된 입출금 net (기본 0)
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
            self._last_net_flow_krw = float(net_flow)  # 벤치마크 알파용
            processed_through = (
                datetime.now().date().isoformat() if source == "kis_profits" else None
            )

            # 이상 감지 — 감지된 입출금으로 설명 안 되는 큰 변동은 침묵하지 않는다.
            # (자동 백엔드가 실제 입출금을 놓치면 그 금액이 그대로 "알파"로 착시되던 문제)
            #
            # 방향에 따라 처리를 달리한다 (2026-08-18 결정):
            #   증가(입금 추정) → deposits.csv에 자동 기록하고 이번 실행에 즉시 반영.
            #     틀려도 최악은 "입금을 알파로 오인" 정도라 위험이 작다.
            #   감소(출금 추정) → 검토용 파일에만 기록, peak/낙폭 계산엔 반영하지 않는다.
            #     진짜 폭락을 출금으로 오인해 낙폭을 지워버리면 이 시스템의 1순위 목표
            #     (하락 회피 지표의 정확성)를 해친다 — 그건 사람이 확인한 뒤 반영한다.
            unexplained = total_all_krw - prev_total - net_flow
            unexplained_rel = unexplained / prev_total
            if abs(unexplained_rel) > IO_ANOMALY_ALERT_THRESHOLD:
                now = datetime.now()
                if unexplained > 0:
                    print(
                        f"  ⚠️ [입출금 감지 경고/{source}] 자산 {prev_total:,.0f}→{total_all_krw:,.0f}원"
                        f" ({(total_all_krw / prev_total - 1):+.1%}) 중 감지된 입출금 {net_flow:+,.0f}원으로"
                        f" 설명 안 되는 증가 {unexplained:+,.0f}원({unexplained_rel:+.1%}p)"
                        f" → 입금 추정, deposits.csv에 자동 기록 + 이번 실행에 반영"
                    )
                    _append_io_event_csv(
                        DEPOSIT_LOG_PATH, now, "(자동감지)", abs(unexplained), "deposit",
                        f"자동감지({source}) — 설명 안 되는 자산 증가 추정치, 정확한 금액인지 확인 권장",
                    )
                    net_flow += unexplained
                else:
                    print(
                        f"  ⚠️ [입출금 감지 경고/{source}] 자산 {prev_total:,.0f}→{total_all_krw:,.0f}원"
                        f" ({(total_all_krw / prev_total - 1):+.1%}) 중 감지된 입출금 {net_flow:+,.0f}원으로"
                        f" 설명 안 되는 감소 {unexplained:+,.0f}원({unexplained_rel:+.1%}p)"
                        f" → 출금 추정이나 실제 하락일 수 있어 낙폭 계산엔 미반영."
                        f" {PENDING_WITHDRAWAL_REVIEW_PATH.name}에 기록 — 실제 출금이면"
                        f" deposits.csv로 옮겨 확인해주세요"
                    )
                    _append_io_event_csv(
                        PENDING_WITHDRAWAL_REVIEW_PATH, now, "(자동감지·미확인)", abs(unexplained),
                        "withdrawal",
                        f"자동감지({source}) — 설명 안 되는 자산 감소 추정치. 실제 출금이면"
                        " deposits.csv로 옮기고, 시장 하락이면 무시(낙폭은 이미 정확히 반영됨)",
                    )
                    # net_flow는 그대로 둔다 — peak/낙폭 계산에 자동 반영하지 않음

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
        usd_withdrawable_cash_krw: float = 0.0  # 주문가능 API 실패 시 보수적 폴백용
        krw_acc_holdings: Dict[str, Dict[str, float]] = {}  # acc_name → {ticker: krw}
        krw_acc_cash: Dict[str, float] = {}  # acc_name → cash
        krw_acc_purchase: Dict[str, float] = {}  # acc_name → 매입금액 합 (제외 계좌 차감용)
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
            acc_purchase_krw: float = 0.0

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
                orderable_val = getattr(stock, "orderable", None)
                qty_val = orderable_val if orderable_val is not None else getattr(stock, "qty", None)
                if qty_val is not None:
                    try:
                        self._held_qty_cache[(acc_name, currency, ticker)] = int(float(qty_val))
                    except (TypeError, ValueError):
                        pass
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
                purch_krw = purch * self.usd_krw if currency == "USD" else purch
                purchase_amount_krw_total += purch_krw

                if currency == "KRW":
                    acc_stock_holdings[ticker] = acc_stock_holdings.get(ticker, 0.0) + krw_amt
                    acc_purchase_krw += purch_krw

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
                        # USD 체결기준 잔고는 당일 매수 종목을 stocks에 즉시 포함하지만,
                        # 출금가능 예수금에는 당일 미결제 매수·매도가 모두 빠질 수 있다.
                        # NAV 현금 = 출금가능 예수금 + 미결제 매도 - 미결제 매수.
                        # gross 매도만 더하면 당일 매수액이 주식+현금에 이중계산된다(2026-08-28).
                        base_cash_usd = float(deposit.withdrawable_amount)
                        if base_cash_usd <= 0:
                            base_cash_usd = float(deposit.amount)
                        usd_withdrawable_cash_krw += max(0.0, base_cash_usd) * self.usd_krw
                        try:
                            raw = balance.raw()
                            cash_usd, sell_usd, buy_usd = _compute_usd_nav_cash(
                                deposit.withdrawable_amount,
                                deposit.amount,
                                raw,
                            )
                            if sell_usd > 0 or buy_usd > 0:
                                net_usd = sell_usd - buy_usd
                                print(
                                    f"  [{acc_name} USD 미결제 순매매]"
                                    f" 매도 ${sell_usd:,.0f} - 매수 ${buy_usd:,.0f}"
                                    f" = ${net_usd:+,.0f} 반영 (T+2 결제 대기)"
                                )
                        except Exception as e:
                            cash_usd = base_cash_usd
                            print(
                                f"  [경고] {acc_name} USD 미결제 순매매 추출 실패: {e}"
                                " — 출금가능 예수금만 사용"
                            )
                        cash = cash_usd * self.usd_krw
                except Exception as e:
                    print(f"  [경고] {acc_name} 예수금 변환 실패: {e} — 0 처리")
                    cash = 0.0
            cash_by_currency[currency] = cash_by_currency.get(currency, 0.0) + cash

            if currency == "KRW":
                krw_acc_holdings[acc_name] = acc_stock_holdings
                krw_acc_cash[acc_name] = cash
                krw_acc_purchase[acc_name] = acc_purchase_krw

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
        # NAV용 현금에는 미결제 순매매가 들어가지만, 주문가능 API 실패 시에는 이를
        # 다시 쓸 수 있다고 가정하지 않고 원래 출금가능 예수금만 보수적으로 사용한다.
        self._usd_cash_krw = usd_withdrawable_cash_krw

        # 유니버스 외 보유 종목 분리 및 안내
        universe_krw = {t: v for t, v in holdings_krw.items() if t in self.universe}
        orphan_krw = {t: v for t, v in holdings_krw.items() if t not in self.universe}

        if orphan_krw:
            total_all = sum(holdings_krw.values()) + sum(cash_by_currency.values())
            print("  [정리 예정] 유니버스 외 보유 종목 (리밸런싱 시 자동 전량 매도):")
            for t, v in orphan_krw.items():
                print(f"    {t}: {v:,.0f} KRW ({v/total_all*100:.1f}%)")

        # excluded_krw_accounts는 비중·drift 계산에서 제외한다 (정리·환전 중 매도가
        # 종목별 전체 보유량을 줄여 drift를 부풀리고, 그게 다시 다른 계좌의 매수를
        # 유발하는 간접 영향까지 차단).
        #
        # 2026-08-01: 성과 계산(총자산·peak·드로우다운·알파·원금)에서도 제외로 전환.
        # 제외 계좌는 "없는 계좌"로 취급한다 — 세금 때문에 동결한 계좌의 잔고가
        # 성과 지표에 섞이면 (a) 입금이 알파로 둔갑하고 (b) 굴리지 않는 현금이
        # 수익률 분모에 들어가 실력 측정을 흐린다.
        #
        # 부수 효과(의도된 것): KRW_1↔USD는 계좌번호가 같아 외부 입출금 API로는
        # 환전이 잡히지 않는데, 제외 기준 원금(P+C)으로 보면 환전이 곧 순유입으로
        # 나타난다 → deposit_log의 KIS 역산 백엔드가 자동 감지한다.
        excluded_ticker_krw: Dict[str, float] = {}
        excluded_cash_krw = 0.0
        excluded_purchase_krw = 0.0
        for acc in self.excluded_krw_accounts:
            for t, v in krw_acc_holdings.get(acc, {}).items():
                excluded_ticker_krw[t] = excluded_ticker_krw.get(t, 0.0) + v
            excluded_cash_krw += krw_acc_cash.get(acc, 0.0)
            excluded_purchase_krw += krw_acc_purchase.get(acc, 0.0)
        excluded_holdings_krw = sum(excluded_ticker_krw.values())

        universe_krw_for_weights = {
            t: max(0.0, v - excluded_ticker_krw.get(t, 0.0)) for t, v in universe_krw.items()
        }

        # 계좌별 분리 계산 (비중·drift 기준 — excluded_krw_accounts 미반영)
        usd_holdings = sum(
            v for t, v in universe_krw_for_weights.items()
            if self.universe[t]["currency"] == "USD"
        )
        krw_holdings = sum(
            v for t, v in universe_krw_for_weights.items()
            if self.universe[t]["currency"] == "KRW"
        )
        total_usd_krw = usd_holdings + cash_by_currency.get("USD", 0.0)
        total_krw_only = krw_holdings + max(0.0, cash_by_currency.get("KRW", 0.0) - excluded_cash_krw)

        universe_total_krw = total_usd_krw + total_krw_only

        if universe_total_krw == 0:
            return 0.0, 0.0, 0.0, {}, 0.0

        # 현재 비중 = 전체 대비 (drift·출력용, excluded_krw_accounts 미반영)
        current_weights = {t: v / universe_total_krw for t, v in universe_krw_for_weights.items()}

        # 드로우다운: 전체 자산(orphan 포함, excluded_krw_accounts 제외) 기준
        # KRW deposit.amount=dnca_tot_amt(매도 즉시 반영)이므로 T+2 보정 불필요
        state = load_state()
        total_all_krw = (
            sum(holdings_krw.values()) + sum(cash_by_currency.values())
            - excluded_holdings_krw - excluded_cash_krw
        )
        peak = state.get("peak_krw", 0.0)

        prev_total = float(state.get("last_total_all_krw", 0.0))
        prev_total_at = state.get("last_total_all_krw_at")
        current_principal_krw = (
            purchase_amount_krw_total + sum(cash_by_currency.values())
            - excluded_purchase_krw - excluded_cash_krw
        )
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
    ) -> Tuple[List[str], List[dict], List[dict]]:
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
            (order_log, deferred_buys, failed_sells)
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
                return [], [], []

        if force_full_rebalance:
            print("  [강제 평준화] per_ticker_drift_threshold 무시 — 모든 차이 주문 생성")

        if self.excluded_krw_accounts:
            print(f"  [계좌 제외] {', '.join(sorted(self.excluded_krw_accounts))} — 리밸런싱 주문 생성 제외 중 (수동 작업)")

        all_orders = self._build_orders(
            current_weights, target_usd, target_krw, total_usd_krw, total_krw_only,
            force_full_rebalance=force_full_rebalance,
        )

        # 동일자산(equivalence group) 상계 — QQQ↔379810처럼 통화만 다른 동일 노출을 팔고
        # 되사는 wash 매매 제거. side 필터 전에 적용해야 USD 매수↔KRW 매도가 상계됨.
        eq_groups = self.config.get("equivalence_groups")
        if eq_groups:
            _before = sum(abs(a) for _, _, a, _ in all_orders)
            all_orders = _net_equivalent_orders(all_orders, eq_groups, self.min_order_krw)
            _removed = _before - sum(abs(a) for _, _, a, _ in all_orders)
            if _removed >= self.min_order_krw:
                print(f"  [동일자산 상계] 통화 왕복 매매 {_removed:,.0f}원 제거 (wash netting)")

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
                return [], [], []

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
                return [], [], []

        # 실제 체결된 의도 금액만 누적 (cap 보호 측면에선 보수적인 의도금액보다
        # 약간 작아지지만, 실패한 주문이 회전율에 잡히는 왜곡 제거).
        actual_traded_krw = 0.0

        sell_orders = [(t, c, a, acc) for t, c, a, acc in all_orders if a < 0]
        buy_orders  = [(t, c, a, acc) for t, c, a, acc in all_orders if a > 0]
        if tracker is not None:
            failed_sell_tickers = {d.get("ticker") for d in tracker.get_failed_sells()}
            sell_orders.sort(key=lambda o: 0 if o[0] in failed_sell_tickers else 1)
        sell_cnt, buy_cnt = len(sell_orders), len(buy_orders)
        side_label = f" [{side.upper()}]" if side != "all" else ""
        print(f"→{side_label} 실행 {len(all_orders)}건 (매도 {sell_cnt}, 매수 {buy_cnt})")

        order_log: List[str] = []
        failed_buys: List[dict] = []
        failed_sells: List[dict] = []

        # 이번 회차 매도 추적기 reset — _fetch_krw_orderable fallback 보정용
        self._recent_sell_proceeds_krw = {}

        # Phase 1: 매도 먼저 실행 — KIS는 체결 즉시 주문가능금액에 반영
        for i, (ticker, currency, amount_diff_krw, acc_name) in enumerate(sell_orders):
            if i > 0 and self.order_throttle_s > 0:
                time.sleep(self.order_throttle_s)
            result = self._execute_order(ticker, currency, amount_diff_krw, acc_name)
            if result:
                order_log.append(result)
                filled_amount = self._last_order_filled_amount_krw
                actual_traded_krw += filled_amount
                # KRW 매도 실제 체결분만 주문가능 fallback에 반영한다.
                if currency == "KRW" and filled_amount > 0:
                    self._recent_sell_proceeds_krw[acc_name] = (
                        self._recent_sell_proceeds_krw.get(acc_name, 0.0) + filled_amount
                    )
            unfilled = self._last_order_unfilled
            if unfilled and unfilled.get("action") == "sell":
                failed_sells.append(dict(unfilled))
                if tracker is not None:
                    tracker.add_failed_sell(
                        unfilled["ticker"], unfilled["qty"], unfilled["amount_krw"],
                        unfilled["currency"], unfilled["status"],
                    )
                if self.messenger:
                    self.messenger.send_order_error(
                        ticker,
                        RuntimeError(
                            f"매도 미완료 {unfilled['qty']}주 ({unfilled['status']}) — "
                            "다른 주문은 계속 진행, 다음 모니터에서 재시도"
                        ),
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

        # USD 매수도 주문가능금액으로 cap (KRW와 동일 — 2026-06-08·09 VWO 초과 수정)
        usd_buys_by_acc: Dict[str, List[Tuple[str, float]]] = {}
        for t, c, a, acc in buy_orders:
            if c == "USD":
                usd_buys_by_acc.setdefault(acc, []).append((t, a))
        for acc_name, buys in usd_buys_by_acc.items():
            # 한 종목 호가 실패가 계좌 전체 예산을 삭감하지 않도록 매수 후보 전 종목을
            # 넘긴다 — ovrs_ord_psbl_amt는 계좌 단위라 유효 호가 하나면 충분.
            ref_tickers = [t for t, _ in buys]
            orderable = self._fetch_usd_orderable(acc_name, ref_tickers)
            total_buy = sum(d for _, d in buys)
            if orderable > 0 and total_buy > orderable:
                scale = orderable / total_buy
                print(f"  [주문가능 cap] {acc_name}: {total_buy:,.0f}원 → {orderable:,.0f}원 ({scale:.1%})")
                buys = [(t, d * scale) for t, d in buys]
            for t, d in buys:
                if d >= self.min_order_krw:
                    scaled_buy_orders.append((t, "USD", d, acc_name))

        # Phase 3: 매수 실행 (잔고부족 거부 시 한도 재조회·축소 재시도)
        for i, (ticker, currency, amount_diff_krw, acc_name) in enumerate(scaled_buy_orders):
            if i > 0 and self.order_throttle_s > 0:
                time.sleep(self.order_throttle_s)
            result, eff_amount = self._execute_buy_capped(
                ticker, currency, amount_diff_krw, acc_name
            )
            if result:
                order_log.append(result)
                actual_traded_krw += self._last_order_filled_amount_krw
                is_funds_error = result.startswith("[오류]") and _looks_like_insufficient_funds(result)
                unfilled = self._last_order_unfilled
                is_timeout = bool(unfilled and unfilled.get("action") == "buy")
                if is_funds_error or is_timeout:
                    # 재시도 중에는 알림을 억제했으므로 최종 실패만 1회 통지
                    if is_funds_error and self.messenger:
                        self.messenger.send_order_error(
                            ticker, RuntimeError("주문가능금액 부족 — 축소 재시도 후에도 실패")
                        )
                    # 부분체결이면 남은 금액만, 주문 거부면 원래 계획 금액을 이연한다.
                    deferred_amount = (
                        float(unfilled["amount_krw"]) if is_timeout and unfilled
                        else abs(amount_diff_krw)
                    )
                    failed_buys.append({
                        "ticker": ticker,
                        "amount_krw": deferred_amount,
                        "currency": currency,
                    })

        # 체결분 기준으로 monthly_traded_krw 누적 — 실패한 주문은 회전율에 포함 안 됨
        self._last_run_traded_krw = actual_traded_krw

        if failed_buys:
            cnt_krw = sum(1 for d in failed_buys if d["currency"] == "KRW")
            cnt_usd = sum(1 for d in failed_buys if d["currency"] == "USD")
            parts = [f"{c} {n}건" for c, n in [("KRW", cnt_krw), ("USD", cnt_usd)] if n > 0]
            print(f"    [매수 실패] {', '.join(parts)} → 다음 실행 시 합성 노출로 대체")
        if failed_sells:
            print(
                f"    [매도 미완료] {len(failed_sells)}건 → 다른 주문은 완료, "
                "다음 모니터에서 우선 재시도"
            )
        return order_log, failed_buys, failed_sells

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
                    if acc_name in self.excluded_krw_accounts:
                        continue
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

    def _get_held_qty(
        self,
        client: pykis.PyKis,
        ticker: str,
        currency: str,
        price: float,
        acc_name: str = "?",
        force_refresh: bool = False,
    ) -> int:
        """매도 직전 실제 보유 수량을 조회한다. 조회 실패 시 RuntimeError를 발생시킨다."""
        cache_key = (acc_name, currency, ticker)
        if not force_refresh and cache_key in self._held_qty_cache:
            return self._held_qty_cache[cache_key]
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
                    qty = int(float(orderable_val))
                    self._held_qty_cache[cache_key] = qty
                    return qty
                qty_val = getattr(s, "qty", None)
                if qty_val is not None:
                    qty = int(float(qty_val))
                    self._held_qty_cache[cache_key] = qty
                    return qty
                # orderable/qty 없으면 평가금액(native currency) ÷ 현재가(native currency)로 추정
                # current_amount는 USD 종목이면 USD, KRX 종목이면 KRW — price도 같은 단위
                qty = math.floor(float(s.current_amount) / price) if price > 0 else 0
                self._held_qty_cache[cache_key] = qty
                return qty
            self._held_qty_cache[cache_key] = 0
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

    def _fetch_usd_orderable(self, acc_name: str, ref_tickers) -> float:
        """해외(USD) 계좌의 주문가능금액을 원화 환산으로 조회한다 (매도 직후 호출).

        KRW 경로와 동일한 버그(주문가능금액 초과)가 USD 계좌에서도 발생(2026-06-08·09 VWO).
        원인: USD 매수는 orderable cap 없이 target×total_usd_krw 스냅샷으로 집행되어,
        매도 미체결·ask 슬리피지·환전 증거금 누적으로 마지막 잔여 매수가 KIS 한도를 초과.

        cap 결정: KIS의 max_ord_psbl_qty(ovrs 매수가능수량)×ask × usd_krw × 0.98.
        - ovrs_ord_psbl_amt(amount)·max_ord_psbl_qty(quantity)는 KIS가 매수증거금·환전·수수료를
          모두 반영해 자체 계산한 권위값 — KIS가 주문 수락/거부에 쓰는 바로 그 기준.
        - 가격은 _execute_order와 동일한 ask(_get_price)를 사용.

        ref_tickers: 기준 호가를 조회할 종목(str 또는 list). ovrs_ord_psbl_amt는 계좌 단위 값이라
          유효 호가를 주는 종목 아무거나면 되므로, 한 종목 호가 실패(VEA ask=0 등, 레이트리밋·stale)
          시 다음 후보로 순차 재시도한다 — 한 종목 실패가 계좌 전체 예산을 삭감하던 버그 수정
          (2026-07-04). 전 종목 실패 시에만 _usd_cash_krw(매도 전 출금가능현금)×0.98 폴백.
        """
        if isinstance(ref_tickers, str):
            ref_tickers = [ref_tickers]
        client = self._clients[acc_name]
        last_err = None
        for ref_ticker in ref_tickers:
            try:
                stock = client.stock(ref_ticker)
                price = self._get_price(stock, "buy", "USD")
                if price <= 0:
                    raise ValueError(f"{ref_ticker} ask 가격 비정상: {price}")
                oa = stock.orderable_amount(price=price)
                amount_usd = float(oa.amount)         # ovrs_ord_psbl_amt (USD, 통화 기준)
                max_qty = int(oa.quantity)             # max_ord_psbl_qty (통화 기준)
                qty_based_usd = float(max_qty) * price
                # amount(주문가능금액)과 qty×price 중 작은 값 — 정수주 반올림으로 qty_based가 약간 작음
                usable_usd = min(amount_usd, qty_based_usd) if amount_usd > 0 else qty_based_usd
                effective_krw = usable_usd * self.usd_krw * 0.98
                print(
                    f"    [주문가능금액] {acc_name}: {effective_krw:,.0f}원"
                    f" (기준 {ref_ticker}, max_qty={max_qty}×${price:.2f}=${qty_based_usd:,.0f},"
                    f" ovrs_ord_psbl=${amount_usd:,.0f}, ask·98% 적용)"
                )
                return effective_krw
            except Exception as e:
                last_err = e
                print(
                    f"  [경고] {acc_name} 주문가능금액 조회 실패 ({ref_ticker}):"
                    f" {type(e).__name__}: {e} → 다음 종목 호가로 재시도"
                )
                continue

        base_cash = self._usd_cash_krw if self._usd_cash_krw > 0 else float("inf")
        fallback = base_cash * 0.98 if base_cash != float("inf") else base_cash
        print(
            f"  [경고] {acc_name} USD 주문가능금액 전 종목({len(ref_tickers)}) 조회 실패"
            f" (마지막 {type(last_err).__name__ if last_err else '?'})"
            f" → fallback {fallback:,.0f}원 (USD현금 {base_cash:,.0f}×0.98)"
        )
        return fallback

    def _wait_for_fill(
        self,
        order,
        reorder: Callable[[float, int], object],
        ticker: str,
        action: str,
        qty: int,
        price: float,
        currency: str,
        max_retries: Optional[int] = None,
        retry_interval: Optional[float] = None,
        chase: float = 0.001,
        max_wait_s: Optional[float] = None,
        poll_interval_s: Optional[float] = None,
        refresh_price: Optional[Callable[[], float]] = None,
    ) -> Tuple[int, float, bool]:
        """미체결 주문을 제한시간 안에서 추적하고 실제 체결수량을 반환한다.

        취소가 성공한 경우에만 *미체결 잔량*을 재주문한다. 취소 실패 시 새 주문을
        내지 않아 중복 주문을 막고, 최종 취소도 확인되지 않으면 closed=False로 반환한다.
        """
        max_retries = self.order_max_retries if max_retries is None else max(int(max_retries), 0)
        retry_interval = (
            self.order_retry_interval_s if retry_interval is None else max(float(retry_interval), 0.01)
        )
        max_wait_s = self.order_timeout_s if max_wait_s is None else max(float(max_wait_s), 0.01)
        poll_interval_s = (
            self.order_poll_interval_s if poll_interval_s is None else max(float(poll_interval_s), 0.01)
        )
        rate = (1.0 + chase) if action == "buy" else (1.0 - chase)
        retries = 0
        total_filled = 0
        current_qty = qty
        started = time.monotonic()
        next_retry_at = started + retry_interval

        def _try_cancel(o) -> bool:
            try:
                o.cancel()
                return True
            except Exception as ce:
                print(f"  [경고] {ticker} 주문 취소 실패: {ce}")
                return False

        while True:
            now = time.monotonic()
            elapsed = now - started
            try:
                pending = order.pending_order
            except Exception as pe:
                print(f"  [경고] {ticker} 체결상태 조회 실패: {pe}")
                if elapsed >= max_wait_s:
                    closed = _try_cancel(order)
                    if not closed:
                        print(f"  [CRITICAL] {ticker}: 제한시간 후 주문 취소 미확인 — 신규 주문 금지")
                    return total_filled, price, closed
                time.sleep(poll_interval_s)
                continue

            if pending is None:
                # 미체결 목록에서 사라졌고 취소를 요청한 상태가 아니므로 현재 주문 전량 체결.
                total_filled += current_qty
                return total_filled, price, True

            executed = max(0, min(int(getattr(pending, "executed_qty", 0)), current_qty))
            pending_qty = max(0, current_qty - executed)
            rejected = bool(getattr(pending, "rejected", False))
            should_reprice = now >= next_retry_at
            should_stop = (
                elapsed >= max_wait_s
                or rejected
                or (retries >= max_retries and should_reprice)
            )

            if should_stop or should_reprice:
                if not _try_cancel(order):
                    # 체결과 취소가 경합하면 "이미 체결" 때문에 취소가 실패할 수 있다.
                    # 한 번 더 조회해 미체결 목록에서 사라졌다면 전량 체결로 확정한다.
                    try:
                        if order.pending_order is None:
                            total_filled += current_qty
                            return total_filled, price, True
                    except Exception:
                        pass
                    if should_stop:
                        print(f"  [CRITICAL] {ticker}: 주문 취소 미확인 — 열린 주문 가능성")
                        return total_filled + executed, price, False
                    next_retry_at = time.monotonic() + retry_interval
                    time.sleep(poll_interval_s)
                    continue

                total_filled += executed
                if pending_qty <= 0:
                    return total_filled, price, True
                if should_stop:
                    reason = "거부" if rejected else "시간 초과"
                    print(
                        f"  [timeout] {ticker}: 주문 {reason} "
                        f"(체결 {total_filled}/{qty}, 재시도 {retries}회, {elapsed:.0f}초)"
                    )
                    return total_filled, price, True

                base_price = price
                if refresh_price is not None:
                    try:
                        fresh = float(refresh_price())
                        if fresh > 0:
                            base_price = fresh
                    except Exception as qe:
                        print(f"  [경고] {ticker} 재가격 호가 조회 실패: {qe} — 직전 가격 사용")
                price = _adjust_tick(base_price * rate, currency)
                current_qty = pending_qty
                order = reorder(price, current_qty)
                retries += 1
                next_retry_at = time.monotonic() + retry_interval
                continue

            time.sleep(poll_interval_s)

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
            filled_qty, price, closed = self._wait_for_fill(
                order, lambda p, q: stock.sell(qty=q, price=p),
                ticker, "sell", qty, price, currency,
                refresh_price=lambda: self._get_price(stock, "sell", currency),
            )
            if filled_qty < qty or not closed:
                if filled_qty > 0:
                    _append_order_log(
                        ticker, "sell", filled_qty, price, currency, self.usd_krw, "partial"
                    )
                status = "unknown_open" if not closed else "timeout"
                _append_order_log(
                    ticker, "sell", qty - filled_qty, price, currency, self.usd_krw, status
                )
                return f"[timeout] 매도 {ticker} 체결 {filled_qty}/{qty}주"

            _append_order_log(ticker, "sell", filled_qty, price, currency, self.usd_krw, "ok")
            return f"매도 {_label_ticker(ticker, self.universe)} {qty}주 @ {price:,.2f} {currency} [정리]"

        except Exception as e:
            print(f"  [error] {ticker}: {e}")
            _append_order_log(ticker, "sell", qty, 0.0, currency, self.usd_krw, f"error:{e}")
            if self.messenger:
                self.messenger.send_order_error(ticker, e)
            return f"[오류] {ticker}: {e}"

    def _execute_buy_capped(
        self,
        ticker: str,
        currency: str,
        amount_krw: float,
        acc_name: str,
        max_attempts: int = 2,
        shrink: float = 0.97,
    ) -> Tuple[Optional[str], float]:
        """매수를 실행하되 '주문가능금액 초과' 거부 시 한도를 재조회해 금액을 줄여 재시도한다.

        사전 cap(매도 직후 1회 조회)은 KIS의 max_buy_qty가 당일 매도대금(T+2 미결제)을
        과대 반영해, 앞선 매수가 실제 현금을 소진한 뒤 순서상 마지막 매수가 거부되는
        고질적 패턴이 있다. 거부를 만나면 라이브 주문가능금액으로 재조회 후 shrink배
        축소해 같은 종목을 재시도 — 종목을 이연/교체하지 않고 가능한 만큼 체결한다.

        재시도 중에는 Slack 오류 알림을 억제하고, 최종 실패 시 호출자가 처리한다.
        반환: (마지막 결과 문자열, 실제 시도한 최종 금액).
        """
        result = self._execute_order(
            ticker, currency, amount_krw, acc_name, notify_error=False
        )
        attempts = 0
        while (
            result is not None
            and result.startswith("[오류]")
            and _looks_like_insufficient_funds(result)
            and attempts < max_attempts
        ):
            attempts += 1
            if currency == "USD":
                live = self._fetch_usd_orderable(acc_name, ticker)
            else:
                live = self._fetch_krw_orderable(acc_name, ticker)
            new_amount = min(amount_krw, live) * shrink if live > 0 else amount_krw * shrink
            if new_amount < self.min_order_krw or new_amount >= amount_krw:
                break
            print(
                f"  [매수 재시도] {_label_ticker(ticker, self.universe)} "
                f"{amount_krw:,.0f}→{new_amount:,.0f}원 (주문가능 {live:,.0f}, {attempts}/{max_attempts})"
            )
            amount_krw = new_amount
            if self.order_throttle_s > 0:
                time.sleep(self.order_throttle_s)
            result = self._execute_order(
                ticker, currency, amount_krw, acc_name, notify_error=False
            )
        return result, amount_krw

    def _execute_order(
        self,
        ticker: str,
        currency: str,
        amount_diff_krw: float,
        acc_name: Optional[str] = None,
        notify_error: bool = True,
    ) -> Optional[str]:
        """지정 종목을 KRW 환산 금액 기준으로 매수/매도한다. 결과 문자열을 반환한다."""
        self._last_order_unfilled = None
        self._last_order_filled_amount_krw = 0.0
        action = "buy" if amount_diff_krw > 0 else "sell"
        amount_local = (
            abs(amount_diff_krw) / self.usd_krw
            if currency == "USD"
            else abs(amount_diff_krw)
        )
        qty, price = 0, 0.0
        initial_held_qty: Optional[int] = None

        try:
            client = self._get_client(ticker, acc_name)
            stock = client.stock(ticker)
            price = self._get_price(stock, action, currency)

            if price <= 0:
                print(f"  [skip] {ticker}: 가격 조회 실패")
                if action == "sell":
                    self._last_order_unfilled = {
                        "ticker": ticker,
                        "qty": 0,
                        "amount_krw": abs(amount_diff_krw),
                        "currency": currency,
                        "action": action,
                        "status": "price_unavailable",
                    }
                return None

            qty = math.floor(amount_local / price)
            if qty <= 0:
                print(f"  [skip] {ticker}: 수량 0")
                return None

            # 매도 시 실제 보유 수량으로 상한 설정
            # 동시 실행이나 이전 주문의 부분 체결로 실제보다 많은 수량을 매도하는 것을 방지한다.
            if action == "sell":
                held_qty = self._get_held_qty(client, ticker, currency, price, acc_name=acc_name or ticker)
                initial_held_qty = held_qty
                if held_qty == 0:
                    print(f"  [skip] {ticker}: 실보유 수량 없음")
                    return None
                if qty > held_qty:
                    print(f"  [경고] {ticker}: 주문 수량 {qty}주 → 실보유 {held_qty}주로 조정")
                    qty = held_qty

            order_fn = getattr(stock, action)
            label = "매수" if action == "buy" else "매도"

            # 유동성 얇은 종목: 주문 분할 + 재시도 확대 (호가 깊이 초과 timeout 방지)
            iq = self.illiquid_cfg.get(ticker, {})
            wait_kwargs = {
                "max_retries": self.order_max_retries,
                "retry_interval": self.order_retry_interval_s,
                "max_wait_s": self.order_timeout_s,
                "poll_interval_s": self.order_poll_interval_s,
            }
            if iq:
                if iq.get("max_retries") is not None:
                    wait_kwargs["max_retries"] = int(iq["max_retries"])
                if iq.get("retry_interval_s") is not None:
                    wait_kwargs["retry_interval"] = int(iq["retry_interval_s"])
                if iq.get("price_chase") is not None:
                    wait_kwargs["chase"] = float(iq["price_chase"])
                if iq.get("max_wait_s") is not None:
                    wait_kwargs["max_wait_s"] = float(iq["max_wait_s"])
            chunk_qty = qty
            if iq.get("max_order_krw"):
                cap_local = float(iq["max_order_krw"]) / (
                    self.usd_krw if currency == "USD" else 1.0
                )
                chunk_qty = max(1, math.floor(cap_local / price))

            split = chunk_qty < qty
            if split:
                print(
                    f"  {action} {_label_ticker(ticker, self.universe)} {qty}주 "
                    f"@ {price:,.2f} {currency} — {chunk_qty}주씩 분할"
                )
            else:
                print(f"  {action} {_label_ticker(ticker, self.universe)} {qty}주 @ {price:,.2f} {currency}")

            filled_qty = 0
            logged_filled_qty = 0
            remaining = qty
            last_price = price
            safely_closed = True
            order_started = time.monotonic()
            order_max_wait = float(wait_kwargs["max_wait_s"])
            while remaining > 0:
                elapsed_order = time.monotonic() - order_started
                if elapsed_order >= order_max_wait:
                    print(
                        f"  [timeout] {ticker}: 종목 전체 제한시간 {order_max_wait:.0f}초 초과 "
                        f"(체결 {filled_qty}/{qty})"
                    )
                    break
                q = min(chunk_qty, remaining)
                order = order_fn(qty=q, price=last_price)
                chunk_wait_kwargs = dict(wait_kwargs)
                chunk_wait_kwargs["max_wait_s"] = max(0.01, order_max_wait - elapsed_order)
                chunk_filled, last_price, chunk_closed = self._wait_for_fill(
                    order, lambda p, rq: order_fn(qty=rq, price=p),
                    ticker, action, q, last_price, currency, **chunk_wait_kwargs,
                    refresh_price=lambda: self._get_price(stock, action, currency),
                )
                chunk_filled = max(0, min(chunk_filled, q))
                if split and chunk_filled == q:
                    _append_order_log(
                        ticker, action, chunk_filled, last_price, currency, self.usd_krw, "ok",
                    )
                    logged_filled_qty += chunk_filled
                filled_qty += chunk_filled
                remaining -= chunk_filled
                safely_closed = safely_closed and chunk_closed
                if chunk_filled < q or not chunk_closed:
                    break
                price = last_price  # 다음 청크 기준가 갱신

            # 매도 timeout은 최종 잔고를 한 번 재조회해 취소 직전 체결 race까지 확정한다.
            if action == "sell" and remaining > 0 and initial_held_qty is not None:
                try:
                    held_after = self._get_held_qty(
                        client, ticker, currency, last_price, acc_name=acc_name or ticker,
                        force_refresh=True,
                    )
                    reconciled = max(0, min(qty, initial_held_qty - held_after))
                    if reconciled != filled_qty:
                        print(
                            f"  [체결 재확인] {ticker}: 주문추적 {filled_qty}주 → 잔고기준 {reconciled}주"
                        )
                        filled_qty = reconciled
                        remaining = qty - filled_qty
                except Exception as re:
                    print(f"  [CRITICAL] {ticker} timeout 후 잔고 재확인 실패: {re}")
                    safely_closed = False

            fx = self.usd_krw if currency == "USD" else 1.0
            self._last_order_filled_amount_krw = filled_qty * last_price * fx
            if action == "sell" and initial_held_qty is not None:
                self._held_qty_cache[(acc_name or ticker, currency, ticker)] = max(
                    0, initial_held_qty - filled_qty
                )

            if not split:
                if filled_qty > 0:
                    _append_order_log(
                        ticker, action, filled_qty, last_price, currency, self.usd_krw,
                        "ok" if remaining == 0 else "partial",
                    )
            elif filled_qty > logged_filled_qty:
                _append_order_log(
                    ticker, action, filled_qty - logged_filled_qty, last_price,
                    currency, self.usd_krw, "partial",
                )
            if remaining > 0:
                failure_status = "unknown_open" if not safely_closed else "timeout"
                _append_order_log(
                    ticker, action, remaining, last_price, currency, self.usd_krw, failure_status
                )
                self._last_order_unfilled = {
                    "ticker": ticker,
                    "qty": remaining,
                    "amount_krw": remaining * last_price * fx,
                    "currency": currency,
                    "action": action,
                    "status": failure_status,
                }

            if filled_qty == 0:
                prefix = "[CRITICAL]" if not safely_closed else "[timeout]"
                return f"{prefix} {action} {ticker} {qty}주"

            note = "" if remaining == 0 else f" (부분체결 {filled_qty}/{qty}, 잔량 다음 실행)"
            return f"{label} {_label_ticker(ticker, self.universe)} {filled_qty}주 @ {last_price:,.2f} {currency}{note}"

        except Exception as e:
            print(f"  [error] {ticker}: {e}")
            _append_order_log(ticker, action, qty, price, currency, self.usd_krw, f"error:{e}")
            if action == "sell":
                fx = self.usd_krw if currency == "USD" else 1.0
                self._last_order_unfilled = {
                    "ticker": ticker,
                    "qty": qty,
                    "amount_krw": qty * price * fx if qty > 0 and price > 0 else abs(amount_diff_krw),
                    "currency": currency,
                    "action": action,
                    "status": f"error:{type(e).__name__}",
                }
            if self.messenger and notify_error:
                self.messenger.send_order_error(ticker, e)
            return f"[오류] {ticker}: {e}"
