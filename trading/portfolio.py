"""포트폴리오 목표 비중 선택 및 리스크 제어."""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple


# ── 연속 노출 (Continuous Exposure) ─────────────────────────────────────────

def blend_regime_targets(regime_probs: Dict[str, float], config: dict) -> dict:
    """
    레짐별 사후 확률을 가중치로 자산군 목표 비중을 혼합한다.

    Discrete regime 전환 대신 Continuous Exposure를 구현:
      Risk-On 70% / Neutral 30% → 비중도 7:3 가중 평균
    이를 통해 레짐 오판·지연에 의한 양방향 슬리피지를 완화한다.
    """
    all_classes: Set[str] = set()
    for targets in config["regime_targets"].values():
        all_classes |= set(targets.keys())

    blended = {cls: 0.0 for cls in all_classes}
    total_prob = sum(
        p for r, p in regime_probs.items()
        if r in config["regime_targets"]
    )
    if total_prob <= 0:
        return blended

    for regime, prob in regime_probs.items():
        if regime not in config["regime_targets"]:
            continue
        norm_prob = prob / total_prob
        for cls, w in config["regime_targets"][regime].items():
            blended[cls] += norm_prob * w

    return blended


# ── 자산군 상한 적용 ─────────────────────────────────────────────────────────

def apply_class_caps(targets: dict, class_max: dict) -> dict:
    """
    자산군별 최대 비중 상한을 적용한다.

    초과분은 cash로 이동하여 포트폴리오 총합 1.0을 유지한다.
    class_max: config["class_max_weight"]
    """
    adjusted = dict(targets)
    excess = 0.0
    for cls, max_w in class_max.items():
        if cls in adjusted and adjusted[cls] > max_w:
            excess += adjusted[cls] - max_w
            adjusted[cls] = max_w
    if excess > 0:
        adjusted["cash"] = adjusted.get("cash", 0.0) + excess
    return adjusted


# ── 변동성 타겟팅 ────────────────────────────────────────────────────────────

def apply_vol_targeting(
    targets: dict,
    realized_vol: float,
    config: dict,
) -> dict:
    """
    실현 변동성(연환산)이 목표를 초과할 때 equity 비중을 비례 축소한다.

    scale = clip(target_vol / realized_vol, floor, 1.0)
    레버리지 없음: realized_vol < target_vol 이면 scale = 1.0 유지.
    축소된 equity 비중은 cash로 이동한다.

    같은 60% equity라도 rvol 25% vs rvol 10%는 완전히 다른 리스크이므로
    포지션 크기를 변동성 역비례로 조정해 실질 리스크를 일정하게 유지한다.
    """
    vol_cfg = config.get("vol_targeting", {})
    if not vol_cfg.get("enabled", False):
        return dict(targets)

    target_vol = float(vol_cfg.get("target_vol", 0.10))
    floor = float(vol_cfg.get("floor", 0.65))
    equity_classes = set(vol_cfg.get("equity_asset_classes", []))

    if realized_vol <= 0 or not equity_classes:
        return dict(targets)

    scale = min(target_vol / realized_vol, 1.0)
    scale = max(scale, floor)

    if scale >= 0.999:
        return dict(targets)

    adjusted = dict(targets)
    equity_reduction = 0.0
    for cls in equity_classes:
        if cls in adjusted and adjusted[cls] > 0:
            reduction = adjusted[cls] * (1.0 - scale)
            equity_reduction += reduction
            adjusted[cls] -= reduction

    if equity_reduction > 0:
        adjusted["cash"] = adjusted.get("cash", 0.0) + equity_reduction

    print(
        f"    변동성 타겟팅: rvol {realized_vol:.1%} / 목표 {target_vol:.0%}"
        f" → equity ×{scale:.2f}  (cash +{equity_reduction:.1%})"
    )
    return adjusted


# ── 계좌별 비중 도출 ─────────────────────────────────────────────────────────

