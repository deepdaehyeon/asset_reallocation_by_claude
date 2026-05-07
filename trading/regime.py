"""규칙 기반 시장 레짐 감지, 히스테리시스 필터, HMM 앙상블."""
from __future__ import annotations

from datetime import date

# 5개 레짐: 성장·인플레·유동성 3축으로 정의
# Goldilocks : 성장↑ + 유동성↑ (인플레 안정)
# Reflation  : 성장↑ + 인플레↑
# Slowdown   : 성장↓ (인플레 낮음)
# Stagflation: 성장↓ + 인플레↑
# Crisis     : 유동성 쇼크
REGIMES = ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis"]

DEFAULT_REGIME = "Slowdown"  # 신뢰도 미달 시 보수적 폴백


def detect_regime(features: dict) -> str:
    """
    피처 딕셔너리로부터 시장 레짐을 분류한다.

    우선순위:
      1. Crisis      — 실현변동성 또는 VIX가 극단적으로 높을 때 (유동성 쇼크)
      2. Stagflation — 성장↓ + 인플레↑
      3. Slowdown    — 성장↓
      4. Goldilocks  — 성장↑ + 인플레 안정
      5. Reflation   — 성장↑ + 인플레↑
      6. 혼재        — 성장 방향성으로 보수적 판단

    성장 proxy: momentum_1m / momentum_3m / credit_signal
    인플레 proxy: hy_spread(FRED) / curve_10y2y(FRED) / vix
    """
    vix    = features["vix"]
    mom1m  = features["momentum_1m"]
    mom3m  = features["momentum_3m"]
    rvol   = features["realized_vol"]
    credit = features["credit_signal"]
    hy_spread = features.get("hy_spread", 4.5)   # 없으면 중립값
    curve     = features.get("curve_10y2y", 0.5)  # 없으면 중립값

    # 1. Crisis: 유동성 쇼크
    if rvol > 0.30 or vix > 40:
        return "Crisis"

    growth_bullish = sum([mom1m > 0.02, mom3m > 0.03, credit > 0.01])
    growth_bearish = sum([mom1m < -0.02, mom3m < -0.03, credit < -0.02])

    infl_rising = sum([hy_spread > 5.0, curve > 1.5, vix > 25])
    infl_low    = sum([hy_spread < 4.0, curve < 0.5,  vix < 18])

    # 2. Stagflation: 성장↓ + 인플레↑
    if growth_bearish >= 2 and infl_rising >= 1:
        return "Stagflation"

    # 3. Slowdown: 성장↓
    if growth_bearish >= 2:
        return "Slowdown"

    # 4. Goldilocks: 성장↑ + 인플레 안정
    if growth_bullish >= 2 and infl_low >= 1:
        return "Goldilocks"

    # 5. Reflation: 성장↑ + 인플레↑
    if growth_bullish >= 2 and infl_rising >= 1:
        return "Reflation"

    # 6. 혼재: 보수적으로 성장 방향성 우선
    if growth_bearish >= 1:
        return "Slowdown"
    return "Goldilocks"


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


# ── HMM 앙상블 ──────────────────────────────────────────────────────────────

