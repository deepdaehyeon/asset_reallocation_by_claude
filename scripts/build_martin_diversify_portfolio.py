"""
Martin 1위 + 상관 분산 규칙으로 초기 포트폴리오 구성 (비교용 검증, 2026-06-15).

사용자 규칙(3):
  1. 1순위 비중 = Martin 1위 자산군.
  2. 붕괴 방지: 1순위와 상관관계가 가장 낮은(분산) 자산군을 산입.
  3. 남은 비율은 Martin 순 × 1순위와의 분산도(상관↓일수록↑). 1순위와 같은 방향(상관↑)은 저비중.

방법:
  - 자산군 13종 각각을 100% 정적 보유로 같은 엔진에 돌려(make_static_config, vol/core/cap/hmm off)
    수익 시계열을 얻는다 → 통화·수익 규약이 실제 백테스트와 동일.
  - 각 자산군 Martin(=CAGR/Ulcer)·1위와의 상관계수 계산.
  - 3규칙을 sequential하게 비중화: W1(1위) + W2(최저상관 분산) + 나머지(budget × score),
    score = max(Martin,0)^α × ((1-corr)/2)^β.
  - 결과 비중을 다시 정적 백테스트해 4지표(Martin·Ulcer·회복·롤3y)를 TRAIN/TEST로 현행(C)과 비교.

한계: ① 전기간 Martin으로 짜면 TEST는 lookahead(낙관) — TRAIN-only 재구성 OOS도 함께 출력.
  ② 짧은 이력 자산군(equity_factor·bond_tips·managed_futures: 2019~2021 시작) Martin·corr 불안정.
  ③ 정적·USD단일·라이브 마찰 미반영. 라이브 반영은 사용자 확인 후.
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
sys.path.insert(0, str(ROOT / "scripts"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from data import load_all_prices  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
from metrics import compute_metrics  # noqa: E402
from walkforward_shrink_oos import build_engine, slice_metrics, run_config, SPLIT  # noqa: E402
from ablation_regime_stack import make_static_config  # noqa: E402
from compare_rule_timing_ab import START, END  # noqa: E402

# ── 구성 파라미터 (튜닝 노브) ─────────────────────────────────────────────────
W1 = 0.30      # 1순위(Martin 1위) 앵커 비중
W2 = 0.20      # 분산 슬롯(1위와 최저상관) 비중
ALPHA = 1.0    # Martin 지수 (규칙1·3 강도)
BETA = 1.0     # 분산도 지수 (규칙2·3 상관 억제 강도)
MIN_W = 0.005  # 이보다 작은 비중은 0으로(정리)


def class_return_series(base, universe_px, signal_px, fred_history, classes):
    """각 자산군을 100% 정적 보유로 돌려 수익 시계열(자산군별 column) 반환."""
    series = {}
    for c in classes:
        cfg = make_static_config(base, {c: 1.0}, vol_on=False, core_on=False)
        cfg["class_max_weight"] = {}  # 단일 자산군 100% — 상한 무력화
        res = build_engine(cfg, universe_px, signal_px, fred_history).run()
        series[c] = res["returns"]
    return pd.DataFrame(series)


def martin_of(returns, lo, hi):
    r = returns.loc[lo:hi].dropna()
    if len(r) < 60:
        return np.nan
    return compute_metrics(r).get("martin", 0.0)


def build_weights(martin, corr_top, top, divr):
    """3규칙 → 비중 dict. top=Martin1위, divr=top과 최저상관."""
    rest = [c for c in martin if c not in (top, divr) and not np.isnan(martin[c])]
    budget = 1.0 - W1 - W2
    scores = {}
    for c in rest:
        m = max(martin[c], 0.0)
        div = max((1.0 - corr_top[c]) / 2.0, 0.0)  # 0(완전동행)~1(완전역행)
        scores[c] = (m ** ALPHA) * (div ** BETA)
    s = sum(scores.values())
    w = {top: W1, divr: W2}
    for c in rest:
        w[c] = budget * (scores[c] / s) if s > 0 else 0.0
    # 미세비중 정리 + 정규화
    w = {c: v for c, v in w.items() if v >= MIN_W}
    tot = sum(w.values())
    return {c: v / tot for c, v in w.items()}


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    classes = list(base["regime_targets"]["Goldilocks"].keys())

    print(f"데이터 로딩 [{START} ~ {END}], split={SPLIT}...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    print("자산군별 단독 보유 수익 산출 중 (13종)...")
    rets = class_return_series(base, universe_px, signal_px, fred_history, classes)

    def construct(lo, hi, tag):
        martin = {c: martin_of(rets[c], lo, hi) for c in classes}
        sub = rets.loc[lo:hi].dropna(how="all")
        valid = [c for c in classes if not np.isnan(martin[c])]
        top = max(valid, key=lambda c: martin[c])
        corr_top = {c: sub[c].corr(sub[top]) for c in classes}
        corr_top = {c: (0.0 if pd.isna(v) else v) for c, v in corr_top.items()}
        divr = min([c for c in valid if c != top], key=lambda c: corr_top[c])
        w = build_weights(martin, corr_top, top, divr)

        print(f"\n{'='*92}")
        print(f"  [{tag}] 자산군 진단 ({lo[:7]}~{hi[:7]}) — 1위={top}, 분산슬롯={divr}")
        print(f"{'='*92}")
        print(f"  {'자산군':>18}{'Martin':>9}{'corr(1위)':>11}{'분산도':>9}{'→비중':>9}")
        print("  " + "─" * 58)
        for c in sorted(classes, key=lambda x: -(w.get(x, 0))):
            m = martin[c]
            mstr = "  n/a" if np.isnan(m) else f"{m:>9.2f}"
            div = (1 - corr_top[c]) / 2
            tags = ("①1위" if c == top else "②분산" if c == divr else "")
            print(f"  {c:>18}{mstr}{corr_top[c]:>11.2f}{div:>9.2f}{w.get(c,0):>9.1%}  {tags}")
        print(f"  {'합계':>18}{'':>29}{sum(w.values()):>9.1%}")
        return w

    w_full = construct(START, END, "전기간(초기 포트폴리오)")
    w_train = construct(START, "2018-12-31", "TRAIN전용(OOS용)")

    # ── 백테스트 비교: 정적 vs 현행 풀시스템 ─────────────────────────────────
    print(f"\n{'='*92}\n  백테스트 비교 (정적 보유 vs 현행 레짐 시스템, drift)\n{'='*92}")

    def static_metrics(w):
        cfg = make_static_config(base, w, vol_on=False, core_on=False)
        cfg["class_max_weight"] = {}
        return run_config(cfg, universe_px, signal_px, fred_history)

    full_tr, full_te = static_metrics(w_full)
    train_tr, train_te = static_metrics(w_train)
    cur_tr, cur_te = run_config(base, universe_px, signal_px, fred_history)

    def row(label, r):
        print(f"  {label:>26}{r['Martin']:>9.2f}{r['CAGR']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{int(r['uw_max']):>8}{r['r3w']:>10.1%}{r['MaxDD']:>9.1%}{r['tx']:>8.2%}")

    def table(title, items):
        print(f"\n  {title}")
        print(f"  {'전략':>26}{'Martin':>9}{'CAGR':>9}{'Ulcer':>8}{'최장UW':>8}{'롤3y최악':>10}{'MaxDD':>9}{'tx':>8}")
        print("  " + "─" * 87)
        for label, r in items:
            row(label, r)

    table("학습창 TRAIN 2010-01~2018-12 (in-sample)",
          [("현행 풀시스템", cur_tr), ("신규(전기간構)", full_tr), ("신규(TRAIN構)", train_tr)])
    table(f"검증창 TEST {SPLIT[:7]}~{END} (OOS) — 신규(TRAIN構)가 진짜 OOS",
          [("현행 풀시스템", cur_te), ("신규(전기간構·lookahead)", full_te), ("신규(TRAIN構·OOS)", train_te)])

    print("\n  판정(규칙4): TEST에서 신규(TRAIN構)의 Martin·Ulcer·회복·롤3y가 현행을 동반 개선하면")
    print("              구성 규칙이 진짜 가치. '전기간構'는 lookahead라 낙관(상한 참고용).")
    print("  주의: 정적·USD단일·짧은이력 자산군 불안정·라이브 마찰 미반영. 라이브 반영은 확인 후.")


if __name__ == "__main__":
    main()
