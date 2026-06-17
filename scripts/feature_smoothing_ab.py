"""
빠른 노이즈 피처 5일 평활 ON/OFF — 4지표+회전 검증.

질문(2026-06-17): HMM 입력의 빠른 시장 피처(vix_term_structure·vix·credit_signal·
  momentum_1m·commodity_mom_1m·dxy_mom_1m)를 5일 평균으로 평활하면 레짐 확률 일별 표류가
  줄어 회전이 감소할 텐데, 4지표(하락방어)는 손상되지 않는가? 같은 엔진·기간·drift·tx로 검증.

주의: 백테스트는 리밸일에만 _get_regime을 재계산하므로(가격 drift 트리거) 평활의 회전
  감소 효과가 라이브보다 작게 잡힌다(라이브는 매일 재계산 → 평활 효과가 큼). 여기서는
  주로 '평활이 레짐 품질·4지표를 해치지 않는가'를 본다. 회전 감소의 본 효과는 라이브.
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
    cfg_off.setdefault("feature_smoothing", {})["enabled"] = False
    cfg_on = copy.deepcopy(base)
    cfg_on.setdefault("feature_smoothing", {})["enabled"] = True

    rows = []
    print("[OFF 평활없음(구)] 실행 중...", flush=True)
    rows.append(metrics_row("OFF 평활없음", build_engine(cfg_off, universe_px, signal_px, fred_history).run()))
    print("[ON 5일평활(신)] 실행 중...", flush=True)
    rows.append(metrics_row("ON 5일평활", build_engine(cfg_on, universe_px, signal_px, fred_history).run()))

    df = pd.DataFrame(rows).set_index("전략")
    print(f"\n{'='*132}")
    print("  노이즈 피처 5일 평활 ON/OFF — 4지표+회전 (drift·tx·USD단일·2010~2025)")
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
