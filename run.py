"""
자동 자산 배분 시스템 진입점.

파이프라인:
  fetch → features → regime → target_weights → risk_controls → rebalance
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
        from portfolio import get_target_weights, apply_risk_controls

        target_weights = get_target_weights(regime, config)

        # ⑤ 포트폴리오 현황 조회
        print("[5] 계좌 잔고 조회 중...")
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

        # ⑥ 리스크 제어 적용
        print("[6] 리스크 제어 적용 중...")
        risk_thresholds = config["risk"]["drawdown_thresholds"]
        target_weights = apply_risk_controls(target_weights, drawdown, risk_thresholds)

        # 목표 비중 출력
        print("    목표 비중:")
        for ticker, w in sorted(target_weights.items(), key=lambda x: -x[1]):
            if w > 0:
                cur = current_weights.get(ticker, 0.0)
                diff = w - cur
                sign = "▲" if diff > 0.005 else ("▼" if diff < -0.005 else " ")
                print(f"      {sign} {ticker:<8} target:{w:.1%}  current:{cur:.1%}")

        # ⑦ 리밸런싱 실행
        print("[7] 리밸런싱 실행...")
        if args.dry_run:
            print("    [dry-run] 주문 생략")
            return

        if not args.dry_run:
            messenger.send_start(regime, features)

        threshold = config["rebalancing"]["drift_threshold"]
        order_log = rebalancer.rebalance(
            current_weights=current_weights,
            target_weights=target_weights,
            total_value_krw=total_krw,
            threshold=threshold,
        )

        messenger.send_complete(
            regime=regime,
            total_krw=total_krw,
            drawdown=drawdown,
            target_weights=target_weights,
            current_weights=current_weights,
            order_log=order_log,
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
