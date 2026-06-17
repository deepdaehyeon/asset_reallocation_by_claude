"""
core_satellite의 core_ratio 스윕 — 현행 30%에서 40/50/60%로 올리며 고정 4지표.

질문(2026-06-17): 코어(Goldilocks 고정·vol면제) 비중을 30→40→50→60으로 올리면 4지표가
  어떻게 변하나? 코어가 커질수록 위성(현행 엔진) 70→40%로 줄어 vol targeting 방어가
  더 희석된다. 회복기간은 짧아질 수 있으나(코어가 반등에 풀로 참여) 낙폭은 깊어질 것.

범위(사용자 합의): core_ratio만 스윕. 나머지는 현행 라이브 그대로 — vol ×1.25(방금 채택)·
  blend_target_vol ON·rf_weight 0.70·평활·시드[42]. drift·tx·USD단일·2010~2025.
주의: 코어는 vol·blend 면제라 core_ratio↑ = vol targeting 방어 희석↑(상호작용).
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

CORE_RATIOS = [0.30, 0.40, 0.50, 0.60]


def make_cfg(base, ratio):
    cfg = copy.deepcopy(base)
    cs = cfg.setdefault("core_satellite", {})
    cs["enabled"] = True
    cs["core_ratio"] = float(ratio)
    return cfg


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]... (현행 라이브 config: vol ×1.25·rf0.7·blend ON)", flush=True)
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    rows = []
    for ratio in CORE_RATIOS:
        label = f"core{int(ratio*100)}"
        print(f"[{label}] 실행 중...", flush=True)
        res = build_engine(make_cfg(base, ratio), universe_px, signal_px, fred_history).run()
        rows.append(metrics_row(label, res))

    df = pd.DataFrame(rows).set_index("전략")
    print(f"\n{'='*136}")
    print("  core_satellite core_ratio 스윕 (vol×1.25·blend·rf0.7 ON·drift·tx·2010~2025)")
    print(f"{'='*136}")
    h = (f"  {'전략':>16}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'tx':>7}"
         f"{'COVID':>8}{'Bear22':>8}")
    print(h)
    print("  " + "─" * (len(h) + 2))
    for label, r in df.iterrows():
        mark = " ◀현행" if label == "core30" else ""
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {label:>16}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}"
              f"{r['tx']:>7.2%}{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")
    return df


if __name__ == "__main__":
    main()
