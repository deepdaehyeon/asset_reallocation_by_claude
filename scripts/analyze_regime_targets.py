"""
#1 Phase 1: regime_targets 진단 분석.

목적:
  현재 regime_targets는 손정의 비중. 데이터로 검증되지 않은 가설.
  각 레짐별 자산 클래스 historical 수익률과 현재 비중을 비교해
  격차 큰 레짐을 식별. 적용 X (분석만).

방법:
  1. universe 자산 클래스 매핑
  2. 가격 데이터 로딩 + proxy 적용
  3. 자산 클래스별 수익률 합성 (해당 종목 단순 평균)
  4. walk-forward로 매일 detect_regime 적용 (in-sample, Phase 1은 진단 목적)
  5. 각 레짐 시점에서 자산 클래스 forward 21일 평균 수익률 + Sharpe
  6. 클래스별 ranking vs 현재 regime_targets 비중 비교

출력:
  레짐별 (현재 비중 / historical Sharpe rank / 격차) 표.

사용:
  python scripts/analyze_regime_targets.py [--start 2010-01-01] [--end 2025-04-30]
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from features import compute_features  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
from data import load_all_prices  # noqa: E402
from regime import detect_regime, REGIMES  # noqa: E402


FORWARD_WINDOW = 21


def build_class_returns(prices: pd.DataFrame, config: dict) -> pd.DataFrame:
    """자산 클래스별 일별 수익률 (해당 클래스 종목 단순 평균)."""
    class_map: dict[str, list[str]] = {}
    for ticker, meta in config["universe"].items():
        cls = meta["asset_class"]
        if cls == "commodity_krw":
            continue  # synthetic bridge, regime_targets 미포함
        if ticker in prices.columns:
            class_map.setdefault(cls, []).append(ticker)

    rets = prices.pct_change(fill_method=None)
    class_rets = pd.DataFrame(index=rets.index)
    for cls, tickers in class_map.items():
        class_rets[cls] = rets[tickers].mean(axis=1)
    return class_rets, class_map


def walk_forward_regimes(signal_px: pd.DataFrame, fred_history: pd.DataFrame,
                          dates: pd.DatetimeIndex) -> pd.Series:
    """walk-forward로 detect_regime 적용 — 각 시점 features 기준."""
    fh_aligned = (
        fred_history.reindex(dates, method="ffill").ffill(limit=45)
        if fred_history is not None and not fred_history.empty else None
    )
    regimes = []
    valid_dates = []
    for date in dates:
        sig = signal_px.loc[:date].tail(70)
        if len(sig) < 30:
            continue
        try:
            feat = compute_features(sig)
        except Exception:
            continue
        if fh_aligned is not None and date in fh_aligned.index:
            row = fh_aligned.loc[date]
            for c in fh_aligned.columns:
                v = row[c]
                if not pd.isna(v):
                    feat[c] = float(v)
        regimes.append(detect_regime(feat))
        valid_dates.append(date)
    return pd.Series(regimes, index=valid_dates, name="regime")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--end", default="2025-04-30")
    args = p.parse_args()

    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)

    print(f"[1] 데이터 로딩 [{args.start} ~ {args.end}]")
    universe_px, signal_px = load_all_prices(
        config=cfg, start=args.start, end=args.end, use_cache=True
    )
    fred_history = fetch_fred_history(args.start, args.end)

    print("[2] 자산 클래스별 수익률 합성")
    class_rets, class_map = build_class_returns(universe_px, cfg)
    print(f"    클래스 ({len(class_map)}개): {dict({k: len(v) for k, v in class_map.items()})}")

    print("[3] walk-forward detect_regime")
    regimes = walk_forward_regimes(signal_px, fred_history, universe_px.index)
    print(f"    레짐 시계열: {len(regimes)}일")
    print(f"    분포: {dict(regimes.value_counts())}")

    # forward 21일 클래스 수익률
    fwd_class_rets = class_rets.rolling(FORWARD_WINDOW).sum().shift(-FORWARD_WINDOW)

    print(f"\n[4] 레짐별 자산 클래스 forward {FORWARD_WINDOW}일 수익률 분석")
    print(f"{'=' * 90}")

    # 각 레짐별 분석
    regime_targets = cfg["regime_targets"]
    for regime in REGIMES:
        if regime not in regime_targets:
            continue
        mask_dates = regimes[regimes == regime].index
        sub = fwd_class_rets.loc[fwd_class_rets.index.isin(mask_dates)].dropna(how="all")
        if len(sub) == 0:
            print(f"\n  ── {regime} ── (시점 없음)")
            continue

        print(f"\n  ── {regime} (n={len(sub)}일) ──")
        # 클래스별 통계
        stats = []
        for cls in class_map:
            if cls not in sub.columns:
                continue
            col = sub[cls].dropna()
            if len(col) == 0:
                continue
            mean_pct = col.mean() * 100
            std_pct = col.std() * 100
            sharpe = (col.mean() / col.std() * np.sqrt(252 / FORWARD_WINDOW)
                      if col.std() > 0 else 0)
            current_w = regime_targets[regime].get(cls, 0.0)
            stats.append({
                "class": cls,
                "mean_pct": mean_pct,
                "sharpe": sharpe,
                "current_w": current_w,
            })
        sdf = pd.DataFrame(stats).sort_values("sharpe", ascending=False)
        sdf["sharpe_rank"] = range(1, len(sdf) + 1)

        # 출력
        print(f"  {'class':<22}{'fwd_mean':>10}{'Sharpe':>10}{'rank':>6}{'current_w':>12}{'note':>14}")
        for _, r in sdf.iterrows():
            # 격차 판정: 상위 3위인데 비중 0%, 또는 하위 3위인데 비중 > 10%
            n = len(sdf)
            note = ""
            if r["sharpe_rank"] <= 3 and r["current_w"] < 0.05:
                note = "★ 상위 저비중"
            elif r["sharpe_rank"] >= n - 2 and r["current_w"] >= 0.10:
                note = "✗ 하위 고비중"
            print(f"  {r['class']:<22}"
                  f"{r['mean_pct']:>+9.2f}%"
                  f"{r['sharpe']:>+10.2f}"
                  f"{int(r['sharpe_rank']):>6}"
                  f"{r['current_w']:>11.1%}"
                  f"  {note}")

    print(f"\n{'=' * 90}")
    print("★ = historical Sharpe 상위지만 현재 비중 5% 미만 (추가 가치)")
    print("✗ = historical Sharpe 하위지만 현재 비중 10% 이상 (재고 권장)")
    print("\n(in-sample 분석. Phase 2에서 walk-forward 최적화 + shrinkage 검토)")


if __name__ == "__main__":
    main()
