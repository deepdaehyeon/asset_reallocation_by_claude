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

# trading/ 모듈 참조 (sys.path에 추가)
_TRADING = Path(__file__).parent.parent / "trading"
if str(_TRADING) not in sys.path:
    sys.path.insert(0, str(_TRADING))

from features import compute_features, compute_feature_matrix
from regime import (
    REGIMES,
    BalancedRFClassifier,
    HmmRegimeClassifier,
    detect_regime,
    ensemble_regime,
)
from portfolio import (
    apply_class_caps,
    apply_risk_controls,
    apply_vol_targeting,
    blend_regime_targets,
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
    ) -> None:
        self.config = deepcopy(config)
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
        self.predict_lookback = hmm_cfg.get("predict_lookback", 60)
        self.rf_enabled = hmm_cfg.get("rf_enabled", True)
        self.rf_weight = float(hmm_cfg.get("rf_weight", 0.40))

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
    ) -> Tuple[str, Dict[str, float], str]:
        """as_of 날짜 이전 데이터로만 레짐 + 블렌딩 확률 계산 (워크포워드).

        Returns: (ensemble_regime, hmm_probs, rule_regime)
        """
        start = as_of - pd.Timedelta(days=self.hmm_lookback + 60)
        sig = self.signal_px[start:as_of]

        if len(sig) < 30:
            return "Slowdown", {r: 1.0 / len(REGIMES) for r in REGIMES}, "Slowdown"

        features = compute_features(sig)
        rule_regime = detect_regime(features)
        blend: Dict[str, float] = {r: 0.0 for r in REGIMES}

        if self.hmm_enabled:
            fred_slice = (
                self.fred_history[:as_of]
                if self.fred_history is not None and not self.fred_history.empty
                else None
            )
            fm = compute_feature_matrix(sig, fred_slice)
            if len(fm) >= self.hmm_min:
                clf = HmmRegimeClassifier()
                with _quiet():
                    clf.fit(fm)
                seq = fm.tail(self.predict_lookback)
                hmm_probs = clf.predict_proba(seq)

                if self.rf_enabled:
                    rf_clf = BalancedRFClassifier()
                    rf_clf.fit(fm)
                    rf_probs = rf_clf.predict_proba(features)
                    w = self.rf_weight
                    raw = {r: (1 - w) * hmm_probs[r] + w * rf_probs[r] for r in REGIMES}
                    total = sum(raw.values())
                    blend = {r: v / total for r, v in raw.items()} if total > 0 else hmm_probs
                else:
                    blend = hmm_probs

                final = ensemble_regime(rule_regime, blend, self.override_thr)
                return final, blend, rule_regime

        blend[rule_regime] = 1.0
        return rule_regime, blend, rule_regime

    def _target_weights(
        self,
        blend_probs: Dict[str, float],
        realized_vol: float,
        portfolio_value: float,
    ) -> Dict[str, float]:
        """블렌딩 확률 → 전체 포트폴리오 기준 종목별 목표 비중."""
        usd_val = portfolio_value * self.usd_ratio
        krw_val = portfolio_value * (1 - self.usd_ratio)

        with _quiet():
            blended = blend_regime_targets(blend_probs, self.config)
            blended = apply_vol_targeting(blended, realized_vol, self.config)
            blended = apply_class_caps(blended, self.config.get("class_max_weight", {}))
            usd_w, krw_w = derive_account_weights(
                blended, self.config, usd_val, krw_val
            )

        return merge_to_total_weights(usd_w, krw_w, usd_val, krw_val)

    # ── 메인 실행 ────────────────────────────────────────────────────────────

    def run(self) -> pd.DataFrame:
        """
        백테스트 실행.

        drift_threshold가 설정된 경우 drift 기반 리밸런싱,
        그렇지 않으면 캘린더(rebal_freq) 기반 리밸런싱을 사용한다.

        Returns
        -------
        pd.DataFrame
            index: date
            columns: value, returns, drawdown, regime, rebalanced, tx_cost[, drift]
        """
        if self.drift_threshold is not None:
            return self._run_drift()
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
                regime, blend_probs, rule_regime = self._get_regime(date)
                current_regime = regime
                current_rule_regime = rule_regime

                sig = self.signal_px[:date].tail(65)
                rv = compute_features(sig).get("realized_vol", 0.15) if len(sig) >= 30 else 0.15

                weights = self._target_weights(blend_probs, rv, portfolio_value)
                weights = _normalize_to_available(weights, available)

                shares = {
                    t: w * portfolio_value / available[t]
                    for t, w in weights.items()
                    if available.get(t, 0) > 0
                }
                rows.append({
                    "date":        date,
                    "value":       portfolio_value,
                    "drawdown":    0.0,
                    "regime":      current_regime,
                    "rule_regime": current_rule_regime,
                    "rebalanced":  True,
                    "tx_cost":     0.0,
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
                regime, blend_probs, rule_regime = self._get_regime(date)
                current_regime = regime
                current_rule_regime = rule_regime

                sig = self.signal_px[:date].tail(65)
                rv = compute_features(sig).get("realized_vol", 0.15) if len(sig) >= 30 else 0.15

                new_weights = self._target_weights(blend_probs, rv, portfolio_value)

                thresholds = self.config["risk"]["drawdown_thresholds"]
                new_weights = _apply_drawdown_scale(
                    new_weights, drawdown, thresholds, self._equity_tickers
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
                "date":        date,
                "value":       portfolio_value,
                "drawdown":    drawdown,
                "regime":      current_regime,
                "rule_regime": current_rule_regime,
                "rebalanced":  do_rebal,
                "tx_cost":     day_tx,
            })

        result = pd.DataFrame(rows).set_index("date")
        result["returns"] = result["value"].pct_change().fillna(0.0)
        return result

    def _run_drift(self) -> pd.DataFrame:
        """
        Drift 기반 리밸런싱.

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
        target_weights: Dict[str, float] = {}
        last_rebal_date: Optional[pd.Timestamp] = None

        rows: List[dict] = []

        for i, date in enumerate(all_dates):
            day_prices = px.loc[date]
            available = day_prices.dropna()

            if i == 0:
                regime, blend_probs, rule_regime = self._get_regime(date)
                current_regime = regime
                current_rule_regime = rule_regime

                sig = self.signal_px[:date].tail(65)
                rv = compute_features(sig).get("realized_vol", 0.15) if len(sig) >= 30 else 0.15

                target_weights = self._target_weights(blend_probs, rv, portfolio_value)
                target_weights = _normalize_to_available(target_weights, available)

                shares = {
                    t: w * portfolio_value / available[t]
                    for t, w in target_weights.items()
                    if available.get(t, 0) > 0
                }
                last_rebal_date = date
                rows.append({
                    "date":        date,
                    "value":       portfolio_value,
                    "drawdown":    0.0,
                    "regime":      current_regime,
                    "rule_regime": current_rule_regime,
                    "rebalanced":  True,
                    "tx_cost":     0.0,
                    "drift":       0.0,
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

            current_w: Dict[str, float] = {
                t: shares.get(t, 0.0) * float(available[t]) / portfolio_value
                for t in available.index
                if t in shares
            }

            all_tickers = set(current_w) | set(target_weights)
            drift = sum(
                abs(current_w.get(t, 0.0) - target_weights.get(t, 0.0))
                for t in all_tickers
            )

            days_since = (date - last_rebal_date).days if last_rebal_date else 999
            moderate_thr = self.config["risk"]["drawdown_thresholds"]["moderate"]
            emergency = drawdown <= moderate_thr

            do_rebal = emergency or (drift > self.drift_threshold and days_since >= self.cooldown_days)

            day_tx = 0.0
            if do_rebal:
                try:
                    regime, blend_probs, rule_regime = self._get_regime(date)
                    current_regime = regime
                    current_rule_regime = rule_regime
                except Exception:
                    # HMM 수렴 실패 시 기존 레짐 유지, 리밸런싱 스킵
                    do_rebal = False

            if do_rebal:
                sig = self.signal_px[:date].tail(65)
                rv = compute_features(sig).get("realized_vol", 0.15) if len(sig) >= 30 else 0.15

                new_weights = self._target_weights(blend_probs, rv, portfolio_value)

                thresholds = self.config["risk"]["drawdown_thresholds"]
                new_weights = _apply_drawdown_scale(
                    new_weights, drawdown, thresholds, self._equity_tickers
                )
                new_weights = _normalize_to_available(new_weights, available)

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
                "date":        date,
                "value":       portfolio_value,
                "drawdown":    drawdown,
                "regime":      current_regime,
                "rule_regime": current_rule_regime,
                "rebalanced":  do_rebal,
                "tx_cost":     day_tx,
                "drift":       drift,
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
) -> Dict[str, float]:
    """드로우다운 수준에 따라 equity 비중을 축소한다."""
    severe = thresholds["severe"]
    moderate = thresholds["moderate"]
    mild = thresholds["mild"]

    if drawdown <= severe:
        scale = 0.0
    elif drawdown <= moderate:
        scale = 0.40
    elif drawdown <= mild:
        scale = 0.75
    else:
        return dict(weights)

    eq_total = sum(weights.get(t, 0.0) for t in equity_tickers)
    reduction = eq_total * (1 - scale)
    cash_ticker = "469830"  # SOL 초단기채 프록시

    adjusted = {
        t: w * scale if t in equity_tickers else w
        for t, w in weights.items()
    }
    adjusted[cash_ticker] = adjusted.get(cash_ticker, 0.0) + reduction
    return adjusted
