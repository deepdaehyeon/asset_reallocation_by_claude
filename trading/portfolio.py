"""포트폴리오 목표 비중 선택 및 리스크 제어."""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

import numpy as np


# ── 연속 노출 (Continuous Exposure) ─────────────────────────────────────────

def blend_regime_targets(regime_probs: Dict[str, float], config: dict) -> dict:
    """
    레짐별 사후 확률을 가중치로 자산군 목표 비중을 혼합한다.

    Discrete regime 전환 대신 Continuous Exposure를 구현:
      Goldilocks 70% / Slowdown 30% → 비중도 7:3 가중 평균
    이를 통해 레짐 오판·지연에 의한 양방향 슬리피지를 완화한다.
    """
    from regime import DEFAULT_REGIME

    all_classes: Set[str] = set()
    for targets in config["regime_targets"].values():
        all_classes |= set(targets.keys())

    blended = {cls: 0.0 for cls in all_classes}
    total_prob = sum(
        p for r, p in regime_probs.items()
        if r in config["regime_targets"]
    )
    if total_prob <= 0:
        # 알 수 없는 레짐(예: Neutral)이 입력된 경우 DEFAULT_REGIME 타겟으로 폴백
        fallback = config["regime_targets"].get(DEFAULT_REGIME, {})
        return {cls: fallback.get(cls, 0.0) for cls in all_classes}

    for regime, prob in regime_probs.items():
        if regime not in config["regime_targets"]:
            continue
        norm_prob = prob / total_prob
        for cls, w in config["regime_targets"][regime].items():
            blended[cls] += norm_prob * w

    return blended


# ── 포트폴리오 EWMA 변동성 계산 ──────────────────────────────────────────────

def compute_portfolio_ewma_vol(
    prices,
    weights: dict,
    lam: float = 0.94,
    annualize: int = 252,
) -> float:
    """
    EWMA 방식으로 포트폴리오 실현 변동성을 계산한다.

    weights: {ticker: float} — 자산별 목표 비중 (prices에 없는 종목은 무시)
    lam:     EWMA 감쇠 파라미터 (RiskMetrics 표준 λ=0.94)
    반환: 연환산 변동성 (0 이상)

    포트폴리오 수익률 = Σ(w_i × r_i) → EWMA 분산 → √annualize 환산
    """
    import pandas as pd

    tickers = [t for t in weights if t in prices.columns and weights[t] > 0]
    if not tickers:
        return 0.0

    w = np.array([weights[t] for t in tickers])
    w /= w.sum()

    rets = prices[tickers].pct_change(fill_method=None).dropna()
    if len(rets) < 10:
        return 0.0

    port_rets = rets.values @ w
    # EWMA 분산
    var = float(np.var(port_rets[:10]))
    for r in port_rets[10:]:
        var = lam * var + (1 - lam) * r * r
    return float(np.sqrt(var * annualize))


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


# ── VIX 기반 동적 자산군 상한 ────────────────────────────────────────────────

def apply_dynamic_class_caps(targets: dict, class_max: dict, vix: float) -> dict:
    """
    VIX 수준에 따라 고변동 자산군 상한을 동적으로 축소한 뒤 apply_class_caps를 적용한다.

    VIX > 30: commodity/equity_individual 상한 50% 축소
    VIX > 25: 25% 축소
    """
    caps = dict(class_max)
    if vix > 30:
        scale = 0.50
    elif vix > 25:
        scale = 0.75
    else:
        return apply_class_caps(targets, caps)

    for cls in ("commodity", "equity_individual"):
        if cls in caps:
            caps[cls] = caps[cls] * scale

    return apply_class_caps(targets, caps)


# ── 변동성 타겟팅 ────────────────────────────────────────────────────────────

