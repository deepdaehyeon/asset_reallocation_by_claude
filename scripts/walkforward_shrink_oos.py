"""
워크포워드 OOS 검증: 레짐 매핑 shrink가 미지 구간에서 도움 되는가? + 스택 vs 정적 분산.

질문(2026-06-14): in-sample shrink 스윕([[experiment_2026-06-14_regime_target_shrink]])은
  구조적으로 비압축(현행)에 유리해 오버핏을 판정 못 했다. 진짜 판정 = 워크포워드:
  학습창(2010~2018)에서 λ를 보고 검증창(2019~2025, COVID+Bear22 포함)에서 일반화 확인.
  동시에 비판 #1·#2 종합: 풀 시스템(λ=0)이 *미지 구간*에서 정적 분산(B)을 이기는가?

방법:
  - 각 config(λ별 풀시스템 + 정적 B/B2)를 2010~2025 1회 실행(HMM은 워크포워드로 매일 as_of까지만).
    config(regime_targets·λ)는 전 구간 고정 → 2019~2025 수익은 config 선택에 대한 진짜 OOS.
  - 수익 시계열을 TRAIN(≤2018)·TEST(≥2019)로 잘라 각각 4지표(Martin·Ulcer·회복·롤3y·CAGR·MaxDD).
  - 누수 방지: 정적 B/B2의 배분은 *수익 평균*이 아니라 config-유래 centroid(5레짐 등가중) — 수익 누수 0.

판정(규칙4):
  - TEST Martin이 λ=0에서 최대(λ↑에 단조↓)면 → 매핑 강건(오버핏 아님) → 현행 유지.
  - TEST Martin이 λ>0에서 peak면 → 매핑이 학습창에 오버핏 → 그 λ만큼 shrink 채택.
  - TEST에서 λ=0(풀스택) vs B(정적 분산) → 스택이 미지 구간에서 분산을 이기는지(비판 #1·#2 답).
"""
from __future__ import annotations

import copy
import sys
import warnings
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "scripts"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from data import load_all_prices  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
from metrics import compute_metrics, rolling_cagr, recovery_duration  # noqa: E402
from compare_rule_timing_ab import START, END, REBAL_FREQ, TX_COST  # noqa: E402
from sweep_regime_target_shrink import compute_centroid, shrink_targets  # noqa: E402
from ablation_regime_stack import make_static_config  # noqa: E402

SPLIT = "2019-01-01"   # TRAIN < SPLIT <= TEST


def build_engine(cfg, universe_px, signal_px, fred_history):
    rb = cfg.get("rebalancing", {})
    return BacktestEngine(
        config=cfg, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=float(rb.get("drift_threshold", 0.015)),
        cooldown_days=int(rb.get("min_rebalance_interval_days", 0)),
        fred_history=fred_history,
    )


def slice_metrics(returns, lo, hi):
    r = returns.loc[lo:hi].dropna()
    m = compute_metrics(r)
    rc3 = rolling_cagr(r, years=3.0)
    rec = recovery_duration(r)
    return {
        "Martin": m.get("martin", 0.0), "CAGR": m.get("cagr", 0.0),
        "Ulcer": m.get("ulcer", 0.0), "MaxDD": m.get("max_drawdown", 0.0),
        "uw_max": rec["max_underwater_days"], "r3w": rc3["worst"],
    }


def run_config(cfg, universe_px, signal_px, fred_history):
    res = build_engine(cfg, universe_px, signal_px, fred_history).run()
    ret = res["returns"]
    train = slice_metrics(ret, START, "2018-12-31")
    test = slice_metrics(ret, SPLIT, END)
    train["tx"] = float(res["tx_cost"].loc[START:"2018-12-31"].sum())
    test["tx"] = float(res["tx_cost"].loc[SPLIT:END].sum())
    return train, test


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}], split={SPLIT}...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)
    centroid = compute_centroid(base["regime_targets"])

    runs = []  # (label, cfg)
    for lam in [0.0, 0.25, 0.5, 0.75, 1.0]:
        cfg = copy.deepcopy(base)
        cfg["regime_targets"] = shrink_targets(base["regime_targets"], lam, centroid)
        runs.append((f"λ={lam:.2f}" + (" (현행)" if lam == 0 else ""), cfg))
    # 정적 분산(누수 없는 config-유래 centroid)
    runs.append(("B 정적(분산)", make_static_config(base, centroid, vol_on=False, core_on=False)))
    runs.append(("B2 정적+vol", make_static_config(base, centroid, vol_on=True, core_on=False, vol_target=0.10)))

    print("\n실행 중 (각 config 2010~2025 1회, 수익 슬라이스)...")
    train_rows, test_rows = {}, {}
    for label, cfg in runs:
        print(f"  [{label}]")
        tr, te = run_config(cfg, universe_px, signal_px, fred_history)
        train_rows[label] = tr
        test_rows[label] = te

    def print_table(title, rows):
        print(f"\n{'='*104}")
        print(f"  {title}")
        print(f"{'='*104}")
        h = (f"  {'전략':>16}{'Martin':>9}{'CAGR':>9}{'Ulcer':>8}{'최장UW':>8}"
             f"{'롤3y최악':>10}{'MaxDD':>9}{'tx':>8}")
        print(h)
        print("  " + "─" * (len(h)))
        best = max(rows, key=lambda k: rows[k]["Martin"])
        for label, r in rows.items():
            mark = " ◀Martin최고" if label == best else ""
            print(f"  {label:>16}{r['Martin']:>9.2f}{r['CAGR']:>9.1%}{r['Ulcer']:>8.2f}"
                  f"{int(r['uw_max']):>8}{r['r3w']:>10.1%}{r['MaxDD']:>9.1%}{r['tx']:>8.2%}{mark}")
        return best

    best_tr = print_table("학습창 TRAIN 2010-01 ~ 2018-12 (in-sample)", train_rows)
    best_te = print_table(f"검증창 TEST {SPLIT[:7]} ~ {END} (OUT-OF-SAMPLE, COVID+Bear22 포함)", test_rows)

    print("\n  판정:")
    print(f"  • 학습창 Martin 최고: {best_tr}")
    print(f"  • 검증창 Martin 최고: {best_te}  ← 결정적")
    print("  • 검증창 최고가 λ=0(현행)이면 매핑 강건(오버핏 아님). λ>0이면 shrink 채택. B면 스택 무가치.")
    print("  주의: TEST 6.3년 단일 경로(COVID·Bear22 각 1회). 롤5y 표본부족이라 롤3y만. USD단일.")


if __name__ == "__main__":
    main()
