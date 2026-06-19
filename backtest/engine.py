"""
워크포워드 백테스트 엔진.

과적합 방지 설계:
  - 미래 데이터 참조 없음: 모든 결정은 rebal_date 이전 데이터만 사용
  - 파라미터 최적화 없음: 현재 config를 그대로 검증
  - HMM은 매 리밸런싱마다 과거 데이터로 재학습 (walk-forward)
"""
from __future__ import annotations

import io
import sys
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning

# trading/ 모듈 참조 (sys.path에 추가)
_TRADING = Path(__file__).parent.parent / "trading"
if str(_TRADING) not in sys.path:
    sys.path.insert(0, str(_TRADING))

from features import compute_features, compute_feature_matrix
from regime import (
    DEFAULT_REGIME,
    REGIMES,
    AnomalyDetector,
    BalancedRFClassifier,
    HmmRegimeClassifier,
    apply_blend_smoothing,
    apply_corroboration_gate,
    compute_combined_confidence,
    compute_rule_confidence,
    detect_regime,
    ensemble_regime,
)
from portfolio import (
    apply_class_caps,
    apply_core_satellite,
    apply_dynamic_class_caps,
    apply_risk_controls,
    apply_vol_targeting,
    blend_regime_targets,
    compute_portfolio_ewma_vol,
    derive_account_weights,
    merge_to_total_weights,
)


@contextmanager
def _quiet():
    """portfolio.py의 print 출력을 억제한다."""
    old = sys.stdout
    sys.stdout = io.StringIO()
    try:
        yield
    finally:
        sys.stdout = old


