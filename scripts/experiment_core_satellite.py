"""
실험: core+satellite 구조 + 레짐 블렌드 ON/OFF 비교 (장기보유자 관점).

배경(2026-06-12):
  - 사용자 정정: 요즘 매일 매매는 '레짐 스위칭'이 아니라 **레짐 블렌딩**(0.6×HMM+0.4×RF
    +EWMA)이 confirmed 레짐(Goldilocks 고정)에서도 목표 비중을 매일 미세하게 밀어내서
    생긴다. 블렌드 없이 홀드하면 drift 리밸런싱 효과만 남는다.
  - 제안: core(Goldilocks 고정) + satellite(레짐 스위칭) 구조.

검증 변형(같은 엔진·기간):
  1) baseline(블렌드ON)   : 현행 — 레짐 스위칭 + 블렌드 + vol타겟 (목표 매일 미세 이동)
  2) 하드레짐(블렌드OFF)  : 스위칭은 하되 blend를 argmax 1-hot로 → 레짐 바뀔 때만 계단식 변경
  3) core50+sat          : 50% Goldilocks 고정(vol·blend 없음) + 50% satellite(현행 엔진)
  4) core70+sat          : 70% core
  5) 순수홀드(Goldilocks) : blend={Goldilocks:1}, vol OFF — 상단 레퍼런스

각 변형에 대해 수익/위험(CAGR·MaxDD·Ulcer·Martin·롤링CAGR) + churn(리밸·tx누적)을 함께
본다 — 블렌드가 churn을 얼마나 만드는지, core 비중이 수익/방어를 어떻게 가르는지.

질문 답: 백테스트는 레짐 블렌딩을 **한다**(_target_weights → blend_regime_targets).
코드 변경 없음. 결과는 docs/에 저장.
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
from engine import (  # noqa: E402
    BacktestEngine,
    _quiet,
    apply_class_caps,
    apply_dynamic_class_caps,
    apply_vol_targeting,
    blend_regime_targets,
    compute_portfolio_ewma_vol,
    derive_account_weights,
    merge_to_total_weights,
)
from fetcher import fetch_fred_history  # noqa: E402
from metrics import compute_metrics, rolling_cagr, recovery_duration  # noqa: E402
from prototype_forward_regime_predictability import run_engine  # noqa: E402
from compare_rule_timing_ab import START, END  # noqa: E402

FIXED = "Goldilocks"


class HardRegimeEngine(BacktestEngine):
    """블렌드 OFF: confirmed 레짐만 1-hot로 사용 → 레짐 바뀔 때만 목표 변경."""
    def _get_regime(self, *a, **k):
        final, blend, rule, cc, rc, hc = super()._get_regime(*a, **k)
        return final, {final: 1.0}, rule, cc, rc, hc


class StaticGoldilocksEngine(BacktestEngine):
    """순수 홀드: 항상 Goldilocks 1-hot."""
    def _get_regime(self, *a, **k):
        return (FIXED, {FIXED: 1.0}, FIXED, 1.0, 1.0, 1.0)


class CoreSatelliteEngine(BacktestEngine):
    """core(Goldilocks 고정, vol·blend 없음) + satellite(현행 엔진) 클래스레벨 블렌드."""
    core_frac = 0.5

    def _target_weights(self, blend_probs, realized_vol, portfolio_value,
                        regime="", vix=0.0, signal_px_slice=None, transition_phase=False):
        usd_val = portfolio_value * self.usd_ratio
        krw_val = portfolio_value * (1 - self.usd_ratio)
        vol_cfg = self.config.get("vol_targeting", {})
        cf = self.core_frac
        with _quiet():
            # satellite: 실제 blend + vol타겟
            sat = blend_regime_targets(blend_probs, self.config, transition_phase=transition_phase)
            if vol_cfg.get("use_portfolio_vol", True) and signal_px_slice is not None:
                lam = float(vol_cfg.get("ewma_lambda", 0.94))
                ticker_w = {t: sat.get(m["asset_class"], 0.0)
                            for t, m in self.config["universe"].items()
                            if m["asset_class"] in sat}
                port_vol = compute_portfolio_ewma_vol(signal_px_slice, ticker_w, lam=lam)
                eff_vol = port_vol if port_vol > 0 else realized_vol
            else:
                eff_vol = realized_vol
            sat = apply_vol_targeting(sat, eff_vol, self.config, regime=regime)
            # core: Goldilocks 고정, vol타겟 없음
            core = blend_regime_targets({FIXED: 1.0}, self.config, transition_phase=False)
            classes = set(core) | set(sat)
            combined = {c: cf * core.get(c, 0.0) + (1 - cf) * sat.get(c, 0.0) for c in classes}
            class_max = self.config.get("class_max_weight", {})
            combined = (apply_dynamic_class_caps(combined, class_max, vix)
                        if vix > 0 else apply_class_caps(combined, class_max))
            usd_w, krw_w = derive_account_weights(combined, self.config, usd_val, krw_val)
        return merge_to_total_weights(usd_w, krw_w, usd_val, krw_val)


def make_coresat(frac):
    return type(f"CoreSat{int(frac*100)}", (CoreSatelliteEngine,), {"core_frac": frac})


def row(label, res):
    r = res["returns"].dropna()
    m = compute_metrics(r)
    rc3 = rolling_cagr(r, years=3.0)
    rc5 = rolling_cagr(r, years=5.0)
    rec = recovery_duration(r)
    return {
        "전략": label,
        "CAGR": m.get("cagr", 0.0),
        "MaxDD": m.get("max_drawdown", 0.0),
        "Ulcer": m.get("ulcer", 0.0),
        "Martin": m.get("martin", 0.0),
        "r3w": rc3["worst"], "r3m": rc3["median"],
        "r5w": rc5["worst"], "r5m": rc5["median"],
        # 회복기간(달력일): 최대낙폭 회복 / 최장 underwater
        "rec_dd": rec["maxdd_recovery_days"],
        "uw_max": rec["max_underwater_days"],
        "리밸": int(res["rebalanced"].sum()),
        "tx": float(res["tx_cost"].sum()),
    }


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    no_vol = copy.deepcopy(base)
    no_vol.setdefault("vol_targeting", {})["enabled"] = False

    print(f"데이터 로딩 [{START} ~ {END}]...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    rows = []
    print("baseline(블렌드ON, core0)...")
    rows.append(row("baseline(core0)", run_engine(BacktestEngine, universe_px, signal_px, fred_history, base)))
    for frac in (0.30, 0.50, 0.70):
        print(f"core{int(frac*100)}+sat...")
        rows.append(row(f"core{int(frac*100)}+sat", run_engine(make_coresat(frac), universe_px, signal_px, fred_history, base)))
    print("순수홀드(core100, Goldilocks)...")
    rows.append(row("순수홀드(core100)", run_engine(StaticGoldilocksEngine, universe_px, signal_px, fred_history, no_vol)))

    # 고정 평가 기준(CLAUDE.md 규칙4): 롤링 CAGR · Ulcer · 회복기간 · Martin
    print(f"\n{'='*116}\n  core 비중 스윕 — 고정 4지표(롤링CAGR·Ulcer·회복기간·Martin) ({START}~{END})\n{'='*116}")
    h = (f"  {'전략':>16}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}{'tx':>7}")
    print(h)
    print("  " + "─" * (len(h) + 4))
    for r in rows:
        rec = "미회복" if r["rec_dd"] < 0 else f"{int(r['rec_dd'])}"
        print(f"  {r['전략']:>16}{r['r3w']:>9.1%}{r['r3m']:>9.1%}{r['r5w']:>9.1%}{r['Ulcer']:>8.2f}"
              f"{rec:>8}{int(r['uw_max']):>8}{r['Martin']:>8.2f}{r['CAGR']:>8.1%}{r['MaxDD']:>8.1%}{r['tx']:>7.2%}")

    print("\n  고정기준 해석: '롤링CAGR'은 경로의존 수익, 'Ulcer/회복일/최장UW'는 하락 고통,")
    print("  'Martin'(=CAGR/Ulcer)은 위험조정 효율. CAGR·MaxDD는 우측 보조참고.")
    print("  회복일=최대낙폭 저점→직전고점 회복 달력일, 최장UW=가장 길게 물려있던 달력일.")
    print("  주의: 백테스트는 USD 단일통화·단일 포트폴리오 — 라이브 USD합성 순환매 미반영.")


if __name__ == "__main__":
    main()
