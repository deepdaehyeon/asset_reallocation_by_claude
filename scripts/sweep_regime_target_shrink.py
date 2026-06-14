"""
실험: 레짐→포지션 매핑 shrink 스윕 — 오버피팅 무게중심을 강하게 당기면?

질문(2026-06-14): 비판 리뷰가 "오버피팅 무게중심은 레짐→포지션 매핑"이라 지적, 강하게 shrink
  처방. 이는 제 ablation([[experiment_2026-06-14_ablation_regime_stack]]: 가치 절반이 순수 분산)·
  메모리([[feedback-regime-targets-no-tuning]]: per-regime 튜닝은 노이즈)·#1(늦은 식별기)이
  전부 가리키던 지점. 레짐별 목표를 공통 centroid로 당겨(shrink) 4지표·회전율이 어떻게 되나?

shrink 정의:
  target'_r = (1-λ)·target_r + λ·centroid,  centroid = mean_r(target_r)  (5개 레짐 등가중)
  λ=0 현행(풀 스위칭), λ=1 전 레짐 동일(배분 스위칭 0). 각 레짐 정규화 후 적용.

핵심 가설(Pareto 개선 탐색): 매핑 압축 → blend 확률이 흔들려도 비중 변화폭↓ → 회전↓ + 오버핏↓.
  중간 λ가 Martin은 λ=0(현행)에 가깝게 유지하면서 회전(tx)을 깎으면 = 즉효 개선.

교란(규칙5 고지):
  - core30·vol ON 유지(라이브 그대로). shrink는 위성 70% 매핑만 압축. λ=1도 코어 30% Goldilocks 고정.
  - vol 타겟 티어는 여전히 레짐별 전환(Crisis 0.06~G 0.13). λ=1 = "배분 스위칭만 0, vol방어·분산·코어 생존".
    → 질문이 깨끗해짐: "vol방어+분산이 있는데 배분 매핑 스위칭이 추가로 필요한가?"
  - hmm ON 유지(blend가 압축된 목표를 가중). USD단일·in-sample.
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
from compare_rule_timing_ab import START, END, REBAL_FREQ, TX_COST, crisis_maxdd  # noqa: E402

CRISIS_WINDOWS = {"COVID 2020": ("2020-02-19", "2020-04-30"),
                  "Bear 2022": ("2022-01-01", "2022-12-31")}
CORE_REGIMES = ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis"]


def compute_centroid(regime_targets):
    """5개 레짐 목표의 등가중 평균(매핑 centroid)."""
    classes = set()
    for r in CORE_REGIMES:
        classes |= set(regime_targets[r].keys())
    cen = {}
    for c in classes:
        cen[c] = sum(regime_targets[r].get(c, 0.0) for r in CORE_REGIMES) / len(CORE_REGIMES)
    return cen


def shrink_targets(base_targets, lam, centroid):
    """각 레짐 목표를 centroid로 λ만큼 당김 + 정규화. Transition은 원본 유지."""
    out = {}
    for r, t in base_targets.items():
        if r not in CORE_REGIMES:
            out[r] = dict(t)
            continue
        classes = set(t.keys()) | set(centroid.keys())
        sh = {c: (1 - lam) * t.get(c, 0.0) + lam * centroid.get(c, 0.0) for c in classes}
        tot = sum(sh.values())
        out[r] = {c: v / tot for c, v in sh.items()} if tot > 0 else dict(t)
    return out


def run_lambda(lam, base, centroid, universe_px, signal_px, fred_history):
    cfg = copy.deepcopy(base)
    cfg["regime_targets"] = shrink_targets(base["regime_targets"], lam, centroid)
    rb = cfg.get("rebalancing", {})
    eng = BacktestEngine(
        config=cfg, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=float(rb.get("drift_threshold", 0.015)),
        cooldown_days=int(rb.get("min_rebalance_interval_days", 0)),
        fred_history=fred_history,
    )
    res = eng.run()
    r = res["returns"].dropna()
    m = compute_metrics(r)
    rc3 = rolling_cagr(r, years=3.0)
    rc5 = rolling_cagr(r, years=5.0)
    rec = recovery_duration(r)
    return {
        "λ": lam,
        "r3w": rc3["worst"], "r3m": rc3["median"], "r5w": rc5["worst"],
        "Ulcer": m.get("ulcer", 0.0),
        "uw_max": rec["max_underwater_days"],
        "Martin": m.get("martin", 0.0),
        "CAGR": m.get("cagr", 0.0), "MaxDD": m.get("max_drawdown", 0.0),
        "tx": float(res["tx_cost"].sum()),
        "COVID": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["COVID 2020"]),
        "Bear22": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["Bear 2022"]),
    }


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]... (core30·vol ON, 매핑만 shrink)")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    centroid = compute_centroid(base["regime_targets"])
    print("  centroid(5레짐 등가중 평균) 상위:")
    for c, v in sorted(centroid.items(), key=lambda x: -x[1])[:8]:
        print(f"    {c:>18} {v:6.1%}")

    lambdas = [0.0, 0.25, 0.5, 0.75, 1.0]
    print(f"\nshrink λ 스윕 {lambdas} 실행 중...")
    rows = []
    for lam in lambdas:
        print(f"  [λ={lam}]")
        rows.append(run_lambda(lam, base, centroid, universe_px, signal_px, fred_history))
    df = pd.DataFrame(rows).set_index("λ")

    print(f"\n{'='*124}")
    print("  레짐→포지션 매핑 shrink 스윕 — λ=0 현행 / λ=1 배분 스위칭 0 (core30·vol·분산은 생존)")
    print(f"{'='*124}")
    h = (f"  {'λ':>5}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'tx':>8}{'COVID':>8}{'Bear22':>8}")
    print(h)
    print("  " + "─" * (len(h)))
    for lam, r in df.iterrows():
        mark = " ◀현행" if lam == 0.0 else ""
        print(f"  {lam:>5.2f}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}"
              f"{r['tx']:>8.2%}{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")

    # 회전 절감 vs Martin 손실
    m0, tx0 = df.loc[0.0, "Martin"], df.loc[0.0, "tx"]
    print("\n  λ별 Martin/회전 변화 (현행 λ=0 대비):")
    for lam, r in df.iterrows():
        if lam == 0.0:
            continue
        dm = r["Martin"] - m0
        dtx = (r["tx"] - tx0) / tx0 if tx0 else 0.0
        print(f"    λ={lam}: ΔMartin {dm:+.2f}  회전 {dtx:+.0%}")

    print("\n  판정(규칙4): Martin 거의 안 떨어지며 회전(tx)을 크게 깎는 λ = Pareto 개선(채택 후보).")
    print("              Martin이 λ↑에 단조 하락하면 매핑은 오버핏 아닌 실가치 → 현행 유지.")
    print("  주의: λ=1도 vol티어·core30·분산 생존(순수 정적 아님). USD단일·in-sample.")
    return df


if __name__ == "__main__":
    main()
