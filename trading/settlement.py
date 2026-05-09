"""T+2 결제 지연 추적 및 지연 매수 대기열 관리."""
from __future__ import annotations

from datetime import date, timedelta
from typing import List

try:
    import holidays as _holidays_lib
    _KR_HOLIDAYS = _holidays_lib.KR()
    _US_HOLIDAYS = _holidays_lib.US()
    _HOLIDAYS_AVAILABLE = True
except ImportError:
    _HOLIDAYS_AVAILABLE = False

DEFERRED_TTL_DAYS = 5  # 지연 매수 만료 기간 (영업일)


def _next_business_day(from_date: date, n: int) -> date:
    """n 영업일 후 날짜 (토·일 + 한국·미국 공휴일 건너뜀)."""
    d = from_date
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            if not _HOLIDAYS_AVAILABLE or (d not in _KR_HOLIDAYS and d not in _US_HOLIDAYS):
                added += 1
    return d


class SettlementTracker:
    """
    매도 T+2 결제 지연과 지연 매수 대기열을 추적한다.

    state.json 의 "pending_sells" / "deferred_buys" 키로 영속화된다.
    """

    T_PLUS = 2  # 결제 영업일

    def __init__(self, state: dict) -> None:
        self._sells: List[dict] = state.get("pending_sells", [])
        self._deferred: List[dict] = state.get("deferred_buys", [])

    # ── 매도 기록 ─────────────────────────────────────────────────────────

    def record_sell(self, ticker: str, amount_krw: float, currency: str) -> None:
        """매도 체결 후 결제 예정일과 함께 기록한다."""
        settle = _next_business_day(date.today(), self.T_PLUS).isoformat()
        self._sells.append(
            {
                "ticker": ticker,
                "amount_krw": amount_krw,
                "currency": currency,
                "settle_date": settle,
            }
        )

    def pending_krw(self, currency: str = "ALL") -> float:
        """아직 결제되지 않은 매도 대금 (KRW 환산) 합계."""
        today = date.today().isoformat()
        return sum(
            s["amount_krw"]
            for s in self._sells
            if (currency == "ALL" or s["currency"] == currency)
            and s["settle_date"] > today
        )

    def purge_settled(self) -> int:
        """결제 완료된 항목을 정리하고 정리 건수를 반환한다."""
        today = date.today().isoformat()
        before = len(self._sells)
        self._sells = [s for s in self._sells if s["settle_date"] > today]
        return before - len(self._sells)

    def pending_summary(self) -> List[str]:
        """미결제 매도 대금 요약 문자열 리스트."""
        today = date.today().isoformat()
        lines = []
        for s in self._sells:
            if s["settle_date"] > today:
                lines.append(
                    f"{s['ticker']} {s['amount_krw']:,.0f}원 (결제일: {s['settle_date']})"
                )
        return lines

    # ── 지연 매수 대기열 ─────────────────────────────────────────────────

    def add_deferred(self, ticker: str, amount_krw: float, currency: str) -> None:
        expire = _next_business_day(date.today(), DEFERRED_TTL_DAYS).isoformat()
        self._deferred.append(
            {
                "ticker": ticker,
                "amount_krw": amount_krw,
                "currency": currency,
                "created": date.today().isoformat(),
                "expires": expire,
            }
        )

    def get_deferred(self) -> List[dict]:
        today = date.today().isoformat()
        active = [d for d in self._deferred if d.get("expires", "9999-12-31") > today]
        expired = len(self._deferred) - len(active)
        if expired:
            print(f"    [지연매수] 만료 항목 {expired}건 자동 정리 (TTL {DEFERRED_TTL_DAYS}영업일 초과)")
            self._deferred = active
        return list(active)

    def clear_deferred(self) -> None:
        self._deferred = []

    # ── 직렬화 ────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {"pending_sells": self._sells, "deferred_buys": self._deferred}
