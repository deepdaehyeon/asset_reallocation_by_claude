"""
엔드투엔드 가치 검증 — 레짐 스위칭이 정적 배분 대비 위험조정 가치를 더하는가?

질문: detect_regime + HMM 앙상블로 레짐을 갈아타는 장치가, 레짐을 고정한
정적 배분(같은 리스크 오버레이·같은 drift 리밸런싱)보다 실제로 나은가?

방법: BacktestEngine._get_regime를 오버라이드해 레짐 스위칭만 제거하고
나머지(vol targeting, class cap, drawdown scaling, drift 리밸런싱)는 동일하게 둔다.
  - full       : 실제 레짐 전략 (베이스라인 엔진 그대로)
  - fixed:<R>  : 항상 레짐 R (blend={R:1.0}) — 5개 레짐 각각
  - equal_blend: 모든 레짐 균등 블렌딩 (무정보 기준선)
  - 60/40      : SPY 0.6 + IEF 0.4 (외부 벤치마크)

야드스틱: full이 최선의 fixed 기준선을 Sharpe AND (MaxDD or Calmar)에서 이겨야
레짐 스위칭이 정당화된다. 추가로 위기 구간(2020 COVID / 2022) MaxDD를 비교한다.

코드 변경 없음 (시뮬레이션). 결과는 docs/에 저장.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from fetcher import fetch_fred_history  # noqa: E402
from regime import REGIMES  # noqa: E402
from data import load_all_prices  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from metrics import compute_metrics, rolling_cagr, recovery_duration  # noqa: E402

CONFIG_PATH = ROOT / "trading" / "config.yaml"
START = "2010-01-01"
END = "2025-04-30"
REBAL_FREQ = "W-FRI"
TX_COST = 0.001

BENCHMARK = {"SPY": 0.60, "IEF": 0.40}

# 위기 구간 — full이 방어 가치를 보여야 하는 곳
CRISIS_WINDOWS = {
    "COVID 2020": ("2020-02-19", "2020-04-30"),
    "Bear 2022": ("2022-01-01", "2022-12-31"),
}


class FixedRegimeEngine(BacktestEngine):
    """_get_regime를 고정 레짐(또는 균등 블렌딩) 반환으로 대체.

    fixed_regime이 REGIMES 중 하나면 blend={그 레짐:1.0},
    None이면 모든 레짐 균등 블렌딩(무정보 기준선).
    나머지 리스크 오버레이/리밸런싱 로직은 베이스라인과 100% 동일.
    """

    def __init__(self, *args, fixed_regime: str | None = None, **kwargs):
        self._fixed_regime = fixed_regime
        super().__init__(*args, **kwargs)

    def _get_regime(self, as_of: pd.Timestamp):
        if self._fixed_regime is not None:
            r = self._fixed_regime
            return r, {x: (1.0 if x == r else 0.0) for x in REGIMES}, r, 1.0, 1.0, 0.0
        uniform = {x: 1.0 / len(REGIMES) for x in REGIMES}
        # 균등 블렌딩에서 확정 레짐은 vol targeting 티어 선택에만 쓰이므로
        # 중립적인 DEFAULT(Slowdown)로 둔다.
        return "Slowdown", uniform, "Slowdown", 1.0, 1.0, 0.0


def _drift_threshold(config: dict) -> float:
    return float(config.get("rebalancing", {}).get("drift_threshold", 0.015))


def _cooldown(config: dict) -> int:
    return int(config.get("rebalancing", {}).get("min_rebalance_interval_days", 0))


def _make_full_engine(config, universe_px, signal_px, fred_history) -> BacktestEngine:
    return BacktestEngine(
        config=config,
        universe_px=universe_px,
        signal_px=signal_px,
        start=START,
        end=END,
        rebal_freq=REBAL_FREQ,
        tx_cost=TX_COST,
        drift_threshold=_drift_threshold(config),
        cooldown_days=_cooldown(config),
        fred_history=fred_history,
    )


def _make_fixed_engine(
    config, universe_px, signal_px, fred_history, fixed_regime
) -> FixedRegimeEngine:
    return FixedRegimeEngine(
        config=config,
        universe_px=universe_px,
        signal_px=signal_px,
        start=START,
        end=END,
        rebal_freq=REBAL_FREQ,
        tx_cost=TX_COST,
        drift_threshold=_drift_threshold(config),
        cooldown_days=_cooldown(config),
        fred_history=fred_history,
        fixed_regime=fixed_regime,
    )


def build_benchmark(universe_px, signal_px) -> pd.Series:
    parts = []
    for t, w in BENCHMARK.items():
        if t in universe_px.columns:
            px = universe_px[t]
        elif t in signal_px.columns:
            px = signal_px[t]
        else:
            continue
        parts.append(px.pct_change() * w)
    if not parts:
        return pd.Series(dtype=float)
    return pd.concat(parts, axis=1).sum(axis=1)[START:END]


def crisis_maxdd(returns: pd.Series, start: str, end: str) -> float:
    """구간 내 max drawdown (진입가 대비 최저)."""
    r = returns[start:end]
    if r.empty:
        return float("nan")
    eq = (1.0 + r).cumprod()
    return float((eq / eq.cummax() - 1.0).min())


def main() -> None:
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    print(f"데이터 로딩 중 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(
        config=config, start=START, end=END, use_cache=True
    )
    print(f"  유니버스 {len(universe_px.columns)}종목 / {len(universe_px)}거래일")

    fred_history = fetch_fred_history(START, END)
    if not fred_history.empty:
        print(f"  FRED 피처: {list(fred_history.columns)}")
    else:
        print("  FRED: 없음 — 가격 파생 피처만")

    # ── 변형 실행 ─────────────────────────────────────────────────────────────
    variants: Dict[str, pd.Series] = {}
    regime_series: Dict[str, pd.Series] = {}

    print("\n전략 실행 중...")
    print("  [full] 실제 레짐 전략")
    full_res = _make_full_engine(config, universe_px, signal_px, fred_history).run()
    variants["full"] = full_res["returns"]
    regime_series["full"] = full_res["regime"]

    for r in REGIMES:
        label = f"fixed:{r}"
        print(f"  [{label}]")
        res = _make_fixed_engine(
            config, universe_px, signal_px, fred_history, fixed_regime=r
        ).run()
        variants[label] = res["returns"]

    print("  [equal_blend] 균등 블렌딩(무정보)")
    eb_res = _make_fixed_engine(
        config, universe_px, signal_px, fred_history, fixed_regime=None
    ).run()
    variants["equal_blend"] = eb_res["returns"]

    bm = build_benchmark(universe_px, signal_px)
    if not bm.empty:
        variants["60/40"] = bm

    # ── 메트릭 집계 ───────────────────────────────────────────────────────────
    rows = []
    for label, ret in variants.items():
        r = ret.dropna()
        m = compute_metrics(r)
        rc3 = rolling_cagr(r, years=3.0)
        rc5 = rolling_cagr(r, years=5.0)
        rec = recovery_duration(r)
        rows.append({
            "variant": label,
            "CAGR": m.get("cagr", 0.0), "MaxDD": m.get("max_drawdown", 0.0),
            "Ulcer": m.get("ulcer", 0.0), "Martin": m.get("martin", 0.0),
            "r3w": rc3["worst"], "r3m": rc3["median"], "r5w": rc5["worst"],
            "rec_dd": rec["maxdd_recovery_days"], "uw_max": rec["max_underwater_days"],
            "COVID_DD": crisis_maxdd(ret, *CRISIS_WINDOWS["COVID 2020"]),
            "Bear22_DD": crisis_maxdd(ret, *CRISIS_WINDOWS["Bear 2022"]),
        })
    df = pd.DataFrame(rows).set_index("variant")

    # ── 출력 (고정 4지표: 롤링CAGR·Ulcer·회복기간·Martin) ────────────────────────
    print(f"\n{'='*112}")
    print("  엔드투엔드 가치 — 레짐 전략 vs 정적 기준선 (고정 4지표)")
    print(f"{'='*112}")
    hdr = (f"  {'variant':<16}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
           f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}")
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    for label, row in df.iterrows():
        mark = " ◀전략" if label == "full" else ""
        rec = "미회복" if row["rec_dd"] < 0 else f"{int(row['rec_dd'])}"
        print(f"  {label:<16}{row['r3w']:>9.1%}{row['r3m']:>9.1%}{row['r5w']:>9.1%}{row['Ulcer']:>8.2f}"
              f"{rec:>8}{int(row['uw_max']):>8}{row['Martin']:>8.2f}{row['CAGR']:>8.1%}{row['MaxDD']:>8.1%}{mark}")

    # ── 판정 (Martin 1차, 회복기간·롤3y최악 보조) ────────────────────────────────
    fixed_labels = [f"fixed:{r}" for r in REGIMES]
    full = df.loc["full"]
    best_martin_fixed = df.loc[fixed_labels, "Martin"].idxmax()
    # 회복기간은 짧을수록(미회복=-1은 최악) 우위 → 미회복을 +inf로 치환해 비교
    def _rec_key(x):
        return float("inf") if x < 0 else x
    rec_vals = df.loc[fixed_labels, "rec_dd"].map(_rec_key)
    best_rec_fixed = rec_vals.idxmin()

    print(f"\n{'='*112}")
    print("  판정 — Martin 1차 판정, 회복기간·롤3y최악 보조 (CLAUDE.md 규칙4)")
    print(f"{'='*112}")
    full_rec = "미회복" if full["rec_dd"] < 0 else f"{int(full['rec_dd'])}d"
    print(f"  full: Martin {full['Martin']:.2f} / Ulcer {full['Ulcer']:.2f} / "
          f"롤3y최악 {full['r3w']:+.1%} / 회복 {full_rec}")
    print(f"  최선 fixed (Martin):  {best_martin_fixed} = {df.loc[best_martin_fixed, 'Martin']:.2f}")
    print(f"  최선 fixed (회복기간): {best_rec_fixed} = {_rec_key(df.loc[best_rec_fixed,'rec_dd']):.0f}d")

    beats_martin = full["Martin"] > df.loc[best_martin_fixed, "Martin"]
    beats_rec = _rec_key(full["rec_dd"]) < _rec_key(df.loc[best_martin_fixed, "rec_dd"])
    beats_r3w = full["r3w"] > df.loc[best_martin_fixed, "r3w"]

    print()
    print(f"  full > 최선fixed Martin?   {'예' if beats_martin else '아니오'}")
    print(f"  full 회복기간 더 짧음?      {'예' if beats_rec else '아니오'}")
    print(f"  full 롤3y최악 더 높음?      {'예' if beats_r3w else '아니오'}")

    if beats_martin and (beats_rec or beats_r3w):
        print("\n  → 레짐 스위칭이 위험조정 가치를 더한다 (야드스틱 충족).")
    elif beats_rec or beats_r3w:
        print("\n  → 레짐 스위칭은 주로 방어(하락 고통 단축) 가치. Martin 우위는 불분명.")
    else:
        print("\n  → 레짐 스위칭이 최선 정적 기준선을 못 이김. 추가 검증 필요.")
    print("  (COVID/Bear22 위기 MaxDD는 표에 미표시 — 방어 보조참고는 별도 절제실험 #2 참조)")

    return df


if __name__ == "__main__":
    main()
