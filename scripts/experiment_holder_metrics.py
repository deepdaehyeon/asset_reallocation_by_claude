"""
실험: 장기보유자(3~5년) 관점 지표로 세 전략 재평가.

배경(2026-06-12): 사용자가 "계좌에 돈 넣고 3~5년 안 뺀다"는 장기보유자라
    MaxDD(한 순간 최저점)보다 ① 보유기간 동안 실제로 받았을 연환산 수익 분포(롤링 CAGR)
    ② 물려있는 고통의 깊이+지속(Ulcer) ③ 물려있는 시간(recovery duration)이 더 와닿는다.
    → metrics를 이 셋 중심으로 보기로 함.

세 전략(experiment_static_goldilocks_hold.py와 동일):
  1) baseline(현행): 레짐 스위칭 + 블렌드 + vol타겟 + 캡
  2) 골디락스정적+vol: Goldilocks 고정, vol타겟·캡 유지
  3) 골디락스순수보유: Goldilocks 고정 + vol타겟 OFF (진짜 홀드)

코드 변경 없음(metrics.py에 지표 추가만). 결과는 docs/에 저장.
"""
from __future__ import annotations

import copy
import sys
import warnings
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "scripts"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from data import load_all_prices  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
from metrics import (  # noqa: E402
    compute_metrics,
    rolling_cagr,
    recovery_duration,
)
from prototype_forward_regime_predictability import run_engine  # noqa: E402
from compare_rule_timing_ab import START, END  # noqa: E402

FIXED = "Goldilocks"


class StaticGoldilocksEngine(BacktestEngine):
    """레짐을 항상 Goldilocks로 고정 (blend_probs={Goldilocks:1})."""
    def _get_regime(self, *a, **k):
        return (FIXED, {FIXED: 1.0}, FIXED, 1.0, 1.0, 1.0)


def holder_row(label, res):
    r = res["returns"].dropna()
    m = compute_metrics(r)
    rc3 = rolling_cagr(r, years=3.0)
    rc5 = rolling_cagr(r, years=5.0)
    rd = recovery_duration(r)
    return {
        "전략": label,
        "CAGR": m.get("cagr", 0.0),
        "Ulcer": m.get("ulcer", 0.0),
        "Martin": m.get("martin", 0.0),
        "MaxDD": m.get("max_drawdown", 0.0),
        "r3_worst": rc3["worst"], "r3_med": rc3["median"], "r3_neg": rc3["pct_negative"],
        "r5_worst": rc5["worst"], "r5_med": rc5["median"], "r5_neg": rc5["pct_negative"],
        "max_uw": rd["max_underwater_days"],
        "maxdd_rec": rd["maxdd_recovery_days"],
        "cur_uw": rd["currently_underwater_days"],
    }


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    no_vol = copy.deepcopy(base)
    no_vol.setdefault("vol_targeting", {})["enabled"] = False

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    print("baseline(현행) 백테스트...")
    rows = [holder_row("baseline(현행)", run_engine(BacktestEngine, universe_px, signal_px, fred_history, base))]
    print("골디락스정적+vol 백테스트...")
    rows.append(holder_row("골디락스정적+vol", run_engine(StaticGoldilocksEngine, universe_px, signal_px, fred_history, base)))
    print("골디락스순수보유(vol OFF) 백테스트...")
    rows.append(holder_row("골디락스순수보유", run_engine(StaticGoldilocksEngine, universe_px, signal_px, fred_history, no_vol)))

    print(f"\n{'='*100}\n  장기보유자 관점 지표 — 세 전략 ({START}~{END})\n{'='*100}")

    print("\n[1] 롤링 CAGR — 진입시점 무관하게 보유기간 동안 받았을 연환산 수익 분포")
    h = f"  {'전략':>16}{'3y최악':>9}{'3y중앙':>9}{'3y음수%':>9}{'5y최악':>9}{'5y중앙':>9}{'5y음수%':>9}"
    print(h)
    print("  " + "─" * (len(h) + 2))
    for r in rows:
        print(f"  {r['전략']:>16}{r['r3_worst']:>9.1%}{r['r3_med']:>8.1%}{r['r3_neg']:>9.1%}"
              f"{r['r5_worst']:>9.1%}{r['r5_med']:>8.1%}{r['r5_neg']:>9.1%}")

    print("\n[2] Ulcer / Martin — 낙폭 깊이+지속(낮을수록↓ 좋음) / 초과수익÷Ulcer(높을수록↑ 좋음)")
    h2 = f"  {'전략':>16}{'CAGR':>8}{'Ulcer':>8}{'Martin':>8}{'MaxDD':>9}"
    print(h2)
    print("  " + "─" * (len(h2) + 2))
    for r in rows:
        print(f"  {r['전략']:>16}{r['CAGR']:>8.1%}{r['Ulcer']:>8.2f}{r['Martin']:>8.2f}{r['MaxDD']:>8.1%}")

    print("\n[3] Recovery duration — 직전 고점 아래 '물려있는' 달력일")
    h3 = f"  {'전략':>16}{'최장수중일':>11}{'MaxDD회복일':>12}{'현재수중일':>11}"
    print(h3)
    print("  " + "─" * (len(h3) + 2))
    for r in rows:
        rec = "미회복" if r['maxdd_rec'] < 0 else f"{r['maxdd_rec']}"
        print(f"  {r['전략']:>16}{r['max_uw']:>11}{rec:>12}{r['cur_uw']:>11}")

    print("\n  주의: 백테스트는 USD 단일통화·단일 포트폴리오(라이브 합성 순환매 미반영).")


if __name__ == "__main__":
    main()
