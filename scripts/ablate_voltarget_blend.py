"""
vol targeting · blend 절제(ablation) — 두 장치가 밥값을 하는가.

사용자 의심: "어떤 신호든 vol targeting·blend가 희석해버리니, 이 둘이 정말 유의미한
장치인가?" 지금까지의 '노이즈' 결론들이 모두 이 둘의 흡수 가정 위에 서 있었으므로
가정 자체를 검증한다.

2×2 (동일 엔진·config, _target_weights만 토글; 타이밍은 현행 라이브=rule 고정):
  - full        : blend ON(HMM) + vol targeting ON   = 현행
  - blend_off   : one-hot(acting regime) + vt ON
  - vt_off      : blend ON + vol targeting OFF
  - both_off    : one-hot + vt OFF                    = 거의 정적 regime_targets

예측: 희석 가설이 맞으면 vt_off/both_off도 Sharpe·MaxDD 비슷하거나 낫다.
두 장치가 방어 엔진이면 vt_off에서 MaxDD/Calmar가 크게 악화. 코드 변경 없음.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Dict

import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "scripts"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from data import load_all_prices  # noqa: E402
from engine import BacktestEngine, _quiet  # noqa: E402
from metrics import compute_metrics  # noqa: E402
from regime import REGIMES  # noqa: E402
from portfolio import (  # noqa: E402
    apply_class_caps, apply_dynamic_class_caps, apply_vol_targeting,
    blend_regime_targets, compute_portfolio_ewma_vol, derive_account_weights,
    merge_to_total_weights,
)
from compare_rule_timing_ab import START, END, REBAL_FREQ, TX_COST, crisis_maxdd  # noqa: E402

CRISIS_WINDOWS = {"COVID 2020": ("2020-02-19", "2020-04-30"),
                  "Bear 2022": ("2022-01-01", "2022-12-31")}


class AblationEngine(BacktestEngine):
    def __init__(self, *a, use_blend: bool = True, use_vt: bool = True, **k):
        self._use_blend = use_blend
        self._use_vt = use_vt
        super().__init__(*a, **k)

    def _target_weights(self, blend_probs, realized_vol, portfolio_value,
                        regime="", vix=0.0, signal_px_slice=None,
                        transition_phase=False):
        usd_val = portfolio_value * self.usd_ratio
        krw_val = portfolio_value * (1 - self.usd_ratio)
        vol_cfg = self.config.get("vol_targeting", {})

        probs = blend_probs if self._use_blend else {
            r: (1.0 if r == regime else 0.0) for r in REGIMES
        }

        with _quiet():
            blended = blend_regime_targets(probs, self.config, transition_phase=transition_phase)

            if self._use_vt:
                if vol_cfg.get("use_portfolio_vol", True) and signal_px_slice is not None:
                    lam = float(vol_cfg.get("ewma_lambda", 0.94))
                    ticker_w = {t: blended.get(m["asset_class"], 0.0)
                                for t, m in self.config["universe"].items()
                                if m["asset_class"] in blended}
                    port_vol = compute_portfolio_ewma_vol(signal_px_slice, ticker_w, lam=lam)
                    eff_vol = port_vol if port_vol > 0 else realized_vol
                else:
                    eff_vol = realized_vol
                blended = apply_vol_targeting(blended, eff_vol, self.config, regime=regime)

            class_max = self.config.get("class_max_weight", {})
            blended = apply_dynamic_class_caps(blended, class_max, vix) if vix > 0 else apply_class_caps(blended, class_max)
            usd_w, krw_w = derive_account_weights(blended, self.config, usd_val, krw_val)

        return merge_to_total_weights(usd_w, krw_w, usd_val, krw_val)


def make_engine(config, universe_px, signal_px, fred_history, use_blend, use_vt):
    return AblationEngine(
        config=config, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=float(config.get("rebalancing", {}).get("drift_threshold", 0.015)),
        cooldown_days=int(config.get("rebalancing", {}).get("min_rebalance_interval_days", 0)),
        fred_history=fred_history, use_blend=use_blend, use_vt=use_vt,
    )


def main() -> None:
    from fetcher import fetch_fred_history
    with open(ROOT / "trading" / "config.yaml") as f:
        config = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}]... (timing_source="
          f"{config.get('regime_filter', {}).get('regime_timing_source')})")
    universe_px, signal_px = load_all_prices(config=config, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    cells = {
        "full (현행)":  (True, True),
        "blend_off":     (False, True),
        "vt_off":        (True, False),
        "both_off":      (False, False),
    }
    rows = []
    print("\n전략 실행 중...")
    for label, (ub, uv) in cells.items():
        print(f"  [{label}]  blend={'ON' if ub else 'OFF'}  vt={'ON' if uv else 'OFF'}")
        res = make_engine(config, universe_px, signal_px, fred_history, ub, uv).run()
        m = compute_metrics(res["returns"])
        rows.append({
            "cell": label, "CAGR": m.get("cagr", 0.0), "Sharpe": m.get("sharpe", 0.0),
            "MaxDD": m.get("max_drawdown", 0.0), "Calmar": m.get("calmar", 0.0),
            "COVID": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["COVID 2020"]),
            "Bear22": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["Bear 2022"]),
        })
    df = pd.DataFrame(rows).set_index("cell")

    print(f"\n{'='*88}")
    print("  vol targeting · blend 절제 2×2 (타이밍 고정=rule)")
    print(f"{'='*88}")
    hdr = f"  {'cell':<14}{'CAGR':>7}{'Sharpe':>8}{'MaxDD':>9}{'Calmar':>8}{'COVID':>9}{'Bear22':>9}"
    print(hdr)
    print("  " + "─" * (len(hdr) - 2))
    for label, r in df.iterrows():
        print(f"  {label:<14}{r['CAGR']:>6.1%}{r['Sharpe']:>8.2f}{r['MaxDD']:>8.1%}"
              f"{r['Calmar']:>8.2f}{r['COVID']:>8.1%}{r['Bear22']:>8.1%}")

    full = df.loc["full (현행)"]
    print(f"\n{'='*88}")
    print("  델타 (cell − full) — 음수 MaxDD/Calmar = 장치 끄면 악화 = 장치가 방어 엔진")
    print(f"{'='*88}")
    for label in ("blend_off", "vt_off", "both_off"):
        r = df.loc[label]
        print(f"  {label:<10} ΔSharpe {r['Sharpe']-full['Sharpe']:>+6.3f} | "
              f"ΔMaxDD {(r['MaxDD']-full['MaxDD'])*100:>+6.2f}pp | "
              f"ΔCalmar {r['Calmar']-full['Calmar']:>+6.3f} | "
              f"ΔCOVID {(r['COVID']-full['COVID'])*100:>+6.2f}pp | "
              f"ΔBear22 {(r['Bear22']-full['Bear22'])*100:>+6.2f}pp")

    return df


if __name__ == "__main__":
    main()
