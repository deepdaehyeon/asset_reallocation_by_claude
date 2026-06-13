"""
진단: 레짐 스위칭이 "저점 매도·고점 매수"(지각 매매)에 빠지는가?

질문(2026-06-13, 사용자): 현재 레짐을 판단해 포지션을 바꾸므로, 레짐 진입 시차 때문에
  하락을 다 맞고 나서 방어로 전환(저점 매도)하고, 다 오른 뒤 위험으로 전환(고점 매수)하는
  늦은 투자자처럼 되는 건 아닌가?

#1은 레짐 *라벨* 기준 event study였다(라벨 직후 평균회귀 확인). 그러나 실제 포트폴리오는
  blend·vol타겟·core30로 라벨처럼 확 안 바뀐다. 그래서 여기선 **실제 포트폴리오의 위험자산
  노출(equity+commodity+MF 비중)** 변화를 잡아, 그 변화 직전/직후 시장(SPY)이 어떻게
  움직였는지 측정한다.

방법:
  - 현행 config로 백테스트 1회. _target_weights를 래핑해 리밸 때 결정된 목표비중을 기록
    (엔진 로직 변경 없음 — 출력만 가로챔). 리밸일 순서 = rebalanced=True 행 순서와 1:1.
  - 위험노출 = Σ(위험자산군 비중). 리밸 간 forward-fill(결정 시점 기준).
  - 각 리밸의 노출변화 Δ vs 직전 N거래일 SPY(추세) & 직후 N거래일 SPY(되돌림).
  - 저점매도 = 노출 크게 ↓ 직후 SPY ↑ / 고점매수 = 노출 크게 ↑ 직후 SPY ↓.
  - corr(Δ노출, 직후 SPY): 음(-)이면 체계적 역행(저점매도·고점매수), 양(+)이면 선행 우위.

판정 보조: 노출 ↓ 시점의 (직전 SPY, 직후 SPY) 평균쌍으로 "하락 다 맞고 팔았는지 + 판 뒤
  반등 놓쳤는지" 동시 확인. core30·vol·blend가 이 지각 비용을 얼마나 완충하는지는 노출
  변화폭 자체가 작은지로 드러남.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
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
from compare_rule_timing_ab import START, END, REBAL_FREQ, TX_COST  # noqa: E402

RISK_CLASSES = {
    "equity_etf", "equity_factor", "equity_sector", "equity_individual",
    "equity_developed", "equity_emerging", "commodity", "managed_futures",
}
FWD = 20   # 직후 거래일
TRAIL = 20  # 직전 거래일


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    universe = cfg["universe"]
    cls_of = {t: m["asset_class"] for t, m in universe.items()}

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=cfg, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)
    spy = signal_px["SPY"].dropna()

    rb = cfg.get("rebalancing", {})
    eng = BacktestEngine(
        config=cfg, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=float(rb.get("drift_threshold", 0.015)),
        cooldown_days=int(rb.get("min_rebalance_interval_days", 0)),
        fred_history=fred_history,
    )

    # _target_weights 래핑: 리밸 때 결정된 목표비중을 순서대로 기록 (엔진 로직 불변)
    captured = []
    orig = eng._target_weights

    def wrapped(*args, **kwargs):
        w = orig(*args, **kwargs)
        captured.append(dict(w))
        return w

    eng._target_weights = wrapped
    print("백테스트 실행 중...")
    res = eng.run()

    rebal_dates = res.index[res["rebalanced"]]
    if len(rebal_dates) != len(captured):
        print(f"  ⚠ 리밸일({len(rebal_dates)}) ≠ 기록({len(captured)}) — 정렬 주의")
    n = min(len(rebal_dates), len(captured))
    rebal_dates = rebal_dates[:n]
    captured = captured[:n]

    # 리밸별 위험노출
    def risk_exposure(w):
        return sum(v for t, v in w.items() if cls_of.get(t) in RISK_CLASSES)

    exp = pd.Series([risk_exposure(w) for w in captured], index=rebal_dates)
    d_exp = exp.diff()

    # SPY 직전/직후 수익 (거래일 기준)
    spy_idx = spy.index
    pos = {d: i for i, d in enumerate(spy_idx)}

    def fwd_ret(date, k):
        i = pos.get(date)
        if i is None or i + k >= len(spy_idx):
            return np.nan
        return spy.iloc[i + k] / spy.iloc[i] - 1.0

    def trail_ret(date, k):
        i = pos.get(date)
        if i is None or i - k < 0:
            return np.nan
        return spy.iloc[i] / spy.iloc[i - k] - 1.0

    rows = []
    for date, de in d_exp.items():
        if pd.isna(de):
            continue
        rows.append({
            "date": date, "exp": exp[date], "d_exp": de,
            "trail": trail_ret(date, TRAIL), "fwd": fwd_ret(date, FWD),
        })
    ev = pd.DataFrame(rows).dropna(subset=["fwd", "trail"])

    print(f"\n{'='*92}")
    print("  레짐 스위칭 매매 타이밍 — 실제 위험자산 노출 변화 vs SPY (저점매도·고점매수 검증)")
    print(f"{'='*92}")
    print(f"  리밸 {len(rebal_dates)}회, 분석가능 {len(ev)}회. 위험노출 평균 {exp.mean():.1%}"
          f" (최소 {exp.min():.1%} ~ 최대 {exp.max():.1%}, 표준편차 {exp.std():.1%})")
    print(f"  FWD/TRAIL = {FWD}/{TRAIL} 거래일. trail=결정 직전 SPY, fwd=결정 직후 SPY.")

    # 임계: 노출 변화폭 상위/하위 (의미있는 risk-off/on)
    thr = ev["d_exp"].abs().quantile(0.75)
    off = ev[ev["d_exp"] <= -thr]   # 위험 크게 축소
    on = ev[ev["d_exp"] >= thr]     # 위험 크게 확대
    mid = ev[ev["d_exp"].abs() < thr]

    print(f"\n  유의미 변화 임계 |Δ노출| ≥ {thr:.1%} (상위 25%)")
    print(f"  {'구간':>18}{'건수':>6}{'평균Δ노출':>10}{'직전SPY':>10}{'직후SPY':>10}")
    print("  " + "─" * 60)
    for name, g in [("위험 축소(risk-off)", off), ("위험 확대(risk-on)", on),
                    ("소폭 변화", mid)]:
        if len(g):
            print(f"  {name:>18}{len(g):>6}{g['d_exp'].mean():>10.1%}"
                  f"{g['trail'].mean():>10.1%}{g['fwd'].mean():>10.1%}")

    corr = ev["d_exp"].corr(ev["fwd"])
    corr_t = ev["d_exp"].corr(ev["trail"])
    print(f"\n  corr(Δ노출, 직후SPY) = {corr:+.3f}  "
          f"(음수면 저점매도·고점매수 / 양수면 선행 우위)")
    print(f"  corr(Δ노출, 직전SPY) = {corr_t:+.3f}  "
          f"(양수면 추세추종: 오른 뒤 사고 빠진 뒤 판다)")

    print("\n  해석:")
    print("  • 위험축소 직후 SPY가 큰 양(+)이면 = 저점에서 팔고 반등 놓침(지각비용).")
    print("  • 위험확대 직후 SPY가 음(-)이면 = 고점에서 사고 하락 맞음.")
    print("  • 단 노출 변화폭(표준편차)이 작으면 blend·vol·core30이 지각비용을 이미 완충.")
    print("  주의: 결정 시점(리밸) 목표비중 기준. drift 사이 가격변동 노출은 미반영. in-sample.")


if __name__ == "__main__":
    main()