class HmmRegimeClassifier:
    """
    GaussianHMM 기반 비지도 레짐 분류기.

    학습: 역사적 피처 행렬로 4-상태 HMM 적합
    레이블 매핑: 각 HMM 상태를 규칙 기반 레짐과 매핑 (다수결)
    추론: 현재 피처 → 레짐별 사후 확률 dict

    의존성: hmmlearn, scikit-learn (requirements.txt)
    """

    N_STATES = 5  # Goldilocks / Reflation / Slowdown / Stagflation / Crisis

    def __init__(self) -> None:
        self._model = None
        self._scaler = None
        self._state_to_regime: dict[int, str] = {}

    def fit(self, feature_matrix) -> None:
        """
        피처 행렬로 HMM을 학습하고 상태-레짐 매핑을 결정한다.

        feature_matrix: pd.DataFrame with columns = HMM_FEATURE_COLS
        """
        from collections import Counter

        import numpy as np
        from hmmlearn import hmm
        from sklearn.preprocessing import StandardScaler

        from features import HMM_FEATURE_COLS

        X = feature_matrix[HMM_FEATURE_COLS].values.astype(float)

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        self._scaler = scaler

        model = hmm.GaussianHMM(
            n_components=self.N_STATES,
            covariance_type="diag",
            n_iter=200,
            random_state=42,
            tol=1e-4,
        )
        model.fit(X_scaled)
        self._model = model

        # 각 행의 규칙 기반 레짐을 레이블로 사용 → HMM 상태 매핑
        states = model.predict(X_scaled)
        rule_labels = [
            detect_regime(row)
            for row in feature_matrix[HMM_FEATURE_COLS].to_dict(orient="records")
        ]
        for s in range(self.N_STATES):
            idxs = [i for i, st in enumerate(states) if st == s]
            if idxs:
                labels = [rule_labels[i] for i in idxs]
                self._state_to_regime[s] = Counter(labels).most_common(1)[0][0]
            else:
                self._state_to_regime[s] = DEFAULT_REGIME

    def predict_proba(self, features: dict) -> dict[str, float]:
        """
        현재 피처에서 레짐별 사후 확률을 반환한다.

        HMM이 학습되지 않은 경우 균등 분포를 반환한다.
        """
        if self._model is None or self._scaler is None:
            return {r: 1.0 / len(REGIMES) for r in REGIMES}

        import numpy as np
        from features import HMM_FEATURE_COLS

        x = np.array([[features[k] for k in HMM_FEATURE_COLS]], dtype=float)
        x_scaled = self._scaler.transform(x)

        # predict_proba returns (n_samples, n_states) posterior
        state_probs = self._model.predict_proba(x_scaled)[0]

        regime_probs: dict[str, float] = {r: 0.0 for r in REGIMES}
        for s, prob in enumerate(state_probs):
            regime = self._state_to_regime.get(s, "Neutral")
            regime_probs[regime] += float(prob)

        return regime_probs


def compute_rule_confidence(features: dict, regime: str) -> float:
    """
    규칙 기반 레짐 판단의 신뢰도 [0.0, 1.0]을 반환한다.

    각 레짐에 기여하는 신호 수 / 최대 가능 신호 수로 계산한다.
    - Crisis     : 1.0 (임계값 초과는 항상 명확)
    - 기타 레짐  : (성장 신호 + 인플레 신호) / 최대 신호 수
    """
    mom1m  = features["momentum_1m"]
    mom3m  = features["momentum_3m"]
    vix    = features["vix"]
    credit = features["credit_signal"]
    hy_spread = features.get("hy_spread", 4.5)
    curve     = features.get("curve_10y2y", 0.5)

    if regime == "Crisis":
        return 1.0

    growth_bullish = sum([mom1m > 0.02, mom3m > 0.03, credit > 0.01])
    growth_bearish = sum([mom1m < -0.02, mom3m < -0.03, credit < -0.02])
    infl_rising    = sum([hy_spread > 5.0, curve > 1.5, vix > 25])
    infl_low       = sum([hy_spread < 4.0, curve < 0.5, vix < 18])

    if regime == "Goldilocks":
        return (growth_bullish + infl_low) / 6
    if regime == "Reflation":
        return (growth_bullish + infl_rising) / 6
    if regime == "Slowdown":
        return growth_bearish / 3
    if regime == "Stagflation":
        return (growth_bearish + infl_rising) / 6
    return 0.5


def ensemble_regime(
    rule_regime: str,
    hmm_probs: dict[str, float],
    override_threshold: float = 0.60,
) -> str:
    """
    규칙 기반 레짐과 HMM 확률 분포를 결합해 최종 레짐을 반환한다.

    HMM이 rule-based와 다른 레짐을 override_threshold 이상 확률로 지지하고,
    rule-based 레짐의 HMM 확률이 25% 미만인 경우에만 HMM 레짐을 채택한다.
    그 외에는 규칙 기반 레짐을 사용한다 (보수적 기본값).
    """
    hmm_top = max(hmm_probs, key=hmm_probs.get)
    if (
        hmm_top != rule_regime
        and hmm_probs[hmm_top] >= override_threshold
        and hmm_probs.get(rule_regime, 0.0) < 0.25
    ):
        return hmm_top
    return rule_regime
