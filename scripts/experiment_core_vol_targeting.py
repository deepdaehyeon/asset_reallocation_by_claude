"""
실험: 코어(30% Goldilocks)에도 vol targeting을 적용하면 4지표가 개선되는가?

질문(2026-06-13): 현행 core+satellite는 vol targeting을 위성 70%에만 적용하고
  코어 30%(정적 Goldilocks)는 풀 equity로 둔다(회복 구간 풀참여 앵커).
  사용자 가설: 코어 equity도 고변동 구간에 깎으면 하락 방어가 더 좋지 않을까?

변형(같은 엔진·기간·config, 가중치 구성 순서만 토글):
  A) 현행(core exempt): vol(위성 blend) → core 혼합.  코어 equity 안 깎임.
  B) core도 깎기(full-port vol): core 혼합 → vol(결합 포트폴리오).  코어 equity도 깎임.
     eff_vol도 결합 포트폴리오 기준으로 재측정(현실적 버전).
  (참고) vol OFF.

floor는 현행 config(0.65) 그대로. 라이브/엔진/config 변경 없음(진단).
"""
from __future__ import annotations

import copy
import sys
import warnings
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "scripts"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from data import load_all_prices  # noqa: E402
from engine import BacktestEngine, _quiet  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
from metrics import compute_metrics, rolling_cagr, recovery_duration  # noqa: E402
from portfolio import (  # noqa: E402
    apply_class_caps, apply_core_satellite, apply_dynamic_class_caps,
    apply_vol_targeting, blend_regime_targets, compute_portfolio_ewma_vol,
    derive_account_weights, merge_to_total_weights,
)
from compare_rule_timing_ab import START, END, REBAL_FREQ, TX_COST, crisis_maxdd  # noqa: E402

CRISIS_WINDOWS = {"COVID 2020": ("2020-02-19", "2020-04-30"),
                  "Bear 2022": ("2022-01-01", "2022-12-31")}


def _eff_vol(weights, signal_px_slice, realized_vol, config, lam):
    """주어진 자산군 비중 dict에 대한 EWMA 연환산 포트폴리오 변동성."""
    if signal_px_slice is None:
        return realized_vol
    ticker_w = {t: weights.get(m["asset_class"], 0.0)
                for t, m in config["universe"].items()
                if m["asset_class"] in weights}
    pv = compute_portfolio_ewma_vol(signal_px_slice, ticker_w, lam=lam)
    return pv if pv > 0 else realized_vol


class CoreVolEngine(BacktestEngine):
    """scale_core=True면 core 혼합 후 결합 포트폴리오 전체에 vol targeting을 적용."""

    scale_core: bool = False

    def _target_weights(self, blend_probs, realized_vol, portfolio_value,
                        regime="", vix=0.0, signal_px_slice=None,
                        transition_phase=False):
        usd_val = portfolio_value * self.usd_ratio
        krw_val = portfolio_value * (1 - self.usd_ratio)
        vol_cfg = self.config.get("vol_targeting", {})
        lam = float(vol_cfg.get("ewma_lambda", 0.94))
        use_pv = vol_cfg.get("use_portfolio_vol", True)

        with _quiet():
            blended = blend_regime_targets(blend_probs, self.config,
                                           transition_phase=transition_phase)
            if self.scale_core:
                # B) core 먼저 혼합 → 결합 포트폴리오에 vol targeting
                blended = apply_core_satellite(blended, self.config)
                eff_vol = _eff_vol(blended, signal_px_slice, realized_vol,
                                   self.config, lam) if use_pv else realized_vol
                blended = apply_vol_targeting(blended, eff_vol, self.config, regime=regime)
            else:
                # A) 현행: vol(위성) → core 혼합(코어 면제)
                eff_vol = _eff_vol(blended, signal_px_slice, realized_vol,
                                   self.config, lam) if use_pv else realized_vol
                blended = apply_vol_targeting(blended, eff_vol, self.config, regime=regime)
                blended = apply_core_satellite(blended, self.config)

            class_max = self.config.get("class_max_weight", {})
            blended = (apply_dynamic_class_caps(blended, class_max, vix)
                       if vix > 0 else apply_class_caps(blended, class_max))
            usd_w, krw_w = derive_account_weights(blended, self.config, usd_val, krw_val)

        return merge_to_total_weights(usd_w, krw_w, usd_val, krw_val)


# (label, vol_enabled, scale_core)
VARIANTS = [
    ("A 현행(core면제)", True,  False),
    ("B core도 깎기",    True,  True),
    ("vol OFF",          False, False),
]


def run_cell(spec, config, universe_px, signal_px, fred_history):
    label, enabled, scale_core = spec
    rb = config.get("rebalancing", {})
    vt = config.setdefault("vol_targeting", {})
    vt["enabled"] = enabled
    print(f"  [{label}]")
    eng = CoreVolEngine(
        config=config, universe_px=universe_px, signal_px=signal_px,
        start=START, end=END, rebal_freq=REBAL_FREQ, tx_cost=TX_COST,
        drift_threshold=float(rb.get("drift_threshold", 0.015)),
        cooldown_days=int(rb.get("min_rebalance_interval_days", 0)),
        fred_history=fred_history,
    )
    eng.scale_core = scale_core
    res = eng.run()
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
        "리밸": int(res["rebalanced"].sum()), "tx": float(res["tx_cost"].sum()),
        "COVID": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["COVID 2020"]),
        "Bear22": crisis_maxdd(res["returns"], *CRISIS_WINDOWS["Bear 2022"]),
    }


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    cs = base.get("core_satellite", {})
    vt = base.get("vol_targeting", {})
    print(f"데이터 로딩 [{START} ~ {END}]... "
          f"(core enabled={cs.get('enabled')} ratio={cs.get('core_ratio')} "
          f"regime={cs.get('core_regime')}, vol_floor={vt.get('floor')})")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    print("\n전략 실행 중...")
    rows = [run_cell(s, copy.deepcopy(base), universe_px, signal_px, fred_history) for s in VARIANTS]
    df = pd.DataFrame(rows).set_index("전략")

    print(f"\n{'='*124}")
    print(f"  코어 vol targeting 적용 여부 — 현행 시스템(core30·floor{vt.get('floor')}) + 고정 4지표")
    print(f"{'='*124}")
    h = (f"  {'전략':>16}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'리밸':>6}"
         f"{'tx':>7}{'COVID':>8}{'Bear22':>8}")
    print(h)
    print("  " + "─" * (len(h) + 6))
    for label, r in df.iterrows():
        mark = " ◀현행" if label.startswith("A") else ""
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {label:>16}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}"
              f"{int(r['리밸']):>6}{r['tx']:>7.2%}{r['COVID']:>8.1%}{r['Bear22']:>8.1%}{mark}")

    print("\n  판정(CLAUDE.md 규칙4): Martin(1차)·롤링CAGR·Ulcer·회복기간. CAGR/MaxDD/COVID 보조.")
    print("  주의: USD 단일통화 백테스트 — 라이브 합성 순환매·실제 회전 미반영. in-sample.")
    return df


if __name__ == "__main__":
    main()
