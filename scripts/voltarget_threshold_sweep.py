"""
vol targeting 발동 기준선(target_vol) 상향 스윕 — 방어로직을 점진적으로 풀며 4지표.

질문(2026-06-17): vol targeting의 목표변동성(이 변동성 넘으면 주식 축소)을 ×배수로 올리면
  방어가 더 늦게/덜 걸린다. 조금씩 풀면서(×1.0~×2.0, 그리고 OFF) 고정 4지표가 어떻게 변하나?

범위 합의(사용자): target_vol 상향(floor 아님). core30 ON(라이브). blend_target_vol ON·
  rf_weight 0.70(현행 라이브 그대로). drift·tx·USD단일·2010~2025.
주의: blend_target_vol ON이라 실제 목표 = Σ p[r]·regime_vols[r]. regime_vols 전체에 ×k →
  블렌드 목표도 ×k로 비례 상향(깔끔). core30 ON이라 완화 효과는 위성 70%로 희석.
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

MULTIPLIERS = [1.0, 1.25, 1.5, 1.75, 2.0]


def make_cfg(base, k):
    cfg = copy.deepcopy(base)
    vt = cfg.setdefault("vol_targeting", {})
    rv = vt.get("regime_target_vol", {})
    vt["regime_target_vol"] = {r: float(v) * k for r, v in rv.items()}
    return cfg


def make_off_cfg(base):
    cfg = copy.deepcopy(base)
    cfg.setdefault("vol_targeting", {})["enabled"] = False
    return cfg


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    g0 = base["vol_targeting"]["regime_target_vol"]["Goldilocks"]
    print(f"데이터 로딩 [{START} ~ {END}]... (Goldilocks 기준 target_vol={g0:.0%})", flush=True)
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    rows = []
    for k in MULTIPLIERS:
        label = f"×{k:.2f} (G{g0*k:.0%})"
        print(f"[{label}] 실행 중...", flush=True)
        res = build_engine(make_cfg(base, k), universe_px, signal_px, fred_history).run()
        rows.append(metrics_row(label, res))
    print("[OFF 방어없음] 실행 중...", flush=True)
    rows.append(metrics_row("OFF 방어없음", build_engine(make_off_cfg(base), universe_px, signal_px, fred_history).run()))

    df = pd.DataFrame(rows).set_index("전략")
    print(f"\n{'='*136}")
    print("  vol targeting target_vol 상향 스윕 — 방어 점진 완화 (core30·blend·rf0.7 ON·drift·tx·2010~2025)")
    print(f"{'='*136}")
    h = (f"  {'전략':>16}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'tx':>7}"
         f"{'COVID':>8}{'Bear22':>8}")
    print(h)
    print("  " + "─" * (len(h) + 2))
    for label, r in df.iterrows():
        mark = " ◀현행" if label.startswith("×1.00") else ""
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {label:>16}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}"
              f"{r['tx']:>7.2%}{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")
    return df


if __name__ == "__main__":
    main()
