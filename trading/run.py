"""자동 자산 배분 시스템 진입점.

모드:
  monitor  — 시장 분석 + 레짐 감지 + 계좌별 트리거 계산 → state.json 저장 (주문 없음)
  krw      — state.json의 trigger_krw 확인 → KRW 계좌(국장)만 리밸런싱 실행
  usd      — state.json의 trigger_usd 확인 → USD 계좌(미장)만 리밸런싱 실행

권장 cron (KST):
  08:50   python run.py --mode monitor    # 장 시작 전 분석
  09:10   python run.py --mode krw        # 국장 실행 (09:00 개장 후)
  23:00   python run.py --mode usd        # 미장 실행 (DST 무관 안전 시각)
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from executor import load_state, save_state
from messenger import Messenger

BASE_DIR = Path(__file__).parent


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="자산 배분 자동화 시스템")
    parser.add_argument(
        "--mode",
        choices=["monitor", "krw", "usd"],
        required=True,
        help="monitor: 분석+트리거저장 / krw: 국장실행 / usd: 미장실행",
    )
    parser.add_argument("--dry-run", action="store_true", help="주문 없이 레짐/비중만 출력")
    parser.add_argument("--config", default=str(BASE_DIR / "config.yaml"))
    return parser.parse_args()


# ── 트리거 판단 ───────────────────────────────────────────────────────────────

def _compute_trigger(
    drift: float,
    regime_changed: bool,
    drawdown: float,
    last_rebalanced_at: Optional[str],
    config: dict,
) -> Tuple[bool, str]:
    """
    리밸런싱 트리거 여부를 결정한다.

    우선순위:
      1. 드로우다운 비상 (moderate 이하) → 쿨다운 무시하고 즉시 트리거
      2. 쿨다운 미경과 → 스킵
      3. 레짐 전환 확정
      4. drift > drift_threshold
    """
    thresholds = config["risk"]["drawdown_thresholds"]
    if drawdown <= thresholds["moderate"]:
        return True, f"drawdown_emergency({drawdown:.1%})"

    min_days = int(config.get("rebalancing", {}).get("min_rebalance_interval_days", 7))
    if last_rebalanced_at:
        days_since = (datetime.now() - datetime.fromisoformat(last_rebalanced_at)).days
        if days_since < min_days:
            return False, f"cooldown({days_since}d/{min_days}d)"

    if regime_changed:
        return True, "regime_change"

    threshold = float(config["rebalancing"]["drift_threshold"])
    if drift > threshold:
        return True, f"drift({drift:.1%})"

    return False, f"no_trigger(drift={drift:.1%})"


def _compute_side_drifts(
    current_weights: Dict[str, float],
    target_usd: Dict[str, float],
    target_krw: Dict[str, float],
    total_krw: float,
    total_usd_krw: float,
    total_krw_only: float,
    config: dict,
) -> Tuple[float, float]:
    """
    KRW / USD 계좌 각각의 drift를 계좌 내 비중 기준으로 계산한다.

    current_weights는 총자산 기준 비중이므로 각 계좌 총액으로 환산 후 비교한다.
    """
    from portfolio import compute_drift

    universe = config["universe"]

    krw_tickers = (
        set(target_krw)
        | {t for t in current_weights if universe.get(t, {}).get("currency") == "KRW"}
    )
    current_krw_norm: Dict[str, float] = {}
    if total_krw_only > 0:
        for t in krw_tickers:
            current_krw_norm[t] = current_weights.get(t, 0.0) * total_krw / total_krw_only
    drift_krw = compute_drift(current_krw_norm, target_krw) if current_krw_norm else 0.0

    usd_tickers = (
        set(target_usd)
        | {t for t in current_weights if universe.get(t, {}).get("currency") == "USD"}
    )
    current_usd_norm: Dict[str, float] = {}
    if total_usd_krw > 0:
        for t in usd_tickers:
            current_usd_norm[t] = current_weights.get(t, 0.0) * total_krw / total_usd_krw
    drift_usd = compute_drift(current_usd_norm, target_usd) if current_usd_norm else 0.0

    return drift_krw, drift_usd


# ── 파이프라인 단계 ───────────────────────────────────────────────────────────

def _run_market_analysis(config: dict, state: dict) -> dict:
    """
    단계 1-3: 시장 데이터 수집 → 피처 계산 → 레짐 감지.

    Returns dict:
        features, regime, blend_probs, combined_conf, regime_changed, regime_filter
    """
    print("━" * 50)
    print("[1] 시장 데이터 수집 중...")
    from fetcher import fetch_signal_prices, fetch_fred_data

    signal_cfg = config["signal"]
    hmm_cfg = config.get("hmm", {})
    hmm_enabled = hmm_cfg.get("enabled", True)
    hmm_lookback = hmm_cfg.get("lookback_days", 500) if hmm_enabled else 0
    effective_lookback = max(signal_cfg["lookback_days"], hmm_lookback)

    prices = fetch_signal_prices(
        tickers=signal_cfg["tickers"],
        lookback_days=effective_lookback,
    )
    print(f"    수집 기간  : {len(prices)}일")

    fred_data = fetch_fred_data()
    if fred_data:
        print(f"    FRED 조회  : {', '.join(fred_data.keys())}")

    print("[2] 피처 계산 중...")
    from features import compute_features, compute_feature_matrix

    features = compute_features(prices, fred_data or None)
    print(f"    momentum_1m : {features['momentum_1m']:+.2%}")
    print(f"    momentum_3m : {features['momentum_3m']:+.2%}")
    print(f"    realized_vol: {features['realized_vol']:.2%} (연환산)")
    print(f"    VIX         : {features['vix']:.1f}")
    print(f"    credit_signal: {features['credit_signal']:+.2%}")
    if "hy_spread" in features:
        print(f"    HY 스프레드 : {features['hy_spread']:.2f}% (FRED)")
    if "curve_10y2y" in features:
        print(f"    10Y-2Y 커브 : {features['curve_10y2y']:+.2f}% (FRED)")

    print("[3] 레짐 감지 중...")
    from regime import (
        detect_regime, RegimeFilter, REGIMES, DEFAULT_REGIME,
        HmmRegimeClassifier, ensemble_regime,
        compute_rule_confidence,
    )

    rule_regime = detect_regime(features)
    hmm_min = hmm_cfg.get("min_samples", 100)
    override_thr = hmm_cfg.get("override_threshold", 0.60)
    predict_lookback = hmm_cfg.get("predict_lookback", 60)
    feature_matrix = compute_feature_matrix(prices)
    raw_regime = rule_regime
    hmm_probs: dict = {}

    if hmm_enabled and len(feature_matrix) >= hmm_min:
        hmm_clf = HmmRegimeClassifier()
        hmm_clf.fit(feature_matrix)
        seq = feature_matrix.tail(predict_lookback)
        hmm_probs = hmm_clf.predict_proba(seq)
        hmm_top = max(hmm_probs, key=hmm_probs.get)
        print(f"    규칙 기반  : {rule_regime}")
        print(
            f"    HMM 예측   : {hmm_top} ({hmm_probs[hmm_top]:.0%}) | "
            + " / ".join(
                f"{r}:{p:.0%}"
                for r, p in sorted(hmm_probs.items(), key=lambda x: -x[1])
            )
        )
        raw_regime = ensemble_regime(rule_regime, hmm_probs, override_thr)
        if raw_regime != rule_regime:
            print(f"    앙상블 조정: {rule_regime} → {raw_regime}")
    elif hmm_enabled:
        print(f"    HMM        : 학습 데이터 부족 ({len(feature_matrix)}/{hmm_min}일), 규칙 기반 사용")

    rule_conf = compute_rule_confidence(features, raw_regime)
    hmm_conf: Optional[float] = hmm_probs.get(raw_regime) if hmm_probs else None
    combined_conf = (rule_conf + hmm_conf) / 2 if hmm_conf is not None else rule_conf

    if hmm_conf is not None:
        print(f"    신뢰도     : {combined_conf:.0%}  (규칙기반 {rule_conf:.0%} | HMM {hmm_conf:.0%})")
    else:
        print(f"    신뢰도     : {combined_conf:.0%}  (규칙기반)")

    conf_threshold = config.get("regime_filter", {}).get("confidence_threshold", 0.40)
    if raw_regime != DEFAULT_REGIME and combined_conf < conf_threshold:
        print(
            f"    신뢰도 미달 ({combined_conf:.0%} < {conf_threshold:.0%})"
            f" → {DEFAULT_REGIME} 폴백 (이전: {raw_regime})"
        )
        raw_regime = DEFAULT_REGIME

    old_confirmed = state.get("confirmed_regime")
    regime_filter = RegimeFilter(state, config)
    regime = regime_filter.update(raw_regime)
    regime_changed = bool(old_confirmed and old_confirmed != regime)

    if regime_changed:
        print(f"    → 레짐 전환 확정: {old_confirmed} → {regime} ★")
    elif regime_filter.is_transitioning:
        cd = regime_filter.cooldown_remaining
        cd_str = f", 쿨다운 {cd}일 남음" if cd > 0 else ""
        print(
            f"    전환 대기  : {regime_filter.candidate} "
            f"({regime_filter.candidate_count}/{regime_filter.confirm_n}회 확인{cd_str})"
        )
        print(f"    확정 레짐  : {regime} (유지)")
    else:
        print(f"    → 레짐: {regime}")

    if hmm_probs:
        blend_probs = dict(hmm_probs)
    else:
        blend_probs = {r: (1.0 if r == regime else 0.0) for r in REGIMES}

    print(
        "    연속 노출  : "
        + " / ".join(
            f"{r} {blend_probs.get(r, 0):.0%}"
            for r in REGIMES
            if blend_probs.get(r, 0) >= 0.05
        )
    )

    return {
        "features": features,
        "regime": regime,
        "blend_probs": blend_probs,
        "combined_conf": combined_conf,
        "regime_changed": regime_changed,
        "regime_filter": regime_filter,
    }


def _compute_targets(
    blended_targets: dict,
    realized_vol: float,
    config: dict,
    total_usd_krw: float,
    total_krw_only: float,
) -> Tuple[dict, dict]:
    """
    단계 5b-5d: vol targeting → class caps → 계좌별 종목 비중 도출.

    Returns (target_usd, target_krw)
    """
    from portfolio import apply_vol_targeting, apply_class_caps, derive_account_weights

    blended = apply_vol_targeting(blended_targets, realized_vol, config)
    blended = apply_class_caps(blended, config.get("class_max_weight", {}))
    target_usd, target_krw = derive_account_weights(blended, config, total_usd_krw, total_krw_only)
    return target_usd, target_krw


def _apply_risk_controls(
    target_usd: dict,
    target_krw: dict,
    drawdown: float,
    prev_deferred: list,
    total_krw_only: float,
    config: dict,
) -> Tuple[dict, dict]:
    """단계 7: 드로우다운 스케일 → 버퍼 플로어 → 합성 노출."""
    from portfolio import apply_risk_controls, enforce_buffer_floor, apply_synthetic_reallocation

    settlement_cfg = config.get("settlement", {})
    buffer_tickers: List[str] = settlement_cfg.get("buffer_tickers", [])
    buffer_min = float(settlement_cfg.get("buffer_min", 0.07))
    synthetic_pairs: dict = settlement_cfg.get("synthetic_pairs", {})

    equity_classes = set(config["risk"].get(
        "equity_asset_classes", ["equity_etf", "equity_factor", "equity_individual"]
    ))
    equity_tickers = {
        t for t, meta in config["universe"].items()
        if meta["asset_class"] in equity_classes
    }
    risk_thresholds = config["risk"]["drawdown_thresholds"]

    target_usd = apply_risk_controls(
        target_usd, drawdown, risk_thresholds, equity_tickers & set(target_usd)
    )
    target_krw = apply_risk_controls(
        target_krw, drawdown, risk_thresholds, equity_tickers & set(target_krw)
    )

    if drawdown <= risk_thresholds["severe"]:
        print(f"    ⚠ SEVERE 드로우다운 ({drawdown:.1%}): equity → 0 (채권·금 유지)")
    elif drawdown <= risk_thresholds["moderate"]:
        print(f"    ⚠ MODERATE 드로우다운 ({drawdown:.1%}): equity ×0.40")
    elif drawdown <= risk_thresholds["mild"]:
        print(f"    ⚠ MILD 드로우다운 ({drawdown:.1%}): equity ×0.75")

    if buffer_tickers:
        target_krw = enforce_buffer_floor(target_krw, buffer_tickers, buffer_min)
        print(f"    버퍼 플로어: {'+'.join(buffer_tickers)} ≥ {buffer_min:.0%}")

    if prev_deferred and synthetic_pairs and total_krw_only > 0:
        target_krw = apply_synthetic_reallocation(
            target_krw, prev_deferred, synthetic_pairs, total_krw_only
        )

    return target_usd, target_krw


# ── 모니터링 실행 ─────────────────────────────────────────────────────────────

def run_monitor(config: dict, state: dict, messenger: Messenger, args) -> None:
    """
    시장 분석 + 계좌별 드리프트 계산 + 트리거 결정 → state.json 저장.
    주문 없음.

    state.json 저장 키:
      trigger_krw / trigger_usd          : 국장/미장 실행 run이 소비하는 플래그
      trigger_reason_krw / _usd          : 트리거 사유 (로깅용)
      trigger_set_at                     : 트리거 설정 시각
      saved_blended_targets              : 실행 run이 재사용할 자산군 블렌딩 비중
      saved_realized_vol                 : 변동성 타겟팅 재사용
      saved_regime / saved_confidence    : Slack 메시지용
      saved_features                     : Slack 시그널 표시용
      last_drift_krw / last_drift_usd    : 드리프트 이력
    """
    market = _run_market_analysis(config, state)

    print("[4] 계좌 잔고 조회 중...")
    if args.dry_run:
        total_krw = total_usd_krw = total_krw_only = 0.0
        current_weights: dict = {}
        drawdown = 0.0
        print("    [dry-run] 계좌 조회 생략 — 트리거 계산 불가")
        state["last_run_at"] = datetime.now().isoformat()
        state.update(market["regime_filter"].to_dict())
        save_state(state)
        print("━" * 50)
        print("모니터링 완료 (dry-run)")
        return

    from executor import KisRebalancer
    rebalancer = KisRebalancer(config, messenger=messenger)
    total_krw, total_usd_krw, total_krw_only, current_weights, drawdown = (
        rebalancer.get_portfolio_state()
    )
    usd_pct = total_usd_krw / total_krw * 100 if total_krw else 0
    krw_pct = total_krw_only / total_krw * 100 if total_krw else 0
    print(f"    총 자산: {total_krw:,.0f} 원  │  USD {usd_pct:.1f}% / KRW {krw_pct:.1f}%")
    print(f"    드로우다운: {drawdown:+.2%}")

    print("[5] 목표 비중 산출 중...")
    from portfolio import blend_regime_targets

    blended_targets = blend_regime_targets(market["blend_probs"], config)
    cls_str = "  ".join(
        f"{k}:{v:.0%}" for k, v in sorted(blended_targets.items(), key=lambda x: -x[1])
        if v >= 0.005
    )
    print(f"    [블렌딩] {cls_str}")

    target_usd, target_krw = _compute_targets(
        blended_targets,
        market["features"]["realized_vol"],
        config,
        total_usd_krw,
        total_krw_only,
    )

    print("[6] 트리거 계산 중...")
    drift_krw, drift_usd = _compute_side_drifts(
        current_weights, target_usd, target_krw,
        total_krw, total_usd_krw, total_krw_only, config,
    )

    trigger_krw, reason_krw = _compute_trigger(
        drift_krw, market["regime_changed"], drawdown,
        state.get("last_rebalanced_krw_at"), config,
    )
    trigger_usd, reason_usd = _compute_trigger(
        drift_usd, market["regime_changed"], drawdown,
        state.get("last_rebalanced_usd_at"), config,
    )

    print(f"    [KRW] drift={drift_krw:.1%}  →  {'✓ 트리거' if trigger_krw else '✗ 스킵'}  ({reason_krw})")
    print(f"    [USD] drift={drift_usd:.1%}  →  {'✓ 트리거' if trigger_usd else '✗ 스킵'}  ({reason_usd})")

    features = market["features"]
    state.update({
        "trigger_krw":            trigger_krw,
        "trigger_reason_krw":     reason_krw,
        "trigger_usd":            trigger_usd,
        "trigger_reason_usd":     reason_usd,
        "trigger_set_at":         datetime.now().isoformat(),
        "saved_blended_targets":  blended_targets,
        "saved_realized_vol":     features["realized_vol"],
        "saved_regime":           market["regime"],
        "saved_confidence":       round(market["combined_conf"], 4),
        "saved_features": {
            k: v for k, v in features.items() if isinstance(v, (int, float))
        },
        "last_run_confidence":    round(market["combined_conf"], 4),
        "last_run_at":            datetime.now().isoformat(),
        "last_drawdown":          round(drawdown, 4),
        "last_total_krw":         float(total_krw),
        "last_drift_krw":         round(drift_krw, 4),
        "last_drift_usd":         round(drift_usd, 4),
    })
    state.update(market["regime_filter"].to_dict())
    save_state(state)
    print("━" * 50)
    print("모니터링 완료")


# ── 국장/미장 실행 ────────────────────────────────────────────────────────────

def run_execution(config: dict, state: dict, messenger: Messenger, args) -> None:
    """
    trigger_krw / trigger_usd 확인 후 해당 계좌만 리밸런싱을 실행한다.

    모니터링 run에서 저장한 saved_blended_targets를 재사용해 자산군 비중을 계산하고,
    실행 시점의 신규 계좌 잔고로 계좌별 비중과 드로우다운을 갱신한다.
    """
    side = args.mode  # "krw" or "usd"
    trigger = state.get(f"trigger_{side}", False)
    reason = state.get(f"trigger_reason_{side}", "-")

    if not trigger:
        print(f"[{side.upper()}] 트리거 없음 ({reason}) → 실행 생략")
        return

    print(f"[{side.upper()}] 트리거 확인: {reason}")

    blended_targets = state.get("saved_blended_targets")
    realized_vol = float(state.get("saved_realized_vol", 0.0))
    from regime import DEFAULT_REGIME
    regime = state.get("saved_regime", DEFAULT_REGIME)
    combined_conf = float(state.get("saved_confidence", 0.0))
    saved_features: dict = state.get("saved_features", {})

    if not blended_targets:
        print("[오류] 저장된 모니터링 결과 없음 → --mode monitor 먼저 실행 필요")
        return

    print("━" * 50)
    print("[4] 계좌 잔고 조회 중...")
    from executor import KisRebalancer
    rebalancer = KisRebalancer(config, messenger=messenger)
    total_krw, total_usd_krw, total_krw_only, current_weights, drawdown = (
        rebalancer.get_portfolio_state()
    )
    usd_pct = total_usd_krw / total_krw * 100 if total_krw else 0
    krw_pct = total_krw_only / total_krw * 100 if total_krw else 0
    print(f"    총 자산: {total_krw:,.0f} 원  │  USD {usd_pct:.1f}% / KRW {krw_pct:.1f}%")
    print(f"    드로우다운: {drawdown:+.2%}")

    print("[4b] 유니버스 외 종목 자동 정리 중...")
    if args.dry_run:
        orphans = rebalancer._orphan_holdings
        if orphans:
            for t, info in orphans.items():
                if side == "all" or info["currency"].lower() == side:
                    print(f"    [dry-run] 매도 예정: {t} ({info['currency']}, {info['amount_krw']:,.0f}원)")
        else:
            print("    정리 대상 없음")
    else:
        orphan_log = rebalancer.sell_orphans(side)
        if orphan_log:
            print(f"    처리 완료 {len(orphan_log)}건:")
            for entry in orphan_log:
                print(f"      {entry}")
        else:
            print("    정리 대상 없음")

    print("[5] 목표 비중 산출 중...")
    target_usd, target_krw = _compute_targets(
        blended_targets, realized_vol, config, total_usd_krw, total_krw_only
    )

    print("[6] 결제 상태 점검 중...")
    from settlement import SettlementTracker

    if args.dry_run:
        tracker = SettlementTracker({})
        prev_deferred: list = []
    else:
        tracker = SettlementTracker(state)
        purged = tracker.purge_settled()
        prev_deferred = tracker.get_deferred()
        tracker.clear_deferred()
        if purged:
            print(f"    결제 완료 {purged}건 정리")
        if prev_deferred:
            print(f"    이전 지연 매수 {len(prev_deferred)}건 → 합성 노출 반영")
            for d in prev_deferred:
                print(f"      {d['ticker']} {d['amount_krw']:,.0f}원 ({d['currency']})")

    print("[7] 리스크 제어 적용 중...")
    target_usd, target_krw = _apply_risk_controls(
        target_usd, target_krw, drawdown, prev_deferred, total_krw_only, config
    )

    from portfolio import merge_to_total_weights
    merged_target = merge_to_total_weights(target_usd, target_krw, total_usd_krw, total_krw_only)
    _print_targets(target_usd, target_krw, merged_target, current_weights, side)

    print("[8] 리밸런싱 실행...")
    if args.dry_run:
        print(f"    [dry-run] {side.upper()} 주문 생략")
        return

    messenger.send_start(regime, saved_features, confidence=combined_conf)

    order_log, new_deferred = rebalancer.rebalance(
        current_weights=current_weights,
        target_usd=target_usd,
        target_krw=target_krw,
        total_usd_krw=total_usd_krw,
        total_krw_only=total_krw_only,
        threshold=0.0,   # 트리거 이미 확정 — drift 재확인 불필요
        tracker=tracker,
        side=side,
    )

    for d in new_deferred:
        tracker.add_deferred(d["ticker"], d["amount_krw"], d["currency"])

    state[f"last_rebalanced_{side}_at"] = datetime.now().isoformat()
    state[f"trigger_{side}"] = False
    state[f"trigger_reason_{side}"] = None
    state["last_run_at"] = datetime.now().isoformat()
    state.update(tracker.to_dict())
    save_state(state)

    if new_deferred:
        print(f"    지연 매수 {len(new_deferred)}건 저장 → 다음 실행 시 합성 노출 반영")

    messenger.send_complete(
        regime=regime,
        total_krw=total_krw,
        drawdown=drawdown,
        target_weights=merged_target,
        current_weights=current_weights,
        order_log=order_log,
        deferred_buys=new_deferred,
        pending_sells=tracker.pending_summary(),
        confidence=combined_conf,
    )

    print("━" * 50)
    print(f"{side.upper()} 리밸런싱 완료")


def _print_targets(
    target_usd: dict,
    target_krw: dict,
    merged_target: dict,
    current_weights: dict,
    side: str,
) -> None:
    targets = target_usd if side == "usd" else target_krw
    label = "USD 계좌" if side == "usd" else "KRW 계좌"
    print(f"    목표 비중 [{label}]:")
    for ticker, w in sorted(targets.items(), key=lambda x: -x[1]):
        if w > 0:
            total_frac = merged_target.get(ticker, 0.0)
            cur = current_weights.get(ticker, 0.0)
            sign = "▲" if total_frac - cur > 0.005 else ("▼" if total_frac - cur < -0.005 else " ")
            print(f"      {sign} {ticker:<8} 계좌:{w:.1%}  전체:{total_frac:.1%}  현재:{cur:.1%}")


# ── 진입점 ────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    messenger = Messenger()

    with open(args.config) as f:
        config = yaml.safe_load(f)

    state = load_state()

    try:
        if args.mode == "monitor":
            run_monitor(config, state, messenger, args)
        else:
            run_execution(config, state, messenger, args)

    except Exception as e:
        messenger.send_system_error(e)
        raise


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
