"""
현재 엔진(현행 config: rf 0.7·vol 블렌드·5일평활·시드[42]) 기준으로 지난 한 달
매일의 레짐과 목표 자산군 비중을 워크포워드로 재계산해 출력.

라이브가 매일 재계산하는 것과 동일하게 _get_regime을 매 거래일 호출(prev_blend·
anchor 체인 유지). 워밍업 구간을 앞에 둬 평활·앵커를 데운 뒤 표시 구간만 출력.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))

warnings.filterwarnings("ignore")

from data import load_all_prices  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from features import compute_features  # noqa: E402

WARMUP_START = "2026-04-01"   # 평활/앵커 데우기
DISPLAY_START = "2026-05-16"  # 출력 시작(약 한 달)
DATA_START = "2024-06-01"     # HMM 500일 룩백 확보


def main():
    base = yaml.safe_load(open(ROOT / "trading" / "config.yaml"))
    end = "2026-06-16"
    print(f"데이터 로딩 [{DATA_START} ~ {end}]...", flush=True)
    up, sp = load_all_prices(config=base, start=DATA_START, end=end, use_cache=True)
    fh = fetch_fred_history(DATA_START, end)

    eng = BacktestEngine(
        config=base, universe_px=up, signal_px=sp,
        start=DATA_START, end=end, drift_threshold=0.015, cooldown_days=0,
        fred_history=fh,
    )
    cls_of = {t: m["asset_class"] for t, m in base["universe"].items()}

    # 표시 구간 + 워밍업 거래일
    days = [d for d in sp.index if pd.Timestamp(WARMUP_START) <= d <= pd.Timestamp(end)]
    rows = []
    for d in days:
        regime, blend, rule_regime, conf, *_ = eng._get_regime(d)
        sig = sp[:d]
        feats = compute_features(sig, smooth_window=eng.smooth_window, smooth_features=eng.smooth_features)
        rvol = feats.get("realized_vol", 0.0)
        vix = feats.get("vix", 0.0)
        w = eng._target_weights(
            blend, rvol, 1.0, regime=regime, vix=vix,
            signal_px_slice=sig, universe_px_slice=up[:d],
        )
        cls_w: dict = {}
        for t, v in w.items():
            c = cls_of.get(t, "cash")
            c = "cash" if c == "cash_usd" else c
            cls_w[c] = cls_w.get(c, 0.0) + v
        if d >= pd.Timestamp(DISPLAY_START):
            rows.append((d, rule_regime, regime, conf, blend, cls_w))

    # 출력
    print(f"\n{'='*120}")
    print(f"  현행 엔진 일별 레짐·자산군 비중  (rf0.7·vol블렌드·평활·시드[42])  [{DISPLAY_START} ~ {end}]")
    print(f"{'='*120}")
    for d, rule_r, acting, conf, blend, cls_w in rows:
        top = sorted(blend.items(), key=lambda x: -x[1])[:3]
        blend_str = "  ".join(f"{r[:5]} {p:.0%}" for r, p in top if p >= 0.05)
        wmain = sorted(cls_w.items(), key=lambda x: -x[1])
        w_str = "  ".join(f"{c}:{v:.0%}" for c, v in wmain if v >= 0.02)
        print(f"\n  {d.date()}  레짐(확정)={acting}  신뢰도 {conf:.0%}")
        print(f"     blend: {blend_str}")
        print(f"     비중 : {w_str}")
    return rows


if __name__ == "__main__":
    main()
