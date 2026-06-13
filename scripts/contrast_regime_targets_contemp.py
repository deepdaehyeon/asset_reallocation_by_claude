"""
#3 데이터 최적 구성 vs 현재 regime_targets 대조 (동시점 기준, 자산군 단위).

질문(2026-06-13): #2에서 forward를 빼고 동시점으로 종목을 재순위했다. 이제 그 결과를
  regime_targets가 실제로 제어하는 단위(=자산군 class)로 올려, "데이터가 그 레짐에서 선호하는
  방향"과 "현재 비중"을 대조한다. 큰 방향성 어긋남(현재 비중확대인데 데이터 하위 / 현재 0인데
  데이터 상위)만 플래그한다.

설계 원칙:
  - [[feedback-regime-targets-no-tuning]]: per-regime 미세튜닝은 노이즈. 따라서 정밀한 "최적 비중
    벡터"를 산출하지 않는다(그게 바로 경고된 튜닝). 대신 *방향성 정렬*만 판정:
    ALIGNED(정렬) / OVER_WEAK(과대-데이터약함) / UNDER_STRONG(과소-데이터강함).
  - 자산군 수익 = asset_routing의 within-class 비중으로 그 자산군 종목들을 결합한 실현수익
    (= 그 자산군을 현재 라우팅대로 든 동시점 체감). 레짐 라벨된 날만.
  - cash는 Martin 분모왜곡(변동성~0)이라 순위 제외, 앵커로만 표기.
  - equity_individual은 사용자 합의로 제외(개별주식). commodity_krw는 합성브릿지 제외.

한계: 자산군 단독 동시점(상관·분산 미반영). 프록시·in-sample. 소표본 레짐(Stagflation 163·
  Crisis 179일은 2020·2022 집중). 방향성 진단이지 비중 처방 아님.
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
from metrics import compute_metrics  # noqa: E402

START = "2010-01-01"
END = "2025-04-30"
MAIN_REGIMES = ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis"]
EXCLUDE_CLASSES = {"equity_individual", "commodity_krw", "cash_usd"}
CASH_CLASS = "cash"  # Martin 분모왜곡 → 순위 제외, 앵커 표기


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
    """asset_routing within-class 비중으로 자산군별 일별 실현수익 시리즈 구성."""
    ret = px.pct_change()
    out = {}
    for cls, members in routing.items():
        if cls in EXCLUDE_CLASSES:
            continue
        avail = {t: w for t, w in members.items() if t in present}
        if not avail:
            continue
        s = sum(avail.values())
        wts = {t: w / s for t, w in avail.items()}  # 결측 종목 제외 후 재정규화
        cr = sum(ret[t] * w for t, w in wts.items())
        out[cls] = cr
    return pd.DataFrame(out)


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    universe = cfg["universe"]
    routing = cfg["asset_routing"]
    targets = cfg["regime_targets"]

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=cfg, start=START, end=END, use_cache=True)
    present = list(universe_px.columns)

    print("일별 rule 레짐 산출 중...")
    regime = daily_rule_regime(signal_px).reindex(universe_px.index).ffill().dropna()

    cret = class_returns(universe_px, routing, present).reindex(regime.index)

    print(f"\n{'='*100}")
    print("  #3 데이터 최적 *방향* vs 현재 비중 — 자산군 단위, 동시점 실현수익 (forward 없음)")
    print(f"{'='*100}")
    print("  판정: ALIGNED=정렬 | OVER_WEAK=비중확대인데 데이터 하위(축소검토) | "
          "UNDER_STRONG=0인데 데이터 상위(추가검토)")

    flags = []
    for rg in MAIN_REGIMES:
        mask = regime == rg
        ndays = int(mask.sum())
        rows = []
        for cls in cret.columns:
            r = cret[cls][mask].dropna()
            if len(r) < 20:
                continue
            m = compute_metrics(r)
            if not m:
                continue
            rows.append({"class": cls, "cagr": m["cagr"], "martin": m["martin"],
                         "maxdd": m["max_drawdown"], "w": float(targets[rg].get(cls, 0.0))})
        df = pd.DataFrame(rows)
        rankable = df[df["class"] != CASH_CLASS].sort_values("martin", ascending=False).reset_index(drop=True)
        nrank = len(rankable)
        half = nrank / 2

        print(f"\n  [{rg}]  ({ndays}일)  — 자산군 Martin 내림차순 (cash는 앵커, 순위제외)")
        print(f"  {'순위':>4}{'자산군':>18}{'현재비중':>9}{'CAGR':>9}{'Martin':>8}{'MaxDD':>9}  판정")
        print("  " + "─" * 88)
        for i, r in rankable.iterrows():
            rank = i + 1
            w = r["w"]
            if w >= 0.10 and rank > half:
                verdict = "OVER_WEAK ⚠"
                flags.append((rg, r["class"], w, rank, nrank, r["martin"], r["cagr"]))
            elif w <= 0.001 and rank <= 3:
                verdict = "UNDER_STRONG ⚠"
                flags.append((rg, r["class"], w, rank, nrank, r["martin"], r["cagr"]))
            else:
                verdict = "ALIGNED"
            print(f"  {rank:>4}{r['class']:>18}{w:>8.0%}{r['cagr']:>9.1%}"
                  f"{r['martin']:>8.2f}{r['maxdd']:>9.1%}  {verdict}")
        cash_row = df[df["class"] == CASH_CLASS]
        if len(cash_row):
            cr = cash_row.iloc[0]
            print(f"  {'—':>4}{'cash(앵커)':>18}{cr['w']:>8.0%}{cr['cagr']:>9.1%}"
                  f"{'n/a':>8}{cr['maxdd']:>9.1%}  (분모왜곡, 순위제외)")

    print(f"\n{'='*100}")
    print("  ⚠ 큰 방향성 어긋남 요약 (미세조정 아닌 방향성만):")
    print(f"{'='*100}")
    if not flags:
        print("  (없음) — 현재 비중이 동시점 데이터 방향과 모두 정렬.")
    else:
        for rg, cls, w, rank, n, mart, cagr in flags:
            kind = "축소검토" if w >= 0.10 else "추가검토"
            print(f"  [{rg}] {cls}: 현재 {w:.0%}, 동시점 {rank}/{n}위 "
                  f"(Martin {mart:.2f}·CAGR {cagr:.1%}) → {kind}")

    print("\n  주의: 자산군 단독 동시점(상관·분산 미반영). 프록시·in-sample. "
          "방향성 진단이지 비중 처방 아님([[feedback-regime-targets-no-tuning]]).")


if __name__ == "__main__":
    main()
