"""
Rule-only(룰 레짐만) vs 현행 동적(HMM 블렌드) vs 정적 50/50 — 4지표 + 회전율.

질문(2026-06-17, 사용자): HMM이 매일 다른 확률을 뱉어 비중이 매일 흔들리고 회전율이 큼.
  '룰 레짐만' 쓰면(HMM 끔) 전환이 드물어 회전이 줄 텐데, 성능은 정적과 동적의 *중간* 아닐까?

설계(같은 엔진·기간 2010~2025·drift·tx·USD단일·4지표):
  S_raw : 정적 50/50 (Goldilocks+Slowdown 평균 고정, 스위칭·HMM·vol·core 전부 off) — 정적 바닥
  R     : 룰온리 (hmm.enabled=False → 블렌드가 룰 레짐 원-핫. 연속믹스·EWMA평활 없음.
          룰이 바뀔 때만 이산 전환. vol·core는 현행과 동일하게 ON) — 신호만 HMM→룰
  C     : 현행 동적 (HMM0.6+RF0.4 블렌드 + α=0.5 평활 + vol + core) — 라이브 그대로

핵심 비교:
  R vs C  = 'HMM 연속 블렌드'가 '안정적 룰 이산스위칭'보다 4지표/회전에서 값을 하는가.
  S_raw vs R vs C = 사용자 가설(R이 정적과 동적의 중간) 검증.
회전율(tx, 기간 총 거래비용)을 같이 본다 — 룰온리의 핵심 기대효과가 회전 감소이므로.

한계: 단일경로(COVID·Bear22 각1회)·USD단일·라이브 합성/실회전 미반영(C·R 모두 백테스트는
  목표 흔들림을 리밸일에만 갱신 → 라이브 일일 회전 과소추정. 상대비교 용도).
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
from ablation_regime_stack import build_engine, metrics_row, make_static_config  # noqa: E402
from static_goldi_slowdown_5050 import blend_5050  # noqa: E402
from compare_rule_timing_ab import START, END  # noqa: E402


def make_rule_only_config(base):
    """현행 config에서 HMM만 끔 → 블렌드가 룰 레짐 원-핫(이산 스위칭).
    regime_targets·vol_targeting·core_satellite는 현행 그대로 유지."""
    cfg = copy.deepcopy(base)
    cfg.setdefault("hmm", {})["enabled"] = False
    return cfg


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]...", flush=True)
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    mix = blend_5050(base)
    rows = []

    print("[S_raw 정적 50/50] 실행 중...", flush=True)
    cfg_s = make_static_config(base, mix, vol_on=False, core_on=False)
    rows.append(metrics_row("S_raw 정적50/50", build_engine(cfg_s, universe_px, signal_px, fred_history).run()))

    print("[R 룰온리 +vol+core] 실행 중...", flush=True)
    cfg_r = make_rule_only_config(base)
    rows.append(metrics_row("R 룰온리", build_engine(cfg_r, universe_px, signal_px, fred_history).run()))

    print("[C 현행 동적] 실행 중...", flush=True)
    rows.append(metrics_row("C. 현행 동적", build_engine(base, universe_px, signal_px, fred_history).run()))

    df = pd.DataFrame(rows).set_index("전략")

    print(f"\n{'='*132}")
    print("  Rule-only vs 현행 동적 vs 정적 50/50 — 4지표+회전 (drift·tx·USD단일·2010~2025)")
    print(f"{'='*132}")
    h = (f"  {'전략':>16}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'tx':>7}"
         f"{'COVID':>8}{'Bear22':>8}")
    print(h)
    print("  " + "─" * (len(h) + 2))
    for label, r in df.iterrows():
        mark = " ◀현행" if "현행" in label else (" ◀후보" if "룰온리" in label else "")
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {label:>16}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}"
              f"{r['tx']:>7.2%}{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")

    print("\n  판정: R이 S_raw~C 사이면 가설 성립. R의 tx가 C보다 크게 낮으면서 4지표 비등하면")
    print("        '룰온리'가 회전 대비 값어치. 단일경로·in-sample·라이브 회전 과소추정 주의.")
    return df


if __name__ == "__main__":
    main()
