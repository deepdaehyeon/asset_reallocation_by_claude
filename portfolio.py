"""포트폴리오 목표 비중 선택 및 리스크 제어."""


def get_target_weights(regime: str, config: dict) -> dict:
    """레짐에 맞는 목표 비중을 config에서 로드한다."""
    return dict(config["regime_weights"][regime])


def apply_risk_controls(
    weights: dict,
    drawdown: float,
    thresholds: dict,
) -> dict:
    """
    드로우다운 수준에 따라 비중 전체를 스케일 다운한다.

    thresholds (config["risk"]["drawdown_thresholds"]):
        severe:   float (예: -0.30) → 전량 현금화
        moderate: float (예: -0.20) → ×0.5
        mild:     float (예: -0.10) → ×0.8
    """
    if drawdown <= thresholds["severe"]:
        return {t: 0.0 for t in weights}
    elif drawdown <= thresholds["moderate"]:
        scale = 0.50
    elif drawdown <= thresholds["mild"]:
        scale = 0.80
    else:
        scale = 1.0

    return {t: w * scale for t, w in weights.items()}


def compute_drift(current: dict, target: dict) -> float:
    """목표 대비 현재 비중 차이의 합계를 반환한다 (리밸런싱 필요 여부 판단)."""
    all_tickers = set(current) | set(target)
    return sum(abs(current.get(t, 0.0) - target.get(t, 0.0)) for t in all_tickers)
