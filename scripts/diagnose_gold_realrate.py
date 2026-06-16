"""
금 상승 트리거 진단 — 실질금리 방향이 레짐과 무관하게 금 수익을 가르는가.

질문(2026-06-16, 사용자): 자산마다 상승 트리거(시장환경)가 있고 그게 레짐 칸막이를
  가로지른다. 금은 실질금리↓(금리인하·유동성)일 때 오르는데 그게 골디락스일 수도 스태그일
  수도 있다. 레짐 분류기(detect_regime)는 성장·인플레·변동성만 보고 실질금리(DFII10)는 엔진에
  전혀 안 들어감(asset_trigger_map_2026-06-16.md). 그럼 실질금리 3개월 변화 부호로 금의 향후
  수익을 가르면, **레짐을 통제한 뒤에도** 차이가 나는가?
    - 갈리면 → 레짐이 놓치는 정보(실질금리)가 금 수익을 설명 → 드라이버 오버레이 가치 있음.
    - 레짐 고정 시 차이가 사라지면 → 레짐이 이미 그 정보를 담음 → 오버레이 불필요.

방법:
  - 금 가격 = gold 클래스 수익 누적지수(라우팅 결합, 411060 KRX금현물).
  - 신호 = real_rate_chg_3m(DFII10 10y 실질수익률의 63영업일 변화), as-of 시점 가용(causal).
    부호: <0 = 최근 3개월 실질금리 하락(금 순풍 가설) / >=0 = 상승·횡보.
  - 레짐 = 일별 rule(detect_regime).
  - 각 날 t에서 금 forward H거래일 수익(price[t+H]/price[t]-1) 측정. H=21·63(~1·3개월).
  - (1) 무조건 부호 분할, (2) **레짐×부호** 분할로 within-regime 차이 확인.
  - 보조: forward 평균·중앙수익 + 양(+) 비율. (상승 트리거 진단이라 forward 수익이 정답지표지만
    V자편향 주의 — 실질금리↓가 위기 바닥과 겹치면 forward수익 과대 → 레짐 통제가 그 통제 역할.)

한계: gold 클래스 단일·USD/KRW 합성 미반영·단일경로·in-sample 전체. real_rate는 월/일 혼재
  ffill(limit). forward 수익 단독은 V편향 가능 → 레짐 통제 + 동시구간 함께 봐 보정. 라이브 미반영.
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
from regime_class_correlation import daily_rule_regime, class_returns, START, END  # noqa: E402

HORIZONS = [21, 63]  # 거래일 (~1·3개월)
REGIMES = ["Goldilocks", "Slowdown", "Reflation", "Stagflation", "Crisis"]


def fwd_ret(parr, pos, H):
    """pos에서 향후 H거래일 누적수익."""
    if pos + H >= len(parr):
        return np.nan
    return float(parr[pos + H] / parr[pos] - 1.0)


def summarize(rets):
    rets = [r for r in rets if not np.isnan(r)]
    if not rets:
        return None
    a = np.array(rets)
    return dict(n=len(a), mean=float(a.mean()), med=float(np.median(a)),
                pos=float((a > 0).mean()))


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    routing = cfg["asset_routing"]

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=cfg, start=START, end=END, use_cache=True)
    present = list(universe_px.columns)

    print("일별 레짐 분류 중...")
    reg = daily_rule_regime(signal_px)

    print("FRED 실질금리 로딩 중...")
    fred = fetch_fred_history(START, END)
    if fred.empty or "real_rate_chg_3m" not in fred.columns:
        print("  [에러] real_rate_chg_3m 없음 (FRED_API_KEY 확인) — 중단")
        return
    rr = fred["real_rate_chg_3m"].reindex(reg.index, method="ffill").ffill(limit=10)

    # 금 가격지수
    gret = class_returns(universe_px, routing, present)["gold"].reindex(reg.index).fillna(0)
    gp = (1 + gret).cumprod()
    parr = gp.values
    pos = {ts: k for k, ts in enumerate(gp.index)}

    # 공통 표본: 레짐·실질금리·가격 모두 있는 날
    common = reg.index[reg.notna() & rr.notna()]
    sign = pd.Series(np.where(rr.loc[common] < 0, "실질금리↓", "실질금리↑·횡보"), index=common)

    def cell_rets(days, H):
        return [fwd_ret(parr, pos[ts], H) for ts in days if ts in pos]

    # ── (1) 무조건 부호 분할 ──────────────────────────────────────────────────
    print(f"\n{'='*82}")
    print("  (1) 무조건 — 실질금리 3개월 변화 부호별 금 forward 수익")
    print(f"{'='*82}")
    hdr = f"  {'그룹':>16}{'표본':>7}"
    for H in HORIZONS:
        hdr += f"{'평균'+str(H)+'d':>10}{'중앙'+str(H)+'d':>10}{'양(+)':>8}"
    print(hdr); print("  " + "─" * (len(hdr)))
    for grp in ["실질금리↓", "실질금리↑·횡보"]:
        days = sign.index[sign == grp]
        row = f"  {grp:>16}{len(days):>7}"
        for H in HORIZONS:
            s = summarize(cell_rets(days, H))
            row += f"{s['mean']:>10.2%}{s['med']:>10.2%}{s['pos']:>8.0%}" if s else f"{'—':>28}"
        print(row)

    # ── (2) 레짐 × 부호 분할 (within-regime 통제) ─────────────────────────────
    for H in HORIZONS:
        print(f"\n{'='*82}")
        print(f"  (2) 레짐 통제 — forward {H}d 금 수익: 실질금리↓ vs ↑ (레짐별)")
        print(f"{'='*82}")
        h2 = (f"  {'레짐':>12}"
              f"{'↓표본':>7}{'↓평균':>9}{'↓양+':>7}"
              f"{'↑표본':>7}{'↑평균':>9}{'↑양+':>7}{'Δ평균(↓-↑)':>12}")
        print(h2); print("  " + "─" * (len(h2)))
        for rg in REGIMES:
            rgdays = sign.index[reg.loc[sign.index] == rg]
            d_dn = rgdays[sign.loc[rgdays] == "실질금리↓"]
            d_up = rgdays[sign.loc[rgdays] == "실질금리↑·횡보"]
            s_dn = summarize(cell_rets(d_dn, H))
            s_up = summarize(cell_rets(d_up, H))
            if not s_dn or not s_up:
                print(f"  {rg:>12}{'  (표본부족)':>20}")
                continue
            delta = s_dn["mean"] - s_up["mean"]
            print(f"  {rg:>12}"
                  f"{s_dn['n']:>7}{s_dn['mean']:>9.2%}{s_dn['pos']:>7.0%}"
                  f"{s_up['n']:>7}{s_up['mean']:>9.2%}{s_up['pos']:>7.0%}{delta:>11.2%}")

    print("\n  판정: 레짐을 고정해도(2) 실질금리↓의 금 forward수익이 ↑보다 꾸준히(Δ>0) 높으면")
    print("  → 레짐이 놓친 실질금리 정보가 금 수익을 설명 = 드라이버 오버레이 가치 있음.")
    print("  레짐 고정 시 Δ가 사라지거나 부호 뒤섞이면 → 레짐이 이미 포착 = 오버레이 불필요.")
    print("  주의: gold클래스·단일경로·in-sample·forward수익 V편향(레짐통제로 일부 보정). 라이브 미반영.")


if __name__ == "__main__":
    main()
