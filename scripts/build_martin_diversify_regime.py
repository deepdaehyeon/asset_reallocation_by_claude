"""
레짐별 Martin 1위 + 상관분산 규칙 포트폴리오 구성 (regime_targets 교체, OOS 검증).

사용자(2026-06-15): "정적 말고 레짐별로 다르게 구성해야지." 3규칙(①최고비중=Martin 1위
  ②붕괴방지=1위와 상관 최저 산입 ③나머지=Martin순×1위와의 분산도)을 *각 레짐 라벨된 날*의
  자산군 Martin·상관으로 계산해 레짐별 목표비중을 짠다. 정적([[build_martin_diversify_portfolio]])
  과 달리 regime_targets에 꽂아 현행 엔진(레짐 스위칭·vol targeting·core30)을 그대로 위에 얹음
  → 방어엔진이 동일하게 작동(정적판의 vol 부재 교란 해소).

방법:
  - 레짐 = 라이브 rule 레짐(일별 detect_regime). 자산군 수익 = asset_routing within-class 결합.
  - 레짐별 sub(그 레짐 날)에서 자산군 Martin·1위와의 상관 → build_weights(W1·W2·α·β).
  - regime_targets[rg]를 교체한 config를 full 엔진으로 run → 4지표 TRAIN/TEST를 현행과 비교.
  - OOS 정직성: TRAIN(≤2018) 레짐날로만 구성→TEST 평가. 전기간構은 lookahead(상한 참고).

한계·교란(규칙5):
  ① equity_individual 생존편향(TSLA·PLTR·NVDA 사후 승자) → '개별주 제외' 변형 동시 출력.
  ② 소표본 레짐(Stagflation·Crisis) 레짐조건 Martin·corr 불안정 → 오버핏 위험.
  ③ per-regime 비중은 엔진이 흡수하는 경향([[feedback-regime-targets-no-tuning]]) → 효과 미미 가능.
  ④ 레짐조건 일수익은 비연속 → CAGR 연율화 근사(Martin은 비율이라 비교적 robust). in-sample·프록시.
"""
from __future__ import annotations

import copy
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
from features import compute_features  # noqa: E402
from regime import detect_regime  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
from metrics import compute_metrics  # noqa: E402
from walkforward_shrink_oos import run_config, SPLIT  # noqa: E402
from compare_rule_timing_ab import START, END  # noqa: E402

MAIN_REGIMES = ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis"]
# 구성 노브 (정적판과 동일 기본값)
W1, W2, ALPHA, BETA, MIN_W = 0.30, 0.20, 1.0, 1.0, 0.005


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


def class_returns(px, routing, present, classes):
    ret = px.pct_change()
    out = {}
    for cls in classes:
        members = routing.get(cls, {})
        avail = {t: w for t, w in members.items() if t in present}
        if not avail:
            continue
        s = sum(avail.values())
        out[cls] = sum(ret[t] * (w / s) for t, w in avail.items())
    return pd.DataFrame(out)


def build_weights(martin, corr_top, top, divr):
    rest = [c for c in martin if c not in (top, divr) and not np.isnan(martin[c])]
    budget = 1.0 - W1 - W2
    scores = {}
    for c in rest:
        m = max(martin[c], 0.0)
        div = max((1.0 - corr_top[c]) / 2.0, 0.0)
        scores[c] = (m ** ALPHA) * (div ** BETA)
    s = sum(scores.values())
    w = {top: W1, divr: W2}
    for c in rest:
        w[c] = budget * (scores[c] / s) if s > 0 else 0.0
    w = {c: v for c, v in w.items() if v >= MIN_W}
    tot = sum(w.values())
    return {c: v / tot for c, v in w.items()}


def construct(cret, regime, classes, lo, hi, exclude=()):
    targets, diag = {}, {}
    usable = [c for c in classes if c not in exclude]
    sub_all = cret.loc[lo:hi]
    reg = regime.loc[lo:hi]
    for rg in MAIN_REGIMES:
        sub = sub_all[reg == rg].dropna(how="all")
        martin = {}
        for c in usable:
            r = sub[c].dropna()
            martin[c] = compute_metrics(r).get("martin", 0.0) if len(r) >= 30 else np.nan
        valid = [c for c in usable if not np.isnan(martin[c])]
        top = max(valid, key=lambda c: martin[c])
        corr_top = {}
        for c in usable:
            cc = sub[c].corr(sub[top])
            corr_top[c] = 0.0 if pd.isna(cc) else cc
        divr = min([c for c in valid if c != top], key=lambda c: corr_top[c])
        targets[rg] = build_weights(martin, corr_top, top, divr)
        diag[rg] = (top, divr, martin, corr_top, len(sub))
    return targets, diag


