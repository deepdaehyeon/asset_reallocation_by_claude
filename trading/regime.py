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

        confirmed = state.get("confirmed_regime")
        # 알 수 없는 레짐(예: 구버전의 "Neutral")은 None으로 처리 → 첫 raw 즉시 확정
        self._confirmed: str | None = confirmed if confirmed in REGIMES else None
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

        feature_matrix: pd.DataFrame with columns ⊇ active feature cols
        """
        from collections import Counter
        import io
        import warnings
        from contextlib import redirect_stderr

        import numpy as np
        from hmmlearn import hmm
        from sklearn.preprocessing import StandardScaler
        from sklearn.exceptions import ConvergenceWarning

        from features import get_active_feature_cols

        active_cols = get_active_feature_cols(feature_matrix)
        self._feature_cols = active_cols

        X = feature_matrix[active_cols].values.astype(float)
        # 수치 안정성: NaN/Inf 제거 후 표준화, 극단값 클리핑
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        X_scaled = np.nan_to_num(X_scaled, nan=0.0, posinf=0.0, neginf=0.0)
        X_scaled = np.clip(X_scaled, -6.0, 6.0)
        self._scaler = scaler

        def _fit_once(seed: int):
            m = hmm.GaussianHMM(
                n_components=self.N_STATES,
                covariance_type="diag",
                n_iter=300,
                random_state=seed,
                tol=1e-3,        # 수렴 판정 완화 (경고/진동 감소)
                min_covar=1e-3,  # 공분산 바닥값으로 수치 불안정 완화
            )
            with warnings.catch_warnings():
                # hmmlearn 내부 EM 모니터 경고는 빈번하며, 수렴 여부는 monitor_로 재확인한다.
                warnings.filterwarnings("ignore", category=ConvergenceWarning)
                # 일부 환경에서는 수렴 메시지가 stderr로 직접 출력될 수 있어, 학습 구간에서만 숨긴다.
                with redirect_stderr(io.StringIO()):
                    m.fit(X_scaled)
            return m

        # HMM은 초기값/seed에 민감하므로, 몇 번 재시도 후 가장 좋은 모델을 채택
        candidates = []
        for seed in (42, 7, 13):
            m = _fit_once(seed)
            converged = bool(getattr(getattr(m, "monitor_", None), "converged", False))
            score = float(m.score(X_scaled))
            candidates.append((converged, score, m))

        # 1순위: converged=True 중 score 최대, 2순위: score 최대
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        model = candidates[0][2]
        self._model = model

        # 각 행의 규칙 기반 레짐을 레이블로 사용 → HMM 상태 매핑
        # 사용 가능한 모든 열을 detect_regime에 전달 (더 정확한 레이블 생성)
        states = model.predict(X_scaled)
        rule_labels = [
            detect_regime(row)
            for row in feature_matrix.to_dict(orient="records")
        ]
        for s in range(self.N_STATES):
            idxs = [i for i, st in enumerate(states) if st == s]
            if idxs:
                labels = [rule_labels[i] for i in idxs]
                self._state_to_regime[s] = Counter(labels).most_common(1)[0][0]
            else:
                self._state_to_regime[s] = DEFAULT_REGIME

    def predict_proba(self, feature_sequence) -> dict[str, float]:
        """
        피처 시퀀스(최근 N일)에서 마지막 시점의 레짐별 사후 확률을 반환한다.

        단일 관측값 대신 시퀀스 전체를 HMM에 통과시켜 전이 확률(transition matrix)과
        과거 문맥이 반영된 마지막 시점의 사후 확률을 사용한다.

        feature_sequence: 학습 시 사용한 피처 컬럼을 가진 pd.DataFrame (shape: T × n_features)
        """
        if self._model is None or self._scaler is None:
            return {r: 1.0 / len(REGIMES) for r in REGIMES}

        import numpy as np

        cols = getattr(self, "_feature_cols", None)
        if cols is None:
            from features import get_active_feature_cols
            cols = get_active_feature_cols(feature_sequence)

        X = feature_sequence[cols].values.astype(float)
        X_scaled = self._scaler.transform(X)

        # predict_proba returns (T, n_states) — 마지막 시점 사후 확률 사용
        state_probs = self._model.predict_proba(X_scaled)[-1]

        regime_probs: dict[str, float] = {r: 0.0 for r in REGIMES}
        for s, prob in enumerate(state_probs):
            regime = self._state_to_regime.get(s, DEFAULT_REGIME)
            regime_probs[regime] += float(prob)

        return regime_probs

    def get_transition_matrix(self) -> dict[str, dict[str, float]]:
        """
        HMM 내부 전이 확률을 레짐 레이블 기준으로 반환한다.

        반환: {from_regime: {to_regime: prob}} — 확률 합 = 1.0 (행 기준)
        """
        if self._model is None:
            return {}

        import numpy as np

        trans: dict[str, dict[str, float]] = {r: {r2: 0.0 for r2 in REGIMES} for r in REGIMES}
        row_count: dict[str, float] = {r: 0.0 for r in REGIMES}

        transmat = self._model.transmat_
        for s_from in range(self.N_STATES):
            r_from = self._state_to_regime.get(s_from, DEFAULT_REGIME)
            for s_to in range(self.N_STATES):
                r_to = self._state_to_regime.get(s_to, DEFAULT_REGIME)
                trans[r_from][r_to] += float(transmat[s_from, s_to])
            row_count[r_from] += 1.0

        # 동일 레짐으로 매핑된 상태가 여럿이면 평균
        for r in REGIMES:
            if row_count[r] > 1:
                trans[r] = {r2: v / row_count[r] for r2, v in trans[r].items()}

        return trans

    def get_transition_entropy(self) -> float:
        """
        현재 레짐 분포의 전환 불확실성을 Shannon entropy로 반환한다 (0 = 완전 확실).

        마지막 predict_proba()를 호출하지 않고도 모델 학습 직후 호출 가능.
        전이 행렬의 행별 entropy 가중 평균 (가중치 = stationary distribution).
        """
        if self._model is None:
            return float("nan")

        import numpy as np

        transmat = self._model.transmat_  # (N, N)
        # 정상 분포 (eigenvector)
        eigenvalues, eigenvectors = np.linalg.eig(transmat.T)
        stationary = np.real(eigenvectors[:, np.argmax(np.real(eigenvalues))])
        stationary = np.abs(stationary)
        stationary /= stationary.sum()

        entropy = 0.0
        for s in range(self.N_STATES):
            row = transmat[s]
            row_ent = -float(np.sum(row * np.log(np.maximum(row, 1e-12))))
            entropy += stationary[s] * row_ent

        return float(entropy)


class BalancedRFClassifier:
    """
    RandomForest 기반 레짐 분류기.

    목적: GaussianHMM의 소수 레짐(Crisis/Stagflation) 과소 탐지 보완.
    학습: 피처 행렬 각 행에 규칙 기반 레이블을 부여한 뒤 RF 학습.
    특징: class_weight='balanced' → 희귀 레짐에 자동 고가중치.
    추론: 현재 피처 벡터(단일 행) → 레짐별 확률 dict.

    HMM이 순서 정보(전이 확률)를 담당하고,
    RF는 피처 공간에서 소수 클래스 경계를 더 민감하게 학습하는 역할을 한다.
    """

    def __init__(self) -> None:
        self._model = None
        self._scaler = None

    def fit(self, feature_matrix) -> None:
        """피처 행렬로 RF를 학습한다. 레이블은 규칙 기반 detect_regime()으로 생성."""
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.preprocessing import StandardScaler

        from features import get_active_feature_cols

        active_cols = get_active_feature_cols(feature_matrix)
        self._feature_cols = active_cols

        X = feature_matrix[active_cols].values.astype(float)
        # 모든 available 열을 detect_regime에 전달해 더 정확한 레이블 생성
        labels = [
            detect_regime(row)
            for row in feature_matrix.to_dict(orient="records")
        ]

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        self._scaler = scaler

        self._model = RandomForestClassifier(
            n_estimators=200,
            class_weight="balanced",
            max_depth=6,
            min_samples_leaf=5,
            random_state=42,
            n_jobs=-1,
        )
        self._model.fit(X_scaled, labels)

    def predict_proba(self, features: dict) -> dict[str, float]:
        """현재 피처 dict → 레짐별 확률 (클래스 균형 가중치 반영)."""
        if self._model is None or self._scaler is None:
            return {r: 1.0 / len(REGIMES) for r in REGIMES}

        import numpy as np

        cols = getattr(self, "_feature_cols", None)
        if cols is None:
            from features import PRICE_FEATURE_COLS
            cols = PRICE_FEATURE_COLS

        x = np.array([[features.get(c, 0.0) for c in cols]], dtype=float)
        x_scaled = self._scaler.transform(x)
        proba = self._model.predict_proba(x_scaled)[0]

        classes = list(self._model.classes_)
        regime_probs: dict[str, float] = {r: 0.0 for r in REGIMES}
        for i, cls in enumerate(classes):
            if cls in regime_probs:
                regime_probs[cls] = float(proba[i])

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
