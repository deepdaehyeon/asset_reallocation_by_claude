"""
vol targeting 반응속도(ewma_lambda) 스윕 — 엔드투엔드 워크포워드 OOS 4지표.

질문(2026-06-16, 사용자): 비중 조정이 안 먹히는 이유는 엔진 스택 맨 아래의 vol targeting이
  신호와 무관하게 실제 노출을 재계산해 흡수하기 때문([[project-voltarget-blend-defense-engine]]).
  그럼 진짜 레버는 vol targeting의 손잡이다. 그중 '반응속도'(ewma_lambda)를 빠르게 하면 —
  검증된 유일한 개선축이 '진입 속도'였으므로([[feedback-regime-timing-lever]]) — 위험 진입 래그를
  줄여 4지표(특히 Ulcer·Martin·회복)가 개선되나? 연속적 디리스킹이라 레짐 깜빡임 whipsaw와
  결이 달라 blend 흡수([[feedback-agility-over-turnover-reduction]])를 피할 수 있나?

  lam 낮을수록 반응 빠름. 반감기 hl=ln0.5/ln(lam):
    0.97≈23d(느림) / 0.94≈11d(현행) / 0.90≈6.6d / 0.85≈4.3d / 0.80≈3.1d(빠름)

방법:
  - 라이브 config 전부 고정(core30 on·drift·drawdown_scaling off) — vol_targeting.ewma_lambda만 교체.
  - 각 lam에 대해 run_config로 2010~2025 1회(config 고정→2019+ 진짜 OOS), TRAIN/TEST 4지표.
  - 현행(0.94) 대조군 Δ표기.

한계: ewma_lambda는 use_portfolio_vol=true 경로에서만 작동(현행 true). 단일통화(USD) 백테스트·
  단일경로(COVID·Bear22 각1회). 라이브 반영은 결과 확인 후 사용자 결정.
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
from fetcher import fetch_fred_history  # noqa: E402
from walkforward_shrink_oos import run_config, SPLIT  # noqa: E402
from compare_rule_timing_ab import START, END  # noqa: E402

import math

LAMBDAS = [0.97, 0.94, 0.90, 0.85, 0.80]
CURRENT = 0.94


def hl(lam):
    return math.log(0.5) / math.log(lam)


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)

    if not base.get("vol_targeting", {}).get("use_portfolio_vol", True):
        print("[경고] use_portfolio_vol=false — ewma_lambda 미작동. 중단.")
        return

    print(f"데이터 로딩 [{START} ~ {END}], split={SPLIT}...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    train_rows, test_rows = {}, {}
    print("\n실행 중 (각 lam config 2010~2025 1회)...")
    for lam in LAMBDAS:
        cfg = copy.deepcopy(base)
        cfg["vol_targeting"]["ewma_lambda"] = lam
        label = f"λ={lam} (hl≈{hl(lam):.0f}d)" + (" 현행" if lam == CURRENT else "")
        print(f"  [{label}] 실행...")
        tr, te = run_config(cfg, universe_px, signal_px, fred_history)
        train_rows[label] = tr
        test_rows[label] = te

    def cur_key(rows):
        return next(k for k in rows if k.startswith(f"λ={CURRENT}"))

    def table(title, rows):
        print(f"\n{'='*94}\n  {title}\n{'='*94}")
        h = (f"  {'설정':>18}{'Martin':>9}{'CAGR':>9}{'Ulcer':>8}{'최장UW':>8}"
             f"{'롤3y최악':>10}{'MaxDD':>9}{'tx':>8}")
        print(h); print("  " + "─" * (len(h)))
        bm = rows[cur_key(rows)]["Martin"]
        for label, r in rows.items():
            d = r["Martin"] - bm
            mark = "" if label.endswith("현행") else f"  Δ{d:+.2f}"
            print(f"  {label:>18}{r['Martin']:>9.2f}{r['CAGR']:>9.1%}{r['Ulcer']:>8.2f}"
                  f"{int(r['uw_max']):>8}{r['r3w']:>10.1%}{r['MaxDD']:>9.1%}{r['tx']:>8.2%}{mark}")

    table("학습창 TRAIN 2010-01~2018-12 (in-sample)", train_rows)
    table(f"검증창 TEST {SPLIT[:7]}~{END} (OUT-OF-SAMPLE)", test_rows)

    print("\n  판정(규칙4): TEST Martin·Ulcer·회복기간이 빠른 λ(낮음)에서 개선되면 반응속도가 레버.")
    print("  단조 악화/무변동이면 vol 반응속도는 흡수(현행 유지). MaxDD 깊이 방어와의 트레이드오프 관찰.")
    print("  주의: 단일통화·단일경로(COVID·Bear22 각1회). 라이브 반영은 확인 후.")


if __name__ == "__main__":
    main()
