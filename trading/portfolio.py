"""포트폴리오 목표 비중 선택 및 리스크 제어."""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

import numpy as np


# ── 연속 노출 (Continuous Exposure) ─────────────────────────────────────────

def blend_regime_targets(
    regime_probs: Dict[str, float],
    config: dict,
    transition_phase: bool = False,
) -> dict:
    """
    레짐별 사후 확률을 가중치로 자산군 목표 비중을 혼합한다.

    Discrete regime 전환 대신 Continuous Exposure를 구현:
      Goldilocks 70% / Slowdown 30% → 비중도 7:3 가중 평균
    이를 통해 레짐 오판·지연에 의한 양방향 슬리피지를 완화한다.

    transition_phase=True인 경우 regime_targets["Transition"] 비중을 직접 반환한다
    (regime_filter.transition_days 동안 risk-off). Transition 비중이 정의되지 않으면
    일반 blend 동작 유지.
    """
    from regime import DEFAULT_REGIME

    all_classes: Set[str] = set()
    for targets in config["regime_targets"].values():
        all_classes |= set(targets.keys())

    # Transition phase: 직접 보수 비중 반환
    if transition_phase and "Transition" in config["regime_targets"]:
        trans = config["regime_targets"]["Transition"]
        return {cls: trans.get(cls, 0.0) for cls in all_classes}

    blended = {cls: 0.0 for cls in all_classes}
    total_prob = sum(
        p for r, p in regime_probs.items()
        if r in config["regime_targets"] and r != "Transition"
    )
    if total_prob <= 0:
        # 알 수 없는 레짐(예: Neutral)이 입력된 경우 DEFAULT_REGIME 타겟으로 폴백
        fallback = config["regime_targets"].get(DEFAULT_REGIME, {})
        return {cls: fallback.get(cls, 0.0) for cls in all_classes}

    for regime, prob in regime_probs.items():
        if regime not in config["regime_targets"] or regime == "Transition":
            continue
        norm_prob = prob / total_prob
        for cls, w in config["regime_targets"][regime].items():
            blended[cls] += norm_prob * w

    return blended


def apply_core_satellite(
    sat: dict,
    config: dict,
    verbose: bool = False,
    eff_vol: float | None = None,
    vol_config: dict | None = None,
) -> dict:
    """
    core+satellite 혼합: 일부를 고정 레짐(기본 Goldilocks) 코어로 묶는다.

    sat은 이미 blend+vol타겟이 적용된 satellite 비중(현행 엔진 산출).
    core는 고정 레짐 타겟(기본 vol·blend 없음). 반환 = core_ratio·core + (1-core_ratio)·sat.

    config["core_satellite"] = {enabled, core_ratio, core_regime}. enabled=False이거나
    core_ratio<=0이면 sat을 그대로 반환(무회귀).

    옵트인 core_vol_targeting: True이고 eff_vol이 주어지면 코어에도 core_regime 기준
    vol targeting을 적용한다(축소분은 코어 내 cash로). 기본 False → 라이브 무변화.
    """
    cs = config.get("core_satellite", {})
    if not cs.get("enabled", False):
        return sat
    cf = float(cs.get("core_ratio", 0.0))
    if cf <= 0:
        return sat
    core_regime = cs.get("core_regime", "Goldilocks")
    core = blend_regime_targets({core_regime: 1.0}, config)
    if cs.get("core_vol_targeting", False) and eff_vol is not None:
        core = apply_vol_targeting(
            core, eff_vol, vol_config if vol_config is not None else config,
            regime=core_regime, blend_probs={core_regime: 1.0},
        )
    classes = set(core) | set(sat)
    combined = {c: cf * core.get(c, 0.0) + (1.0 - cf) * sat.get(c, 0.0) for c in classes}
    if verbose:
        print(f"    [core+satellite] core {cf:.0%} ({core_regime} 고정) "
              f"+ satellite {1 - cf:.0%} (엔진)")
    return combined


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

    VIX > 30: commodity 상한 50% 축소 (equity_individual은 2026-07-04 제거)
    VIX > 25: 25% 축소
    """
    caps = dict(class_max)
    if vix > 30:
        scale = 0.50
    elif vix > 25:
        scale = 0.75
    else:
        return apply_class_caps(targets, caps)

    for cls in ("commodity",):
        if cls in caps:
            caps[cls] = caps[cls] * scale

    return apply_class_caps(targets, caps)


# ── 변동성 타겟팅 ────────────────────────────────────────────────────────────

def apply_vol_targeting(
    targets: dict,
    realized_vol: float,
    config: dict,
    regime: str = "",
    blend_probs: dict | None = None,
) -> dict:
    """
    포트폴리오 실현 변동성(EWMA 연환산)이 목표를 초과할 때 equity 비중을 비례 축소한다.

    레짐별 target_vol 지원:
      Goldilocks 13% / Reflation 11% / Slowdown 9% / Stagflation 8% / Crisis 6%
    regime 미지정 시 config의 target_vol(기본 10%)을 사용한다.

    blend_target_vol(옵션): vol_targeting.blend_target_vol=True이고 blend_probs가 주어지면
      목표변동성을 단일 레짐이 아니라 blend 확률로 가중평균한다(연속 단계).
      target_vol = Σ p[r]·regime_vols[r] / Σ p[r]  (regime_vols에 있는 레짐만).
      비중 블렌딩과 동일 철학. 단 룰 빠른진입(regime_timing_source=rule)의 속도는 둔해질 수 있어
      A/B 검증 대상 — 기본 OFF. ([[experiment_2026-06-17_voltarget_blend]])

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
    if vol_cfg.get("blend_target_vol", False) and blend_probs:
        present = {r: float(regime_vols[r]) for r in regime_vols if r in blend_probs}
        mass = sum(float(blend_probs[r]) for r in present)
        if mass > 0:
            target_vol = sum(float(blend_probs[r]) * v for r, v in present.items()) / mass
        else:
            target_vol = float(vol_cfg.get("target_vol", 0.10))
    elif regime and regime in regime_vols:
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

    dest = str(vol_cfg.get("reduction_dest", "cash"))
    if equity_reduction > 0:
        if dest == "defensive":
            dest_classes = ["bond_krw", "bond_tips", "gold", "cash"]
        elif dest == "nonequity":
            dest_classes = ["bond_krw", "bond_tips", "gold", "cash",
                            "commodity", "managed_futures"]
        else:  # "cash" (기본) — 현행
            dest_classes = ["cash"]
        base = sum(adjusted.get(c, 0.0) for c in dest_classes)
        if base > 0 and dest_classes != ["cash"]:
            # 축소분을 대상 클래스에 현재 비중 비례 분배 (분배 대상 총량 0이면 cash 폴백)
            for c in dest_classes:
                adjusted[c] = adjusted.get(c, 0.0) + equity_reduction * (adjusted.get(c, 0.0) / base)
        else:
            adjusted["cash"] = adjusted.get("cash", 0.0) + equity_reduction

    print(
        f"    변동성 타겟팅: rvol {realized_vol:.1%} / 목표 {target_vol:.0%}"
        f" → equity ×{scale:.2f}  ({dest} +{equity_reduction:.1%})"
    )
    return adjusted


