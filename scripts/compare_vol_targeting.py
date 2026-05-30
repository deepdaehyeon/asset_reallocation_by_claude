"""
변동성 타겟팅 파라미터 스윕 — drawdown scaling 제거(OFF 고정) 전제.

2x2 사전 확인 (2010-2025): drawdown 제거는 ΔSharpe +0.004로 무영향 → OFF 고정.
vol targeting은 ΔSharpe -0.058이나 MaxDD +3pp·Vol -1.5pp 개선 (리스크 감소 도구).
→ 본 스윕은 그 트레이드오프를 파라미터로 최적화한다.

스윕 레버 (drawdown 항상 OFF):
  floor      : equity 축소 한도 (1.0=축소없음 ~ 0.35=최대 65% 컷)
  tv_mult    : regime_target_vol 전체 배율 (낮을수록 타이트 → 더 자주/많이 컷)

기준점: floor 0.65 + tv_mult 1.0 = 현재 config.

사용:
  python scripts/compare_vol_targeting.py [--start 2010-01-01] [--end 2025-04-30]
"""
from __future__ import annotations

import argparse
import sys
import warnings
from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from fetcher import fetch_fred_history  # noqa: E402
from data import load_all_prices  # noqa: E402
from engine import BacktestEngine  # noqa: E402
from metrics import compute_metrics  # noqa: E402


# (label, vol_enabled, floor, tv_mult)
VARIANTS = [
    ("vol_off",        False, None, None),
    # floor 스윕 (목표 배율 1.0 고정)
    ("floor_0.80",     True,  0.80, 1.0),
    ("floor_0.65*",    True,  0.65, 1.0),   # 현재 config
    ("floor_0.50",     True,  0.50, 1.0),
    ("floor_0.35",     True,  0.35, 1.0),
    # 목표 타이트함 스윕 (floor 0.65 고정)
    ("tv_x0.70",       True,  0.65, 0.70),
    ("tv_x0.85",       True,  0.65, 0.85),
    ("tv_x1.15",       True,  0.65, 1.15),
    ("tv_x1.30",       True,  0.65, 1.30),
]


def run_one(label, vol_on, floor, tv_mult, base_config, universe_px, signal_px, args, fred_history):
    cfg = deepcopy(base_config)
    cfg.setdefault("risk", {})["drawdown_scaling_enabled"] = False  # 항상 OFF
    vt = cfg.setdefault("vol_targeting", {})
    vt["enabled"] = vol_on
    if vol_on:
        vt["floor"] = floor
        base_rtv = base_config.get("vol_targeting", {}).get("regime_target_vol", {})
        vt["regime_target_vol"] = {k: round(v * tv_mult, 4) for k, v in base_rtv.items()}
        if "target_vol" in vt:
            vt["target_vol"] = round(base_config["vol_targeting"]["target_vol"] * tv_mult, 4)
    print(f"\n{'=' * 60}\n  {label}  (vol={vol_on}, floor={floor}, tv_mult={tv_mult})\n{'=' * 60}")
    engine = BacktestEngine(
        config=cfg, universe_px=universe_px, signal_px=signal_px,
        start=args.start, end=args.end, rebal_freq="W-FRI", tx_cost=0.001,
        fred_history=fred_history,
    )
    result = engine.run()
    m = compute_metrics(result["returns"])
    print(
        f"  CAGR {m.get('cagr',0):+.2%} | Vol {m.get('volatility',0):.2%} | "
        f"Sharpe {m.get('sharpe',0):.3f} | MaxDD {m.get('max_drawdown',0):.2%} | "
        f"Calmar {m.get('calmar',0):.3f}"
    )
    return {"label": label, "metrics": m}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--end", default="2025-04-30")
    args = p.parse_args()

    cfg_path = ROOT / "trading" / "config.yaml"
    with open(cfg_path) as f:
        base_config = yaml.safe_load(f)

    print(f"[1] 데이터 로딩  [{args.start} ~ {args.end}]")
    universe_px, signal_px = load_all_prices(
        config=base_config, start=args.start, end=args.end, use_cache=True
    )
    fred_history = fetch_fred_history(args.start, args.end)
    if not fred_history.empty:
        print(f"    FRED 매크로 {len(fred_history.columns)}개 포함")

    res = {}
    for label, vol_on, floor, tv_mult in VARIANTS:
        res[label] = run_one(label, vol_on, floor, tv_mult, base_config, universe_px, signal_px, args, fred_history)

    print(f"\n{'=' * 76}\n  비교 요약 (drawdown OFF 고정)\n{'=' * 76}")
    print(f"  {'variant':<15}{'Sharpe':>10}{'MaxDD':>10}{'Calmar':>10}{'Vol':>10}{'CAGR':>10}")
    print("  " + "-" * 65)
    for label, _, _, _ in VARIANTS:
        m = res[label]["metrics"]
        print(
            f"  {label:<15}"
            f"{m.get('sharpe',0):>10.3f}"
            f"{m.get('max_drawdown',0):>10.2%}"
            f"{m.get('calmar',0):>10.3f}"
            f"{m.get('volatility',0):>10.2%}"
            f"{m.get('cagr',0):>10.2%}"
        )

    # 권장: vol_off 대비 Calmar 최대 (리스크조정·드로우다운 종합), Sharpe 악화 ≤0.08
    base = res["vol_off"]["metrics"]
    print(f"\n  {'─' * 74}")
    print(f"  vol_off 기준: Sharpe {base.get('sharpe',0):.3f}, MaxDD {base.get('max_drawdown',0):.2%}, "
          f"Calmar {base.get('calmar',0):.3f}")
    best = None
    for label, vol_on, _, _ in VARIANTS:
        if not vol_on:
            continue
        m = res[label]["metrics"]
        ds = m.get("sharpe", 0) - base.get("sharpe", 0)
        if ds < -0.08:
            continue  # Sharpe 과도 희생 제외
        score = m.get("calmar", 0)
        if best is None or score > best[1]:
            best = (label, score, m)
    if best:
        m = best[2]
        print(f"  ▶ 권장: {best[0]} — Sharpe {m.get('sharpe',0):.3f}, MaxDD {m.get('max_drawdown',0):.2%}, "
              f"Calmar {m.get('calmar',0):.3f} (Sharpe 희생 ≤0.08 중 Calmar 최대)")


if __name__ == "__main__":
    main()
