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

    try:
        # ① 시장 데이터 수집
        print("━" * 50)
        print("[1] 시장 데이터 수집 중...")
        from fetcher import fetch_signal_prices

        signal_cfg = config["signal"]
        prices = fetch_signal_prices(
            tickers=signal_cfg["tickers"],
            lookback_days=signal_cfg["lookback_days"],
        )

        # ② 피처 계산
        print("[2] 피처 계산 중...")
        from features import compute_features

        features = compute_features(prices)
        print(f"    momentum_1m : {features['momentum_1m']:+.2%}")
        print(f"    momentum_3m : {features['momentum_3m']:+.2%}")
        print(f"    realized_vol: {features['realized_vol']:.2%} (연환산)")
        print(f"    VIX         : {features['vix']:.1f}")
        print(f"    credit_signal: {features['credit_signal']:+.2%}")

        # ③ 레짐 감지
        print("[3] 레짐 감지 중...")
        from regime import detect_regime

        regime = detect_regime(features)
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
        from executor import load_state, save_state
        from settlement import SettlementTracker

        settlement_cfg = config.get("settlement", {})
        buffer_tickers: list = settlement_cfg.get("buffer_tickers", [])
        buffer_min: float = settlement_cfg.get("buffer_min", 0.07)
        synthetic_pairs: dict = settlement_cfg.get("synthetic_pairs", {})

        if args.dry_run:
            tracker = SettlementTracker({})
            prev_deferred: list = []
        else:
            state = load_state()
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
            return

        messenger.send_start(regime, features)

        threshold = config["rebalancing"]["drift_threshold"]
        order_log, new_deferred = rebalancer.rebalance(
            current_weights=current_weights,
            target_weights=target_weights,
            total_value_krw=total_krw,
            threshold=threshold,
            tracker=tracker,
        )

        # 지연 매수 저장 + state 갱신
        for d in new_deferred:
            tracker.add_deferred(d["ticker"], d["amount_krw"], d["currency"])
        state = load_state()
        state.update(tracker.to_dict())
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