# ── 계좌별 비중 도출 ─────────────────────────────────────────────────────────

def route_accounts(econ: dict, cur_k: dict, cur_u: dict,
                   krw_room: float, usd_room: float) -> Tuple[dict, dict]:
    """등가 그룹의 목표·현재보유·계좌용량으로 KRW/USD 배정(won)을 직접 계산한다.

    현재보유를 최대한 유지하고, 델타(목표−현재)만 여유 있는 계좌로 라우팅한다(KRW 우선).
    통화 왕복(기존 보유를 팔아 다른 통화로 되사기)을 원천적으로 만들지 않는다:
      - 부족분: KRW 여유 있으면 KRW, 없으면 USD에서 추가 매수.
      - 초과분: 그 통화에서 매도(현금 확보).
      - KRW 진짜 과포화: 필요한 만큼만 KRW→USD 이동(진짜 relocate).
      - 양쪽 다 초과: 비례 축소(진짜 계좌 한계).
    econ/cur_k/cur_u/room 모두 won 단위.
    """
    alloc_k = {g: 0.0 for g in econ}
    alloc_u = {g: 0.0 for g in econ}
    for g, T in econ.items():
        k = min(cur_k.get(g, 0.0), T)
        u = min(cur_u.get(g, 0.0), max(0.0, T - k))
        alloc_k[g], alloc_u[g] = k, u
    used_k = sum(alloc_k.values())
    used_u = sum(alloc_u.values())
    for g, T in econ.items():
        need = T - (alloc_k[g] + alloc_u[g])
        if need <= 1e-9:
            continue
        add_k = min(need, max(0.0, krw_room - used_k))
        alloc_k[g] += add_k
        used_k += add_k
        need -= add_k
        if need > 1e-9:
            alloc_u[g] += need
            used_u += need
    if used_k > krw_room + 1e-6:
        excess = used_k - krw_room
        for g in econ:
            if excess <= 1e-9:
                break
            mv = min(alloc_k[g], excess, max(0.0, usd_room - used_u))
            if mv <= 0:
                continue
            alloc_k[g] -= mv
            alloc_u[g] += mv
            used_k -= mv
            used_u += mv
            excess -= mv
    if used_u > usd_room + 1e-6 and used_u > 0:
        s = usd_room / used_u
        alloc_u = {g: v * s for g, v in alloc_u.items()}
    if used_k > krw_room + 1e-6 and used_k > 0:
        s = krw_room / used_k
        alloc_k = {g: v * s for g, v in alloc_k.items()}
    return alloc_k, alloc_u


