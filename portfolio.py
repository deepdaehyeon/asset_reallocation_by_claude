"""포트폴리오 목표 비중 선택 및 리스크 제어."""
from typing import List


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


def enforce_buffer_floor(
    weights: dict,
    buffer_tickers: List[str],
    buffer_min: float,
) -> dict:
    """
    버퍼 자산(469830·SHY)이 항상 buffer_min 이상을 유지하도록 비중을 조정한다.

    부족분만큼 비-버퍼 자산 비중을 pro-rata로 차감하고,
    비중이 가장 큰 버퍼 자산에 그 차액을 추가한다.
    """
    buf_set = set(buffer_tickers)
    current_buf = sum(weights.get(t, 0.0) for t in buf_set)
    if current_buf >= buffer_min:
        return dict(weights)

    shortage = buffer_min - current_buf
    non_buf_total = sum(w for t, w in weights.items() if t not in buf_set)
    if non_buf_total <= 0:
        return dict(weights)

    scale = 1.0 - shortage / non_buf_total
    primary = max(buf_set, key=lambda t: weights.get(t, 0.0))
    return {
        t: (w + shortage if t == primary else w) if t in buf_set else w * scale
        for t, w in weights.items()
    }


def apply_synthetic_reallocation(
    target: dict,
    deferred_buys: List[dict],
    synthetic_pairs: dict,
    total_krw: float,
) -> dict:
    """
    이전 실행에서 지연된 USD 매수에 대해 KRW 동등 자산 비중을 임시 증가시킨다.

    deferred_buys: [{ticker, amount_krw, currency}, ...]
    synthetic_pairs: {usd_ticker: krw_ticker}  (config에서 로드)

    동작:
      IEF 매수 지연 2% → 305080(TIGER 미국채10년) 목표비중 += 2%
      다음 실행에서 IEF가 실제 매수되면 synthetic이 사라지고 305080은 자연스럽게 정리됨.
    """
    if not deferred_buys or total_krw <= 0:
        return dict(target)

    adjusted = dict(target)
    for item in deferred_buys:
        syn = synthetic_pairs.get(item["ticker"])
        if not syn or item.get("currency") != "USD":
            continue
        extra = item["amount_krw"] / total_krw
        adjusted[syn] = adjusted.get(syn, 0.0) + extra
        print(
            f"    [합성] {item['ticker']} 지연 → {syn} +{extra:.1%} 임시 반영"
        )
    return adjusted
