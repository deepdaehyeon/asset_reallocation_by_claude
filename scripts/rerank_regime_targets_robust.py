"""
층 1 Step 2 — V-shape 편향 제거 재랭킹.

문제: Phase 1 진단(analyze_regime_targets.py)은 레짐별 자산 우위를 forward 21일
수익률 Sharpe로 랭킹했다. 그런데 detect_regime은 후행적이라 Crisis/Stagflation
분류 시점이 폭락 바닥 근처 → forward 윈도가 V-shape 반등을 통째로 잡아 위험자산
(equity)에 비현실적 우위를 준다(진단 문서 메타이슈 #2). Step 1에서 이 편향대로
넣은 Crisis equity_etf가 실제 Crisis 일별 방어를 약화시킴이 확인됨.

처방: 편향에 둔감한 지표로 재랭킹하고 forward 랭킹과 비교해 "베타에 휘둘린"
자산을 찾는다.
  - contemp_sharpe : 레짐 체류일의 **동시점** 일별 수익률 mean/std (forward 아님).
                     "그 레짐에 있는 동안" 실제로 뭐가 작동했나 — 반등 선취 없음.
  - fwd21_sharpe   : 기존 Phase 1 지표 (비교 기준선).
  - fwd21_dd       : 레짐 체류일의 forward 21일 max-drawdown 평균 (하방 위험).
                     방어 시스템엔 반등 총수익보다 이게 더 관련.

랭킹이 크게 뒤집히는 자산 = forward 편향 의심. 현재 비중과 함께 출력.

코드 변경 없음 (진단). 결과는 docs/에 저장.
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

from features import compute_features  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
from data import load_all_prices  # noqa: E402
from regime import detect_regime, REGIMES  # noqa: E402

START = "2010-01-01"
END = "2025-04-30"
FWD = 21
ANNUAL = np.sqrt(252)


def build_class_returns(prices: pd.DataFrame, config: dict):
    class_map: dict[str, list[str]] = {}
    for ticker, meta in config["universe"].items():
        cls = meta["asset_class"]
        if cls == "commodity_krw":
            continue
        if ticker in prices.columns:
            class_map.setdefault(cls, []).append(ticker)
    rets = prices.pct_change(fill_method=None)
    class_rets = pd.DataFrame(index=rets.index)
    for cls, tks in class_map.items():
        class_rets[cls] = rets[tks].mean(axis=1)
    return class_rets, class_map


def walk_forward_regimes(signal_px, fred_history, dates) -> pd.Series:
    fh = (
        fred_history.reindex(dates, method="ffill").ffill(limit=45)
        if fred_history is not None and not fred_history.empty else None
    )
    regimes, valid = [], []
    for date in dates:
        sig = signal_px.loc[:date].tail(70)
        if len(sig) < 30:
            continue
        try:
            feat = compute_features(sig)
        except Exception:
            continue
        if fh is not None and date in fh.index:
            row = fh.loc[date]
            for c in fh.columns:
                v = row[c]
                if not pd.isna(v):
                    feat[c] = float(v)
        regimes.append(detect_regime(feat))
        valid.append(date)
    return pd.Series(regimes, index=valid, name="regime")


def fwd_maxdd(prices_col: pd.Series, window: int) -> pd.Series:
    """각 시점의 forward window일 max-drawdown (진입가 대비 최저)."""
    arr = prices_col.values
    n = len(arr)
    out = np.full(n, np.nan)
    for i in range(n):
        j = min(i + 1 + window, n)
        if j <= i + 1 or arr[i] <= 0:
            continue
        seg = arr[i + 1:j]
        if len(seg) == 0:
            continue
        out[i] = seg.min() / arr[i] - 1.0
    return pd.Series(out, index=prices_col.index)


def main() -> None:
    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)

    print(f"[1] 데이터 로딩 [{START} ~ {END}]")
    universe_px, signal_px = load_all_prices(
        config=cfg, start=START, end=END, use_cache=True
    )
    fred_history = fetch_fred_history(START, END)

    print("[2] 클래스 수익률 + 클래스별 합성 가격")
    class_rets, class_map = build_class_returns(universe_px, cfg)
    # 클래스 합성 가격 (단순 평균 수익률 누적) — fwd maxdd용
    class_px = (1.0 + class_rets.fillna(0.0)).cumprod()

    print("[3] walk-forward detect_regime")
    regimes = walk_forward_regimes(signal_px, fred_history, universe_px.index)
    print(f"    분포: {dict(regimes.value_counts())}")

    fwd_ret = class_rets.rolling(FWD).sum().shift(-FWD)
    fwd_dd = pd.DataFrame(
        {cls: fwd_maxdd(class_px[cls], FWD) for cls in class_map}, index=class_px.index
    )

    regime_targets = cfg["regime_targets"]
    print(f"\n[4] 편향 제거 재랭킹  (contemp vs forward, n=레짐일)")
    print("=" * 104)

    flips = []  # 랭킹 역전 기록
    for regime in REGIMES:
        if regime not in regime_targets:
            continue
        rdates = regimes[regimes == regime].index
        if len(rdates) == 0:
            continue

        stats = []
        for cls in class_map:
            # contemp: 레짐 체류일 동시점 수익률
            contemp = class_rets[cls].reindex(rdates).dropna()
            # forward
            fret = fwd_ret[cls].reindex(rdates).dropna()
            fdd = fwd_dd[cls].reindex(rdates).dropna()
            if len(contemp) < 10:
                continue
            c_sharpe = (contemp.mean() / contemp.std() * ANNUAL) if contemp.std() > 0 else 0.0
            f_sharpe = (
                fret.mean() / fret.std() * np.sqrt(252 / FWD)
                if len(fret) > 0 and fret.std() > 0 else 0.0
            )
            stats.append({
                "class": cls,
                "contemp_sharpe": c_sharpe,
                "fwd21_sharpe": f_sharpe,
                "fwd21_dd": fdd.mean() * 100 if len(fdd) else np.nan,
                "current_w": regime_targets[regime].get(cls, 0.0),
            })
        sdf = pd.DataFrame(stats)
        sdf["contemp_rank"] = sdf["contemp_sharpe"].rank(ascending=False).astype(int)
        sdf["fwd_rank"] = sdf["fwd21_sharpe"].rank(ascending=False).astype(int)
        # shift<0 = forward 랭킹이 contemp보다 높음 → forward가 과대평가 (V-shape 선취 의심)
        sdf["rank_shift"] = sdf["fwd_rank"] - sdf["contemp_rank"]
        sdf = sdf.sort_values("contemp_rank")

        print(f"\n  ── {regime} (n={len(rdates)}일) ── (contemp_rank 순)")
        print(f"  {'class':<20}{'contempSR':>10}{'cRank':>6}{'fwdSR':>9}{'fRank':>6}"
              f"{'shift':>6}{'fwd_dd':>9}{'cur_w':>8}  note")
        for _, r in sdf.iterrows():
            note = ""
            # forward가 크게 과대평가(shift≤-3)인데 현재 비중 높음 → V-shape 함정 의심
            if r["rank_shift"] <= -3 and r["current_w"] >= 0.08:
                note = "⚠ forward 과대평가+고비중"
                flips.append((regime, r["class"], int(r["fwd_rank"]),
                              int(r["contemp_rank"]), r["current_w"]))
            elif r["contemp_rank"] <= 3 and r["current_w"] < 0.05:
                note = "★ contemp 상위 저비중"
            print(f"  {r['class']:<20}{r['contemp_sharpe']:>+10.2f}{r['contemp_rank']:>6}"
                  f"{r['fwd21_sharpe']:>+9.2f}{r['fwd_rank']:>6}{r['rank_shift']:>+6}"
                  f"{r['fwd21_dd']:>+8.1f}%{r['current_w']:>7.1%}  {note}")

    print(f"\n{'='*104}")
    print("⚠ = forward Sharpe 랭킹이 contemp보다 3계단 이상 높음(V-shape 선취 의심) + 현재 비중 ≥8%")
    print("★ = contemp Sharpe 상위 3위지만 현재 비중 5% 미만")
    if flips:
        print("\n  V-shape 함정 의심 (forward 편향으로 과대평가됐을 고비중):")
        for reg, cls, fr, cr, w in flips:
            print(f"    {reg:<12} {cls:<18} forward #{fr} ↔ contemp #{cr}  현재 {w:.0%}")
    else:
        print("\n  ⚠ 플래그 없음.")


if __name__ == "__main__":
    main()
