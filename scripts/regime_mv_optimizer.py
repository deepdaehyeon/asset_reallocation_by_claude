"""
레짐별 평균-분산(MV) 최적화 — *진단용*. 수익(μ)·공분산(Σ)·현재비중을 한눈에.

질문(2026-06-15): 사용자 "레짐별 종목 수익률·비중·상관관계가 있으니 이를 종합해 최적
  포트폴리오를 뽑자". 1차로 자산군 단위 long-only MV 최적해를 위험회피(λ_risk) 스윕으로
  산출하고, *현재 regime_targets*와 나란히 비교한다. 단 이건 처방이 아니라 방향 진단이다.

왜 진단용인가(오버핏 함정):
  1. per-regime 비중 미세튜닝은 in-sample 노이즈다([[feedback-regime-targets-no-tuning]]).
     엔진(vol targeting·class cap·drift·분류래그)이 정밀 비중을 흡수한다.
  2. 소표본 레짐(Stagflation 163·Crisis 179일)은 μ·Σ 추정이 불안정 → MV가 과적합.
  3. 개별주식은 생존편향(PLTR·NVDA 사후 승자). 그래서 equity_individual 제외.
  MV는 μ의 작은 추정오차에 극단적으로 반응(error maximizer)하므로, 가중치 캡·Σ 수축
  (Ledoit-Wolf식 대각 shrink)·현재비중 L2 앵커로 강하게 정규화한다. 그래도 참고용.

방법(규칙4 정합: 채택은 MV 점수가 아니라 4지표 OOS로):
  - 레짐 = 라이브 acting regime(rule, 일별 detect_regime).
  - 자산군 수익 = asset_routing within-class 결합(체감 자산군 수익).
  - μ = 연율 평균, Σ = 연율 공분산(대각 shrink δ로 정규화).
  - maximize  μ'w − λ_risk·w'Σw − γ·||w − w_current||²   s.t. w≥0, Σw=1, w≤cap.
  - λ_risk 스윕으로 공격↔방어 스펙트럼을 보여주고 현재비중과 대조.
한계: 프록시·in-sample. 동시점(평균회귀 꼬리 포함). 처방 아님 — 견고 후보만 OOS로.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.optimize import minimize

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from data import load_all_prices  # noqa: E402
from features import compute_features  # noqa: E402
from regime import detect_regime  # noqa: E402

START = "2010-01-01"
END = "2025-04-30"
MAIN_REGIMES = ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis"]
EXCLUDE_CLASSES = {"commodity_krw", "cash_usd", "bond_usd", "equity_individual"}
ANNUAL = 252
WEIGHT_CAP = 0.30          # 단일 자산군 상한 (집중 방지)
SHRINK_DELTA = 0.30        # Σ 대각 수축 강도 (0=원본, 1=대각만)
ANCHOR_GAMMA = 5.0         # 현재비중 L2 앵커 (과적합 억제)
RISK_AVERSIONS = [2.0, 5.0, 10.0]  # 공격→방어

ORDER = ["equity_etf", "equity_factor", "equity_sector",
         "equity_developed", "equity_emerging", "commodity", "managed_futures",
         "gold", "bond_tips", "bond_krw", "cash"]
SHORT = {
    "equity_etf": "eqETF", "equity_factor": "eqFac", "equity_sector": "eqSec",
    "equity_developed": "eqDEV", "equity_emerging": "eqEMG",
    "commodity": "comm", "managed_futures": "MF", "gold": "gold",
    "bond_tips": "tips", "bond_krw": "bond", "cash": "cash",
}


def daily_rule_regime(signal_px, lookback=130, buffer=60):
    idx = signal_px.index
    out = {}
    min_start = signal_px.index.min() + pd.Timedelta(days=lookback + buffer)
    for as_of in idx:
        if as_of < min_start:
            continue
        sig = signal_px[as_of - pd.Timedelta(days=lookback + buffer):as_of]
        if len(sig) < 30:
            continue
        out[as_of] = detect_regime(compute_features(sig))
    return pd.Series(out).sort_index()


def class_returns(px, routing, present):
    ret = px.pct_change()
    out = {}
    for cls, members in routing.items():
        if cls in EXCLUDE_CLASSES:
            continue
        avail = {t: w for t, w in members.items() if t in present}
        if not avail:
            continue
        s = sum(avail.values())
        wts = {t: w / s for t, w in avail.items()}
        out[cls] = sum(ret[t] * w for t, w in wts.items())
    return pd.DataFrame(out)


def shrink_cov(sigma, delta):
    d = np.diag(np.diag(sigma))
    return (1 - delta) * sigma + delta * d


def optimize(mu, sigma, w_cur, lam_risk, cap, gamma):
    n = len(mu)

    def neg_obj(w):
        return -(mu @ w - lam_risk * (w @ sigma @ w) - gamma * np.sum((w - w_cur) ** 2))

    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    bounds = [(0.0, cap)] * n
    w0 = w_cur / w_cur.sum() if w_cur.sum() > 0 else np.full(n, 1 / n)
    res = minimize(neg_obj, w0, method="SLSQP", bounds=bounds, constraints=cons,
                   options={"maxiter": 500, "ftol": 1e-10})
    w = np.clip(res.x, 0, None)
    return w / w.sum()


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    routing = cfg["asset_routing"]
    targets = cfg["regime_targets"]

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=cfg, start=START, end=END, use_cache=True)
    present = list(universe_px.columns)

    print("일별 rule 레짐 산출 중...")
    regime = daily_rule_regime(signal_px).reindex(universe_px.index).ffill().dropna()
    cret = class_returns(universe_px, routing, present).reindex(regime.index)
    cols = [c for c in ORDER if c in cret.columns]
    cret = cret[cols]

    print(f"\n  설정: cap={WEIGHT_CAP:.0%}, Σ대각수축δ={SHRINK_DELTA}, "
          f"현재비중앵커γ={ANCHOR_GAMMA}, 위험회피λ={RISK_AVERSIONS}")
    print(f"  ※ 자산군 단위·long-only·진단용. 개별주식 제외(생존편향). 채택은 4지표 OOS로.")

    for rg in MAIN_REGIMES:
        sub = cret[regime == rg].dropna(how="all").dropna(axis=1, how="any")
        ndays = len(sub)
        rcols = list(sub.columns)
        mu = sub.mean().values * ANNUAL
        sigma = shrink_cov(sub.cov().values * ANNUAL, SHRINK_DELTA)

        # 현재비중(자산군 합=1 정규화; 표시 자산군만)
        cur = np.array([float(targets.get(rg, {}).get(c, 0.0)) for c in rcols])
        cur_norm = cur / cur.sum() if cur.sum() > 0 else np.full(len(rcols), 1 / len(rcols))

        print(f"\n{'='*92}")
        print(f"  [{rg}]  ({ndays}일)  자산군 MV 최적비중 vs 현재비중(정규화)")
        print(f"{'='*92}")
        # μ·연율vol 참고열
        vol = np.sqrt(np.diag(sigma))
        sols = {f"λ={lr:g}": optimize(mu, sigma, cur_norm, lr, WEIGHT_CAP, ANCHOR_GAMMA)
                for lr in RISK_AVERSIONS}

        hdr = f"  {'자산군':>8}{'연수익':>8}{'연변동':>8}{'현재':>8}"
        for k in sols:
            hdr += f"{k:>9}"
        print(hdr)
        print("  " + "─" * 86)
        order_idx = np.argsort(-mu)
        for i in order_idx:
            c = rcols[i]
            row = f"  {SHORT.get(c, c):>8}{mu[i]:>7.0%}{vol[i]:>8.0%}{cur_norm[i]:>8.0%}"
            for k, w in sols.items():
                row += f"{w[i]:>9.0%}"
            print(row)

    print("\n  주의: 자산군 단위·long-only·동시점 in-sample MV. μ는 추정오차에 민감(error")
    print("  maximizer) → 캡·Σ수축·현재비중앵커로 정규화했어도 처방 아님. 견고 후보만 OOS 검증.")


if __name__ == "__main__":
    main()
