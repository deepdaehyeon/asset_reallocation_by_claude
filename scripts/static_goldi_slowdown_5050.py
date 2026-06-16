"""
정적 골디락스+슬로우다운 50/50 vs 현행 동적 시스템 — 4지표 비교.

질문(2026-06-17, 사용자): 빈도 높은 두 레짐(Goldilocks·Slowdown) 비중을 반반 고정한
  정적 포트폴리오가 현행 동적(레짐 스위칭+blend+vol+core) 대비 4지표에서 어떤가?
  배경: HMM이 매일 레짐을 바꿔 회전율이 큼. 레짐이 중장기 지속이 상식인데
  블렌딩으로 겨우 회전을 누르는 구조. 정적이 비등하면 회전 비용이 정당화 안 됨.

설계:
  - 50/50 블렌드 = (regime_targets["Goldilocks"] + regime_targets["Slowdown"]) / 2.
  - 세 전략 같은 엔진·기간(2010~2025)·drift·tx·4지표:
      C  현행 동적(풀 시스템)
      S_raw  50/50 정적 (vol off, core off, hmm off) — 순수 고정비중 드리프트 리밸
      S_vol  50/50 정적 + 레짐무관 vol타겟(단일 0.10) — 방어엔진만 유지, 스위칭 제거
  - core_satellite는 정적 앵커라 끔(켜면 30%를 Goldilocks로 고정해 50/50 오염).

판정(규칙4): S가 C에 4지표 비등하면서 회전율(tx) 훨씬 낮으면 → 회전 비용 정당화 약함.
  C가 Martin·회복에서 유의 우위면 → 스위칭이 그만큼 값을 함.
한계: 단일경로(COVID·Bear22 각1회)·USD단일·in-sample(정적은 오버핏 없음, C는 워크포워드 HMM).
"""
from __future__ import annotations

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
from ablation_regime_stack import (  # noqa: E402
    build_engine, metrics_row, make_static_config,
)
from compare_rule_timing_ab import START, END  # noqa: E402


def blend_5050(base):
    g = base["regime_targets"]["Goldilocks"]
    s = base["regime_targets"]["Slowdown"]
    keys = set(g) | set(s)
    avg = {k: (g.get(k, 0.0) + s.get(k, 0.0)) / 2 for k in keys}
    tot = sum(avg.values())
    return {k: v / tot for k, v in avg.items() if v > 0}


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    mix = blend_5050(base)
    print("\n  50/50 (Goldilocks+Slowdown) 자산군 비중:")
    for c, v in sorted(mix.items(), key=lambda x: -x[1]):
        if v >= 0.005:
            print(f"    {c:>18} {v:6.1%}")
    eq = sum(v for c, v in mix.items() if c.startswith("equity"))
    print(f"    {'(주식계열 합)':>18} {eq:6.1%}")

    rows = []
    print("\n[C 현행 동적] 실행 중...")
    rows.append(metrics_row("C. 현행 동적", build_engine(base, universe_px, signal_px, fred_history).run()))

    print("[S_raw 50/50 정적] 실행 중...")
    cfg_raw = make_static_config(base, mix, vol_on=False, core_on=False)
    rows.append(metrics_row("S_raw 50/50정적", build_engine(cfg_raw, universe_px, signal_px, fred_history).run()))

    print("[S_vol 50/50 정적+vol] 실행 중...")
    cfg_vol = make_static_config(base, mix, vol_on=True, core_on=False, vol_target=0.10)
    rows.append(metrics_row("S_vol 50/50+vol", build_engine(cfg_vol, universe_px, signal_px, fred_history).run()))

    df = pd.DataFrame(rows).set_index("전략")

    print(f"\n{'='*132}")
    print("  정적 50/50(Goldi+Slow) vs 현행 동적 — 4지표 (drift·tx·USD단일·2010~2025)")
    print(f"{'='*132}")
    h = (f"  {'전략':>16}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'tx':>7}"
         f"{'COVID':>8}{'Bear22':>8}")
    print(h)
    print("  " + "─" * (len(h) + 2))
    for label, r in df.iterrows():
        mark = " ◀현행" if "현행" in label else ""
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {label:>16}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}"
              f"{r['tx']:>7.2%}{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")

    print("\n  판정(규칙4): S가 C에 Martin·Ulcer·회복 비등 + tx 훨씬 낮으면 회전 비용 정당화 약함.")
    print("              C가 Martin·회복 유의 우위면 스위칭이 값을 함. 단일경로·in-sample 주의.")
    return df


if __name__ == "__main__":
    main()
