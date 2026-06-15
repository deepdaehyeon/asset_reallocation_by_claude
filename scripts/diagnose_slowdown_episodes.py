"""
Goldilocks↔Slowdown 분리 정밀화 1단계 진단 — 짧은 Slowdown은 진짜 낙폭 전조인가 노이즈인가.

질문(2026-06-15, 사용자): 발생확률상 Goldi 59%+Slow 25%=84.6%가 핵심 구간. Slowdown은
  진입 200회·평균 4.7일로 잘게 깜빡임(detect_regime이 성장모멘텀 약세신호 2개로 즉시 발동).
  이 짧은 Slowdown들이 (a) 진짜 둔화(뒤에 주식 낙폭 따라옴) → 더 빨리·깨끗하게 잡는 게 레버,
  (b) 분류 노이즈(며칠 만에 Goldi 복귀, 낙폭 없음) → 헛스윙 억제가 레버. 처방이 정반대라 먼저 측정.

방법:
  - 레짐 = 일별 rule(detect_regime). Slowdown 연속구간(에피소드) 추출.
  - 위험자산 = equity_etf 클래스(라우팅 결합) 가격지수.
  - 각 에피소드 시작일의 forward 최대낙폭(향후 H거래일 내 보유시 최악 낙폭) 측정.
    forward 수익률 단독 금지(V자 편향) → forward-MDD가 "둔화가 낙폭 전조였나"의 정답 지표.
  - 에피소드를 지속일(1-2/3-5/6-10/>10d)로 버킷, 버킷별 forward-MDD 중앙값·"실낙폭(<-5%)" 비율.
  - 기준선: 무작위 Goldilocks 날의 forward-MDD 분포와 비교 → Slowdown 라벨이 정보가치 있나.

한계: rule raw 레짐(라이브 필터·평활 미적용). equity_etf만 위험프록시(전체 포트와 다름).
  단일 경로·in-sample. forward-MDD는 horizon 선택에 민감(여러 H 동봉).
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
from regime_class_correlation import daily_rule_regime, class_returns, START, END  # noqa: E402

HORIZONS = [21, 42, 63]  # 거래일 (~1·2·3개월)
REAL_DD = -0.05  # 이보다 깊으면 '진짜 낙폭'


def forward_mdd(price, idx_pos, H):
    """idx_pos에서 향후 H거래일 보유 시 최악(최저) 누적수익 = forward 최대낙폭."""
    seg = price[idx_pos: idx_pos + H + 1]
    if len(seg) < 2:
        return np.nan
    p0 = seg[0]
    return float(seg[1:].min() / p0 - 1.0)


def episodes(reg, target):
    """target 레짐 연속구간 → [(start_ts, length)]."""
    out = []
    vals = reg.values
    i = 0
    while i < len(vals):
        if vals[i] == target:
            j = i
            while j < len(vals) and vals[j] == target:
                j += 1
            out.append((reg.index[i], j - i))
            i = j
        else:
            i += 1
    return out


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    routing = cfg["asset_routing"]

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=cfg, start=START, end=END, use_cache=True)
    present = list(universe_px.columns)

    print("일별 레짐 분류 중...")
    reg = daily_rule_regime(signal_px)

    # 위험자산 가격지수 (equity_etf 클래스)
    cret = class_returns(universe_px, routing, present)["equity_etf"].reindex(reg.index).fillna(0)
    eq_price = (1 + cret).cumprod()
    parr = eq_price.values
    pos = {ts: k for k, ts in enumerate(eq_price.index)}

    def fmdd_at(ts, H):
        return forward_mdd(parr, pos[ts], H)

    # Slowdown 에피소드
    sl = episodes(reg, "Slowdown")
    print(f"\nSlowdown 에피소드 {len(sl)}개 (총 {sum(d for _,d in sl)}일)")

    # 버킷
    buckets = [("1-2d", 1, 2), ("3-5d", 3, 5), ("6-10d", 6, 10), (">10d", 11, 10**9)]
    print(f"\n{'='*88}")
    print("  Slowdown 에피소드: 지속일 버킷별 forward 최대낙폭(시작일 기준, equity_etf)")
    print(f"{'='*88}")
    hdr = f"  {'버킷':>8}{'에피소드수':>9}{'일수합':>8}"
    for H in HORIZONS:
        hdr += f"{'mdd'+str(H)+'d중앙':>13}{'<-5%비율':>10}"
    print(hdr); print("  " + "─" * (len(hdr)))
    for name, lo, hi in buckets:
        eps = [(ts, d) for ts, d in sl if lo <= d <= hi]
        if not eps:
            continue
        row = f"  {name:>8}{len(eps):>9}{sum(d for _,d in eps):>8}"
        for H in HORIZONS:
            mdds = [fmdd_at(ts, H) for ts, _ in eps]
            mdds = [m for m in mdds if not np.isnan(m)]
            med = np.median(mdds) if mdds else np.nan
            frac = np.mean([m < REAL_DD for m in mdds]) if mdds else np.nan
            row += f"{med:>12.1%}{frac:>10.0%}"
        print(row)

    # 기준선: Goldilocks 날 전체 vs Slowdown 시작일 전체
    print(f"\n{'='*88}")
    print("  기준선 비교 — forward 최대낙폭 중앙값 (라벨이 낙폭 정보가치 있나)")
    print(f"{'='*88}")
    goldi_days = reg.index[reg == "Goldilocks"]
    slow_starts = [ts for ts, _ in sl]
    short_starts = [ts for ts, d in sl if d <= 2]
    groups = [("Goldilocks 전체날", goldi_days),
              ("Slowdown 시작일 전체", pd.Index(slow_starts)),
              ("짧은 Slowdown(≤2d) 시작일", pd.Index(short_starts))]
    hdr2 = f"  {'그룹':>22}{'표본':>7}"
    for H in HORIZONS:
        hdr2 += f"{'mdd'+str(H)+'d중앙':>13}{'<-5%비율':>10}"
    print(hdr2); print("  " + "─" * (len(hdr2)))
    for name, days in groups:
        row = f"  {name:>22}{len(days):>7}"
        for H in HORIZONS:
            mdds = [fmdd_at(ts, H) for ts in days if ts in pos]
            mdds = [m for m in mdds if not np.isnan(m)]
            med = np.median(mdds) if mdds else np.nan
            frac = np.mean([m < REAL_DD for m in mdds]) if mdds else np.nan
            row += f"{med:>12.1%}{frac:>10.0%}"
        print(row)

    print("\n  판정: 짧은 Slowdown의 forward-MDD가 Goldilocks 기준선과 비슷하면 → 노이즈(헛스윙)")
    print("  → 억제가 레버. 기준선보다 뚜렷이 깊으면 → 진짜 전조 → 더 빨리/깨끗이 잡는 게 레버.")
    print("  주의: rule raw·equity_etf 프록시·단일경로·in-sample. forward-MDD는 V자편향 회피용.")


if __name__ == "__main__":
    main()
