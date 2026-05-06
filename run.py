"""
자동 자산 배분 시스템 진입점.

파이프라인:
  fetch → features → regime → blend_probs → blend_targets
  → vol_targeting → class_caps → (usd_weights, krw_weights)
  → risk_controls → settlement_check → synthetic_reallocation
  → rebalance → save_state
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

import yaml

from messenger import Messenger

BASE_DIR = Path(__file__).parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="자산 배분 자동화 시스템")
    parser.add_argument("--dry-run", action="store_true", help="주문 없이 레짐/비중만 출력")
    parser.add_argument("--config", default=str(BASE_DIR / "config.yaml"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    messenger = Messenger()

    with open(args.config) as f:
        config = yaml.safe_load(f)

    from executor import load_state, save_state
    state = load_state()

    try:
        # ① 시장 데이터 수집
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

        # ② 피처 계산
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

        # ③ 레짐 감지 + Continuous Exposure 확률 구성
        print("[3] 레짐 감지 중...")
        from regime import (
            detect_regime, RegimeFilter, REGIMES,
            HmmRegimeClassifier, ensemble_regime,
            compute_rule_confidence,
        )

        rule_regime = detect_regime(features)

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

        state["last_run_confidence"] = round(combined_conf, 4)

        # 신뢰도 미달 → Neutral 폴백 (표시·알림 목적)
        conf_threshold = config.get("regime_filter", {}).get("confidence_threshold", 0.40)
        if raw_regime != "Neutral" and combined_conf < conf_threshold:
            print(
                f"    신뢰도 미달 ({combined_conf:.0%} < {conf_threshold:.0%})"
                f" → Neutral 폴백 (이전: {raw_regime})"
            )
            raw_regime = "Neutral"

        # 히스테리시스 필터 (표시·알림·state용 confirmed_regime)
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

        # Continuous Exposure용 레짐 확률 구성
        # HMM 확률이 있으면 직접 사용, 없으면 confirmed_regime 단일 확률
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

        # ④ 계좌 잔고 조회
        print("[4] 계좌 잔고 조회 중...")
        if args.dry_run:
            total_krw = total_usd_krw = total_krw_only = 0.0
            current_weights: dict = {}
            drawdown = 0.0
            print("    [dry-run] 계좌 조회 생략")
        else:
            from executor import KisRebalancer

            rebalancer = KisRebalancer(config, messenger=messenger)
            total_krw, total_usd_krw, total_krw_only, current_weights, drawdown = (
                rebalancer.get_portfolio_state()
            )
            usd_ratio = total_usd_krw / total_krw * 100 if total_krw else 0
            krw_ratio_pct = total_krw_only / total_krw * 100 if total_krw else 0
            print(f"    총 자산 (유니버스): {total_krw:,.0f} 원")
            print(f"    USD 계좌          : {total_usd_krw:,.0f} 원 ({usd_ratio:.1f}%)")
            print(f"    KRW 계좌          : {total_krw_only:,.0f} 원 ({krw_ratio_pct:.1f}%)")
            print(f"    드로우다운        : {drawdown:+.2%}")
            state["last_drawdown"] = round(drawdown, 4)
            state["last_total_krw"] = float(total_krw)

        # ⑤ 목표 비중 산출
        # Continuous Exposure: 레짐 확률 가중 평균 → vol targeting → class caps → 계좌별 비중
        print("[5] 목표 비중 산출 중...")
        from portfolio import (
            blend_regime_targets,
            apply_vol_targeting,
            apply_class_caps,
            derive_account_weights,
            merge_to_total_weights,
            apply_risk_controls,
            enforce_buffer_floor,
            apply_synthetic_reallocation,
        )

        # 5-a: 레짐 확률 가중 평균 (Discrete → Continuous)
        blended_targets = blend_regime_targets(blend_probs, config)
        cls_str = "  ".join(
            f"{k}:{v:.0%}" for k, v in sorted(blended_targets.items(), key=lambda x: -x[1])
            if v >= 0.005
        )
        print(f"    [블렌딩] {cls_str}")

        # 5-b: 변동성 타겟팅 (rvol > target_vol → equity 비중 자동 축소)
        blended_targets = apply_vol_targeting(blended_targets, features["realized_vol"], config)

        # 5-c: 자산군 최대 비중 상한 (DBMF ≤10%, equity_individual ≤12% 등)
        class_max = config.get("class_max_weight", {})
        if class_max:
            blended_targets = apply_class_caps(blended_targets, class_max)

        # 5-d: 계좌별 종목 비중으로 변환
        target_usd, target_krw = derive_account_weights(
            blended_targets, config, total_usd_krw, total_krw_only
        )

        # ⑥ 결제 상태 점검
        print("[6] 결제 상태 점검 중...")
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

        # ⑦ 리스크 제어 + 버퍼 플로어 + 합성 노출
        print("[7] 리스크 제어 적용 중...")
        risk_thresholds = config["risk"]["drawdown_thresholds"]

        # equity 종목 집합 (계좌별 분리)
        equity_classes = set(
            config["risk"].get(
                "equity_asset_classes",
                ["equity_etf", "equity_factor", "equity_individual"],
            )
        )
        equity_tickers_all = {
            t for t, meta in config["universe"].items()
            if meta["asset_class"] in equity_classes
        }
        usd_equity = equity_tickers_all & set(target_usd.keys())
        krw_equity = equity_tickers_all & set(target_krw.keys())

        target_usd = apply_risk_controls(target_usd, drawdown, risk_thresholds, usd_equity)
        target_krw = apply_risk_controls(target_krw, drawdown, risk_thresholds, krw_equity)

        # 드로우다운 레벨 출력
        if drawdown <= risk_thresholds["severe"]:
            print(f"    ⚠ SEVERE 드로우다운 ({drawdown:.1%}): equity → 0 (채권·금 유지)")
        elif drawdown <= risk_thresholds["moderate"]:
            print(f"    ⚠ MODERATE 드로우다운 ({drawdown:.1%}): equity ×0.40")
        elif drawdown <= risk_thresholds["mild"]:
            print(f"    ⚠ MILD 드로우다운 ({drawdown:.1%}): equity ×0.75")

        if buffer_tickers:
            target_krw = enforce_buffer_floor(target_krw, buffer_tickers, buffer_min)
            print(f"    버퍼 플로어 적용: {'+'.join(buffer_tickers)} ≥ {buffer_min:.0%} (KRW 계좌 기준)")

        if prev_deferred and synthetic_pairs and total_krw_only > 0:
            print("    합성 노출 적용 중 (지연 USD 매수 → KRW 동등 자산):")
            target_krw = apply_synthetic_reallocation(
                target_krw, prev_deferred, synthetic_pairs, total_krw_only
            )

        # 목표 비중 출력
        merged_target = merge_to_total_weights(
            target_usd, target_krw, total_usd_krw, total_krw_only
        )

        usd_r = (
            total_usd_krw / (total_usd_krw + total_krw_only)
            if (total_usd_krw + total_krw_only) > 0 else 0.30
        )
        equity_frac = sum(
            merged_target.get(t, 0)
            for t in ("379800", "379810", "TSLA", "PLTR", "VTV", "USMV")
        )
        factor_frac  = sum(merged_target.get(t, 0) for t in ("VTV", "USMV"))
        comm_frac    = merged_target.get("DBC", 0)
        mf_frac      = merged_target.get("DBMF", 0)
        bond_frac    = sum(merged_target.get(t, 0) for t in ("IEF", "SHY", "305080"))
        gold_frac    = merged_target.get("411060", 0)
        cash_frac    = merged_target.get("469830", 0)
        print(
            f"    [계좌비율 USD:{usd_r:.0%} KRW:{1-usd_r:.0%}]  "
            f"EQ:{equity_frac:.0%}(팩터{factor_frac:.0%}) "
            f"CM:{comm_frac:.0%} MF:{mf_frac:.0%} "
            f"BD:{bond_frac:.0%} AU:{gold_frac:.0%} CS:{cash_frac:.0%}"
        )

        print("    목표 비중 [USD 계좌]:")
        for ticker, w in sorted(target_usd.items(), key=lambda x: -x[1]):
            if w > 0:
                total_frac = merged_target.get(ticker, 0.0)
                cur = current_weights.get(ticker, 0.0)
                sign = "▲" if total_frac - cur > 0.005 else ("▼" if total_frac - cur < -0.005 else " ")
                print(f"      {sign} {ticker:<8} USD계좌:{w:.1%}  전체:{total_frac:.1%}  현재:{cur:.1%}")
        print("    목표 비중 [KRW 계좌]:")
        for ticker, w in sorted(target_krw.items(), key=lambda x: -x[1]):
            if w > 0:
                total_frac = merged_target.get(ticker, 0.0)
                cur = current_weights.get(ticker, 0.0)
                sign = "▲" if total_frac - cur > 0.005 else ("▼" if total_frac - cur < -0.005 else " ")
                print(f"      {sign} {ticker:<8} KRW계좌:{w:.1%}  전체:{total_frac:.1%}  현재:{cur:.1%}")

        # ⑧ 리밸런싱 실행
        print("[8] 리밸런싱 실행...")
        if args.dry_run:
            print("    [dry-run] 주문 생략")
            state["last_run_at"] = datetime.now().isoformat()
            state.update(regime_filter.to_dict())
            save_state(state)
            return

        messenger.send_start(regime, features, confidence=combined_conf)

        threshold = config["rebalancing"]["drift_threshold"]
        order_log, new_deferred = rebalancer.rebalance(
            current_weights=current_weights,
            target_usd=target_usd,
            target_krw=target_krw,
            total_usd_krw=total_usd_krw,
            total_krw_only=total_krw_only,
            threshold=threshold,
            tracker=tracker,
        )

        for d in new_deferred:
            tracker.add_deferred(d["ticker"], d["amount_krw"], d["currency"])
        state["last_run_at"] = datetime.now().isoformat()
        state.update(tracker.to_dict())
        state.update(regime_filter.to_dict())
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
        print("완료")

    except Exception as e:
        messenger.send_system_error(e)
        raise


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