def derive_account_weights(
    targets: dict,
    config: dict,
    total_usd_krw: float,
    total_krw_only: float,
) -> Tuple[dict, dict]:
    """
    블렌딩·조정된 자산군 목표 비중으로부터 계좌별 종목 비중을 동적으로 도출한다.

    USD 배정 우선순위:
      1순위 — commodity, managed_futures (KRW 대체재 없음, 전액 배정)
      2순위 — equity_factor + equity_individual (USD 전용, 예산 내 비례 배분)
      3순위 — bond_usd (잔여 예산에만)
      잔여 USD → 1~3 항목 비례 확대 (USD 계좌 100% 소진)

    KRW 배정:
      equity_etf = 총 equity 목표 - USD equity 실제 배분 (자동 흡수)
      gold, bond_krw, cash = 목표 그대로

    Args:
        targets: blend_regime_targets() 또는 regime_targets[regime] 반환값
    """
    total = total_usd_krw + total_krw_only
    if total <= 0:
        fb = config.get("account_ratio_fallback", {"usd": 0.30, "krw": 0.70})
        total = 1.0
        total_usd_krw = float(fb["usd"])
        total_krw_only = float(fb["krw"])

    routing = config["asset_routing"]
    krw_ratio = total_krw_only / total

    # ── USD 예산 배정 ────────────────────────────────────────────────────────
    usd_pool: dict = {}
    usd_remaining = float(total_usd_krw)

    for cls in ("commodity", "managed_futures"):
        amt = min(targets.get(cls, 0.0) * total, usd_remaining)
        usd_pool[cls] = amt
        usd_remaining -= amt

    # equity_factor + equity_individual: USD 예산 내에서 비례 배분
    eq_factor_wanted = targets.get("equity_factor", 0.0) * total
    eq_ind_wanted = targets.get("equity_individual", 0.0) * total
    eq_usd_wanted = eq_factor_wanted + eq_ind_wanted
    eq_usd_actual = min(eq_usd_wanted, max(usd_remaining, 0.0))
    usd_remaining -= eq_usd_actual

    if eq_usd_wanted > 0:
        factor_ratio = eq_factor_wanted / eq_usd_wanted
        usd_pool["equity_factor"] = eq_usd_actual * factor_ratio
        usd_pool["equity_individual"] = eq_usd_actual * (1.0 - factor_ratio)
    else:
        usd_pool["equity_factor"] = 0.0
        usd_pool["equity_individual"] = 0.0

    if eq_usd_wanted - eq_usd_actual > total * 0.001:
        print(
            f"    [USD 예산 조정] equity(factor+individual) "
            f"{eq_usd_wanted/total:.1%} → {eq_usd_actual/total:.1%} "
            f"(USD 비중 {total_usd_krw/total:.0%} 한도)"
        )

    bond_usd_actual = min(targets.get("bond_usd", 0.0) * total, max(usd_remaining, 0.0))
    usd_pool["bond_usd"] = bond_usd_actual
    usd_remaining -= bond_usd_actual

    # 잔여 USD → 기배정 항목 비례 확대 (USD 계좌 100% 소진)
    allocated = sum(usd_pool.values())
    if allocated > 0 and usd_remaining > 1.0:
        scale = total_usd_krw / allocated
        usd_pool = {k: v * scale for k, v in usd_pool.items()}

    # 계좌 비중으로 변환
    usd_w: dict = {}
    for cls, amt in usd_pool.items():
        for ticker, split in routing.get(cls, {}).items():
            usd_w[ticker] = usd_w.get(ticker, 0.0) + (amt / total_usd_krw) * split

    # ── KRW 배정 ────────────────────────────────────────────────────────────
    eq_factor_final = usd_pool.get("equity_factor", 0.0)
    eq_ind_final = usd_pool.get("equity_individual", 0.0)
    equity_total_target = (
        targets.get("equity_etf", 0.0)
        + targets.get("equity_individual", 0.0)
        + targets.get("equity_factor", 0.0)
    )
    equity_etf_of_total = max(
        0.0,
        equity_total_target - (eq_factor_final + eq_ind_final) / total,
    )

    krw_w: dict = {}
    if krw_ratio > 0:
        for ticker, split in routing.get("equity_etf", {}).items():
            krw_w[ticker] = (equity_etf_of_total / krw_ratio) * split

        for cls in ("gold", "bond_krw", "cash"):
            frac = targets.get(cls, 0.0) / krw_ratio
            for ticker, split in routing.get(cls, {}).items():
                krw_w[ticker] = krw_w.get(ticker, 0.0) + frac * split

    # KRW 합계 > 100% 시 비례 정규화 (계좌 예산 초과 방지)
    krw_total = sum(krw_w.values())
    if krw_total > 1.0:
        krw_w = {t: w / krw_total for t, w in krw_w.items()}

    return usd_w, krw_w


