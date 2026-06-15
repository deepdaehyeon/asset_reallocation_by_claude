"""
레짐별 자산군 상관관계 행렬 — 동시점(그 레짐 라벨된 날)의 일별수익 상관.

질문(2026-06-15): 사용자 "레짐별 종목 상관관계를 뽑고, 수익·비중·상관을 종합해 최적
  포트폴리오를 뽑자". 1단계로 상관 구조를 측정한다. regime_targets가 자산군 단위로
  작동하고 같은 자산군 내 종목은 상관~1이라, 자산군 단위 행렬이 의사결정에 정합적이다.

방법:
  - 레짐 = 라이브 acting regime(rule, 일별 detect_regime).
  - 자산군 수익 = asset_routing within-class 비중 결합(= 그 자산군을 라우팅대로 든 체감).
  - 각 레짐 라벨된 날만 모아 자산군 간 일별수익 상관행렬.
  - 추가: 위험핵(equity_etf) 대비 상관 순위 → 분산자(저/음의 상관) 식별.

한계: 소표본 레짐(Stagflation 163·Crisis 179일)은 상관 추정 불안정. 프록시·in-sample.
  상관은 평균회귀 꼬리(레짐 경계) 포함. 최적화 입력으로 쓰면 오버핏 위험(별도 고지).
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

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
EXCLUDE_CLASSES = {"commodity_krw", "cash_usd", "bond_usd"}  # 합성·USD현금·bond_krw중복 제외
# 표시 순서(위험→방어)
ORDER = ["equity_etf", "equity_factor", "equity_sector", "equity_individual",
         "equity_developed", "equity_emerging", "commodity", "managed_futures",
         "gold", "bond_tips", "bond_krw", "cash"]
SHORT = {
    "equity_etf": "eqETF", "equity_factor": "eqFac", "equity_sector": "eqSec",
    "equity_individual": "eqIND", "equity_developed": "eqDEV", "equity_emerging": "eqEMG",
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


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    routing = cfg["asset_routing"]

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=cfg, start=START, end=END, use_cache=True)
    present = list(universe_px.columns)

    print("일별 rule 레짐 산출 중...")
    regime = daily_rule_regime(signal_px).reindex(universe_px.index).ffill().dropna()
    cret = class_returns(universe_px, routing, present).reindex(regime.index)
    cols = [c for c in ORDER if c in cret.columns]
    cret = cret[cols]

    for rg in MAIN_REGIMES:
        sub = cret[regime == rg].dropna(how="all")
        corr = sub.corr()
        ndays = len(sub)
        labels = [SHORT.get(c, c)[:5] for c in corr.columns]
        print(f"\n{'='*100}")
        print(f"  [{rg}]  ({ndays}일)  자산군 일별수익 상관행렬 (동시점)")
        print(f"{'='*100}")
        head = "        " + "".join(f"{l:>7}" for l in labels)
        print(head)
        for i, c in enumerate(corr.index):
            row = f"  {SHORT.get(c, c)[:5]:>5} "
            for j, c2 in enumerate(corr.columns):
                v = corr.iloc[i, j]
                row += f"{v:>7.2f}" if pd.notna(v) else f"{'—':>7}"
            print(row)

        # 위험핵(equity_etf) 대비 상관 → 분산자 식별
        if "equity_etf" in corr.columns:
            ec = corr["equity_etf"].drop("equity_etf").sort_values()
            div = ", ".join(f"{SHORT.get(k,k)}={v:+.2f}" for k, v in ec.items())
            print(f"  · 주식(eqETF) 대비 상관(낮을수록 분산효과 큼): {div}")

    print("\n  주의: 소표본 레짐(Stagflation·Crisis)은 상관 추정 불안정. 프록시·in-sample.")
    print("  같은 자산군 내 종목(379800/379810 등)은 상관~1 → 자산군 단위로 집계.")


if __name__ == "__main__":
    main()
