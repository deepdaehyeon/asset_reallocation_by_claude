"""
회전율 현실화 검증: 거래비용·라이브 마찰 뒤에도 레짐 스택(C)이 정적(B/B2)을 이기는가?

질문(2026-06-14): ablation([[experiment_2026-06-14_ablation_regime_stack]])에서 C가 정적+vol(B2)을
  Martin 2.54 vs 1.68로 이겼으나, C의 우위는 tx 3.14%(B의 4.5배)로 산 것이고 라이브 회전은
  백테스트의 ~10배([[project-live-turnover-vs-backtest-gap]]). C의 한계 우위(B2→C +0.86 Martin)가
  실거래 마찰 뒤에도 남는가, 아니면 거래비용에 먹히는가?

방법:
  ① 대칭 tx 스윕 — 편도 거래비용을 0.1%→1.0%로 올리며 4단(A/B/B2/C)의 Martin 역전점 탐색.
     엔진은 turnover×tx_cost로 차감하므로 회전 높은 C가 비용 상승에 불리(자동 차등).
  ② 비대칭 현실 시나리오 — 라이브에선 C만 blend 미세거래로 회전 ~10배, 정적 B/B2는 drift만이라
     거의 안 늘어남. 비용은 linear in turnover이므로 tx_cost×10 == 거래수×10. 그래서
     B@0.1%·B2@0.3%·C@1.0%(10배)로 "같은 단가, C만 10배 거래"를 근사.

판정(규칙4): C의 Martin이 어느 tx에서 B2·B 아래로 떨어지는지. 비대칭 현실점에서 C가 지면
  스택의 한계 우위가 라이브 마찰에 증발 → 정적 분산이 실거래상 더 합리적일 수 있음.
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
from compare_rule_timing_ab import START, END, REBAL_FREQ  # noqa: E402
from ablation_regime_stack import (  # noqa: E402
    run_full_and_capture_avg, make_static_config, REGIMES,
)


def build_engine(config, universe_px, signal_px, fred_history, tx):
    rb = config.get("rebalancing", {})
    return BacktestEngine(
        config=config, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=tx,
        drift_threshold=float(rb.get("drift_threshold", 0.015)),
        cooldown_days=int(rb.get("min_rebalance_interval_days", 0)),
        fred_history=fred_history,
    )


def run_metrics(config, universe_px, signal_px, fred_history, tx):
    res = build_engine(config, universe_px, signal_px, fred_history, tx).run()
    r = res["returns"].dropna()
    m = compute_metrics(r)
    rec = recovery_duration(r)
    return {
        "Martin": m.get("martin", 0.0), "CAGR": m.get("cagr", 0.0),
        "Ulcer": m.get("ulcer", 0.0), "MaxDD": m.get("max_drawdown", 0.0),
        "uw_max": rec["max_underwater_days"], "tx": float(res["tx_cost"].sum()),
    }


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    # STATIC_AVG 1회 산출 (C 풀 시스템 평균배분)
    print("STATIC_AVG 산출(C 풀 시스템)...")
    _, static_avg = run_full_and_capture_avg(base, universe_px, signal_px, fred_history)

    # 각 단 config
    cfg_b = make_static_config(base, static_avg, vol_on=False, core_on=False)
    cfg_b2 = make_static_config(base, static_avg, vol_on=True, core_on=False, vol_target=0.10)
    rungs = {"B. 정적평균": cfg_b, "B2. 정적+vol": cfg_b2, "C. 풀시스템": base}

    # ① 대칭 tx 스윕
    tx_levels = [0.001, 0.002, 0.003, 0.005, 0.0075, 0.010]
    print(f"\n① 대칭 tx 스윕 {[f'{t:.1%}' for t in tx_levels]} 실행 중...")
    mart = {name: {} for name in rungs}
    cagr = {name: {} for name in rungs}
    for name, cfg in rungs.items():
        for tx in tx_levels:
            r = run_metrics(cfg, universe_px, signal_px, fred_history, tx)
            mart[name][tx] = r["Martin"]
            cagr[name][tx] = r["CAGR"]
        print(f"  [{name}] 완료")

    print(f"\n{'='*100}")
    print("  ① 대칭 거래비용 스윕 — Martin (모든 단에 같은 편도비용 적용)")
    print(f"{'='*100}")
    hdr = "  " + f"{'전략':>14}" + "".join(f"{f'{t:.2%}':>11}" for t in tx_levels)
    print(hdr)
    print("  " + "─" * (len(hdr)))
    for name in rungs:
        row = "  " + f"{name:>14}" + "".join(f"{mart[name][t]:>11.2f}" for t in tx_levels)
        print(row)
    print("\n  (참고) CAGR:")
    for name in rungs:
        row = "  " + f"{name:>14}" + "".join(f"{cagr[name][t]:>11.1%}" for t in tx_levels)
        print(row)

    # 역전점: C가 B2·B 아래로 떨어지는 첫 tx
    def crossover(loser, winner):
        for t in tx_levels:
            if mart[loser][t] < mart[winner][t]:
                return t
        return None
    cb2 = crossover("C. 풀시스템", "B2. 정적+vol")
    cb = crossover("C. 풀시스템", "B. 정적평균")
    print(f"\n  C가 B2 아래로 떨어지는 tx: {f'{cb2:.2%}' if cb2 else '없음(1.0%까지 C 우위)'}")
    print(f"  C가 B  아래로 떨어지는 tx: {f'{cb:.2%}' if cb else '없음(1.0%까지 C 우위)'}")

    # ② 비대칭 현실 시나리오: B@0.1%, B2@0.3%, C@1.0%(10배)
    print(f"\n{'='*100}")
    print("  ② 비대칭 현실 시나리오 — 라이브 회전 차등(C만 ~10배, 정적은 drift만)")
    print(f"{'='*100}")
    scen = {"B. 정적평균": (cfg_b, 0.001), "B2. 정적+vol": (cfg_b2, 0.003), "C. 풀시스템": (base, 0.010)}
    print(f"  {'전략':>14}{'편도tx':>9}{'Martin':>9}{'CAGR':>9}{'Ulcer':>8}{'MaxDD':>9}{'최장UW':>8}{'누적tx':>9}")
    print("  " + "─" * 74)
    arows = {}
    for name, (cfg, tx) in scen.items():
        r = run_metrics(cfg, universe_px, signal_px, fred_history, tx)
        arows[name] = r
        print(f"  {name:>14}{tx:>9.2%}{r['Martin']:>9.2f}{r['CAGR']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{r['MaxDD']:>9.1%}{int(r['uw_max']):>8}{r['tx']:>9.1%}")

    c_m = arows["C. 풀시스템"]["Martin"]
    print(f"\n  판정: 비대칭 현실점에서 C Martin {c_m:.2f} vs "
          f"B2 {arows['B2. 정적+vol']['Martin']:.2f} / B {arows['B. 정적평균']['Martin']:.2f}")
    print("  주의: 라이브 10배는 근사(tx_cost×10=거래수×10). 단가·차등은 가정. in-sample·USD단일.")


if __name__ == "__main__":
    main()