def merge_to_total_weights(
    usd_w: dict,
    krw_w: dict,
    total_usd_krw: float,
    total_krw_only: float,
) -> dict:
    """계좌별 비중을 전체 포트폴리오 기준 비중으로 변환한다. drift·출력 용도."""
    total = total_usd_krw + total_krw_only
    if total <= 0:
        return {}
    merged = {t: w * total_usd_krw / total for t, w in usd_w.items()}
    merged.update({t: w * total_krw_only / total for t, w in krw_w.items()})
    return merged


# ── 리스크 제어 ───────────────────────────────────────────────────────────────

def apply_risk_controls(
    weights: dict,
    drawdown: float,
    thresholds: dict,
    equity_tickers: Optional[Set[str]] = None,
) -> dict:
    """
    드로우다운 수준에 따라 비중을 단계적으로 조정한다.

    severe (-30%):   equity만 0으로 축소 — 채권·금·현금 유지
                     "바닥에서 전량 현금화" 패턴을 방지한다.
    moderate (-20%): equity 40% 수준으로 강제 축소 (Risk-Off 강제 효과)
    mild (-10%):     equity 75% 유지 (소폭 방어)

    equity_tickers: USD·KRW 계좌 각각 해당 계좌의 equity 종목 집합
    """
    severe = thresholds["severe"]
    moderate = thresholds["moderate"]
    mild = thresholds["mild"]

    if drawdown <= severe:
        # equity만 0 — 채권·금·현금은 그대로 유지
        if equity_tickers:
            return {
                t: (0.0 if t in equity_tickers else w)
                for t, w in weights.items()
            }
        return {t: 0.0 for t in weights}

    if drawdown <= moderate:
        if equity_tickers:
            return {
                t: (w * 0.40 if t in equity_tickers else w)
                for t, w in weights.items()
            }
        return {t: w * 0.50 for t, w in weights.items()}

    if drawdown <= mild:
        if equity_tickers:
            return {
                t: (w * 0.75 if t in equity_tickers else w)
                for t, w in weights.items()
            }
        return {t: w * 0.80 for t, w in weights.items()}

    return dict(weights)


def compute_drift(current: dict, target: dict) -> float:
    """목표 대비 현재 비중 차이의 합계를 반환한다 (리밸런싱 필요 여부 판단)."""
    all_tickers = set(current) | set(target)
    return sum(abs(current.get(t, 0.0) - target.get(t, 0.0)) for t in all_tickers)


# ── 결제 버퍼 ────────────────────────────────────────────────────────────────

def enforce_buffer_floor(
    weights: dict,
    buffer_tickers: List[str],
    buffer_min: float,
) -> dict:
    """
    버퍼 자산(469830)이 항상 buffer_min 이상을 유지하도록 비중을 조정한다.

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
    synthetic_pairs: {usd_ticker: krw_ticker}
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
