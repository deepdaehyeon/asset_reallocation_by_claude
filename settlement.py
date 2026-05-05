"""T+2 결제 지연 추적 및 지연 매수 대기열 관리."""
from __future__ import annotations

from datetime import date, timedelta
from typing import List


def _next_business_day(from_date: date, n: int) -> date:
    """n 영업일 후 날짜 (토·일 건너뜀, 공휴일 미고려)."""
    d = from_date
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
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
        self._deferred.append(
            {
                "ticker": ticker,
                "amount_krw": amount_krw,
                "currency": currency,
                "created": date.today().isoformat(),
            }
        )

    def get_deferred(self) -> List[dict]:
        return list(self._deferred)

    def clear_deferred(self) -> None:
        self._deferred = []

    # ── 직렬화 ────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {"pending_sells": self._sells, "deferred_buys": self._deferred}
