"""규칙 기반 시장 레짐 감지 및 히스테리시스 필터."""
from __future__ import annotations

from datetime import date

REGIMES = ["Risk-On", "Neutral", "Risk-Off", "High-Vol"]


def detect_regime(features: dict) -> str:
    """
    피처 딕셔너리로부터 시장 레짐을 분류한다.

    우선순위:
      1. High-Vol  — 실현변동성 또는 VIX가 극단적으로 높을 때
      2. Risk-Off  — 베어리시 신호 2개 이상
      3. Risk-On   — 불리시 신호 2개 이상
      4. Neutral   — 혼재

    Returns:
        "Risk-On" | "Neutral" | "Risk-Off" | "High-Vol"
    """
    vix = features["vix"]
    mom1m = features["momentum_1m"]
    mom3m = features["momentum_3m"]
    rvol = features["realized_vol"]
    credit = features["credit_signal"]

    if rvol > 0.25 or vix > 35:
        return "High-Vol"

    bearish = sum([
        mom1m < -0.03,
        mom3m < -0.05,
        vix > 25,
        credit < -0.03,
    ])

    bullish = sum([
        mom1m > 0.02,
        mom3m > 0.04,
        vix < 18,
        credit > 0.02,
    ])

    if bearish >= 2:
        return "Risk-Off"
    if bullish >= 2:
        return "Risk-On"
    return "Neutral"


class RegimeFilter:
    """
    레짐 전환 과잉 반응 억제 필터.

    전환 조건 두 가지를 모두 충족해야 확정한다.
      1. Confirmation: raw 레짐이 N회 연속 동일
      2. Cooldown: 마지막 전환 후 최소 K 달력일 경과

    최초 실행(state에 confirmed_regime 없음)은 즉시 확정한다.
    """

    def __init__(self, state: dict, config: dict) -> None:
        cfg = config.get("regime_filter", {})
        self._confirm_n: int = cfg.get("confirmation_count", 3)
        self._cooldown: int = cfg.get("cooldown_days", 5)

        self._confirmed: str | None = state.get("confirmed_regime")
        self._candidate: str | None = state.get("candidate_regime")
        self._count: int = state.get("candidate_count", 0)
        self._last_switch: str | None = state.get("last_switch_date")

    def update(self, raw: str) -> str:
        """raw 레짐을 받아 확정 레짐을 반환하고 내부 상태를 갱신한다."""
        today = date.today().isoformat()

        if self._confirmed is None:
            self._confirmed = raw
            self._candidate = raw
            self._count = 1
            self._last_switch = today
            return self._confirmed

        if raw == self._confirmed:
            self._candidate = raw
            self._count = 1
            return self._confirmed

        # 전환 후보 누적
        if raw != self._candidate:
            self._candidate = raw
            self._count = 1
        else:
            self._count += 1

        if self._count >= self._confirm_n and self._cooldown_ok(today):
            self._confirmed = raw
            self._last_switch = today
            self._candidate = raw
            self._count = 1

        return self._confirmed

    def _cooldown_ok(self, today_iso: str) -> bool:
        if not self._last_switch:
            return True
        elapsed = (date.fromisoformat(today_iso) - date.fromisoformat(self._last_switch)).days
        return elapsed >= self._cooldown

    # ── 상태 조회 ─────────────────────────────────────────────────────────

    @property
    def confirmed(self) -> str | None:
        return self._confirmed

    @property
    def candidate(self) -> str | None:
        return self._candidate

    @property
    def candidate_count(self) -> int:
        return self._count

    @property
    def confirm_n(self) -> int:
        return self._confirm_n

    @property
    def is_transitioning(self) -> bool:
        """후보 레짐이 확정 레짐과 다른 전환 대기 상태."""
        return bool(self._candidate and self._candidate != self._confirmed)

    @property
    def cooldown_remaining(self) -> int:
        """쿨다운 잔여 달력일 (0이면 이미 경과)."""
        if not self._last_switch:
            return 0
        elapsed = (date.today() - date.fromisoformat(self._last_switch)).days
        return max(0, self._cooldown - elapsed)

    def to_dict(self) -> dict:
        return {
            "confirmed_regime": self._confirmed,
            "candidate_regime": self._candidate,
            "candidate_count": self._count,
            "last_switch_date": self._last_switch,
        }