class BacktestEngine:
    """
    워크포워드 백테스트 엔진.

    Parameters
    ----------
    config        : trading/config.yaml 내용 (dict)
    universe_px   : 유니버스 종목 일별 가격 DataFrame (columns = ticker)
    signal_px     : 레짐 신호용 가격 DataFrame (SPY / ^VIX / TLT / HYG)
    start         : 백테스트 시작일 "YYYY-MM-DD"
    end           : 백테스트 종료일 "YYYY-MM-DD"
    rebal_freq    : 리밸런싱 주기 pandas offset 문자열
                    'W-FRI' = 주별(금요일) / 'BMS' = 월초
    tx_cost       : 편도 거래비용 (기본 0.001 = 0.1%)
    usd_ratio     : USD 계좌 비중 (기본 0.30, 환율 변동 미반영)
    """

    def __init__(
        self,
        config: dict,
        universe_px: pd.DataFrame,
        signal_px: pd.DataFrame,
        start: str,
        end: str,
        rebal_freq: str = "W-FRI",
        tx_cost: float = 0.001,
        usd_ratio: float = 0.30,
        drift_threshold: Optional[float] = None,
        cooldown_days: int = 7,
        fred_history: Optional[pd.DataFrame] = None,
        trigger_mode: bool = False,
    ) -> None:
        self.config = deepcopy(config)
        self.trigger_mode = trigger_mode
        self.universe_px = universe_px[start:end]
        self.signal_px = signal_px
        self.start = start
        self.end = end
        self.rebal_freq = rebal_freq
        self.tx_cost = tx_cost
        self.usd_ratio = usd_ratio
        self.drift_threshold = drift_threshold
        self.cooldown_days = cooldown_days
        self.fred_history = fred_history

        hmm_cfg = config.get("hmm", {})
        self.hmm_enabled = hmm_cfg.get("enabled", True)
        self.hmm_lookback = hmm_cfg.get("lookback_days", 500)
        self.hmm_min = hmm_cfg.get("min_samples", 100)
        self.override_thr = hmm_cfg.get("override_threshold", 0.60)
        self.crisis_priority_threshold = hmm_cfg.get("crisis_priority_threshold", None)
        self.use_forward_hmm = bool(hmm_cfg.get("use_forward_hmm", False))
        self.forward_hmm_horizon = int(hmm_cfg.get("forward_hmm_horizon", 1))
        self.predict_lookback = hmm_cfg.get("predict_lookback", 60)
        self.rf_enabled = hmm_cfg.get("rf_enabled", True)
        self.rf_weight = float(hmm_cfg.get("rf_weight", 0.40))
        self.hmm_fit_seeds = list(hmm_cfg.get("fit_seeds", [])) or None
        self.rf_forward_window = int(hmm_cfg.get("rf_forward_window", 0))
        self.rf_label_mode = str(hmm_cfg.get("rf_label_mode", "rule_at_future"))
        self.confidence_method = str(
            config.get("regime_filter", {}).get("confidence_method", "mean")
        )
        self.blend_smoothing_alpha = float(
            config.get("regime_filter", {}).get("blend_smoothing_alpha", 0.0)
        )
        # 신뢰도 가변 평활 (옵트인). off면 기존 고정-α 평활과 동일하게 동작.
        cs_cfg = config.get("regime_filter", {}).get("confidence_smoothing", {}) or {}
        self.conf_smoothing_enabled = bool(cs_cfg.get("enabled", False))
        self.conf_smoothing_ref = float(cs_cfg.get("conf_ref", 0.4))
        # 비-Crisis 디리스크 코로보레이션 게이트 (옵트인, 레버 C).
        cg_cfg = config.get("regime_filter", {}).get("corroboration_gate", {}) or {}
        self.corrob_gate_enabled = bool(cg_cfg.get("enabled", False))
        self.corrob_gate_gamma = float(cg_cfg.get("gamma", 0.0))
        anomaly_cfg = config.get("anomaly", {})
        self.anomaly_enabled = bool(anomaly_cfg.get("enabled", True))
        self.anomaly_contamination = float(anomaly_cfg.get("contamination", 0.05))
        self.anomaly_penalty = float(anomaly_cfg.get("confidence_penalty", 0.5))
        # 층 2: acting regime을 rule(빠른 타이밍) vs ensemble final로 선택. blend는 항상 HMM 유지.
        self.regime_timing_source = str(
            config.get("regime_filter", {}).get("regime_timing_source", "ensemble")
        )
        # 워크포워드 진행 중 직전 blend를 보관 (EWMA 평활용). _get_regime 호출 사이에 유지.
        self._prev_blend: Optional[Dict[str, float]] = None
        # Transition phase 추적: 직전 confirmed regime이 바뀐 시점 기록
        self.transition_days = int(
            config.get("regime_filter", {}).get("transition_days", 0)
        )
        self._last_regime_change_date: Optional[pd.Timestamp] = None
        self._prev_final_regime: Optional[str] = None
        self.unsupervised_mapping = bool(hmm_cfg.get("unsupervised_mapping", True))
        self.mapping_weights = hmm_cfg.get("mapping_weights")
        self.crisis_rvol_threshold = hmm_cfg.get("crisis_rvol_threshold")
        self.crisis_rvol_ratio = hmm_cfg.get("crisis_rvol_ratio")
        # label-switching 정렬: 워크포워드 일별 루프에서 anchor를 누적 유지 (라이브 state.json 역할).
        self.stabilize_mapping = bool(hmm_cfg.get("stabilize_mapping", False))
        self.mapping_deadband = float(hmm_cfg.get("mapping_deadband", 0.75))
        self.hmm_min_covar = float(hmm_cfg.get("min_covar", 1e-3))
        self._hmm_anchor: list = []

        # 빠른 노이즈 피처 평활 (config feature_smoothing) — HMM/RF/rule 입력 공용.
        fs_cfg = config.get("feature_smoothing", {}) or {}
        self.smooth_window = int(fs_cfg.get("window", 0)) if fs_cfg.get("enabled") else 0
        self.smooth_features = list(fs_cfg.get("features", [])) if fs_cfg.get("enabled") else None

        eq_classes = set(config.get("vol_targeting", {}).get(
            "equity_asset_classes",
            ["equity_etf", "equity_factor", "equity_individual"],
        ))
        self._equity_tickers = {
            t for t, meta in config["universe"].items()
            if meta["asset_class"] in eq_classes
        }

    # ── 내부 유틸 ────────────────────────────────────────────────────────────

    def _get_regime(
        self, as_of: pd.Timestamp
    ) -> Tuple[str, Dict[str, float], str, float]:
        """as_of 날짜 이전 데이터로만 레짐 + 블렌딩 확률 + confidence 계산 (워크포워드).

        Returns: (ensemble_regime, blend_probs, rule_regime, combined_conf)
        combined_conf: (rule_conf + hmm_conf) / 2 — 라이브 run.py와 동일 산식 (anomaly 패널티 제외).
        """
        self._current_as_of = as_of  # stagflation_subregime 오버레이가 참조 (리밸 직전 항상 호출됨)
        start = as_of - pd.Timedelta(days=self.hmm_lookback + 60)
        sig = self.signal_px[start:as_of]

        if len(sig) < 30:
            return "Slowdown", {r: 1.0 / len(REGIMES) for r in REGIMES}, "Slowdown", 0.0, 0.0, 0.0

        features = compute_features(
            sig, smooth_window=self.smooth_window, smooth_features=self.smooth_features
        )
        rule_regime = detect_regime(features)
        blend: Dict[str, float] = {r: 0.0 for r in REGIMES}

        if self.hmm_enabled:
            fred_slice = (
                self.fred_history[:as_of]
                if self.fred_history is not None and not self.fred_history.empty
                else None
            )
            fm = compute_feature_matrix(
                sig, fred_slice,
                smooth_window=self.smooth_window, smooth_features=self.smooth_features,
            )
            if len(fm) >= self.hmm_min:
                clf = HmmRegimeClassifier(
                    unsupervised_mapping=self.unsupervised_mapping,
                    mapping_weights=self.mapping_weights,
                    crisis_rvol_threshold=self.crisis_rvol_threshold,
                    crisis_rvol_ratio=self.crisis_rvol_ratio,
                    stabilize_mapping=self.stabilize_mapping,
                    mapping_deadband=self.mapping_deadband,
                    min_covar=self.hmm_min_covar,
                    fit_seeds=self.hmm_fit_seeds,
                )
                clf.set_anchor(self._hmm_anchor)
                # hmmlearn EM은 경계 케이스에서 수렴 경고가 잦다.
                # 수렴 여부는 내부에서 재시도/선택 로직으로 보완하므로, 백테스트 출력은 조용히 유지.
                import warnings
                with _quiet(), warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=ConvergenceWarning)
                    clf.fit(fm)
                if self.stabilize_mapping and clf.mapping_method == "unsupervised":
                    self._hmm_anchor = clf.current_anchor
                seq = fm.tail(self.predict_lookback)
                if self.use_forward_hmm:
                    hmm_probs = clf.predict_proba_forward(seq, horizon=self.forward_hmm_horizon)
                else:
                    hmm_probs = clf.predict_proba(seq)

                if self.rf_enabled:
                    rf_clf = BalancedRFClassifier(
                        forward_window=self.rf_forward_window,
                        label_mode=self.rf_label_mode,
                    )
                    rf_clf.fit(fm)
                    rf_probs = rf_clf.predict_proba(features)
                    w = self.rf_weight
                    raw = {r: (1 - w) * hmm_probs[r] + w * rf_probs[r] for r in REGIMES}
                    total = sum(raw.values())
                    blend = {r: v / total for r, v in raw.items()} if total > 0 else hmm_probs
                else:
                    blend = hmm_probs

                # 비-Crisis 디리스크 코로보레이션 게이트 (레버 C, raw blend에 적용 후 평활).
                if self.corrob_gate_enabled and self.corrob_gate_gamma > 0:
                    blend = apply_corroboration_gate(
                        blend, rule_regime, gamma=self.corrob_gate_gamma,
                        crisis_priority_threshold=self.crisis_priority_threshold,
                    )

                # blend EWMA 평활 (whipsaw 억제, 외부 비평 #6-c).
                # 신뢰도 가변 감쇠(옵트인): 라이브 run.py와 동일하게 raw blend +
                # rule_regime 기준 신뢰도에 anomaly 패널티까지 반영해 채택 속도를 조절.
                conf_for_smoothing = None
                if (self.conf_smoothing_enabled
                        and self.blend_smoothing_alpha > 0
                        and self._prev_blend is not None):
                    anomaly_score = 0.0
                    if self.anomaly_enabled and len(fm) >= self.hmm_min:
                        anom_det = AnomalyDetector(contamination=self.anomaly_contamination)
                        anom_det.fit(fm)
                        anomaly_score = anom_det.anomaly_score(features)
                    cs_hmm_conf = blend.get(rule_regime, 0.0)
                    conf_for_smoothing = compute_combined_confidence(
                        compute_rule_confidence(features, rule_regime), cs_hmm_conf,
                        method=self.confidence_method,
                    ) * (1.0 - self.anomaly_penalty * anomaly_score)
                if self.blend_smoothing_alpha > 0 and self._prev_blend is not None:
                    blend = apply_blend_smoothing(
                        blend, self._prev_blend, self.blend_smoothing_alpha,
                        confidence=conf_for_smoothing,
                        conf_ref=self.conf_smoothing_ref,
                        crisis_priority_threshold=self.crisis_priority_threshold,
                    )
                self._prev_blend = dict(blend)

                ensemble_final = ensemble_regime(
                    rule_regime, blend, self.override_thr,
                    crisis_priority_threshold=self.crisis_priority_threshold,
                )
                # 층 2 결론: rule이 ensemble보다 +3~5d 빠른 진입 → 위험조정 견고 개선.
                final = rule_regime if self.regime_timing_source == "rule" else ensemble_final
                rule_conf = compute_rule_confidence(features, final)
                hmm_conf = blend.get(final, 0.0)
                combined_conf = compute_combined_confidence(
                    rule_conf, hmm_conf, method=self.confidence_method
                )
                return final, blend, rule_regime, combined_conf, rule_conf, hmm_conf

        blend[rule_regime] = 1.0
        rule_conf = compute_rule_confidence(features, rule_regime)
        return rule_regime, blend, rule_regime, rule_conf, rule_conf, 0.0

    def _check_transition(self, current_date: pd.Timestamp, current_regime: str) -> bool:
        """직전 confirmed regime이 바뀐 시점부터 transition_days 이내인지 판단."""
        if self.transition_days <= 0:
            return False
        if self._prev_final_regime is not None and self._prev_final_regime != current_regime:
            self._last_regime_change_date = current_date
        self._prev_final_regime = current_regime
        if self._last_regime_change_date is None:
            return False
        elapsed = (current_date - self._last_regime_change_date).days
        return 0 < elapsed <= self.transition_days

    def _target_weights(
        self,
        blend_probs: Dict[str, float],
        realized_vol: float,
        portfolio_value: float,
        regime: str = "",
        vix: float = 0.0,
        signal_px_slice: Optional[pd.DataFrame] = None,
        universe_px_slice: Optional[pd.DataFrame] = None,
        transition_phase: bool = False,
    ) -> Dict[str, float]:
        """블렌딩 확률 → 전체 포트폴리오 기준 종목별 목표 비중."""
        usd_val = portfolio_value * self.usd_ratio
        krw_val = portfolio_value * (1 - self.usd_ratio)

        vol_cfg = self.config.get("vol_targeting", {})

        with _quiet():
            blend_cfg = self._subregime_config(getattr(self, "_current_as_of", None), regime)
            blended = blend_regime_targets(
                blend_probs, blend_cfg, transition_phase=transition_phase
            )

            # portfolio EWMA vol — 실제 보유(유니버스) 가격으로 계산해야 ticker_w와
            # 교집합이 생긴다. universe_px_slice 부재 시에만 signal_px_slice로 폴백
            # (구 동작: 신호 티커뿐이라 교집합 공집합 → port_vol=0 → realized_vol).
            vol_px = universe_px_slice if universe_px_slice is not None else signal_px_slice
            if vol_cfg.get("use_portfolio_vol", True) and vol_px is not None:
                lam = float(vol_cfg.get("ewma_lambda", 0.94))
                ticker_w = {t: blended.get(m["asset_class"], 0.0)
                            for t, m in self.config["universe"].items()
                            if m["asset_class"] in blended}
                port_vol = compute_portfolio_ewma_vol(vol_px, ticker_w, lam=lam)
                eff_vol = port_vol if port_vol > 0 else realized_vol
            else:
                eff_vol = realized_vol

            blended = apply_vol_targeting(blended, eff_vol, blend_cfg, regime=regime, blend_probs=blend_probs)
            blended = apply_core_satellite(blended, self.config, eff_vol=eff_vol, vol_config=blend_cfg)
            class_max = self.config.get("class_max_weight", {})
            blended = apply_dynamic_class_caps(blended, class_max, vix) if vix > 0 else apply_class_caps(blended, class_max)
            usd_w, krw_w = derive_account_weights(
                blended, self.config, usd_val, krw_val
            )

        return merge_to_total_weights(usd_w, krw_w, usd_val, krw_val)

    def _subregime_config(self, as_of, acting_regime=""):
        """stagflation_subregime 오버레이: 긴축형 스태그(실질금리↑) 시 Stagflation의
        목표비중(tightening_targets)·vol 목표(tightening_vol)·vol floor(tightening_floor)를
        교체한 config를 반환. 비활성/조건 미달/디스인플레형이면 self.config 그대로(동작 불변).

        gating: tightening_targets는 blend로 자연 게이팅(Stagflation 슬롯), tightening_vol은
        apply_vol_targeting 내부 regime 룩업으로 자연 게이팅. tightening_floor는 전역값이라
        acting_regime == "Stagflation"일 때만 적용(다른 레짐 floor 오염 방지).
        """
        sub = self.config.get("stagflation_subregime", {})
        if not sub.get("enabled") or as_of is None:
            return self.config
        if self.fred_history is None or self.fred_history.empty:
            return self.config
        feat = sub.get("split_feature", "real_rate_chg_3m")
        if feat not in self.fred_history.columns:
            return self.config
        hist = self.fred_history[feat][:as_of].dropna()
        if hist.empty:
            return self.config
        if float(hist.iloc[-1]) < float(sub.get("threshold", 0.0)):
            return self.config  # 디스인플레형(실질금리↓·하락) → 현행 스태그 유지
        tight = sub.get("tightening_targets")
        tight_vol = sub.get("tightening_vol")
        tight_floor = sub.get("tightening_floor")
        if tight_floor is not None and acting_regime != "Stagflation":
            tight_floor = None  # floor는 전역값 → Stagflation 작동 시에만
        if not tight and tight_vol is None and tight_floor is None:
            return self.config
        cfg = dict(self.config)
        if tight:
            rt = dict(self.config["regime_targets"])
            rt["Stagflation"] = tight
            cfg["regime_targets"] = rt
        if tight_vol is not None or tight_floor is not None:
            vc = dict(self.config.get("vol_targeting", {}))
            if tight_vol is not None:
                rtv = dict(vc.get("regime_target_vol", {}))
                rtv["Stagflation"] = float(tight_vol)
                vc["regime_target_vol"] = rtv
            if tight_floor is not None:
                vc["floor"] = float(tight_floor)
            cfg["vol_targeting"] = vc
        return cfg

    # ── 메인 실행 ────────────────────────────────────────────────────────────

    def run(self, regime_cache: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        """
        백테스트 실행.

        drift_threshold가 설정된 경우 drift 기반 리밸런싱,
        그렇지 않으면 캘린더(rebal_freq) 기반 리밸런싱을 사용한다.

        regime_cache: precompute_regime_path()로 미리 계산한 결과 (drift 모드에서만
        사용됨). drift_threshold·tx_cost·cooldown_days 스윕처럼 레짐 계산 자체가 같은
        여러 셀에 재사용하면 HMM/RF 재학습을 셀마다 반복하지 않는다.

        Returns
        -------
        pd.DataFrame
            index: date
            columns: value, returns, drawdown, regime, rebalanced, tx_cost[, drift]
        """
        if self.trigger_mode:
            return self._run_triggered()
        if self.drift_threshold is not None:
            return self._run_drift(regime_cache=regime_cache)
        return self._run_calendar()

    def _run_calendar(self) -> pd.DataFrame:
        """캘린더 기반 리밸런싱 (기존 로직)."""
        px = self.universe_px.copy()
        all_dates = px.index

        rebal_dates = set(
            pd.date_range(self.start, self.end, freq=self.rebal_freq).normalize()
        )

        portfolio_value = 1.0
        peak_value = 1.0
        shares: Dict[str, float] = {}
        current_regime = "Slowdown"
        current_rule_regime = "Slowdown"
        weights: Dict[str, float] = {}

        rows: List[dict] = []

        for i, date in enumerate(all_dates):
            day_prices = px.loc[date]
            available = day_prices.dropna()

            if i == 0:
                regime, blend_probs, rule_regime, conf, rc, hc = self._get_regime(date)
                current_regime = regime
                current_rule_regime = rule_regime
                current_combined_conf = conf
                current_rule_conf = rc
                current_hmm_conf = hc
                is_transition = self._check_transition(date, regime)

                sig = self.signal_px[:date].tail(65)
                feat = compute_features(sig) if len(sig) >= 30 else {}
                rv = feat.get("realized_vol", 0.15)
                vix = feat.get("vix", 0.0)

                weights = self._target_weights(
                    blend_probs, rv, portfolio_value,
                    regime=regime, vix=vix, signal_px_slice=sig,
                    universe_px_slice=px[:date].tail(65),
                    transition_phase=is_transition,
                )
                weights = _normalize_to_available(weights, available)

                shares = {
                    t: w * portfolio_value / available[t]
                    for t, w in weights.items()
                    if available.get(t, 0) > 0
                }
                rows.append({
                    "date":          date,
                    "value":         portfolio_value,
                    "drawdown":      0.0,
                    "regime":        current_regime,
                    "rule_regime":   current_rule_regime,
                    "combined_conf": current_combined_conf,
                "rule_conf":     current_rule_conf,
                "hmm_conf":      current_hmm_conf,
                    "rebalanced":    True,
                    "tx_cost":       0.0,
                })
                continue

            portfolio_value = sum(
                shares.get(t, 0.0) * float(available[t])
                for t in available.index
                if t in shares
            )
            if portfolio_value <= 0:
                portfolio_value = rows[-1]["value"]

            peak_value = max(peak_value, portfolio_value)
            drawdown = (portfolio_value - peak_value) / peak_value

            do_rebal = date.normalize() in rebal_dates
            day_tx = 0.0

            if do_rebal:
                regime, blend_probs, rule_regime, conf, rc, hc = self._get_regime(date)
                current_regime = regime
                current_rule_regime = rule_regime
                current_combined_conf = conf
                current_rule_conf = rc
                current_hmm_conf = hc
                is_transition = self._check_transition(date, regime)

                sig = self.signal_px[:date].tail(65)
                feat = compute_features(sig) if len(sig) >= 30 else {}
                rv = feat.get("realized_vol", 0.15)
                vix = feat.get("vix", 0.0)

                new_weights = self._target_weights(
                    blend_probs, rv, portfolio_value,
                    regime=regime, vix=vix, signal_px_slice=sig,
                    universe_px_slice=px[:date].tail(65),
                    transition_phase=is_transition,
                )

                thresholds = self.config["risk"]["drawdown_thresholds"]
                if self.config["risk"].get("drawdown_scaling_enabled", True):
                    new_weights = _apply_drawdown_scale(
                        new_weights, drawdown, thresholds, self._equity_tickers,
                        cash_split=self.config["risk"].get("drawdown_cash_split"),
                    )
                new_weights = _normalize_to_available(new_weights, available)

                old_weights: Dict[str, float] = {
                    t: shares.get(t, 0.0) * float(available[t]) / portfolio_value
                    for t in available.index
                    if t in shares
                }
                turnover = sum(
                    abs(new_weights.get(t, 0.0) - old_weights.get(t, 0.0))
                    for t in set(new_weights) | set(old_weights)
                ) / 2
                day_tx = turnover * self.tx_cost
                portfolio_value *= (1 - day_tx)

                shares = {
                    t: w * portfolio_value / float(available[t])
                    for t, w in new_weights.items()
                    if available.get(t, 0) > 0
                }
                weights = new_weights

            rows.append({
                "date":          date,
                "value":         portfolio_value,
                "drawdown":      drawdown,
                "regime":        current_regime,
                "rule_regime":   current_rule_regime,
                "combined_conf": current_combined_conf,
                "rule_conf":     current_rule_conf,
                "hmm_conf":      current_hmm_conf,
                "rebalanced":    do_rebal,
                "tx_cost":       day_tx,
            })

        result = pd.DataFrame(rows).set_index("date")
        result["returns"] = result["value"].pct_change().fillna(0.0)
        return result

    def precompute_regime_path(self) -> pd.DataFrame:
        """
        전체 날짜에 대해 레짐·블렌딩을 한 번만 순차 계산해 날짜별로 저장한다.

        drift_threshold·tx_cost·cooldown_days처럼 레짐 계산 자체에는 영향이 없는
        파라미터를 스윕할 때, 이 결과를 여러 셀의 `_run_drift(regime_cache=...)`에
        재사용하면 HMM/RF 재학습(스윕 비용의 대부분)을 셀마다 반복하지 않아도 된다.
        hmm/rf/feature_smoothing 등 레짐 계산에 관련된 config가 다르면 이 캐시는
        무효이니 다시 만들어야 한다.

        주의: 이 메서드는 self의 순차적 상태(EWMA 블렌딩 평활·label-switching 앵커)를
        소모한다 — 호출한 엔진 인스턴스를 이후 실제 시뮬레이션에 재사용하지 말 것
        (캐시만 뽑아내는 probe 용도로 쓰고 버린다).
        """
        px = self.universe_px
        rows: List[dict] = []
        for date in px.index:
            try:
                regime, blend_probs, rule_regime, conf, rc, hc = self._get_regime(date)
                is_transition = self._check_transition(date, regime)
                ok = True
            except Exception:
                regime, blend_probs, rule_regime = None, {}, None
                conf = rc = hc = 0.0
                is_transition = False
                ok = False
            sig = self.signal_px[:date].tail(65)
            feat = compute_features(sig) if len(sig) >= 30 else {}
            rows.append({
                "date":          date,
                "ok":            ok,
                "regime":        regime,
                "blend_probs":   blend_probs,
                "rule_regime":   rule_regime,
                "conf":          conf,
                "rc":            rc,
                "hc":            hc,
                "is_transition": is_transition,
                "rv":            feat.get("realized_vol", 0.15),
                "vix":           feat.get("vix", 0.0),
            })
        return pd.DataFrame(rows).set_index("date")

    def _evaluate_target(
        self,
        date: pd.Timestamp,
        portfolio_value: float,
        drawdown: float,
        available: pd.Series,
        px: pd.DataFrame,
        regime_cache: Optional[pd.DataFrame],
    ) -> dict:
        """오늘의 목표 비중과 레짐 정보를 구한다. regime_cache가 있으면 그 날짜의 값을
        읽어 재사용(HMM/RF 재호출 없음), 없으면 직접 계산한다."""
        if regime_cache is not None:
            cached = regime_cache.loc[date]
            if not bool(cached["ok"]):
                return {"ok": False}
            regime, blend_probs, rule_regime = cached["regime"], cached["blend_probs"], cached["rule_regime"]
            conf, rc, hc, is_transition = cached["conf"], cached["rc"], cached["hc"], bool(cached["is_transition"])
            rv, vix = cached["rv"], cached["vix"]
            self._current_as_of = date  # _get_regime이 했을 부수효과를 캐시 경로에서도 재현
            sig = self.signal_px[:date].tail(65)
        else:
            try:
                regime, blend_probs, rule_regime, conf, rc, hc = self._get_regime(date)
                is_transition = self._check_transition(date, regime)
            except Exception:
                return {"ok": False}
            sig = self.signal_px[:date].tail(65)
            feat = compute_features(sig) if len(sig) >= 30 else {}
            rv = feat.get("realized_vol", 0.15)
            vix = feat.get("vix", 0.0)

        new_weights = self._target_weights(
            blend_probs, rv, portfolio_value,
            regime=regime, vix=vix, signal_px_slice=sig,
            universe_px_slice=px[:date].tail(65),
            transition_phase=is_transition,
        )
        thresholds = self.config["risk"]["drawdown_thresholds"]
        if self.config["risk"].get("drawdown_scaling_enabled", True):
            new_weights = _apply_drawdown_scale(
                new_weights, drawdown, thresholds, self._equity_tickers,
                cash_split=self.config["risk"].get("drawdown_cash_split"),
            )
        new_weights = _normalize_to_available(new_weights, available)
        return {
            "ok": True, "new_weights": new_weights, "regime": regime,
            "rule_regime": rule_regime, "conf": conf, "rc": rc, "hc": hc,
        }

    def _run_drift(self, regime_cache: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        """
        Drift 기반 리밸런싱.

        매일 레짐·블렌딩을 재평가해 "오늘의" 목표 비중을 구하고, 그 목표 대비 실제
        보유의 차이(드리프트)를 잰다 — 거래 여부와 무관하게 매일 갱신한다는 점에서
        라이브 run.py의 모니터링과 동일하다(거래를 안 해도 목표는 매일 다시 계산됨).
        이전 구현은 거래가 실제로 일어난 날에만 목표를 갱신해, 가격이 안 움직여도
        레짐·블렌딩 신호만으로 생기는 드리프트를 전혀 못 잡았다(라이브↔백테스트 회전율
        괴리의 원인 중 하나, 2026-06-19 발견).

        regime_cache: precompute_regime_path()로 미리 계산한 결과. 주어지면 매일
        HMM/RF를 다시 학습하지 않고 재사용한다(drift_threshold 등 스윕 가속용).

        트리거 조건 (run.py _compute_trigger와 동일):
          1. drawdown <= moderate threshold → 쿨다운 무시, 즉시 리밸런싱
          2. 쿨다운 미경과 → 스킵
          3. drift > drift_threshold → 리밸런싱
        """
        px = self.universe_px.copy()
        all_dates = px.index

        portfolio_value = 1.0
        peak_value = 1.0
        shares: Dict[str, float] = {}
        current_regime = "Slowdown"
        current_rule_regime = "Slowdown"
        current_combined_conf = 0.0
        current_rule_conf = 0.0
        current_hmm_conf = 0.0
        target_weights: Dict[str, float] = {}
        last_rebal_date: Optional[pd.Timestamp] = None

        rows: List[dict] = []

        for i, date in enumerate(all_dates):
            day_prices = px.loc[date]
            available = day_prices.dropna()

            if i > 0:
                portfolio_value = sum(
                    shares.get(t, 0.0) * float(available[t])
                    for t in available.index
                    if t in shares
                )
                if portfolio_value <= 0:
                    portfolio_value = rows[-1]["value"]

            peak_value = max(peak_value, portfolio_value)
            drawdown = (portfolio_value - peak_value) / peak_value if i > 0 else 0.0

            current_w: Dict[str, float] = {
                t: shares.get(t, 0.0) * float(available[t]) / portfolio_value
                for t in available.index
                if t in shares
            }

            # 매일 레짐·블렌딩을 재평가해 "오늘의" 목표 비중을 구한다(거래 여부와 무관)
            ev = self._evaluate_target(date, portfolio_value, drawdown, available, px, regime_cache)
            regime_ok = ev["ok"]
            if regime_ok:
                new_weights = ev["new_weights"]
                current_regime = ev["regime"]
                current_rule_regime = ev["rule_regime"]
                current_combined_conf = ev["conf"]
                current_rule_conf = ev["rc"]
                current_hmm_conf = ev["hc"]
                target_weights = new_weights
            else:
                # HMM 수렴 실패 시 직전 목표 유지(그 날은 가격 드리프트만 반영)
                new_weights = target_weights

            if i == 0:
                shares = {
                    t: w * portfolio_value / available[t]
                    for t, w in new_weights.items()
                    if available.get(t, 0) > 0
                }
                last_rebal_date = date
                rows.append({
                    "date":          date,
                    "value":         portfolio_value,
                    "drawdown":      0.0,
                    "regime":        current_regime,
                    "rule_regime":   current_rule_regime,
                    "combined_conf": current_combined_conf,
                    "rule_conf":     current_rule_conf,
                    "hmm_conf":      current_hmm_conf,
                    "rebalanced":    True,
                    "tx_cost":       0.0,
                    "drift":         0.0,
                })
                continue

            all_tickers = set(current_w) | set(new_weights)
            drift = sum(
                abs(current_w.get(t, 0.0) - new_weights.get(t, 0.0))
                for t in all_tickers
            )

            days_since = (date - last_rebal_date).days if last_rebal_date else 999
            moderate_thr = self.config["risk"]["drawdown_thresholds"]["moderate"]
            emergency = drawdown <= moderate_thr

            do_rebal = regime_ok and (
                emergency or (drift > self.drift_threshold and days_since >= self.cooldown_days)
            )

            day_tx = 0.0
            if do_rebal:
                turnover = sum(
                    abs(new_weights.get(t, 0.0) - current_w.get(t, 0.0))
                    for t in set(new_weights) | set(current_w)
                ) / 2
                day_tx = turnover * self.tx_cost
                portfolio_value *= (1 - day_tx)

                shares = {
                    t: w * portfolio_value / float(available[t])
                    for t, w in new_weights.items()
                    if available.get(t, 0) > 0
                }
                last_rebal_date = date
                drift = 0.0

            rows.append({
                "date":          date,
                "value":         portfolio_value,
                "drawdown":      drawdown,
                "regime":        current_regime,
                "rule_regime":   current_rule_regime,
                "combined_conf": current_combined_conf,
                "rule_conf":     current_rule_conf,
                "hmm_conf":      current_hmm_conf,
                "rebalanced":    do_rebal,
                "tx_cost":       day_tx,
                "drift":         drift,
            })

        result = pd.DataFrame(rows).set_index("date")
        result["returns"] = result["value"].pct_change().fillna(0.0)
        return result

    def _run_triggered(self) -> pd.DataFrame:
        """
        R3 검증용: 라이브 run.py의 whipsaw 억제 레이어를 충실히 모델링한다.

        기존 _run_drift/_run_calendar와 달리, 라이브 트리거 경로 전체를 재현한다:
          - 주간 cadence(rebal_freq)로 레짐 평가 (라이브는 매 실행 = 주간 가정)
          - confidence fallback: combined_conf < threshold → 이전 확정 레짐 유지
          - confirmation 히스테리시스: raw 레짐 N회 연속 → 확정 (per_regime override 포함)
          - regime_changed 트리거: 확정 레짐 변경 시 drift 밴드 우회 강제 리밸런스 (토글)
          - drift 트리거: drift > drift_threshold

        자산군 비중은 blend_probs(평활 적용)에서 산출하고, 확정 레짐은
        vol_targeting 티어 선택 + 트리거에만 영향 — 라이브와 동일.

        토글 (config로 제어, R3 스윕에서 variant별 설정):
          regime_filter.confirmation_count   — 확정 N회
          regime_filter.confidence_threshold — 0이면 fallback 비활성
          rebalancing.regime_change_trigger  — false면 regime_changed 강제 트리거 제거
        """
        rf_cfg = self.config.get("regime_filter", {})
        rb_cfg = self.config.get("rebalancing", {})
        confirm_n_default = int(rf_cfg.get("confirmation_count", 3))
        cooldown_default = int(rf_cfg.get("cooldown_days", 0))
        conf_threshold = float(rf_cfg.get("confidence_threshold", 0.0))
        per_regime = {
            r: dict(v) for r, v in (rf_cfg.get("per_regime") or {}).items()
            if r in REGIMES and isinstance(v, dict)
        }
        regime_change_trigger = bool(rb_cfg.get("regime_change_trigger", True))
        drift_thr = float(rb_cfg.get("drift_threshold", 0.015))
        trade_cooldown = int(rb_cfg.get("min_rebalance_interval_days", 0))
        moderate_thr = self.config["risk"]["drawdown_thresholds"]["moderate"]

        # ── 날짜 인지 confirmation 필터 (RegimeFilter의 date.today() 비의존 버전) ──
        conf_state = {"confirmed": None, "candidate": None, "count": 0, "last_switch": None}

        def _confirm_update(raw: str, today: pd.Timestamp) -> Tuple[str, bool]:
            """raw 레짐 → (확정 레짐, regime_changed)."""
            st = conf_state
            if st["confirmed"] is None:
                st.update(confirmed=raw, candidate=raw, count=1, last_switch=today)
                return raw, False
            if raw == st["confirmed"]:
                st["candidate"] = raw
                st["count"] = 1
                return st["confirmed"], False
            if raw != st["candidate"]:
                st["candidate"] = raw
                st["count"] = 1
            else:
                st["count"] += 1
            n = int(per_regime.get(raw, {}).get("confirmation_count", confirm_n_default))
            cd = int(per_regime.get(raw, {}).get("cooldown_days", cooldown_default))
            cooldown_ok = (
                st["last_switch"] is None
                or (today - st["last_switch"]).days >= cd
            )
            if st["count"] >= n and cooldown_ok:
                old = st["confirmed"]
                st.update(confirmed=raw, candidate=raw, count=1, last_switch=today)
                return raw, (old != raw)
            return st["confirmed"], False

        px = self.universe_px.copy()
        all_dates = px.index
        rebal_dates = set(
            pd.date_range(self.start, self.end, freq=self.rebal_freq).normalize()
        )

        portfolio_value = 1.0
        peak_value = 1.0
        shares: Dict[str, float] = {}
        target_weights: Dict[str, float] = {}
        confirmed_regime = DEFAULT_REGIME
        current_rule_regime = DEFAULT_REGIME
        current_combined_conf = 0.0
        current_rule_conf = 0.0
        current_hmm_conf = 0.0
        last_rebal_date: Optional[pd.Timestamp] = None

        rows: List[dict] = []

        for i, date in enumerate(all_dates):
            available = px.loc[date].dropna()

            if i > 0:
                portfolio_value = sum(
                    shares.get(t, 0.0) * float(available[t])
                    for t in available.index if t in shares
                )
                if portfolio_value <= 0:
                    portfolio_value = rows[-1]["value"]
            peak_value = max(peak_value, portfolio_value)
            drawdown = (portfolio_value - peak_value) / peak_value if i > 0 else 0.0

            current_w: Dict[str, float] = {
                t: shares.get(t, 0.0) * float(available[t]) / portfolio_value
                for t in available.index if t in shares
            } if portfolio_value > 0 else {}

            emergency = drawdown <= moderate_thr
            should_eval = (date.normalize() in rebal_dates) or emergency or i == 0

            do_rebal = False
            day_tx = 0.0
            drift = 0.0

            if should_eval:
                try:
                    regime_raw, blend_probs, rule_regime, conf, rc, hc = self._get_regime(date)
                except Exception:
                    regime_raw = None
                if regime_raw is not None:
                    current_rule_regime = rule_regime
                    current_combined_conf = conf
                    current_rule_conf = rc
                    current_hmm_conf = hc

                    # confidence fallback (라이브 run.py:402) — 이전 확정 레짐 유지
                    raw = regime_raw
                    if conf_threshold > 0 and conf < conf_threshold:
                        prev = conf_state["confirmed"]
                        raw = prev if prev in REGIMES else DEFAULT_REGIME

                    confirmed_regime, regime_changed = _confirm_update(raw, date)
                    is_transition = self._check_transition(date, confirmed_regime)

                    sig = self.signal_px[:date].tail(65)
                    feat = compute_features(sig) if len(sig) >= 30 else {}
                    rv = feat.get("realized_vol", 0.15)
                    vix = feat.get("vix", 0.0)

                    new_weights = self._target_weights(
                        blend_probs, rv, portfolio_value,
                        regime=confirmed_regime, vix=vix, signal_px_slice=sig,
                        transition_phase=is_transition,
                    )
                    new_weights = _normalize_to_available(new_weights, available)

                    drift = sum(
                        abs(current_w.get(t, 0.0) - new_weights.get(t, 0.0))
                        for t in set(current_w) | set(new_weights)
                    )
                    days_since = (date - last_rebal_date).days if last_rebal_date else 999
                    do_rebal = (
                        i == 0
                        or emergency
                        or (regime_change_trigger and regime_changed)
                        or (drift > drift_thr and days_since >= trade_cooldown)
                    )

                    if do_rebal:
                        turnover = sum(
                            abs(new_weights.get(t, 0.0) - current_w.get(t, 0.0))
                            for t in set(new_weights) | set(current_w)
                        ) / 2
                        day_tx = turnover * self.tx_cost
                        portfolio_value *= (1 - day_tx)
                        shares = {
                            t: w * portfolio_value / float(available[t])
                            for t, w in new_weights.items()
                            if available.get(t, 0) > 0
                        }
                        target_weights = new_weights
                        last_rebal_date = date
                        drift = 0.0

            rows.append({
                "date":          date,
                "value":         portfolio_value,
                "drawdown":      drawdown,
                "regime":        confirmed_regime,
                "rule_regime":   current_rule_regime,
                "combined_conf": current_combined_conf,
                "rule_conf":     current_rule_conf,
                "hmm_conf":      current_hmm_conf,
                "rebalanced":    do_rebal,
                "tx_cost":       day_tx,
                "drift":         drift,
            })

        result = pd.DataFrame(rows).set_index("date")
        result["returns"] = result["value"].pct_change().fillna(0.0)
        return result


# ── 헬퍼 함수 ─────────────────────────────────────────────────────────────────

def _normalize_to_available(
    weights: Dict[str, float],
    available: pd.Series,
) -> Dict[str, float]:
    """유효 가격이 있는 종목만 남기고 합계를 1로 정규화한다."""
    filtered = {t: w for t, w in weights.items() if t in available.index and w > 0}
    total = sum(filtered.values())
    if total <= 0:
        return {}
    return {t: w / total for t, w in filtered.items()}


def _apply_drawdown_scale(
    weights: Dict[str, float],
    drawdown: float,
    thresholds: dict,
    equity_tickers: set,
    cash_split: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """드로우다운 수준에 따라 equity 비중을 축소한다.

    cash_split: 축소분을 배분할 {티커: 가중치} 맵. None이면 469830 전량 (기존 동작).
    """
    severe = thresholds["severe"]
    moderate = thresholds["moderate"]
    mild = thresholds["mild"]
    floor = float(thresholds.get("equity_floor_pct", 0.10))

    if drawdown <= severe:
        scale = floor
    elif drawdown <= moderate:
        scale = 0.40
    elif drawdown <= mild:
        scale = 0.75
    else:
        return dict(weights)

    eq_total = sum(weights.get(t, 0.0) for t in equity_tickers)
    reduction = eq_total * (1 - scale)

    split = cash_split or {"469830": 1.0}  # SOL 초단기채 프록시
    split_total = sum(split.values())
    if split_total <= 0:
        split = {"469830": 1.0}
        split_total = 1.0

    adjusted = {
        t: w * scale if t in equity_tickers else w
        for t, w in weights.items()
    }
    for tk, wt in split.items():
        adjusted[tk] = adjusted.get(tk, 0.0) + reduction * (wt / split_total)
    return adjusted
