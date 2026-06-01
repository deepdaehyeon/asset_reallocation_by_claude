"""
디커플링/상관구조를 레짐 변화점 신호로 쓸 수 있는가 — 진단(코드 변경 없음).

사용자 가설: 자산 간 디커플링(상관 붕괴)도 레짐 변화점으로 볼 수 있지 않나?
현재 시스템은 60일 평균 상관(compute_rolling_correlation)을 출력만 하고 detect_regime엔
안 쓴다. 빌드 전에 "선행성 + 기존 신호 대비 증분"을 싸게 검증한다.

세 상관 신호 (60일 롤링):
  - avg_corr : 유니버스 페어 평균 상관 (위기 때 →1 급등)
  - sb_corr  : SPY-TLT 상관 (주식-채권 디커플링/부호전환)
  - disp     : 횡단면 일수익률 분산 (21일 평활) — 높을수록 디커플링

세 가지를 측정:
  1. 선행성: Spearman(signal(t−lead), forward-21d SPY maxdd). lead=0/5/10/21.
     baseline로 VIX·realized_vol도 같이 → "vol 대비 증분 정보"가 있는지.
  2. 중복성: 각 신호 vs VIX/vol 동시점 상관 (높으면 새 정보 적음).
  3. 에피소드 리드: SPY 드로우다운 ≥8% 11개 에피소드에서 신호가 peak 대비 며칠 앞서
     극단(상관 급등=avg_corr↑, 디커플링=disp↑/sb_corr↓)에 도달하는지.

결과는 docs/에 저장.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.stats import spearmanr

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "scripts"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from data import load_all_prices  # noqa: E402
from analyze_regime_timing import detect_episodes, fwd_maxdd, EPISODE_MIN_DD, FWD  # noqa: E402

START = "2010-01-01"
END = "2025-04-30"
CORR_WIN = 60
LEADS = [0, 5, 10, 21]


def rolling_avg_pair_corr(rets: pd.DataFrame, win: int) -> pd.Series:
    out = pd.Series(index=rets.index, dtype=float)
    cols = rets.columns
    n = len(cols)
    iu = np.triu_indices(n, k=1)
    for i in range(win, len(rets) + 1):
        c = rets.iloc[i - win:i].corr().values
        out.iloc[i - 1] = float(np.nanmean(c[iu]))
    return out


def main() -> None:
    with open(ROOT / "trading" / "config.yaml") as f:
        config = yaml.safe_load(f)

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=config, start=START, end=END, use_cache=True)

    spy = signal_px["SPY"]
    # 공통 인덱스
    idx = spy[START:END].index
    uni = universe_px.reindex(idx).ffill()
    sig = signal_px.reindex(idx).ffill()

    uni_rets = uni.pct_change()
    spy_rets = sig["SPY"].pct_change()
    tlt_rets = sig["TLT"].pct_change() if "TLT" in sig else None

    # ── 신호 ──────────────────────────────────────────────────────────────
    avg_corr = rolling_avg_pair_corr(uni_rets.dropna(how="all", axis=1), CORR_WIN)
    sb_corr = spy_rets.rolling(CORR_WIN).corr(tlt_rets) if tlt_rets is not None else None
    disp = uni_rets.std(axis=1).rolling(21).mean()  # 횡단면 분산 21일 평활
    vix = sig["^VIX"] if "^VIX" in sig else None
    rvol = spy_rets.rolling(21).std() * np.sqrt(252)

    spy_fwd_dd = fwd_maxdd(spy.reindex(idx).ffill(), FWD)

    signals = {"avg_corr": avg_corr, "sb_corr": sb_corr, "disp": disp,
               "VIX(base)": vix, "rvol(base)": rvol}
    signals = {k: v for k, v in signals.items() if v is not None}

    # ── 1. 선행성: Spearman(signal(t-lead), fwd21 SPY maxdd) ───────────────
    print(f"\n{'='*86}")
    print(f"  1. 선행성 — Spearman( signal(t−lead) , forward-{FWD}d SPY maxdd )")
    print(f"     (음수=신호↑일수록 미래 낙폭 큼=스트레스 예고. |값| 클수록 강함)")
    print(f"{'='*86}")
    hdr = f"  {'signal':<12}" + "".join(f"lead{l:>2}d" .rjust(10) for l in LEADS)
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    for name, s in signals.items():
        row = f"  {name:<12}"
        for lead in LEADS:
            df = pd.DataFrame({"s": s.shift(lead), "dd": spy_fwd_dd}).dropna()
            rho = spearmanr(df["s"], df["dd"]).correlation if len(df) > 30 else np.nan
            row += f"{rho:>+10.3f}"
        print(row)

    # ── 2. 중복성: 신호 vs VIX/rvol 동시점 상관 ────────────────────────────
    print(f"\n{'='*86}")
    print("  2. 중복성 — 신호 vs 기존 vol 신호 동시점 Spearman (높으면 새 정보 적음)")
    print(f"{'='*86}")
    print(f"  {'signal':<12}{'vs VIX':>12}{'vs rvol':>12}")
    print("  " + "─" * 36)
    for name in ("avg_corr", "sb_corr", "disp"):
        if name not in signals:
            continue
        s = signals[name]
        r_vix = spearmanr(*_align(s, vix)).correlation if vix is not None else np.nan
        r_rv = spearmanr(*_align(s, rvol)).correlation
        print(f"  {name:<12}{r_vix:>+12.3f}{r_rv:>+12.3f}")

    # ── 3. 에피소드 리드 ──────────────────────────────────────────────────
    spy_al = spy.reindex(idx).ffill()
    episodes = detect_episodes(spy_al, EPISODE_MIN_DD)
    print(f"\n{'='*86}")
    print(f"  3. 에피소드 리드 — SPY 드로우다운 ≥{EPISODE_MIN_DD:.0%} {len(episodes)}건, peak 대비 신호 극단 도달일")
    print("     (음수=peak보다 앞섬=선행. 각 신호의 252일 롤링 80%ile(avg_corr/disp) /")
    print("      20%ile(sb_corr) 돌파 시점을 peak−30~peak+10d 창에서 탐색)")
    print(f"{'='*86}")
    print(f"  {'peak':<12}{'trough':<12}{'dd':>6} │{'avg_corr':>11}{'disp':>9}{'sb_corr':>10}")
    print("  " + "─" * 70)

    lead_agg = {"avg_corr": [], "disp": [], "sb_corr": []}
    for peak, trough, dd in episodes:
        cells = {}
        for name, hi in (("avg_corr", True), ("disp", True), ("sb_corr", False)):
            if name not in signals:
                cells[name] = "—"; continue
            s = signals[name]
            thr = s.rolling(252, min_periods=60).quantile(0.80 if hi else 0.20)
            cross = (s >= thr) if hi else (s <= thr)
            win = cross[(cross.index >= peak - pd.Timedelta(days=30)) &
                        (cross.index <= peak + pd.Timedelta(days=10)) & cross]
            if len(win):
                lag = (win.index[0] - peak).days
                lead_agg[name].append(lag)
                cells[name] = f"{lag:+d}d"
            else:
                cells[name] = "—"
        print(f"  {peak.date()!s:<12}{trough.date()!s:<12}{dd:>+5.0%} │"
              f"{cells['avg_corr']:>11}{cells['disp']:>9}{cells['sb_corr']:>10}")

    print(f"\n  리드 중앙값 (도달한 에피소드만):")
    for name in ("avg_corr", "disp", "sb_corr"):
        v = lead_agg[name]
        med = f"{np.median(v):+.0f}d (n={len(v)})" if v else "—"
        print(f"    {name:<10} {med}")


def _align(a: pd.Series, b: pd.Series):
    df = pd.DataFrame({"a": a, "b": b}).dropna()
    return df["a"], df["b"]


if __name__ == "__main__":
    main()
