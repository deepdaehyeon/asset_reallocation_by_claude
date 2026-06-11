"""
실험: 골디락스 정적 보유 vs 현행(레짐 스위칭+블렌드+vol타겟) — 사용자 가설 검증.

가설(2026-06-12): "골디락스로 포지션을 홀드했으면 지금처럼 순환매하는 것보다 수익이
    훨씬 좋았을 것이다."

검증 설계 — 세 전략을 같은 엔진·기간으로 비교:
  1) baseline(현행): 레짐 스위칭 + 블렌드 평활 + vol타겟 + 캡 (전체 방어 스택)
  2) 골디락스정적+vol타겟: 레짐을 항상 Goldilocks로 고정(blend={Goldilocks:1}),
     단 vol타겟·캡은 유지 → "스위칭만 끈" 효과 격리
  3) 골디락스순수보유: Goldilocks 고정 + vol타겟 OFF → 사용자가 말한 진짜 "홀드"

주의(중요):
  - 백테스트는 단일 깨끗한 포트폴리오라, 라이브의 USD합성 순환매(운영 아티팩트)는
    재현하지 않는다. 여기서 측정되는 '리밸/tx'는 레짐 스위칭+drift 리밸런싱이지
    합성 출렁임이 아니다. 즉 이 실험은 *전략적* 스위칭 vs 홀드의 트레이드오프를 본다.
  - 가설의 핵심은 '수익'이지만, 이 시스템의 본분은 하락 회피이므로 MaxDD·COVID·Bear22를
    반드시 함께 본다.

코드 변경 없음. 결과는 docs/에 저장.
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
from prototype_forward_regime_predictability import run_engine, bt_row  # noqa: E402
from compare_rule_timing_ab import START, END  # noqa: E402

FIXED = "Goldilocks"


class StaticGoldilocksEngine(BacktestEngine):
    """레짐을 항상 Goldilocks로 고정 (blend_probs={Goldilocks:1})."""
    def _get_regime(self, *a, **k):
        return (FIXED, {FIXED: 1.0}, FIXED, 1.0, 1.0, 1.0)


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    no_vol = copy.deepcopy(base)
    no_vol.setdefault("vol_targeting", {})["enabled"] = False

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    print("baseline(현행) 백테스트...")
    rows = [bt_row("baseline(현행)", run_engine(BacktestEngine, universe_px, signal_px, fred_history, base))]
    print("골디락스정적+vol타겟 백테스트...")
    rows.append(bt_row("골디락스정적+vol", run_engine(StaticGoldilocksEngine, universe_px, signal_px, fred_history, base)))
    print("골디락스순수보유(vol OFF) 백테스트...")
    rows.append(bt_row("골디락스순수보유", run_engine(StaticGoldilocksEngine, universe_px, signal_px, fred_history, no_vol)))

    print(f"\n{'='*98}\n  골디락스 정적 보유 vs 현행 — 가설 검증 ({START}~{END})\n{'='*98}")
    hdr = (f"  {'전략':>16}{'CAGR':>8}{'Sharpe':>8}{'MaxDD':>9}{'Calmar':>8}"
           f"{'리밸':>7}{'tx누적':>9}{'COVID':>9}{'Bear22':>9}")
    print(hdr)
    print("  " + "─" * (len(hdr) + 4))
    for r in rows:
        print(f"  {r['전략']:>16}{r['CAGR']:>8.1%}{r['Sharpe']:>8.2f}{r['MaxDD']:>8.1%}"
              f"{r['Calmar']:>8.2f}{int(r['리밸']):>7}{r['tx누적']:>8.2%}"
              f"{r['COVID']:>8.1%}{r['Bear22']:>8.1%}")

    print("\n  주의: 백테스트는 라이브 USD합성 순환매를 재현 안 함(단일 포트폴리오). 여기 '리밸/tx'는")
    print("        레짐 스위칭+drift 리밸런싱이다. CAGR뿐 아니라 MaxDD·COVID·Bear22를 함께 볼 것.")


if __name__ == "__main__":
    main()
