"""
코어에도 골디락스 vol targeting 적용 — core0 vs core30 비교 (2026-06-17).

질문(사용자): 코어(Goldilocks 고정)는 지금 vol targeting 면제라 위기 때 방어가 안 걸린다
  (core_ratio 스윕에서 코어↑ = MaxDD 심화의 근원). 코어에도 골디락스 vol targeting을
  걸면 어떻게 되나? 코어0(코어 없음)과 코어30(코어 vol타겟)을 비교.

변형 3종(현행 라이브 위 — vol×1.25·blend·rf0.7·평활·시드[42], drift·tx·USD단일·2010~2025):
  1. core0            — core_ratio=0 (코어 없음, 순수 엔진)
  2. core30 현행       — core_ratio=0.30, 코어 vol 면제(라이브 그대로) [참고 baseline]
  3. core30 +코어vol타겟 — core_ratio=0.30, core_vol_targeting=True (코어에 골디락스 vol타겟)
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


def make_cfg(base, *, ratio, core_vol):
    cfg = copy.deepcopy(base)
    cs = cfg.setdefault("core_satellite", {})
    cs["enabled"] = ratio > 0
    cs["core_ratio"] = float(ratio)
    cs["core_vol_targeting"] = bool(core_vol)
    return cfg


VARIANTS = [
    ("core0 (코어없음)",        dict(ratio=0.0,  core_vol=False)),
    ("core30 현행(vol면제)",    dict(ratio=0.30, core_vol=False)),
    ("core30 +코어vol타겟",     dict(ratio=0.30, core_vol=True)),
]


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]... (현행 라이브: vol×1.25·rf0.7·blend ON)", flush=True)
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    rows = []
    for label, kw in VARIANTS:
        print(f"[{label}] 실행 중...", flush=True)
        res = build_engine(make_cfg(base, **kw), universe_px, signal_px, fred_history).run()
        rows.append(metrics_row(label, res))

    df = pd.DataFrame(rows).set_index("전략")
    print(f"\n{'='*140}")
    print("  코어 vol targeting A/B — core0 vs core30(vol면제) vs core30(코어vol타겟)  (vol×1.25·blend·rf0.7·drift·tx·2010~2025)")
    print(f"{'='*140}")
    h = (f"  {'전략':>20}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'tx':>7}"
         f"{'COVID':>8}{'Bear22':>8}")
    print(h)
    print("  " + "─" * (len(h) + 2))
    for label, r in df.iterrows():
        mark = " ◀현행" if label == "core30 현행(vol면제)" else ""
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {label:>20}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}"
              f"{r['tx']:>7.2%}{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")
    return df


if __name__ == "__main__":
    main()
