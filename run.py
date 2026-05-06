"""
자동 자산 배분 시스템 진입점.

파이프라인:
  fetch → features → regime → target_weights → risk_controls
  → settlement_check → synthetic_reallocation → rebalance → save_state
"""
import argparse
import sys
from pathlib import Path

import yaml

from messenger import Messenger

BASE_DIR = Path(__file__).parent

# ── 실행 모드 ──────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="자산 배분 자동화 시스템")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="주문 없이 레짐/비중만 출력",
    )
    parser.add_argument(
        "--config",
        default=str(BASE_DIR / "config.yaml"),
        help="설정 파일 경로",
    )
    return parser.parse_args()


# ── 메인 파이프라인 ────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    messenger = Messenger()

    with open(args.config) as f:
        config = yaml.safe_load(f)

    from executor import load_state, save_state
    state = load_state()

    try:
        # ① 시장 데이터 수집 (HMM용 확장 lookback 포함)
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

        # FRED 데이터 조회 (API 키 있을 때만, 없으면 빈 dict)
        fred_data = fetch_fred_data()
        if fred_data:
            print(f"    FRED 조회  : {', '.join(fred_data.keys())}")

        # ② 피처 계산 (현재 시점 기준, FRED 연동)
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

        # ③ 레짐 감지 (규칙 기반 + HMM 앙상블 + 신뢰도 + 히스테리시스 필터)
        print("[3] 레짐 감지 중...")
        from regime import (
            detect_regime, RegimeFilter,
            HmmRegimeClassifier, ensemble_regime,
            compute_rule_confidence,
        )

        # 규칙 기반
        rule_regime = detect_regime(features)

        # HMM 앙상블
        hmm_min = hmm_cfg.get("min_samples", 100)
        override_thr = hmm_cfg.get("override_threshold", 0.60)
        feature_matrix = compute_feature_matrix(prices)
        raw_regime = rule_regime
        hmm_probs: dict = {}

        if hmm_enabled and len(feature_matrix) >= hmm_min:
            hmm_clf = HmmRegimeClassifier()
            hmm_clf.fit(feature_matrix)
            hmm_probs = hmm_clf.predict_proba(features)
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
        else:
            if hmm_enabled:
                print(f"    HMM        : 학습 데이터 부족 ({len(feature_matrix)}/{hmm_min}일), 규칙 기반 사용")

        # 신뢰도 계산
        rule_conf = compute_rule_confidence(features, raw_regime)
        hmm_conf: float | None = hmm_probs.get(raw_regime) if hmm_probs else None
        combined_conf = (rule_conf + hmm_conf) / 2 if hmm_conf is not None else rule_conf

        if hmm_conf is not None:
            print(f"    신뢰도     : {combined_conf:.0%}  (규칙기반 {rule_conf:.0%} | HMM {hmm_conf:.0%})")
        else:
            print(f"    신뢰도     : {combined_conf:.0%}  (규칙기반)")

        # 신뢰도 미달 → Neutral 폴백
        conf_threshold = config.get("regime_filter", {}).get("confidence_threshold", 0.40)
        if raw_regime != "Neutral" and combined_conf < conf_threshold:
            print(
                f"    신뢰도 미달 ({combined_conf:.0%} < {conf_threshold:.0%})"
                f" → Neutral 폴백 (이전: {raw_regime})"
            )
            raw_regime = "Neutral"

        # 히스테리시스 필터
        old_confirmed = state.get("confirmed_regime")
        regime_filter = RegimeFilter(state, config)
        regime = regime_filter.update(raw_regime)

        if old_confirmed and old_confirmed != regime:
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

        # ④ 목표 비중 로드
        print("[4] 목표 비중 산출 중...")
        from portfolio import (
            get_target_weights,
            apply_risk_controls,
            enforce_buffer_floor,
            apply_synthetic_reallocation,
        )

        target_weights = get_target_weights(regime, config)

        # ⑤ 결제 상태 점검 (T+2 추적 + 지연 매수 대기열 확인)
        print("[5] 결제 상태 점검 중...")
        from settlement import SettlementTracker

        settlement_cfg = config.get("settlement", {})
        buffer_tickers: list = settlement_cfg.get("buffer_tickers", [])
        buffer_min: float = settlement_cfg.get("buffer_min", 0.07)
        synthetic_pairs: dict = settlement_cfg.get("synthetic_pairs", {})

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
            pending_krw = tracker.pending_krw()
            if pending_krw > 0:
                print(f"    미결제 매도 대금: {pending_krw:,.0f}원 (T+2 대기 중)")
            if prev_deferred:
                print(f"    이전 지연 매수 {len(prev_deferred)}건 → 합성 노출 반영 예정")
                for d in prev_deferred:
                    print(f"      {d['ticker']} {d['amount_krw']:,.0f}원 ({d['currency']})")

        # ⑥ 계좌 잔고 조회
        print("[6] 계좌 잔고 조회 중...")
        if args.dry_run:
            total_krw, current_weights, drawdown = 0.0, {}, 0.0
            print("    [dry-run] 계좌 조회 생략")
        else:
            from executor import KisRebalancer

            rebalancer = KisRebalancer(config, messenger=messenger)
            universe_total, current_weights, drawdown = rebalancer.get_portfolio_state()
            total_krw = universe_total  # orphan은 이미 경고 출력됨
            print(f"    유니버스 기준 자산: {universe_total:,.0f} 원")
            print(f"    드로우다운        : {drawdown:+.2%}")

        # ⑦ 리스크 제어 + 버퍼 플로어 + 합성 노출 적용
        print("[7] 리스크 제어 적용 중...")
        risk_thresholds = config["risk"]["drawdown_thresholds"]
        target_weights = apply_risk_controls(target_weights, drawdown, risk_thresholds)

        if buffer_tickers:
            target_weights = enforce_buffer_floor(target_weights, buffer_tickers, buffer_min)
            print(f"    버퍼 플로어 적용: {'+'.join(buffer_tickers)} ≥ {buffer_min:.0%}")

        if prev_deferred and synthetic_pairs and total_krw > 0:
            print("    합성 노출 적용 중 (지연 USD 매수 → KRW 동등 자산):")
            target_weights = apply_synthetic_reallocation(
                target_weights, prev_deferred, synthetic_pairs, total_krw
            )

        # 목표 비중 출력
        print("    목표 비중:")
        for ticker, w in sorted(target_weights.items(), key=lambda x: -x[1]):
            if w > 0:
                cur = current_weights.get(ticker, 0.0)
                diff = w - cur
                sign = "▲" if diff > 0.005 else ("▼" if diff < -0.005 else " ")
                print(f"      {sign} {ticker:<8} target:{w:.1%}  current:{cur:.1%}")

        # ⑧ 리밸런싱 실행
        print("[8] 리밸런싱 실행...")
        if args.dry_run:
            print("    [dry-run] 주문 생략")
            state.update(regime_filter.to_dict())
            save_state(state)
            return

        messenger.send_start(regime, features, confidence=combined_conf)

        threshold = config["rebalancing"]["drift_threshold"]
        order_log, new_deferred = rebalancer.rebalance(
            current_weights=current_weights,
            target_weights=target_weights,
            total_value_krw=total_krw,
            threshold=threshold,
            tracker=tracker,
        )

        # 지연 매수 저장 + state 갱신 (settlement + regime filter)
        for d in new_deferred:
            tracker.add_deferred(d["ticker"], d["amount_krw"], d["currency"])
        state.update(tracker.to_dict())
        state.update(regime_filter.to_dict())
        save_state(state)

        if new_deferred:
            print(f"    지연 매수 {len(new_deferred)}건 저장 → 다음 실행 시 합성 노출 반영")

        messenger.send_complete(
            regime=regime,
            total_krw=total_krw,
            drawdown=drawdown,
            target_weights=target_weights,
            current_weights=current_weights,
            order_log=order_log,
            deferred_buys=new_deferred,
            pending_sells=tracker.pending_summary(),
            confidence=combined_conf,
        )

        print("━" * 50)
        print("완료")

    except Exception as e:
        messenger.send_system_error(e)
        raise


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
