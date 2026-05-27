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


def _growth_inflation_signals(features: dict) -> tuple[int, int, int, int]:
    """
    피처에서 성장·인플레 신호 카운트를 추출한다. detect_regime과 confidence 양쪽에서 사용.

    성장 신호 (4점 만점):
      momentum_1m, momentum_3m, credit_signal, yield_curve(10Y-2Y)
      ※ yield curve inversion(curve<0)은 침체 신호, 가파른 커브(curve>1.0)는 확장 신호.

    인플레 신호 (3점 만점):
      hy_spread, vix, commodity_mom_1m
      ※ curve는 인플레 지표가 아니므로 제외. 원자재 모멘텀이 더 직접적 인플레 시그널.
    """
    mom1m   = features["momentum_1m"]
    mom3m   = features["momentum_3m"]
    vix     = features["vix"]
    credit  = features["credit_signal"]
    hy_spr  = features.get("hy_spread", 4.5)         # 중립값
    curve   = features.get("curve_10y2y", 0.5)       # 중립값
    commod  = features.get("commodity_mom_1m", 0.0)  # 없으면 0

    growth_bullish = sum([
        mom1m > 0.02,
        mom3m > 0.03,
        credit > 0.01,
        curve > 1.0,   # 가파른 커브 = 확장 신호
    ])
    growth_bearish = sum([
        mom1m < -0.02,
        mom3m < -0.03,
        credit < -0.02,
        curve < 0.0,   # 역전 = 침체 선행 신호
    ])
    infl_rising = sum([
        hy_spr > 5.0,
        vix > 25,
        commod > 0.05,   # 원자재 +5% 모멘텀 = 인플레 압력
    ])
    infl_low = sum([
        hy_spr < 4.0,
        vix < 18,
        commod < -0.05,
    ])
    return growth_bullish, growth_bearish, infl_rising, infl_low


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

    성장 proxy: momentum_1m / momentum_3m / credit_signal / yield_curve(역전)
    인플레 proxy: hy_spread / vix / commodity_mom_1m
    """
    rvol = features["realized_vol"]
    vix  = features["vix"]

    # 1. Crisis: 유동성 쇼크
    if rvol > 0.30 or vix > 40:
        return "Crisis"

    growth_bullish, growth_bearish, infl_rising, infl_low = _growth_inflation_signals(features)

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
        self._default_confirm_n: int = cfg.get("confirmation_count", 3)
        self._default_cooldown: int = cfg.get("cooldown_days", 5)
        # 레짐별 override (예: Crisis는 confirm=1·cooldown=0). 키 없는 레짐은 default 사용.
        per_regime = cfg.get("per_regime") or {}
        self._per_regime: dict[str, dict] = {
            r: dict(v) for r, v in per_regime.items() if r in REGIMES and isinstance(v, dict)
        }

        confirmed = state.get("confirmed_regime")
        # 알 수 없는 레짐(예: 구버전의 "Neutral")은 None으로 처리 → 첫 raw 즉시 확정
        self._confirmed: str | None = confirmed if confirmed in REGIMES else None
        self._candidate: str | None = state.get("candidate_regime")
        self._count: int = state.get("candidate_count", 0)
        self._last_switch: str | None = state.get("last_switch_date")

    def _confirm_n_for(self, regime: str | None) -> int:
        if regime and regime in self._per_regime:
            return int(self._per_regime[regime].get("confirmation_count", self._default_confirm_n))
        return self._default_confirm_n

    def _cooldown_for(self, regime: str | None) -> int:
        if regime and regime in self._per_regime:
            return int(self._per_regime[regime].get("cooldown_days", self._default_cooldown))
        return self._default_cooldown

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

        # 전환하려는 레짐(raw) 기준으로 임계 결정 — Crisis로 빠르게 진입 가능.
        confirm_n = self._confirm_n_for(raw)
        cooldown = self._cooldown_for(raw)
        if self._count >= confirm_n and self._cooldown_ok(today, cooldown):
            self._confirmed = raw
            self._last_switch = today
            self._candidate = raw
            self._count = 1

        return self._confirmed

    def _cooldown_ok(self, today_iso: str, cooldown: int | None = None) -> bool:
        if cooldown is None:
            cooldown = self._default_cooldown
        if not self._last_switch:
            return True
        elapsed = (date.fromisoformat(today_iso) - date.fromisoformat(self._last_switch)).days
        return elapsed >= cooldown

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
        """현재 후보 레짐 기준 confirmation count (Crisis 등 per_regime override 반영)."""
        return self._confirm_n_for(self._candidate if self.is_transitioning else None)

    @property
    def is_transitioning(self) -> bool:
        """후보 레짐이 확정 레짐과 다른 전환 대기 상태."""
        return bool(self._candidate and self._candidate != self._confirmed)

    @property
    def cooldown_remaining(self) -> int:
        """현재 후보 레짐 기준 쿨다운 잔여 달력일 (0이면 이미 경과)."""
        if not self._last_switch:
            return 0
        cooldown = self._cooldown_for(self._candidate if self.is_transitioning else None)
        elapsed = (date.today() - date.fromisoformat(self._last_switch)).days
        return max(0, cooldown - elapsed)

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

    학습: 역사적 피처 행렬로 5-상태 HMM 적합
    레이블 매핑:
      Primary (unsupervised=True, 기본): 각 HMM state의 피처 통계(성장·인플레·변동성)로 레짐 결정.
        detect_regime 자기참조 없음. ambiguous할 경우 legacy로 자동 폴백.
      Legacy (unsupervised=False 또는 폴백 시): detect_regime 다수결 + 미매핑 레짐 강제 매핑.
    추론: 현재 피처 → 레짐별 사후 확률 dict

    의존성: hmmlearn, scikit-learn (requirements.txt)
    """

    N_STATES = 5  # Goldilocks / Reflation / Slowdown / Stagflation / Crisis

    # 기본 매핑 가중치 — config(hmm.mapping_weights)에서 override.
    # mom1m/mom3m/credit은 스케일이 ±0.05라 그대로 합산, curve/vix/hy/cpi는 스케일 보정.
    DEFAULT_MAPPING_WEIGHTS: dict[str, float] = {
        "growth_curve":   0.01,
        "infl_commodity": 1.0,
        "infl_hy_zscore": 0.03,
        "infl_cpi":       0.01,
        "infl_vix":       0.005,
    }
    DEFAULT_CRISIS_RVOL_THRESHOLD = 0.30  # 룰 기반 detect_regime의 rvol>0.30과 통일
    DEFAULT_CRISIS_RVOL_RATIO = 1.5

    def __init__(
        self,
        unsupervised_mapping: bool = True,
        mapping_weights: dict | None = None,
        crisis_rvol_threshold: float | None = None,
        crisis_rvol_ratio: float | None = None,
    ) -> None:
        self._model = None
        self._scaler = None
        self._state_to_regime: dict[int, str] = {}
        self._unsupervised_mapping = unsupervised_mapping
        self._mapping_method: str = "unknown"  # "unsupervised" | "legacy" | "legacy-fallback"

        weights = dict(self.DEFAULT_MAPPING_WEIGHTS)
        if mapping_weights:
            weights.update({k: float(v) for k, v in mapping_weights.items() if k in weights})
        self._mapping_weights = weights
        self._crisis_rvol_threshold = float(
            crisis_rvol_threshold if crisis_rvol_threshold is not None
            else self.DEFAULT_CRISIS_RVOL_THRESHOLD
        )
        self._crisis_rvol_ratio = float(
            crisis_rvol_ratio if crisis_rvol_ratio is not None
            else self.DEFAULT_CRISIS_RVOL_RATIO
        )

    def fit(self, feature_matrix) -> None:
        """
        피처 행렬로 HMM을 학습하고 상태-레짐 매핑을 결정한다.

        feature_matrix: pd.DataFrame with columns ⊇ active feature cols
        """
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
                tol=1e-3,
                min_covar=1e-3,
            )
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=ConvergenceWarning)
                with redirect_stderr(io.StringIO()):
                    m.fit(X_scaled)
            return m

        candidates = []
        for seed in (42, 7, 13):
            m = _fit_once(seed)
            converged = bool(getattr(getattr(m, "monitor_", None), "converged", False))
            score = float(m.score(X_scaled))
            candidates.append((converged, score, m))

        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        model = candidates[0][2]
        self._model = model

        states = model.predict(X_scaled)

        # ── 매핑 결정: unsupervised 우선, ambiguous하면 legacy 폴백 ──────────
        mapping: dict[int, str] | None = None
        if self._unsupervised_mapping:
            mapping = self._unsupervised_state_mapping(states, feature_matrix)
            if mapping is None:
                self._mapping_method = "legacy-fallback"
            else:
                self._mapping_method = "unsupervised"

        if mapping is None:
            mapping = self._legacy_state_mapping(states, feature_matrix)
            if not self._unsupervised_mapping:
                self._mapping_method = "legacy"

        self._state_to_regime = mapping

    def _unsupervised_state_mapping(
        self, states, feature_matrix
    ) -> dict[int, str] | None:
        """
        각 HMM state별 피처 평균에서 레짐을 추론한다.

        규칙:
          1. realized_vol이 가장 높고 충분히 극단적인 state → Crisis
          2. 나머지 state를 성장 score 기준 정렬:
             - 성장 score = momentum_1m + momentum_3m + credit_signal (+curve_10y2y 가중)
             - 인플레 score = commodity_mom_1m + hy_spread_zscore + (vix-20)/50 + cpi_yoy/10
          3. 4개 state면: 하위 2 (성장↓) → {Slowdown, Stagflation}, 상위 2 (성장↑) → {Goldilocks, Reflation}
             각 그룹 내에서 인플레 score 높은 쪽이 Stagflation / Reflation, 낮은 쪽이 Slowdown / Goldilocks
          4. ambiguous (분류된 레짐 < 3종 또는 빈 state ≥ 2개) → None 반환하여 legacy 폴백

        detect_regime 자기참조 없음. 매핑 품질이 낮을 경우 None을 돌려 legacy 경로로 위임.
        """
        import numpy as np

        cols = self._feature_cols

        # state별 피처 평균
        state_stats: dict[int, dict[str, float] | None] = {}
        empty_states = 0
        for s in range(self.N_STATES):
            idxs = np.where(states == s)[0]
            if len(idxs) == 0:
                state_stats[s] = None
                empty_states += 1
                continue
            means = {
                c: float(feature_matrix[c].iloc[idxs].mean())
                for c in cols
                if c in feature_matrix.columns
            }
            state_stats[s] = means

        if empty_states >= 2:
            return None  # 너무 많은 state가 비어 있음 → 매핑 신뢰도 낮음

        valid = [s for s, st in state_stats.items() if st is not None]
        if len(valid) < 3:
            return None

        # 1. Crisis 식별: realized_vol 최댓값이 충분히 극단적인 경우만 (config 임계 사용)
        rv_pairs = [(s, state_stats[s].get("realized_vol", 0.0)) for s in valid]
        rv_pairs.sort(key=lambda x: x[1], reverse=True)
        crisis_state: int | None = rv_pairs[0][0]
        crisis_rv = rv_pairs[0][1]
        second_rv = rv_pairs[1][1] if len(rv_pairs) > 1 else 0.0
        is_crisis_clear = crisis_rv >= self._crisis_rvol_threshold or (
            second_rv > 1e-6 and crisis_rv >= second_rv * self._crisis_rvol_ratio
        )
        if not is_crisis_clear:
            crisis_state = None

        # 2. 나머지 state의 성장·인플레 score 계산 (config 가중치 사용)
        w = self._mapping_weights

        def growth_score(st: dict[str, float]) -> float:
            score = 0.0
            score += st.get("momentum_1m", 0.0)
            score += st.get("momentum_3m", 0.0)
            score += st.get("credit_signal", 0.0)
            if "curve_10y2y" in st:
                # 가파른 커브(>1.0)는 확장, 역전(<0)은 침체 — 중립 0.5 기준
                score += (st["curve_10y2y"] - 0.5) * w["growth_curve"]
            return score

        def infl_score(st: dict[str, float]) -> float:
            score = 0.0
            score += st.get("commodity_mom_1m", 0.0) * w["infl_commodity"]
            if "hy_spread_zscore" in st:
                score += st["hy_spread_zscore"] * w["infl_hy_zscore"]
            if "cpi_yoy" in st:
                score += (st["cpi_yoy"] - 2.0) * w["infl_cpi"]
            if "vix" in st:
                score += (st["vix"] - 20.0) * w["infl_vix"]
            return score

        others = [s for s in valid if s != crisis_state]
        if not others:
            return None

        scored = [
            (s, growth_score(state_stats[s]), infl_score(state_stats[s]))
            for s in others
        ]

        mapping: dict[int, str] = {}
        if crisis_state is not None:
            mapping[crisis_state] = "Crisis"

        # 비지도 매핑은 성장 score 정렬 후 인플레로 결합
        scored.sort(key=lambda x: x[1])  # growth 오름차순
        n = len(scored)

        if n == 1:
            mapping[scored[0][0]] = DEFAULT_REGIME
        elif n == 2:
            lo, hi = scored
            mapping[lo[0]] = "Slowdown" if lo[1] < 0 else "Goldilocks"
            mapping[hi[0]] = "Reflation" if hi[2] > 0 else "Goldilocks"
        elif n == 3:
            lo, mid, hi = scored
            mapping[lo[0]] = "Stagflation" if lo[2] > 0 else "Slowdown"
            mapping[mid[0]] = "Goldilocks"
            mapping[hi[0]] = "Reflation" if hi[2] > 0 else "Goldilocks"
        else:  # n == 4
            neg = scored[:2]
            pos = scored[2:]
            # neg group: 인플레 높은 쪽 = Stagflation, 낮은 쪽 = Slowdown
            if neg[0][2] >= neg[1][2]:
                mapping[neg[0][0]] = "Stagflation"
                mapping[neg[1][0]] = "Slowdown"
            else:
                mapping[neg[0][0]] = "Slowdown"
                mapping[neg[1][0]] = "Stagflation"
            # pos group: 인플레 높은 쪽 = Reflation, 낮은 쪽 = Goldilocks
            if pos[0][2] >= pos[1][2]:
                mapping[pos[0][0]] = "Reflation"
                mapping[pos[1][0]] = "Goldilocks"
            else:
                mapping[pos[0][0]] = "Goldilocks"
                mapping[pos[1][0]] = "Reflation"

        # 비어 있던 state는 DEFAULT_REGIME로 채움 (predict_proba에서 거의 0 확률)
        for s in range(self.N_STATES):
            if s not in mapping:
                mapping[s] = DEFAULT_REGIME

        # 매핑 품질 검증: 분류된 distinct 레짐이 3종 이상이어야 신뢰
        distinct = set(mapping[s] for s in valid)
        if len(distinct) < 3:
            return None

        return mapping

    def _legacy_state_mapping(self, states, feature_matrix) -> dict[int, str]:
        """detect_regime 다수결 기반 매핑 (legacy 및 unsupervised 폴백)."""
        from collections import Counter

        rule_labels = [
            detect_regime(row)
            for row in feature_matrix.to_dict(orient="records")
        ]

        mapping: dict[int, str] = {}
        for s in range(self.N_STATES):
            idxs = [i for i, st in enumerate(states) if st == s]
            if idxs:
                labels = [rule_labels[i] for i in idxs]
                mapping[s] = Counter(labels).most_common(1)[0][0]
            else:
                mapping[s] = DEFAULT_REGIME

        # 미매핑 희귀 레짐 강제 매핑 (Crisis/Stagflation 보강)
        seen_regimes = set(rule_labels)
        mapped_regimes = set(mapping.values())
        unmapped = seen_regimes - mapped_regimes
        for regime in unmapped:
            regime_idxs = [i for i, lbl in enumerate(rule_labels) if lbl == regime]
            if not regime_idxs:
                continue
            regime_states = [states[i] for i in regime_idxs]
            dominant_state = Counter(regime_states).most_common(1)[0][0]
            mapping[dominant_state] = regime

        return mapping

    @property
    def state_to_regime(self) -> dict[int, str]:
        """학습 후 결정된 state→regime 매핑 (없으면 빈 dict)."""
        return dict(self._state_to_regime)

    @property
    def mapping_method(self) -> str:
        """매핑 경로: unsupervised | legacy | legacy-fallback | unknown."""
        return self._mapping_method

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

        # 학습 시점에는 있던 컬럼이 추론 시 데이터 fetch 실패로 누락될 수 있음.
        # 0으로 폴백 (BalancedRF.predict_proba와 동일한 방어 패턴).
        missing = [c for c in cols if c not in feature_sequence.columns]
        if missing:
            feature_sequence = feature_sequence.copy()
            for c in missing:
                feature_sequence[c] = 0.0
            print(f"    [HMM 추론] 누락 컬럼 {missing} → 0으로 폴백")

        X = feature_sequence[cols].values.astype(float)
        X_scaled = self._scaler.transform(X)

        # predict_proba returns (T, n_states) — 마지막 시점 사후 확률 사용
        state_probs = self._model.predict_proba(X_scaled)[-1]

        regime_probs: dict[str, float] = {r: 0.0 for r in REGIMES}
        for s, prob in enumerate(state_probs):
            regime = self._state_to_regime.get(s, DEFAULT_REGIME)
            regime_probs[regime] += float(prob)

        return regime_probs

    def predict_proba_forward(self, feature_sequence, horizon: int = 1) -> dict[str, float]:
        """
        Transition matrix를 곱해 horizon-step ahead 사후 확률을 반환한다.

        P(state_{t+h}) = P(state_t) @ transmat^h
        그 후 state→regime 매핑으로 레짐 확률 변환.

        호리즌이 길수록 transition 누적이 stationary distribution에 수렴 →
        h=1, 2 정도만 의미 있음. h=1이 기본.

        Crisis transition 진입 후행 문제(진단 결과)를 완화하기 위한 forward 시그널.
        """
        if self._model is None or self._scaler is None:
            return {r: 1.0 / len(REGIMES) for r in REGIMES}

        import numpy as np

        cols = getattr(self, "_feature_cols", None)
        if cols is None:
            from features import get_active_feature_cols
            cols = get_active_feature_cols(feature_sequence)

        missing = [c for c in cols if c not in feature_sequence.columns]
        if missing:
            feature_sequence = feature_sequence.copy()
            for c in missing:
                feature_sequence[c] = 0.0

        X = feature_sequence[cols].values.astype(float)
        X_scaled = self._scaler.transform(X)
        state_probs = self._model.predict_proba(X_scaled)[-1]

        # transition matrix를 horizon회 적용
        transmat = self._model.transmat_  # (N, N), [i,j] = P(s_{t+1}=j | s_t=i)
        forward = state_probs.copy()
        for _ in range(max(1, int(horizon))):
            forward = forward @ transmat

        regime_probs: dict[str, float] = {r: 0.0 for r in REGIMES}
        for s, prob in enumerate(forward):
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
    학습 라벨링 (`forward_window`, `label_mode`):
      forward_window=0
        → 동일 시점 detect_regime() (룰의 근사기 — 자기참조 있음, 기존 동작)
      forward_window=N>0, label_mode='rule_at_future' (기본)
        → t+N 시점 detect_regime() 라벨 (옵션 1, forward-looking — 룰 임계 그대로 사용)
      forward_window=N>0, label_mode='quantile'
        → t+N 시점의 (momentum_1m, realized_vol) 학습 분포 분위 기반 라벨 (옵션 2)
          매핑:
            - rvol  ≥ p80                       → Crisis
            - return ≥ p60, rvol  < median      → Goldilocks
            - return ≥ p60, rvol ≥ median       → Reflation
            - return ≤ p40, rvol ≥ median       → Stagflation
            - 그 외                             → Slowdown
          detect_regime 호출 없음 → 룰의 절대 임계·카운팅 편향에서 자유로움.
      forward_window>0인데 표본 부족(len(fm) ≤ N+1)이면 rule 라벨로 안전 폴백.

    HMM이 순서 정보(전이 확률)를 담당하고,
    RF는 피처 공간에서 소수 클래스 경계를 더 민감하게 학습하는 역할을 한다.
    """

    VALID_LABEL_MODES = ("rule_at_future", "quantile", "forward_quantile_v2")

    def __init__(
        self,
        forward_window: int = 0,
        label_mode: str = "rule_at_future",
    ) -> None:
        self._model = None
        self._scaler = None
        self._forward_window = int(forward_window)
        if label_mode not in self.VALID_LABEL_MODES:
            raise ValueError(
                f"label_mode must be one of {self.VALID_LABEL_MODES}, got {label_mode!r}"
            )
        self._label_mode = label_mode
        self._label_method: str = "rule"
        self._train_samples: int = 0

    @property
    def label_method(self) -> str:
        return self._label_method

    @property
    def train_samples(self) -> int:
        """학습에 실제로 사용된 표본 수 (forward 모드에선 N만큼 줄어듦)."""
        return self._train_samples

    def fit(self, feature_matrix) -> None:
        """피처 행렬로 RF를 학습한다. 라벨링은 forward_window·label_mode에 따라 분기."""
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.preprocessing import StandardScaler

        from features import get_active_feature_cols

        active_cols = get_active_feature_cols(feature_matrix)
        self._feature_cols = active_cols

        n = len(feature_matrix)
        records = feature_matrix.to_dict(orient="records")

        if self._forward_window > 0:
            fw = self._forward_window
            if n <= fw + 1:
                labels = [detect_regime(r) for r in records]
                train_fm = feature_matrix
                self._label_method = "rule (forward 폴백: 표본 부족)"
            elif self._label_mode == "quantile":
                labels = self._compute_quantile_labels(feature_matrix, fw)
                if labels is None:
                    labels = [detect_regime(r) for r in records]
                    train_fm = feature_matrix
                    self._label_method = "rule (quantile 폴백: 컬럼 누락)"
                else:
                    train_fm = feature_matrix.iloc[:n - fw]
                    self._label_method = f"forward_quantile_{fw}"
            elif self._label_mode == "forward_quantile_v2":
                labels = self._compute_quantile_labels_v2(feature_matrix, fw)
                if labels is None:
                    labels = [detect_regime(r) for r in records]
                    train_fm = feature_matrix
                    self._label_method = "rule (quantile_v2 폴백: 컬럼 누락)"
                else:
                    train_fm = feature_matrix.iloc[:n - fw]
                    self._label_method = f"forward_quantile_v2_{fw}"
            else:  # 'rule_at_future'
                labels = [detect_regime(records[t + fw]) for t in range(n - fw)]
                train_fm = feature_matrix.iloc[:n - fw]
                self._label_method = f"forward_rule_{fw}"
        else:
            labels = [detect_regime(r) for r in records]
            train_fm = feature_matrix
            self._label_method = "rule"

        self._train_samples = len(train_fm)
        X = train_fm[active_cols].values.astype(float)

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

    @staticmethod
    def _compute_quantile_labels(feature_matrix, forward_window: int):
        """
        t의 라벨 = t+forward_window 시점의 (momentum_1m, realized_vol) 분위 매핑.

        feature_matrix에 momentum_1m·realized_vol가 모두 있어야 한다 (없으면 None 반환 → 폴백).
        detect_regime 호출 없음. forward 통계는 학습 분포 분위로 비교 → 절대 임계 회피.
        """
        import numpy as np

        cols_needed = ("momentum_1m", "realized_vol")
        if not all(c in feature_matrix.columns for c in cols_needed):
            return None

        fw = forward_window
        n = len(feature_matrix)
        # 학습 셋(t=0..n-fw-1)의 라벨에 사용할 forward 통계 — t+fw 시점 값을 본다
        fwd_returns = feature_matrix["momentum_1m"].iloc[fw:fw + (n - fw)].values
        fwd_rvols = feature_matrix["realized_vol"].iloc[fw:fw + (n - fw)].values

        if len(fwd_returns) == 0:
            return None

        # 학습 분포에서의 분위 (스칼라)
        ret_p60 = float(np.nanpercentile(fwd_returns, 60))
        ret_p40 = float(np.nanpercentile(fwd_returns, 40))
        rvol_p80 = float(np.nanpercentile(fwd_rvols, 80))
        rvol_med = float(np.nanmedian(fwd_rvols))

        labels: list[str] = []
        for r, v in zip(fwd_returns, fwd_rvols):
            if np.isnan(r) or np.isnan(v):
                labels.append("Slowdown")
                continue
            if v >= rvol_p80:
                labels.append("Crisis")
            elif r >= ret_p60 and v < rvol_med:
                labels.append("Goldilocks")
            elif r >= ret_p60:
                labels.append("Reflation")
            elif r <= ret_p40 and v >= rvol_med:
                labels.append("Stagflation")
            else:
                labels.append("Slowdown")
        return labels

    @staticmethod
    def _compute_quantile_labels_v2(feature_matrix, forward_window: int):
        """
        t의 라벨 = t+forward_window 시점의 (momentum_1m, realized_vol) 옵션 2 매핑.

        Phase 1 시뮬레이션과 동일 매핑:
          - Crisis      : forward 변동성 top 10%
          - Reflation   : forward 수익률 top 30% + 변동성 ≥ median
          - Goldilocks  : forward 수익률 top 30% + 변동성 < median
          - Stagflation : forward 수익률 bottom 30% + 변동성 ≥ median
          - Slowdown    : forward 수익률 bottom 30% + 변동성 < median
          - 나머지 ~40%: 가장 가까운 코어에 할당 (z-score Manhattan 거리)

        기존 'quantile' 모드(p80 Crisis, p60/p40 cutoff, default Slowdown)와 차이:
          - Crisis 임계 더 엄격 (p80 → p90)
          - 코어 4-quadrant 명확히 분리 (top/bottom 30%)
          - 나머지 시점을 default Slowdown이 아니라 가장 가까운 코어에 할당
        """
        import numpy as np

        cols_needed = ("momentum_1m", "realized_vol")
        if not all(c in feature_matrix.columns for c in cols_needed):
            return None

        fw = forward_window
        n = len(feature_matrix)
        fwd_returns = feature_matrix["momentum_1m"].iloc[fw:fw + (n - fw)].values
        fwd_rvols = feature_matrix["realized_vol"].iloc[fw:fw + (n - fw)].values

        if len(fwd_returns) == 0:
            return None

        ret_p70 = float(np.nanpercentile(fwd_returns, 70))
        ret_p30 = float(np.nanpercentile(fwd_returns, 30))
        vol_p90 = float(np.nanpercentile(fwd_rvols, 90))
        vol_med = float(np.nanmedian(fwd_rvols))

        # 코어 중심점 (Manhattan 거리 측정용)
        core_centers = {
            "Goldilocks":  (ret_p70, 0.0),
            "Reflation":   (ret_p70, vol_med),
            "Slowdown":    (ret_p30, 0.0),
            "Stagflation": (ret_p30, vol_med),
        }

        labels: list[str] = []
        for r, v in zip(fwd_returns, fwd_rvols):
            if np.isnan(r) or np.isnan(v):
                labels.append("Slowdown")
                continue
            # 1. Crisis: 변동성 극단
            if v >= vol_p90:
                labels.append("Crisis")
                continue
            # 2. 코어 4-quadrant
            if r >= ret_p70 and v < vol_med:
                labels.append("Goldilocks")
                continue
            if r >= ret_p70 and v >= vol_med:
                labels.append("Reflation")
                continue
            if r <= ret_p30 and v < vol_med:
                labels.append("Slowdown")
                continue
            if r <= ret_p30 and v >= vol_med:
                labels.append("Stagflation")
                continue
            # 3. 중간 ~40%: 가장 가까운 코어에 (Manhattan)
            best = min(core_centers.items(),
                       key=lambda kv: abs(r - kv[1][0]) + abs(v - kv[1][1]))
            labels.append(best[0])
        return labels

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


class AnomalyDetector:
    """
    IsolationForest 기반 시장 이상 상태 탐지기 (unsupervised, detect_regime 자기참조 없음).

    레짐 분류기가 아닌 **독립적인 보조 신호**.
    현재 피처 조합이 학습 분포에서 얼마나 벗어났는지 0~1 점수로 반환.
    - score ≈ 0  → 학습 분포 내부, 익숙한 상태
    - score ≈ 1  → 학습 데이터에서 본 적 없는 패턴 (Black Swan 후보)

    용도:
    - 높은 anomaly_score → 레짐 분류 신뢰도 하향 → confidence_threshold 폴백 트리거
    - 라벨 불필요(unsupervised) → HMM/RF의 detect_regime 자기참조 문제와 무관

    구현: 학습 데이터의 IsolationForest decision_function 분포에서의 percentile rank.
    """

    def __init__(self, contamination: float = 0.05) -> None:
        self._model = None
        self._scaler = None
        self._train_scores = None
        self._contamination = contamination
        self._feature_cols: list[str] = []

    def fit(self, feature_matrix) -> None:
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import StandardScaler
        import numpy as np

        from features import get_active_feature_cols

        active_cols = get_active_feature_cols(feature_matrix)
        self._feature_cols = active_cols

        X = feature_matrix[active_cols].values.astype(float)
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        X_scaled = np.clip(X_scaled, -6.0, 6.0)
        self._scaler = scaler

        self._model = IsolationForest(
            n_estimators=200,
            contamination=self._contamination,
            random_state=42,
            n_jobs=-1,
        )
        self._model.fit(X_scaled)
        # 학습 분포 보존 — 추론 시 percentile rank 계산에 사용
        self._train_scores = self._model.decision_function(X_scaled)

    def anomaly_score(self, features) -> float:
        """
        현재 피처가 학습 분포에서 얼마나 이상한지 0~1 점수 반환.

        features: dict (단일 시점) 또는 pd.DataFrame (마지막 row 사용).
        구현: 학습 decision_function 분포에서 현재 raw 점수의 rank.
              raw가 낮을수록(더 anomalous) anomaly score가 높음.
        """
        if self._model is None or self._scaler is None or self._train_scores is None:
            return 0.0

        import numpy as np

        cols = self._feature_cols
        if isinstance(features, dict):
            x = np.array([[features.get(c, 0.0) for c in cols]], dtype=float)
        else:
            seq = features.copy()
            for c in cols:
                if c not in seq.columns:
                    seq[c] = 0.0
            x = seq[cols].tail(1).values.astype(float)

        x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
        x_scaled = self._scaler.transform(x)
        x_scaled = np.clip(x_scaled, -6.0, 6.0)

        raw = float(self._model.decision_function(x_scaled)[0])
        # 학습 분포에서 현재 raw보다 낮은(=더 이상한) 비율 = anomaly rank
        # raw < min(train) → anomaly 1.0, raw > max(train) → anomaly 0.0
        rank_in_training = float(np.sum(self._train_scores <= raw)) / len(self._train_scores)
        return float(1.0 - rank_in_training)


def compute_combined_confidence(
    rule_conf: float,
    hmm_conf: float | None,
    method: str = "mean",
) -> float:
    """
    규칙 기반 신뢰도와 HMM 사후 확률을 결합한 단일 신뢰도를 반환한다.

    method:
      "mean"    — (rule_conf + hmm_conf) / 2  (기존 동작, 단조성 보장 안 함)
      "min"     — min(rule_conf, hmm_conf)    (보수: 두 신호 동의 강도)
      "product" — rule_conf * hmm_conf        (보수: 두 신호 곱)

    hmm_conf가 None이면(HMM 비활성/학습 데이터 부족) rule_conf 그대로 사용.
    """
    if hmm_conf is None:
        return float(rule_conf)
    if method == "min":
        return float(min(rule_conf, hmm_conf))
    if method == "product":
        return float(rule_conf * hmm_conf)
    # 기본/미지정/'mean'
    return float((rule_conf + hmm_conf) / 2)


def compute_rule_confidence(features: dict, regime: str) -> float:
    """
    규칙 기반 레짐 판단의 신뢰도 [0.0, 1.0]을 반환한다.

    각 레짐에 기여하는 신호 수 / 최대 가능 신호 수로 계산한다.
    성장 신호 max=4, 인플레 신호 max=3 — 일관된 분모 사용.
    - Crisis: 1.0
    - Slowdown: growth_bearish / 4 (인플레 무관)
    - Goldilocks/Reflation/Stagflation: (growth + infl) / 7
    """
    if regime == "Crisis":
        return 1.0

    growth_bullish, growth_bearish, infl_rising, infl_low = _growth_inflation_signals(features)

    if regime == "Goldilocks":
        return (growth_bullish + infl_low) / 7
    if regime == "Reflation":
        return (growth_bullish + infl_rising) / 7
    if regime == "Slowdown":
        return growth_bearish / 4
    if regime == "Stagflation":
        return (growth_bearish + infl_rising) / 7
    return 0.5


def ensemble_regime(
    rule_regime: str,
    combined_probs: dict[str, float],
    override_threshold: float = 0.60,
    crisis_priority_threshold: float | None = None,
) -> str:
    """
    규칙 기반 레짐과 (HMM+RF 가중평균) 확률 분포를 결합해 최종 레짐을 반환한다.

    1. Crisis 비대칭 우선순위 (`crisis_priority_threshold`, None이면 비활성):
       blend["Crisis"]가 임계 이상이면 다른 조건 무시하고 즉시 Crisis 반환.
       이유: Crisis 진입 지연이 가장 큰 transition 손실을 만들기 때문 (진단 결과).
       위험 진입의 false positive 비용은 보수 자산 비중 일시 상승으로 제한적인 반면
       Crisis 진입 지연의 비용은 폭락 구간 추가 노출.

    2. 그 외 일반 override:
       blend top이 rule과 다르고, top 확률이 override_threshold 이상,
       rule 확률이 25% 미만인 경우에만 다수 레짐 채택.
       그 외에는 보수적으로 rule 유지.
    """
    # 1. Crisis 비대칭 우선 (옵트인)
    if (
        crisis_priority_threshold is not None
        and combined_probs.get("Crisis", 0.0) >= crisis_priority_threshold
    ):
        return "Crisis"

    # 2. 일반 다수결 override
    top = max(combined_probs, key=combined_probs.get)
    if (
        top != rule_regime
        and combined_probs[top] >= override_threshold
        and combined_probs.get(rule_regime, 0.0) < 0.25
    ):
        return top
    return rule_regime
