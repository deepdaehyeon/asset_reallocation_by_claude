"""
RF / HMM 비율 스윕 — blend = (1-w)·HMM + w·RF 에서 w(rf_weight)를 바꿔 4지표 비교.

질문(2026-06-17): 비중을 만드는 blend는 HMM(비지도) 60% + RF(룰을 베낀 지도) 40%다.
  이 60/40 혼합비가 최적인가? w=0(순수 HMM) ~ 1(순수 RF)을 스윕해 고정 4지표로 본다.

범위 합의(사용자): core30 OFF(순수 신호 — 코어 30% 희석 제거). feature_smoothing은
  현행 config 그대로 ON. drift 리밸·tx·USD단일·2010~2025. 한 점의 최고값이 아니라
  넓은 구간 안정성으로 판단(비율 미세조정은 노이즈 가능 — [[feedback-regime-targets-no-tuning]]).
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "scripts"))

import warnings
warnings.filterwarnings("ignore", message="Model is not converging.*")

from data import load_all_prices  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
from ablation_regime_stack import build_engine, metrics_row  # noqa: E402
from compare_rule_timing_ab import START, END  # noqa: E402

WEIGHTS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]


def make_cfg(base, rf_weight):
    cfg = copy.deepcopy(base)
    cfg.setdefault("core_satellite", {})["enabled"] = False  # 순수 신호
    cfg.setdefault("hmm", {})["rf_weight"] = float(rf_weight)
    cfg["hmm"]["rf_enabled"] = True
    return cfg


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]...", flush=True)
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    rows = []
    for w in WEIGHTS:
        hmm_pct, rf_pct = int(round((1 - w) * 100)), int(round(w * 100))
        label = f"HMM{hmm_pct}/RF{rf_pct}"
        print(f"[{label}] 실행 중...", flush=True)
        res = build_engine(make_cfg(base, w), universe_px, signal_px, fred_history).run()
        rows.append(metrics_row(label, res))

    df = pd.DataFrame(rows).set_index("전략")
    print(f"\n{'='*132}")
    print("  RF/HMM 비율 스윕 — core30 OFF·평활 ON·drift·tx·USD단일·2010~2025 (현행=HMM60/RF40)")
    print(f"{'='*132}")
    h = (f"  {'전략':>12}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'tx':>7}"
         f"{'COVID':>8}{'Bear22':>8}")
    print(h)
    print("  " + "─" * (len(h) + 2))
    for label, r in df.iterrows():
        mark = " ◀현행" if label == "HMM60/RF40" else ""
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {label:>12}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}"
              f"{r['tx']:>7.2%}{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")
    return df


if __name__ == "__main__":
    main()
