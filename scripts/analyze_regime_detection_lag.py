"""
#1 레짐 탐지 지연 측정 — 모델은 미래를 예측하나, 아니면 (늦게) 현재를 식별하나?

질문(2026-06-13): 사용자 전제 "모델은 미래 레짐을 예측 못 하고 현재 레짐만 (늦게) 안다".
  이를 수치로 못박는다. 레짐 에피소드 진입일(라벨이 그 레짐으로 처음 바뀐 날) 기준으로
  SPY의 진입 전/후 누적 움직임을 비교한다. 특징적 움직임이 *진입 전*에 이미 끝났다면 = 늦은 식별.

방법: 일별 rule acting regime → 에피소드 분해 → 진입일 0 정렬 event study.
  - 진입 전 [-W,0] vs 진입 후 [0,+W] SPY 누적수익 (W=20거래일).
  - 방어 레짐(Slowdown/Stagflation/Crisis): 진입 시점에 직전 60일 고점 대비 이미 난 낙폭.
  - 진입일 기준 forward 20d SPY 수익: 음(추세지속) vs 양(저점 근처 = 평균회귀).
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
MAIN = ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis"]
DEFENSIVE = {"Slowdown", "Stagflation", "Crisis"}
W = 20  # event window (거래일)


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


def episodes(regime):
    """연속 동일 레짐 구간 → [(regime, entry_idx, exit_idx)] (정수 위치)."""
    vals = regime.values
    eps = []
    s = 0
    for i in range(1, len(vals)):
        if vals[i] != vals[s]:
            eps.append((vals[s], s, i - 1))
            s = i
    eps.append((vals[s], s, len(vals) - 1))
    return eps


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]...")
    _, signal_px = load_all_prices(config=cfg, start=START, end=END, use_cache=True)
    spy = signal_px["SPY"].dropna()

    print("일별 rule 레짐 산출 중...")
    regime = daily_rule_regime(signal_px).reindex(spy.index).ffill().dropna()
    spy = spy.reindex(regime.index)
    spy_ret = spy.pct_change()
    n = len(regime)

    eps = episodes(regime)
    pos = {t: i for i, t in enumerate(regime.index)}

    print(f"\n총 {len(eps)}개 에피소드, {n}거래일")
    print(f"\n{'='*104}")
    print("  레짐 탐지 지연 — 진입일(0) 정렬 event study (SPY 기준, W=±20거래일)")
    print(f"{'='*104}")
    print(f"  {'레짐':>12}{'에피소드':>7}{'중앙길이':>8}{'진입前20d':>10}{'진입後20d':>10}"
          f"{'탐지시낙폭':>11}{'fwd20d':>9}")
    print("  " + "─" * 98)

    summary = {}
    for rg in MAIN:
        ent = [e for e in eps if e[0] == rg and e[1] >= W and e[2] + W < n]
        if not ent:
            continue
        durs, pre, post, dd_at, fwd = [], [], [], [], []
        for _, si, ei in ent:
            durs.append(ei - si + 1)
            p0 = spy.iloc[si]
            pre.append(spy.iloc[si] / spy.iloc[si - W] - 1.0)       # 진입 전 20d
            post.append(spy.iloc[si + W] / spy.iloc[si] - 1.0)      # 진입 후 20d
            fwd.append(post[-1])
            peak = spy.iloc[max(0, si - 60):si + 1].max()           # 직전 60일 고점
            dd_at.append(spy.iloc[si] / peak - 1.0)
        summary[rg] = {
            "n": len(ent), "dur": np.median(durs),
            "pre": np.mean(pre), "post": np.mean(post),
            "dd": np.mean(dd_at), "fwd": np.mean(fwd),
        }
        s = summary[rg]
        print(f"  {rg:>12}{s['n']:>7}{s['dur']:>8.0f}{s['pre']:>10.1%}{s['post']:>10.1%}"
              f"{s['dd']:>11.1%}{s['fwd']:>9.1%}")

    print("\n  해석 가이드:")
    print("  • 진입前20d = 라벨이 붙기 직전 20거래일 SPY 누적수익 (이 레짐의 '특징적 움직임'이 탐지 전에 얼마나 진행됐나)")
    print("  • 진입後20d = 라벨 붙은 뒤 20거래일 SPY (탐지 후 무슨 일이 일어나나)")
    print("  • 탐지시낙폭 = 진입 시점에 직전 60일 고점 대비 이미 난 SPY 낙폭 (방어 레짐이 얼마나 늦었나)")
    print("  • 방어 레짐(Slowdown/Stagflation/Crisis): 진입前 큰 음(-) + 진입後 회복(+) = 늦은 식별 + 평균회귀")
    print("  주의: rule 레짐 라벨이 기준(ground truth 아님). in-sample. SPY 기준(개별 자산 아님).")


if __name__ == "__main__":
    main()
