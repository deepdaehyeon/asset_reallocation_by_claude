"""
오라클 레짐 천장 — "모든 레짐을 맞춘다면" 성적 상한.

분류기(HMM/RF/rule)를 완벽한 미래예지 오라클로 대체:
  매 리밸 시점, 5개 레짐 타겟 포트폴리오 중 다음 H거래일 forward 수익률이 가장 높은 레짐을
  one-hot으로 선택(look-ahead 의도적). regime_targets·vol targeting·class cap·drift 리밸 등
  다운스트림 파이프라인은 현행 그대로 → **레짐 분류 스킬만** 격리한 상한치.

H(랭킹 horizon)에 따라 상한이 달라지므로 5/21/63거래일을 함께 본다.
baseline(현행 분류기)도 같은 엔진으로 돌려 직접 비교.

코드 변경 없음(시뮬레이션). 결과는 docs/에 저장.
"""
from __future__ import annotations

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
from metrics import compute_metrics  # noqa: E402
from regime import REGIMES  # noqa: E402
from compare_rule_timing_ab import START, END, REBAL_FREQ, TX_COST, crisis_maxdd  # noqa: E402

CRISIS_WINDOWS = {"COVID 2020": ("2020-02-19", "2020-04-30"),
                  "Bear 2022": ("2022-01-01", "2022-12-31")}
HORIZONS = [5, 21, 63]


def regime_ticker_weights(config: dict) -> dict[str, dict[str, float]]:
    """regime_targets(자산군) × asset_routing(종목) → 레짐별 종목 가중 벡터."""
    rt = config["regime_targets"]
    routing = config["asset_routing"]
    out: dict[str, dict[str, float]] = {}
    for reg in REGIMES:
        tw: dict[str, float] = {}
        for ac, w in rt[reg].items():
            for tk, sub in routing.get(ac, {}).items():
                tw[tk] = tw.get(tk, 0.0) + w * sub
        out[reg] = tw
    return out


class OracleEngine(BacktestEngine):
    """_get_regime을 미래예지 오라클로 대체. HMM/RF 학습 없음."""

    def __init__(self, *args, horizon: int = 21, **kwargs):
        super().__init__(*args, **kwargs)
        self._oracle_h = horizon
        self._reg_tw = regime_ticker_weights(self.config)
        px = self.universe_px
        self._fwd = px.shift(-horizon) / px - 1.0  # 종목별 forward 수익률(look-ahead)

    def _get_regime(self, as_of):
        if as_of in self._fwd.index:
            row = self._fwd.loc[as_of]
        else:
            sub = self._fwd.loc[:as_of]
            row = sub.iloc[-1] if len(sub) else None
        best, best_r = REGIMES[0], -1e18
        if row is not None:
            for reg, tw in self._reg_tw.items():
                num = wsum = 0.0
                for tk, w in tw.items():
                    v = row.get(tk)
                    if v is not None and v == v:  # not NaN
                        num += w * v
                        wsum += w
                r = num / wsum if wsum > 0 else -1e18
                if r > best_r:
                    best_r, best = r, reg
        blend = {x: 0.0 for x in REGIMES}
        blend[best] = 1.0
        # (final, blend, rule_regime, combined_conf, rule_conf, hmm_conf)
        return best, blend, best, 1.0, 1.0, 1.0


def metrics_row(label, res):
    m = compute_metrics(res["returns"])
    return {
        "전략": label,
        "CAGR": m.get("cagr", 0.0), "Sharpe": m.get("sharpe", 0.0),
        "MaxDD": m.get("max_drawdown", 0.0), "Calmar": m.get("calmar", 0.0),
        "리밸": int(res["rebalanced"].sum()), "tx누적": float(res["tx_cost"].sum()),
        "COVID": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["COVID 2020"]),
        "Bear22": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["Bear 2022"]),
    }


def run_engine(cls, universe_px, signal_px, fred_history, config, **extra):
    rb = config.get("rebalancing", {})
    return cls(
        config=config, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=float(rb.get("drift_threshold", 0.015)),
        cooldown_days=int(rb.get("min_rebalance_interval_days", 0)),
        fred_history=fred_history, **extra,
    ).run()


def main() -> None:
    from fetcher import fetch_fred_history
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    rows = []
    print("\nbaseline(현행 분류기) 실행 중...")
    rows.append(metrics_row("baseline(현행)",
                            run_engine(BacktestEngine, universe_px, signal_px, fred_history, base)))
    for h in HORIZONS:
        print(f"oracle(H={h}d) 실행 중...")
        res = run_engine(OracleEngine, universe_px, signal_px, fred_history, base, horizon=h)
        rows.append(metrics_row(f"oracle H={h}d", res))

    df = pd.DataFrame(rows).set_index("전략")
    print(f"\n{'='*98}")
    print("  오라클 레짐 천장 — 분류 스킬만 격리(다운스트림 파이프라인 동일)")
    print(f"{'='*98}")
    hdr = (f"  {'전략':>14}{'CAGR':>8}{'Sharpe':>8}{'MaxDD':>9}{'Calmar':>8}"
           f"{'리밸':>7}{'tx누적':>9}{'COVID':>9}{'Bear22':>9}")
    print(hdr)
    print("  " + "─" * (len(hdr) + 6))
    for label, r in df.iterrows():
        print(f"  {label:>14}{r['CAGR']:>8.1%}{r['Sharpe']:>8.2f}{r['MaxDD']:>8.1%}"
              f"{r['Calmar']:>8.2f}{int(r['리밸']):>7}{r['tx누적']:>8.2%}"
              f"{r['COVID']:>8.1%}{r['Bear22']:>8.1%}")
    return df


if __name__ == "__main__":
    main()
