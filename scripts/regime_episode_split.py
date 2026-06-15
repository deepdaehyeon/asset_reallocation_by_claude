"""
레짐별 자산 성격의 에피소드 안정성 진단 — 동시점 수익을 TRAIN(≤2018)/TEST(≥2019)로 분할.
스태그플레이션에서 방어자산이 2010년대↔2022 부호 역전한 게 비중조정 OOS 실패의 원인이었다
(stagflation_episode_split.py). Slowdown·Crisis도 같은 이질성이 있는지 일반화해 확인.

사용: python regime_episode_split.py [Regime1 Regime2 ...]   (기본 Slowdown Crisis)
방법: 자산군 단위 asset_routing 결합. 해당 레짐 라벨일만 추출 후 기간 분할 + 연속구간 표기.
한계: 소표본 레짐은 분할 시 더 작음. 프록시·in-sample.
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
KEY = ["equity_etf", "equity_factor", "commodity", "managed_futures",
       "gold", "bond_tips", "bond_krw", "cash"]


def episode_groups(days):
    groups, cur = [], [days[0]]
    for prev, nxt in zip(days[:-1], days[1:]):
        if (nxt - prev).days > 30:
            groups.append((cur[0], cur[-1], len(cur)))
            cur = []
        cur.append(nxt)
    groups.append((cur[0], cur[-1], len(cur)))
    return groups


def main():
    regimes = sys.argv[1:] or ["Slowdown", "Crisis"]
    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    routing = cfg["asset_routing"]

    sys.stderr.write("데이터 로딩...\n")
    universe_px, signal_px = load_all_prices(config=cfg, start=START, end=END, use_cache=True)
    present = list(universe_px.columns)
    regime = daily_rule_regime(signal_px).reindex(universe_px.index).ffill().dropna()
    cret = class_returns(universe_px, routing, present).reindex(regime.index)

    for rg in regimes:
        mask = regime == rg
        days = regime.index[mask]
        print(f"\n{'='*70}\n  [{rg}] 총 {int(mask.sum())}일 — 에피소드 안정성\n{'='*70}")

        print("[연속 구간 — 갭 30일+ 분리]")
        for lo, hi, n in episode_groups(days):
            seg = "TRAIN" if hi < pd.Timestamp(SPLIT) else ("TEST" if lo >= pd.Timestamp(SPLIT) else "걸침")
            print(f"  {lo.date()} ~ {hi.date()}  ({n}일)  [{seg}]")

        def block(title, sel):
            sub = cret[sel]
            print(f"\n[{title}]  ({int(sel.sum())}일)")
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

        block("전체 2010~2025", mask)
        block("학습창 TRAIN ≤2018", mask & (regime.index < pd.Timestamp(SPLIT)))
        block("검증창 TEST ≥2019", mask & (regime.index >= pd.Timestamp(SPLIT)))

    print("\n  핵심: 자산 CAGR/Martin 부호가 TRAIN↔TEST 역전이면 에피소드 이질(고정비중 OOS 취약).")
    print("  방어자산이 양 시대 일관 양수면 그 레짐 비중은 상대적으로 안정적. 프록시·in-sample.")


if __name__ == "__main__":
    main()
