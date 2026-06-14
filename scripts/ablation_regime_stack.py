"""
Ablation 사다리: 레짐 스택은 "분산 + 일반 변동성관리"를 넘어 값을 하는가?

질문(2026-06-14, 사용자): 정교한 레짐 스택(rule+HMM+blend+per-regime target+core30)이
  단순한 "정적 분산 포트폴리오 + 일반 vol 타겟팅"보다 정말 더 버는가? 아니면 비싼 장식인가?
  비판 #2: 하락 방어의 실제 주역이 vol+blend+core(일반 위험관리)라면, 레짐 기계의 한계
  기여가 작을 수 있다. 같은 4지표·같은 경로로 바닥부터 쌓아 기여도를 분해해 확인한다.

방법: 모든 레짐 목표를 동일하게 만들면 엔진은 그대로 돌되 레짐 스위칭 효과가 0이 되어
  "정적" 포트폴리오가 된다. drift 리밸·tx비용·계좌분리·지표를 전부 같은 기계로 계산 → 공정 비교.

사다리(같은 기간·drift·tx·4지표):
  A.  60/40 정적           — 주식 60·채권 40 고정 (순진한 바닥 기준)
  B.  정적 평균배분         — 풀 시스템의 시간평균 자산배분을 고정 (스위칭·vol·core 없음 = 순수 분산)
  B2. 정적 평균 + vol       — B + 레짐무관 변동성 축소(단일 목표 10%) = 레짐 없는 일반 위험관리
  C.  풀 시스템            — 현행 config 전체 (레짐 스위칭 + blend + vol + core30)

핵심: B2 → C = 레짐 스택이 "분산 + 일반 vol"을 넘어 추가하는 한계 가치.
  작으면 레짐 기계가 복잡도값을 못 함. 크면(특히 Ulcer·회복·Martin) 스위칭이 실제 방어를 만듦.

교란 처리: ① 전 레짐 목표 동일 → 스위칭 0(엔진 불변). ② B2 vol 목표 단일 0.10(레짐정보 차단).
  ③ core30은 정적 앵커라 A/B/B2서 OFF, 그 평균효과는 B의 평균배분에 녹음. ④ in-sample.
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
REGIMES = ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis", "Transition"]


def build_engine(config, universe_px, signal_px, fred_history):
    rb = config.get("rebalancing", {})
    return BacktestEngine(
        config=config, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=float(rb.get("drift_threshold", 0.015)),
        cooldown_days=int(rb.get("min_rebalance_interval_days", 0)),
        fred_history=fred_history,
    )


def metrics_row(label, res):
    r = res["returns"].dropna()
    m = compute_metrics(r)
    rc3 = rolling_cagr(r, years=3.0)
    rc5 = rolling_cagr(r, years=5.0)
    rec = recovery_duration(r)
    return {
        "전략": label,
        "r3w": rc3["worst"], "r3m": rc3["median"], "r5w": rc5["worst"],
        "Ulcer": m.get("ulcer", 0.0),
        "rec_dd": rec["maxdd_recovery_days"], "uw_max": rec["max_underwater_days"],
        "Martin": m.get("martin", 0.0),
        "CAGR": m.get("cagr", 0.0), "MaxDD": m.get("max_drawdown", 0.0),
        "tx": float(res["tx_cost"].sum()),
        "COVID": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["COVID 2020"]),
        "Bear22": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["Bear 2022"]),
    }


def run_full_and_capture_avg(base, universe_px, signal_px, fred_history):
    """C(풀 시스템)를 돌리고 시간평균 자산군 배분(STATIC_AVG)을 산출."""
    cls_of = {t: m["asset_class"] for t, m in base["universe"].items()}
    eng = build_engine(base, universe_px, signal_px, fred_history)
    captured = []
    orig = eng._target_weights

    def wrapped(*a, **k):
        w = orig(*a, **k)
        captured.append(dict(w))
        return w

    eng._target_weights = wrapped
    res = eng.run()

    # 리밸별 종목 비중 → 자산군 합산(cash_usd→cash 폴딩) → 시간평균 → 정규화
    acc = {}
    for w in captured:
        cw = {}
        for t, v in w.items():
            c = cls_of.get(t, "cash")
            if c == "cash_usd":
                c = "cash"
            cw[c] = cw.get(c, 0.0) + v
        for c, v in cw.items():
            acc[c] = acc.get(c, 0.0) + v
    n = max(1, len(captured))
    avg = {c: v / n for c, v in acc.items()}
    # regime_targets가 아는 클래스만 유지(commodity_krw 등 제외) 후 정규화
    keep = set(base["regime_targets"]["Goldilocks"].keys())
    avg = {c: v for c, v in avg.items() if c in keep}
    tot = sum(avg.values())
    avg = {c: v / tot for c, v in avg.items()}
    return res, avg


def make_static_config(base, target, *, vol_on, core_on, vol_target=0.10):
    """모든 레짐 목표를 target으로 통일 → 정적. vol/core/hmm 토글."""
    cfg = copy.deepcopy(base)
    cfg["regime_targets"] = {r: dict(target) for r in REGIMES}
    cfg.setdefault("hmm", {})["enabled"] = False  # 출력 불변(목표 통일), 속도만 ↑
    cfg.setdefault("core_satellite", {})["enabled"] = bool(core_on)
    cfg.setdefault("vol_targeting", {})["enabled"] = bool(vol_on)
    if vol_on:
        cfg["vol_targeting"]["regime_target_vol"] = {r: vol_target for r in REGIMES}
    return cfg


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    rows = []

    # C: 풀 시스템 + 평균배분 산출
    print("\n[C 풀 시스템] 실행 + 평균배분 산출 중...")
    res_c, static_avg = run_full_and_capture_avg(base, universe_px, signal_px, fred_history)
    print("  STATIC_AVG(시간평균 자산군 배분):")
    for c, v in sorted(static_avg.items(), key=lambda x: -x[1]):
        if v >= 0.005:
            print(f"    {c:>18} {v:6.1%}")

    # A: 60/40
    print("\n[A 60/40 정적] 실행 중...")
    cfg_a = make_static_config(base, {"equity_etf": 0.60, "bond_krw": 0.40},
                               vol_on=False, core_on=False)
    rows.append(metrics_row("A. 60/40 정적", build_engine(cfg_a, universe_px, signal_px, fred_history).run()))

    # B: 정적 평균배분
    print("[B 정적 평균배분] 실행 중...")
    cfg_b = make_static_config(base, static_avg, vol_on=False, core_on=False)
    rows.append(metrics_row("B. 정적 평균배분", build_engine(cfg_b, universe_px, signal_px, fred_history).run()))

    # B2: 정적 평균 + vol
    print("[B2 정적 평균 + vol] 실행 중...")
    cfg_b2 = make_static_config(base, static_avg, vol_on=True, core_on=False, vol_target=0.10)
    rows.append(metrics_row("B2. 정적평균+vol", build_engine(cfg_b2, universe_px, signal_px, fred_history).run()))

    # C row
    rows.append(metrics_row("C. 풀 시스템", res_c))

    df = pd.DataFrame(rows).set_index("전략")

    print(f"\n{'='*132}")
    print("  Ablation 사다리 — 레짐 스택은 '분산 + 일반 vol'을 넘어 값을 하는가? (drift·USD단일·in-sample)")
    print(f"{'='*132}")
    h = (f"  {'전략':>16}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'tx':>7}"
         f"{'COVID':>8}{'Bear22':>8}")
    print(h)
    print("  " + "─" * (len(h) + 2))
    for label, r in df.iterrows():
        mark = " ◀현행" if "풀" in label else ""
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {label:>16}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}"
              f"{r['tx']:>7.2%}{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")

    # 한계 점프 분해
    def delta(a, b, col):
        return df.loc[b, col] - df.loc[a, col]

    print("\n  한계 기여 분해 (Martin / Ulcer / 회복일 / MaxDD):")
    steps = [("A→B  (분산 추가)", "A. 60/40 정적", "B. 정적 평균배분"),
             ("B→B2 (일반 vol 추가)", "B. 정적 평균배분", "B2. 정적평균+vol"),
             ("B2→C (레짐 스택 추가)", "B2. 정적평균+vol", "C. 풀 시스템")]
    for name, a, b in steps:
        print(f"    {name:>22}: ΔMartin {delta(a,b,'Martin'):+.2f}  "
              f"ΔUlcer {delta(a,b,'Ulcer'):+.2f}  "
              f"Δ회복일 {int(delta(a,b,'uw_max')):+d}  "
              f"ΔMaxDD {delta(a,b,'MaxDD'):+.1%}")

    print("\n  판정(규칙4): B2→C 점프가 작으면 레짐 스택 = 비싼 장식. 크면(특히 Ulcer·회복·Martin)")
    print("              레짐 스위칭이 실제 방어를 만듦. 주의: in-sample·USD단일·라이브 마찰 미반영.")
    return df


if __name__ == "__main__":
    main()
