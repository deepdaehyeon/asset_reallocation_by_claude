"""
스태그플레이션 자산 성격의 에피소드 불안정 진단 — 동시점 수익을 TRAIN(≤2018)/TEST(≥2019)로 분할.

질문(2026-06-15): C2(commodity 18→10→bond)가 OOS에서 깨진 이유 가설 = 스태그플레이션은
  에피소드마다 자산 성격이 뒤집힌다(2010년대: 원자재 약세·채권 양호 / 2022: 원자재 급등·채권 폭락).
  스태그 라벨된 날의 자산군 동시점 CAGR·Martin을 학습창/검증창으로 쪼개 직접 확인.
방법: 자산군 단위, asset_routing 결합. Stagflation 라벨일만 추출 후 기간 분할. 연속 스태그 구간도 표기.
한계: 소표본(전체 163일, 분할 시 더 작음). 프록시·in-sample.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "scripts"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

import yaml  # noqa: E402
from data import load_all_prices  # noqa: E402
from metrics import compute_metrics  # noqa: E402
from regime_portfolio_review_summary import (  # noqa: E402
    daily_rule_regime, class_returns, SHORT, START, END,
)

SPLIT = "2019-01-01"
KEY = ["commodity", "bond_krw", "gold", "bond_tips", "cash", "managed_futures", "equity_etf"]


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    routing = cfg["asset_routing"]

    sys.stderr.write("데이터 로딩...\n")
    universe_px, signal_px = load_all_prices(config=cfg, start=START, end=END, use_cache=True)
    present = list(universe_px.columns)
    regime = daily_rule_regime(signal_px).reindex(universe_px.index).ffill().dropna()
    cret = class_returns(universe_px, routing, present).reindex(regime.index)

    mask = regime == "Stagflation"
    days = regime.index[mask]
    print(f"\n스태그플레이션 총 {int(mask.sum())}일")

    # 연속 구간(에피소드) 표기
    print("\n[연속 스태그 구간 — 갭 30일 이상이면 분리]")
    groups, cur = [], [days[0]]
    for prev, nxt in zip(days[:-1], days[1:]):
        if (nxt - prev).days > 30:
            groups.append((cur[0], cur[-1], len(cur)))
            cur = []
        cur.append(nxt)
    groups.append((cur[0], cur[-1], len(cur)))
    for lo, hi, n in groups:
        seg = "TRAIN" if hi < pd.Timestamp(SPLIT) else ("TEST" if lo >= pd.Timestamp(SPLIT) else "걸침")
        print(f"  {lo.date()} ~ {hi.date()}  ({n}일)  [{seg}]")

    def block(title, sel):
        sub = cret[sel]
        n = int(sel.sum())
        print(f"\n[{title}]  ({n}일)")
        print(f"  {'자산군':>8}{'CAGR':>9}{'Martin':>9}{'MaxDD':>9}")
        rows = []
        for c in KEY:
            if c not in sub.columns:
                continue
            r = sub[c].dropna()
            if len(r) < 15:
                rows.append((c, None))
                continue
            m = compute_metrics(r)
            rows.append((c, m))
        for c, m in rows:
            if m is None:
                print(f"  {SHORT.get(c,c):>8}{'표본부족':>9}")
            else:
                print(f"  {SHORT.get(c,c):>8}{m['cagr']:>8.1%}{m['martin']:>9.2f}{m['max_drawdown']:>8.1%}")

    block("전체 2010~2025", mask)
    block("학습창 TRAIN ≤2018", mask & (regime.index < pd.Timestamp(SPLIT)))
    block("검증창 TEST ≥2019", mask & (regime.index >= pd.Timestamp(SPLIT)))

    print("\n  핵심: commodity·bond의 CAGR 부호가 TRAIN↔TEST에서 뒤집히면 = 에피소드 불안정.")
    print("  → 고정 비중 미세조정이 OOS에서 깨지는 구조적 이유(소표본·성격 변동). 프록시·in-sample.")


if __name__ == "__main__":
    main()