def make_cfg(base, regime_targets):
    cfg = copy.deepcopy(base)
    classes = list(base["regime_targets"]["Goldilocks"].keys())
    for rg, w in regime_targets.items():
        cfg["regime_targets"][rg] = {c: float(w.get(c, 0.0)) for c in classes}
    return cfg


def print_diag(tag, diag, classes):
    print(f"\n{'='*96}\n  [{tag}] 레짐별 구성 (1위 / 분산슬롯 / 주요비중)\n{'='*96}")
    for rg in MAIN_REGIMES:
        top, divr, martin, corr_top, n = diag[rg]
        print(f"\n  · {rg} ({n}일)  ①1위={top}(M{martin[top]:.2f})  ②분산={divr}(corr{corr_top[divr]:+.2f})")


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    routing = base["asset_routing"]
    classes = list(base["regime_targets"]["Goldilocks"].keys())

    print(f"데이터 로딩 [{START} ~ {END}], split={SPLIT}...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)
    present = list(universe_px.columns)

    print("일별 rule 레짐 산출 중...")
    regime = daily_rule_regime(signal_px).reindex(universe_px.index).ffill().dropna()
    cret = class_returns(universe_px, routing, present, classes).reindex(regime.index)

    # 구성 4종: (전기간/TRAIN) × (전체포함/개별주제외)
    full_all, diag_full = construct(cret, regime, classes, START, END)
    train_all, _ = construct(cret, regime, classes, START, "2018-12-31")
    train_noind, diag_ni = construct(cret, regime, classes, START, "2018-12-31",
                                     exclude=("equity_individual",))

    print_diag("전기간·전체포함 (초기 포트폴리오)", diag_full, classes)

    # 레짐별 비중표(전기간·전체포함)
    print(f"\n{'='*96}\n  레짐별 목표비중 (전기간·전체포함, %)\n{'='*96}")
    hdr = f"  {'자산군':>18}" + "".join(f"{rg[:6]:>9}" for rg in MAIN_REGIMES)
    print(hdr); print("  " + "─" * (len(hdr)))
    for c in classes:
        row = f"  {c:>18}"
        for rg in MAIN_REGIMES:
            row += f"{full_all[rg].get(c,0)*100:>9.1f}"
        print(row)

    print("\n실행 중 (full 엔진, 각 config 2010~2025 1회)...")
    cur_tr, cur_te = run_config(base, universe_px, signal_px, fred_history)
    fa_tr, fa_te = run_config(make_cfg(base, full_all), universe_px, signal_px, fred_history)
    ta_tr, ta_te = run_config(make_cfg(base, train_all), universe_px, signal_px, fred_history)
    ni_tr, ni_te = run_config(make_cfg(base, train_noind), universe_px, signal_px, fred_history)

    def table(title, items):
        print(f"\n  {title}")
        print(f"  {'전략':>30}{'Martin':>9}{'CAGR':>9}{'Ulcer':>8}{'최장UW':>8}{'롤3y최악':>10}{'MaxDD':>9}{'tx':>8}")
        print("  " + "─" * 91)
        base_m = items[0][1]["Martin"]
        for label, r in items:
            d = r["Martin"] - base_m
            mark = "" if label.startswith("현행") else f"  Δ{d:+.2f}"
            print(f"  {label:>30}{r['Martin']:>9.2f}{r['CAGR']:>9.1%}{r['Ulcer']:>8.2f}"
                  f"{int(r['uw_max']):>8}{r['r3w']:>10.1%}{r['MaxDD']:>9.1%}{r['tx']:>8.2%}{mark}")

    print(f"\n{'='*96}\n  백테스트 (full 엔진: 레짐스위칭+vol+core, drift)\n{'='*96}")
    table("학습창 TRAIN 2010-01~2018-12 (in-sample)",
          [("현행", cur_tr), ("신규(전기간構·전체)", fa_tr), ("신규(TRAIN構·전체)", ta_tr),
           ("신규(TRAIN構·개별주제외)", ni_tr)])
    table(f"검증창 TEST {SPLIT[:7]}~{END} (OOS) — TRAIN構가 진짜 OOS",
          [("현행", cur_te), ("신규(전기간構·전체·lookahead)", fa_te), ("신규(TRAIN構·전체·OOS)", ta_te),
           ("신규(TRAIN構·개별주제외·OOS)", ni_te)])

    print("\n  판정(규칙4): TEST에서 TRAIN構가 현행 대비 Martin·Ulcer·회복·롤3y 동반개선이면 채택검토.")
    print("  주의: 개별주 생존편향·소표본레짐 불안정·per-regime 엔진흡수·in-sample구성. 라이브 반영은 확인 후.")


if __name__ == "__main__":
    main()
