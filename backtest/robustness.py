"""
레짐 비중 로버스트니스 검증.

검증 3단계:
  1. 서브기간 일관성  — 5개 시장 국면에서 성과 일관성 확인
  2. 레짐 의도 달성  — 공격(Goldilocks/Reflation)/방어(Slowdown/Stagflation/Crisis) 의도 달성 여부
  3. 비중 교란 테스트 — 핵심 비중 ±25% 변화에도 전략 성격 유지 여부 (--perturb 플래그 필요)

목적:
  최고 수익률 비중 탐색(과적합)이 아니라, 설정한 비중이
  "어느 시기에서도, 약간 비중이 달라도" 의도대로 작동함을 확인.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Dict, List, Tuple

import pandas as pd

from engine import BacktestEngine
from metrics import compute_metrics


# ── 서브기간 정의 ─────────────────────────────────────────────────────────────
# 서로 다른 시장 국면을 대표하는 5개 기간
SUBPERIODS: Dict[str, Tuple[str, str]] = {
    "GFC 회복기 (2010-13)":    ("2010-01-01", "2013-12-31"),
    "QE 강세장 (2014-16)":     ("2014-01-01", "2016-12-31"),
    "저금리 강세장 (2017-19)":  ("2017-01-01", "2019-12-31"),
    "COVID + 회복 (2020-21)":  ("2020-01-01", "2021-12-31"),
    "인플레 쇼크 (2022-24)":    ("2022-01-01", "2024-12-31"),
}

# ── 레짐 의도 ─────────────────────────────────────────────────────────────────
# growth: Goldilocks/Reflation → 해당 레짐 체류 기간에 양수 CAGR
# defense: Slowdown/Stagflation/Crisis → 해당 레짐 체류 기간에 BM보다 낮은 MaxDD
REGIME_INTENT: Dict[str, str] = {
    "Goldilocks":  "growth",
    "Reflation":   "growth",
    "Slowdown":    "defense",
    "Stagflation": "defense",
    "Crisis":      "defense",
}

# ── 교란 대상 자산군 ─────────────────────────────────────────────────────────
# 각 레짐에서 의도가 가장 강하게 반영된 핵심 비중 항목
PERTURB_TARGETS: Dict[str, List[str]] = {
    "Goldilocks":  ["equity_etf", "equity_individual"],
    "Reflation":   ["commodity", "gold"],
    "Slowdown":    ["managed_futures", "bond_usd", "bond_krw"],
    "Stagflation": ["commodity", "gold", "cash"],
    "Crisis":      ["cash", "managed_futures"],
}

PERTURB_SCALES = [0.75, 0.875, 1.0, 1.125, 1.25]


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _scale_regime_weights(
    config: dict, regime: str, classes: List[str], scale: float
) -> dict:
    """지정 레짐의 지정 자산군 비중을 scale 배 조정 후 합계 1.0으로 재정규화."""
    cfg = deepcopy(config)
    targets = cfg["regime_targets"][regime]
    for cls in classes:
        if cls in targets:
            targets[cls] *= scale
    total = sum(targets.values())
    if total > 0:
        cfg["regime_targets"][regime] = {k: v / total for k, v in targets.items()}
    return cfg


def _run_engine(
    config, universe_px, signal_px, start, end, rebal_freq, tx_cost
) -> Tuple[pd.DataFrame, dict]:
    engine = BacktestEngine(
        config=config,
        universe_px=universe_px,
        signal_px=signal_px,
        start=start,
        end=end,
        rebal_freq=rebal_freq,
        tx_cost=tx_cost,
    )
    result = engine.run()
    return result, compute_metrics(result["returns"])


# ── 1단계: 서브기간 일관성 ────────────────────────────────────────────────────

def run_subperiod_analysis(
    base_config: dict,
    universe_px: pd.DataFrame,
    signal_px: pd.DataFrame,
    bm_returns: pd.Series,
    rebal_freq: str = "W-FRI",
    tx_cost: float = 0.001,
) -> pd.DataFrame:
    """
    5개 서브기간별로 전략 vs 벤치마크(60/40) 성과를 비교한다.

    Sharpe가 모든 기간에서 양수이고, 기간 간 분산이 낮을수록 로버스트하다.
    """
    rows = []
    for name, (s, e) in SUBPERIODS.items():
        if len(universe_px[s:e]) < 100:
            continue
        print(f"    {name} ...", end=" ", flush=True)
        result, m = _run_engine(base_config, universe_px, signal_px, s, e, rebal_freq, tx_cost)
        if not m:
            print("데이터 부족, 건너뜀")
            continue
        bm_slice = bm_returns[s:e]
        bm_m = compute_metrics(bm_slice) if len(bm_slice) > 10 else {}
        dominant = result["regime"].value_counts().index[0]
        rows.append({
            "기간":       name,
            "전략 CAGR":  m["cagr"],
            "Sharpe":     m["sharpe"],
            "전략 MaxDD":  m["max_drawdown"],
            "BM CAGR":    bm_m.get("cagr", 0),
            "BM MaxDD":   bm_m.get("max_drawdown", 0),
            "초과수익":    m["cagr"] - bm_m.get("cagr", 0),
            "DD 절감":     abs(bm_m.get("max_drawdown", 0)) - abs(m["max_drawdown"]),
            "주도 레짐":   dominant,
        })
        print(f"CAGR {m['cagr']:+.1%}  Sharpe {m['sharpe']:.2f}  MaxDD {m['max_drawdown']:.1%}")

    return pd.DataFrame(rows).set_index("기간") if rows else pd.DataFrame()


# ── 2단계: 레짐 의도 달성 검증 ───────────────────────────────────────────────

def run_regime_intent_validation(
    result: pd.DataFrame,
    bm_returns: pd.Series,
) -> pd.DataFrame:
    """
    레짐별 전략이 의도한 역할을 달성하는지 검증한다.

    growth 레짐 (Goldilocks, Reflation):
        해당 레짐 체류 기간에 전략 CAGR > 0 (양수 수익)
    defense 레짐 (Slowdown, Stagflation, Crisis):
        해당 레짐 체류 기간에 |전략 MaxDD| < |BM MaxDD| (BM보다 손실 방어)
    """
    rows = []
    for regime, intent in REGIME_INTENT.items():
        mask = result["regime"] == regime
        if mask.sum() < 10:
            continue
        strat_r = result["returns"][mask]
        bm_r = bm_returns.reindex(strat_r.index).fillna(0)
        m_s = compute_metrics(strat_r)
        m_b = compute_metrics(bm_r)
        if not m_s or not m_b:
            continue

        s_cagr = m_s["cagr"]
        b_cagr = m_b["cagr"]
        s_dd   = m_s["max_drawdown"]
        b_dd   = m_b["max_drawdown"]

        if intent == "growth":
            passed = s_cagr > 0.0
            criterion = "전략 CAGR > 0%"
        else:
            passed = abs(s_dd) < abs(b_dd)
            criterion = "|전략 DD| < |BM DD|"

        rows.append({
            "레짐":        regime,
            "의도":        "성장추종" if intent == "growth" else "손실방어",
            "기준":        criterion,
            "체류일":      int(mask.sum()),
            "전략 CAGR":   s_cagr,
            "BM CAGR":     b_cagr,
            "전략 MaxDD":  s_dd,
            "BM MaxDD":    b_dd,
            "달성":        "✓ OK" if passed else "✗ FAIL",
        })

    return pd.DataFrame(rows).set_index("레짐") if rows else pd.DataFrame()


# ── 3단계: 비중 교란 테스트 ──────────────────────────────────────────────────

def run_weight_perturbation(
    base_config: dict,
    universe_px: pd.DataFrame,
    signal_px: pd.DataFrame,
    start: str,
    end: str,
    rebal_freq: str = "W-FRI",
    tx_cost: float = 0.001,
) -> Tuple[Dict[str, pd.DataFrame], dict]:
    """
    레짐별 핵심 비중을 ±25% 교란했을 때 CAGR/Sharpe/MaxDD 변화폭을 측정한다.

    CAGR 범위(max - min) < 2%  → ✓ 로버스트
    CAGR 범위 2~4%            → △ 보통
    CAGR 범위 > 4%            → ✗ 민감 (과적합 우려)
    """
    _, base_m = _run_engine(base_config, universe_px, signal_px, start, end, rebal_freq, tx_cost)

    all_results: Dict[str, pd.DataFrame] = {}
    for regime, classes in PERTURB_TARGETS.items():
        print(f"    {regime} ({', '.join(classes)}) ...", end=" ", flush=True)
        rows = []
        for scale in PERTURB_SCALES:
            cfg = _scale_regime_weights(base_config, regime, classes, scale)
            _, m = _run_engine(cfg, universe_px, signal_px, start, end, rebal_freq, tx_cost)
            rows.append({
                "scale":        scale,
                "cagr":         m.get("cagr", 0),
                "sharpe":       m.get("sharpe", 0),
                "max_drawdown": m.get("max_drawdown", 0),
                "cagr_diff":    m.get("cagr", 0) - base_m.get("cagr", 0),
                "is_base":      (scale == 1.0),
            })
        df = pd.DataFrame(rows).set_index("scale")
        cagr_range = df["cagr"].max() - df["cagr"].min()
        judge = "✓ 로버스트" if cagr_range < 0.02 else ("△ 보통" if cagr_range < 0.04 else "✗ 민감")
        print(f"CAGR 범위 {cagr_range:.1%}  {judge}")
        all_results[regime] = df

    return all_results, base_m
