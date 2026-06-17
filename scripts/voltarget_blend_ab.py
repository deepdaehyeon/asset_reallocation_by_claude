"""
vol targeting 목표변동성: 단일 레짐 단계(현행) vs 확률블렌드 가중평균 A/B.

질문(2026-06-17): vol targeting의 목표변동성을 지금처럼 확정 레짐 하나로 고르지 말고,
  비중과 똑같이 blend 확률로 가중평균(연속 단계)하면 어떤가? target_vol = Σ p[r]·vol[r].

트레이드오프(시작 전 합의): ① 확정 레짐(룰)의 빠른 위험진입(서브기간 6/6 검증,
  [[feedback-regime-timing-lever]]) 속도가 둔해질 수 있음. ② HMM 블렌드 흔들림이 vol 강도로
  유입(5일 평활로 완화됨). 라이브 블렌드 평균이 더 방어적(target_vol 0.13→~0.11)이라 상시 축소↑.
  ∴ 가정 말고 A/B로 검증. 범위(사용자): 백테스트만, 라이브 미적용(config 토글 기본 OFF).

설정: 현행 라이브 config 그대로(core30 ON·평활 ON·drift·tx·USD단일·2010~2025).
  OFF=blend_target_vol false(룰 단계), ON=true(확률블렌드). 그 외 전부 동일.
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


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]...", flush=True)
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    cfg_off = copy.deepcopy(base)
    cfg_off.setdefault("vol_targeting", {})["blend_target_vol"] = False
    cfg_on = copy.deepcopy(base)
    cfg_on.setdefault("vol_targeting", {})["blend_target_vol"] = True

    rows = []
    print("[OFF 룰단계(현행)] 실행 중...", flush=True)
    rows.append(metrics_row("OFF 룰단계", build_engine(cfg_off, universe_px, signal_px, fred_history).run()))
    print("[ON 확률블렌드] 실행 중...", flush=True)
    rows.append(metrics_row("ON 확률블렌드", build_engine(cfg_on, universe_px, signal_px, fred_history).run()))

    df = pd.DataFrame(rows).set_index("전략")
    print(f"\n{'='*132}")
    print("  vol targeting 목표변동성: 룰단계(현행) vs 확률블렌드 — core30·평활 ON·drift·tx·2010~2025")
    print(f"{'='*132}")
    h = (f"  {'전략':>14}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'tx':>7}"
         f"{'COVID':>8}{'Bear22':>8}")
    print(h)
    print("  " + "─" * (len(h) + 2))
    for label, r in df.iterrows():
        mark = " ◀신" if "ON" in label else ""
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {label:>14}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}"
              f"{r['tx']:>7.2%}{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")
    return df


if __name__ == "__main__":
    main()