def apply_vol_targeting(
    targets: dict,
    realized_vol: float,
    config: dict,
    regime: str = "",
) -> dict:
    """
    포트폴리오 실현 변동성(EWMA 연환산)이 목표를 초과할 때 equity 비중을 비례 축소한다.

    레짐별 target_vol 지원:
      Goldilocks 13% / Reflation 11% / Slowdown 9% / Stagflation 8% / Crisis 6%
    regime 미지정 시 config의 target_vol(기본 10%)을 사용한다.

    scale = clip(target_vol / portfolio_vol, floor, 1.0)
    레버리지 없음: portfolio_vol < target_vol 이면 scale = 1.0 유지.
    축소된 equity 비중은 cash로 이동한다.
    """
    vol_cfg = config.get("vol_targeting", {})
    if not vol_cfg.get("enabled", False):
        return dict(targets)

    # 레짐별 목표 변동성 (config > 하드코딩 폴백)
    _regime_defaults = {
        "Goldilocks":  0.13,
        "Reflation":   0.11,
        "Slowdown":    0.09,
        "Stagflation": 0.08,
        "Crisis":      0.06,
    }
    regime_vols = vol_cfg.get("regime_target_vol", _regime_defaults)
    if regime and regime in regime_vols:
        target_vol = float(regime_vols[regime])
    else:
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

    # equity_factor + equity_sector + equity_individual: USD 예산 내에서 비례 배분
    eq_factor_wanted = targets.get("equity_factor", 0.0) * total
    eq_sector_wanted = targets.get("equity_sector", 0.0) * total
    eq_ind_wanted    = targets.get("equity_individual", 0.0) * total
    eq_usd_wanted    = eq_factor_wanted + eq_sector_wanted + eq_ind_wanted
    eq_usd_actual    = min(eq_usd_wanted, max(usd_remaining, 0.0))
    usd_remaining   -= eq_usd_actual

    if eq_usd_wanted > 0:
        usd_pool["equity_factor"]     = eq_usd_actual * eq_factor_wanted / eq_usd_wanted
        usd_pool["equity_sector"]     = eq_usd_actual * eq_sector_wanted / eq_usd_wanted
        usd_pool["equity_individual"] = eq_usd_actual * eq_ind_wanted    / eq_usd_wanted
    else:
        usd_pool["equity_factor"]     = 0.0
        usd_pool["equity_sector"]     = 0.0
        usd_pool["equity_individual"] = 0.0

    if eq_usd_wanted - eq_usd_actual > total * 0.001:
        print(
            f"    [USD 예산 조정] equity(factor+sector+individual) "
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
    eq_sector_final = usd_pool.get("equity_sector", 0.0)
    eq_ind_final    = usd_pool.get("equity_individual", 0.0)
    equity_total_target = (
        targets.get("equity_etf", 0.0)
        + targets.get("equity_individual", 0.0)
        + targets.get("equity_factor", 0.0)
        + targets.get("equity_sector", 0.0)
    )
    equity_etf_of_total = max(
        0.0,
        equity_total_target - (eq_factor_final + eq_sector_final + eq_ind_final) / total,
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
    equity_floor_pct: float = 0.10,
    cash_tickers: Optional[List[str]] = None,
) -> dict:
    """
    드로우다운 수준에 따라 비중을 단계적으로 조정한다.

    severe (-30%):   equity를 floor(기본 10%)까지 축소 — 채권·금·현금 유지
                     완전 청산 시 반등 구간 전체를 놓칠 수 있어 최소 비중 유지
    moderate (-20%): equity 40% 수준으로 강제 축소 (Slowdown/Crisis 강제 효과)
    mild (-10%):     equity 75% 유지 (소폭 방어)

    equity_tickers: USD·KRW 계좌 각각 해당 계좌의 equity 종목 집합
    equity_floor_pct: severe 시 equity 최소 유지율 (기본 10%)
    """
    severe = thresholds["severe"]
    moderate = thresholds["moderate"]
    mild = thresholds["mild"]
    floor = float(thresholds.get("equity_floor_pct", equity_floor_pct))

    def _add_reduction_to_cash(adjusted: dict, reduction: float) -> dict:
        if reduction <= 0:
            return adjusted
        prefs = cash_tickers or []
        # 우선순위 티커가 있으면 그쪽으로 이동, 없으면 '미할당 현금'을 허용
        for t in prefs:
            adjusted[t] = adjusted.get(t, 0.0) + reduction
            return adjusted
        return adjusted

    if drawdown <= severe:
        # equity를 floor 비율까지만 축소 — 축소분은 cash_tickers로 이동 (없으면 미할당 현금)
        adjusted = dict(weights)
        reduction = 0.0
        if equity_tickers:
            for t in list(adjusted.keys()):
                if t in equity_tickers:
                    old = adjusted[t]
                    adjusted[t] = old * floor
                    reduction += old - adjusted[t]
        else:
            # equity_tickers 미지정이면 전체를 축소하는 레거시 동작을 유지하되,
            # 축소분을 특정 티커로 이동시키지 않는다.
            return {t: w * floor for t, w in weights.items()}
        return _add_reduction_to_cash(adjusted, reduction)

    if drawdown <= moderate:
        adjusted = dict(weights)
        reduction = 0.0
        if equity_tickers:
            for t in list(adjusted.keys()):
                if t in equity_tickers:
                    old = adjusted[t]
                    adjusted[t] = old * 0.40
                    reduction += old - adjusted[t]
            return _add_reduction_to_cash(adjusted, reduction)
        return {t: w * 0.50 for t, w in weights.items()}

    if drawdown <= mild:
        adjusted = dict(weights)
        reduction = 0.0
        if equity_tickers:
            for t in list(adjusted.keys()):
                if t in equity_tickers:
                    old = adjusted[t]
                    adjusted[t] = old * 0.75
                    reduction += old - adjusted[t]
            return _add_reduction_to_cash(adjusted, reduction)
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
