"""
스태그플레이션 하위국면 진단 — 실질금리가 2010년대 vs 2022 에피소드를 가르는 축인가?

배경: stagflation_episode_split.py에서 스태그 방어자산이 시대마다 부호 역전(2010년대
  채권·금·tips 영웅 → 2022 전부 마이너스, cash만 강건)을 확인. 외부 리뷰는 "실질금리·
  유동성이 2022를 설명하는 열쇠"라 진단. 이 스크립트는 *달력*(2010s/2022)이 아니라
  *실질금리 부호/추세*로 스태그일을 갈라, 두 하위그룹의 자산성격이 실제로 반대인지 확인한다.
  반대로 갈리면 → 실질금리가 스태그 하위국면 분기의 올바른 축(고정비중 분리 가능).

방법: 스태그 라벨일을 ① 실질금리 수준(DFII10 ≷ 0), ② 3개월 변화(상승/하락)로 분할 후
  자산군 동시점 CAGR/Martin/MaxDD를 비교. 두 분할의 분리력을 대조.
한계: 소표본(스태그 163일을 또 분할). 동시점·프록시·in-sample. 진단 전용(엔진 미변경).
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
from fetcher import fetch_fred_history  # noqa: E402
from regime_portfolio_review_summary import (  # noqa: E402
    daily_rule_regime, class_returns, SHORT, START, END,
)

KEY = ["equity_etf", "equity_factor", "commodity", "managed_futures",
       "gold", "bond_tips", "bond_krw", "cash"]


def episode_groups(days):
    if len(days) == 0:
        return []
    groups, cur = [], [days[0]]
    for prev, nxt in zip(days[:-1], days[1:]):
        if (nxt - prev).days > 30:
            groups.append((cur[0], cur[-1], len(cur)))
            cur = []
        cur.append(nxt)
    groups.append((cur[0], cur[-1], len(cur)))
    return groups


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    routing = cfg["asset_routing"]

    sys.stderr.write("데이터 로딩...\n")
    universe_px, signal_px = load_all_prices(config=cfg, start=START, end=END, use_cache=True)
    present = list(universe_px.columns)
    regime = daily_rule_regime(signal_px).reindex(universe_px.index).ffill().dropna()
    cret = class_returns(universe_px, routing, present).reindex(regime.index)

    fred = fetch_fred_history(START, END)
    if "real_rate_10y" not in fred.columns:
        sys.stderr.write("실질금리(DFII10) 미수신 — FRED_API_KEY 확인 필요. 중단.\n")
        return
    rr = fred["real_rate_10y"].reindex(regime.index).ffill()
    rr_chg = fred.get("real_rate_chg_3m", pd.Series(dtype=float)).reindex(regime.index).ffill()

    stag = regime == "Stagflation"
    days = regime.index[stag]
    print(f"\n{'='*72}\n  [Stagflation] 총 {int(stag.sum())}일 — 실질금리 분기 진단\n{'='*72}")

    print("\n[실질금리(DFII10) 분포 — 스태그 라벨일]")
    rr_s = rr[stag].dropna()
    print(f"  관측 {len(rr_s)}일 | min {rr_s.min():+.2f}% | median {rr_s.median():+.2f}% | max {rr_s.max():+.2f}%")
    print(f"  음(<0): {int((rr_s < 0).sum())}일 | 양(≥0): {int((rr_s >= 0).sum())}일")

    def block(title, sel):
        sub = cret[sel]
        n = int(sel.sum())
        print(f"\n[{title}]  ({n}일)")
        if n < 15:
            print("  표본부족(<15일) — 생략")
            return
        # 구간 표기
        sub_days = regime.index[sel]
        segs = episode_groups(sub_days)
        seg_str = ", ".join(f"{lo.date()}~{hi.date()}({n}d)" for lo, hi, n in segs[:6])
        print(f"  구간: {seg_str}{' ...' if len(segs) > 6 else ''}")
        print(f"  {'자산군':>8}{'CAGR':>9}{'Martin':>9}{'MaxDD':>9}")
        for c in KEY:
            if c not in sub.columns:
                continue
            r = sub[c].dropna()
            if len(r) < 15:
                print(f"  {SHORT.get(c,c):>8}{'표본부족':>9}")
                continue
            m = compute_metrics(r)
            print(f"  {SHORT.get(c,c):>8}{m['cagr']:>8.1%}{m['martin']:>9.2f}{m['max_drawdown']:>8.1%}")

    print(f"\n{'─'*72}\n  분할 A: 실질금리 수준 (DFII10 ≷ 0)\n{'─'*72}")
    block("A1 실질금리 음 (<0) — 디스인플레/완화형 가설", stag & (rr < 0))
    block("A2 실질금리 양 (≥0) — 인플레파이팅/긴축형 가설", stag & (rr >= 0))

    print(f"\n{'─'*72}\n  분할 B: 실질금리 3개월 변화 (상승 vs 하락)\n{'─'*72}")
    block("B1 실질금리 하락 (Δ3m<0)", stag & (rr_chg < 0))
    block("B2 실질금리 상승 (Δ3m≥0) — 긴축 가속 가설", stag & (rr_chg >= 0))

    print("\n  핵심: 두 하위그룹에서 방어자산(bond·gold·tips) 부호가 갈리면(한쪽 양/한쪽 음)")
    print("        → 실질금리가 스태그 하위국면 분기축. 양쪽 같으면 분기 무의미.")
    print("  한계: 소표본·동시점·프록시·in-sample. 진단 전용(엔진 미변경).")


if __name__ == "__main__":
    main()