def derive_account_weights(
    targets: dict,
    config: dict,
    total_usd_krw: float,
    total_krw_only: float,
    current_weights: Optional[dict] = None,
) -> Tuple[dict, dict]:
    """자산군 목표 비중 + 현재보유 → 계좌별 종목 비중을 직접 도출한다 (2026-07-13 재작성).

    핵심: 경제적 등가 자산(KRW ETF ↔ USD ETF, 예 379800↔SPY·411060↔GLD)을 **하나로 보고**,
    목표·현재보유·계좌용량으로 각 계좌 매수/매도를 직접 계산한다(`route_accounts`). 기존 보유는
    유지하고 델타만 여유 계좌로 → 통화 왕복(역합성↔상계 교착)이 원천 소멸.

      - USD 전용(commodity·MF·factor·developed·emerging): USD 계좌 채움. equity 부족분은
        equity_etf 그룹 KRW로 근사 대체(대체재 없음).
      - KRW 전용(equity_sector·bond_tips): KRW 계좌.
      - 등가 그룹(equity_etf·gold·bond·cash): route_accounts로 직접 배분.
      - 잔여 USD → cash_usd(SGOV).

    current_weights 없으면(백테스트) 현재보유 0으로 보고 KRW 우선 채움(기존과 유사).
    """
    total = total_usd_krw + total_krw_only
    if total <= 0:
        fb = config.get("account_ratio_fallback", {"usd": 0.30, "krw": 0.70})
        total = 1.0
        total_usd_krw = float(fb["usd"])
        total_krw_only = float(fb["krw"])

    routing = config["asset_routing"]
    uni = config.get("universe", {})
    usd_cash_min = float(config.get("rebalancing", {}).get("usd_cash_min", 0.01))
    krw_cash_min = float(config.get("rebalancing", {}).get("krw_cash_min", 0.01))

    def _kfrac(won):
        return won / total_krw_only if total_krw_only > 0 else 0.0

    def _ufrac(won):
        return won / total_usd_krw if total_usd_krw > 0 else 0.0

    # 등가 그룹 정의 (config reverse_synthetic.map: KRW class → USD class)
    rs_map = config.get("reverse_synthetic", {}).get("map", {
        "bond_krw": "bond_usd", "cash": "cash_usd",
        "gold": "gold_usd", "equity_etf": "equity_etf_usd"})
    groups: dict = {}
    for krw_cls, usd_cls in rs_map.items():
        econ_frac = targets.get(krw_cls, 0.0)
        if krw_cls == "bond_krw":
            econ_frac += targets.get("bond_usd", 0.0)  # 미국채: bond_krw+bond_usd 통합
        groups[krw_cls] = {
            "econ": econ_frac * total,
            "krw_tk": routing.get(krw_cls, {}),
            "usd_tk": routing.get(usd_cls, {}),
        }

    # 현재 그룹 보유(won) — KRW/USD 분리
    tk2grp_k: dict = {}
    tk2grp_u: dict = {}
    for g, info in groups.items():
        for tk in info["krw_tk"]:
            tk2grp_k[tk] = g
        for tk in info["usd_tk"]:
            tk2grp_u[tk] = g
    cur_k = {g: 0.0 for g in groups}
    cur_u = {g: 0.0 for g in groups}
    if current_weights:
        for tk, w in current_weights.items():
            won = float(w) * total
            if tk in tk2grp_k:
                cur_k[tk2grp_k[tk]] += won
            elif tk in tk2grp_u:
                cur_u[tk2grp_u[tk]] += won

    # ── USD 전용 배정 (대체불가 우선; USD equity 부족분은 equity_etf 그룹 KRW 대체) ──
    usd_room = total_usd_krw * (1.0 - usd_cash_min)
    usd_pool: dict = {}
    for cls in ("commodity", "managed_futures"):
        amt = min(targets.get(cls, 0.0) * total, usd_room)
        usd_pool[cls] = amt
        usd_room -= amt
    for cls in ("equity_factor", "equity_developed", "equity_emerging"):
        want = targets.get(cls, 0.0) * total
        amt = min(want, usd_room)
        usd_pool[cls] = amt
        usd_room -= amt
        short = want - amt
        if short > total * 0.001 and "equity_etf" in groups:
            groups["equity_etf"]["econ"] += short  # KRW 근사 대체
            print(f"    [USD 부족 대체] {cls} {short/total*100:.1f}%p → equity_etf(KRW)")
    usd_room = max(0.0, usd_room)

    # ── KRW 전용 배정 ──
    krw_room = total_krw_only * (1.0 - krw_cash_min)
    krw_pool: dict = {}
    for cls in ("equity_sector", "bond_tips"):
        amt = targets.get(cls, 0.0) * total
        krw_pool[cls] = amt
        krw_room -= amt
    krw_room = max(0.0, krw_room)

    # ── 등가 그룹: 직접 계좌 배분 ──
    econ = {g: groups[g]["econ"] for g in groups}
    alloc_k, alloc_u = route_accounts(econ, cur_k, cur_u, krw_room, usd_room)

    # ── 종목 비중 조립 ──
    krw_w: dict = {}
    for cls, won in krw_pool.items():
        for tk, split in routing.get(cls, {}).items():
            krw_w[tk] = krw_w.get(tk, 0.0) + _kfrac(won) * split
    for g, info in groups.items():
        for tk, split in info["krw_tk"].items():
            krw_w[tk] = krw_w.get(tk, 0.0) + _kfrac(alloc_k[g]) * split

    usd_w: dict = {}
    for cls, won in usd_pool.items():
        for tk, split in routing.get(cls, {}).items():
            usd_w[tk] = usd_w.get(tk, 0.0) + _ufrac(won) * split
    for g, info in groups.items():
        for tk, split in info["usd_tk"].items():
            usd_w[tk] = usd_w.get(tk, 0.0) + _ufrac(alloc_u[g]) * split
    # 잔여 USD → SGOV(cash_usd)
    usd_used = sum(usd_pool.values()) + sum(alloc_u.values())
    leftover = total_usd_krw * (1.0 - usd_cash_min) - usd_used
    if leftover > total * 0.001:
        for tk, split in routing.get("cash_usd", {}).items():
            usd_w[tk] = usd_w.get(tk, 0.0) + _ufrac(leftover) * split

    # KRW 1% 현금 reserve 보존 후 정규화 (역합성 후에도 남은 초과분·매핑 불가 클래스 대비)
    krw_cash_min = float(config.get("rebalancing", {}).get("krw_cash_min", 0.01))
    krw_investable = 1.0 - krw_cash_min
    krw_total = sum(krw_w.values())
    if krw_total > krw_investable:
        krw_w = {t: w * krw_investable / krw_total for t, w in krw_w.items()}

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


