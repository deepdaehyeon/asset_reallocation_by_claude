"""
레짐별 포트폴리오 리뷰 요약 — 비중 + 동시점 수익(Martin·CAGR·Ulcer·MaxDD·회복) + 상관행렬.
외부 LLM 리뷰용 마크다운 표를 stdout으로 출력(코멘트는 별도 문서에서 사람이 작성).

질문(2026-06-15): 사용자 "레짐별 상관·수익·비중을 잘 정리해 외부 LLM 리뷰받을 용도로 보여줘".
방법: 자산군 단위. 비중=라이브 regime_targets[class]. 수익=동시점(그 레짐 라벨된 날) asset_routing
  결합 일별수익의 compute_metrics. 상관=동시점 자산군 일별수익 상관행렬.
한계: 프록시·in-sample·동시점(평균회귀 꼬리). 소표본 레짐(Stag163·Crisis179) 추정 불안정.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from data import load_all_prices  # noqa: E402
from features import compute_features  # noqa: E402
from regime import detect_regime  # noqa: E402
from metrics import compute_metrics, recovery_duration  # noqa: E402

START = "2010-01-01"
END = "2025-04-30"
MAIN_REGIMES = ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis"]
EXCLUDE_CLASSES = {"commodity_krw", "cash_usd", "bond_usd"}
ORDER = ["equity_etf", "equity_factor", "equity_sector",
         "equity_developed", "equity_emerging", "commodity", "managed_futures",
         "gold", "bond_tips", "bond_krw", "cash"]
SHORT = {
    "equity_etf": "eqETF", "equity_factor": "eqFac", "equity_sector": "eqSec",
    "equity_developed": "eqDEV", "equity_emerging": "eqEMG",
    "commodity": "comm", "managed_futures": "MF", "gold": "gold",
    "bond_tips": "tips", "bond_krw": "bond", "cash": "cash",
}
LOWVOL = {"cash"}


def daily_rule_regime(signal_px, lookback=130, buffer=60):
    out = {}
    min_start = signal_px.index.min() + pd.Timedelta(days=lookback + buffer)
    for as_of in signal_px.index:
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
        out[cls] = sum(ret[t] * (w / s) for t, w in avail.items())
    return pd.DataFrame(out)


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    routing, targets = cfg["asset_routing"], cfg["regime_targets"]

    sys.stderr.write("데이터 로딩...\n")
    universe_px, signal_px = load_all_prices(config=cfg, start=START, end=END, use_cache=True)
    present = list(universe_px.columns)
    regime = daily_rule_regime(signal_px).reindex(universe_px.index).ffill().dropna()
    cret = class_returns(universe_px, routing, present).reindex(regime.index)
    cols = [c for c in ORDER if c in cret.columns]
    cret = cret[cols]

    counts = regime.value_counts()
    print(f"<!-- 동시점 in-sample, 자산군 단위, 데이터 {START}~{END}. 비중=라이브 regime_targets. -->")
    print(f"\n레짐별 일수: " + ", ".join(f"{r} {int(counts.get(r,0))}" for r in MAIN_REGIMES) + "\n")

    for rg in MAIN_REGIMES:
        sub = cret[regime == rg].dropna(how="all")
        ndays = len(sub)
        rows = []
        for c in cols:
            r = sub[c].dropna()
            if len(r) < 20:
                continue
            m = compute_metrics(r)
            rec = recovery_duration(r)
            rows.append({
                "cls": c, "w": float(targets.get(rg, {}).get(c, 0.0)),
                "cagr": m["cagr"], "martin": m["martin"], "ulcer": m["ulcer"],
                "maxdd": m["max_drawdown"], "uw": rec["max_underwater_days"],
                "lowvol": c in LOWVOL,
            })
        df = pd.DataFrame(rows).sort_values("w", ascending=False)

        print(f"\n## {rg} ({ndays}일)\n")
        print("### 비중 + 동시점 수익\n")
        print("| 자산군 | 비중 | CAGR | Martin | Ulcer | MaxDD | 최장UW |")
        print("|---|---:|---:|---:|---:|---:|---:|")
        for _, r in df.iterrows():
            mtag = f"{r['martin']:.2f}" + ("¹" if r["lowvol"] else "")
            print(f"| {SHORT.get(r['cls'], r['cls'])} | {r['w']:.0%} | {r['cagr']:.1%} | "
                  f"{mtag} | {r['ulcer']:.2f} | {r['maxdd']:.1%} | {int(r['uw'])}d |")

        # 상관
        corr = sub[cols].corr()
        labs = [SHORT.get(c, c) for c in corr.columns]
        print("\n### 상관행렬 (동시점 일별수익)\n")
        print("| | " + " | ".join(labs) + " |")
        print("|---|" + "---:|" * len(labs))
        for i, c in enumerate(corr.index):
            vals = " | ".join(f"{corr.iloc[i,j]:.2f}" if pd.notna(corr.iloc[i,j]) else "—"
                              for j in range(len(corr.columns)))
            print(f"| **{SHORT.get(c,c)}** | {vals} |")

        if "equity_etf" in corr.columns:
            ec = corr["equity_etf"].drop("equity_etf").sort_values()
            div = ", ".join(f"{SHORT.get(k,k)} {v:+.2f}" for k, v in ec.items())
            print(f"\n주식(eqETF) 대비 상관(낮을수록 분산↑): {div}")

    print("\n---\n¹ cash는 저변동이라 Martin 분모(Ulcer) 왜곡 → CAGR·MaxDD 병행 해석.")
    print("주의: 동시점 in-sample 프록시. 소표본 레짐(Stagflation·Crisis)은 추정 불안정.")


if __name__ == "__main__":
    main()