def compute_drift(current: dict, target: dict, groups: list | None = None) -> float:
    """목표 대비 현재 비중 차이의 합계를 반환한다 (리밸런싱 필요 여부 판단).

    groups: 경제적 동일 자산 묶음 [[ticker, ...], ...]. 주어지면 같은 그룹의 종목들을
      합산한 뒤 차이를 계산한다 — QQQ(USD)↔379810(KRW 나스닥)처럼 통화(계좌)만 다른
      동일 노출의 재배치를 drift(=회전 유발)로 세지 않기 위함(역합성 부작용 방지).
      그룹에 없는 종목은 각자 개별 비교.
    """
    if groups:
        t2g: dict = {}
        for i, g in enumerate(groups):
            for t in g:
                t2g[t] = i
        cur_g: dict = {}
        tgt_g: dict = {}
        for t in set(current) | set(target):
            key = t2g.get(t, t)   # 그룹 소속이면 그룹 id, 아니면 종목 자신
            cur_g[key] = cur_g.get(key, 0.0) + current.get(t, 0.0)
            tgt_g[key] = tgt_g.get(key, 0.0) + target.get(t, 0.0)
        return sum(abs(cur_g.get(k, 0.0) - tgt_g.get(k, 0.0)) for k in set(cur_g) | set(tgt_g))

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

    정규화는 입력 target의 합계를 보존한다 (예: 99% → 99%). 1.0으로 정규화하면
    상위에서 확보한 1% 현금 reserve가 손실되므로 원래 합계로 스케일링한다.
    """
    if not deferred_buys or total_krw <= 0:
        return dict(target)

    original_sum = sum(target.values())
    adjusted = dict(target)
    added_any = False
    for item in deferred_buys:
        syn = synthetic_pairs.get(item["ticker"])
        if not syn or item.get("currency") != "USD":
            continue
        extra_total = item["amount_krw"] / total_krw
        # syn: str (단일 티커) 또는 dict (다중 티커 + 가중치)
        if isinstance(syn, dict):
            for tk, weight in syn.items():
                adjusted[tk] = adjusted.get(tk, 0.0) + extra_total * weight
            syn_label = "+".join(f"{tk}×{w:.0%}" for tk, w in syn.items())
        else:
            adjusted[syn] = adjusted.get(syn, 0.0) + extra_total
            syn_label = syn
        added_any = True
        print(
            f"    [합성] {item['ticker']} 지연 → {syn_label} +{extra_total:.1%} 임시 반영"
        )

    if added_any:
        total = sum(adjusted.values())
        if total > 0 and original_sum > 0:
            scale = original_sum / total
            adjusted = {k: v * scale for k, v in adjusted.items()}

    return adjusted
