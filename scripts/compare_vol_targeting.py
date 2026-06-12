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
from metrics import compute_metrics, rolling_cagr, recovery_duration  # noqa: E402


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
    r = result["returns"].dropna()
    m = compute_metrics(r)
    rc3 = rolling_cagr(r, years=3.0)
    rc5 = rolling_cagr(r, years=5.0)
    rec = recovery_duration(r)
    print(
        f"  Martin {m.get('martin',0):.2f} | Ulcer {m.get('ulcer',0):.2f} | "
        f"롤3y최악 {rc3['worst']:+.1%} | 회복 {int(rec['maxdd_recovery_days'])}d | "
        f"(CAGR {m.get('cagr',0):+.2%} MaxDD {m.get('max_drawdown',0):.2%})"
    )
    return {
        "label": label, "metrics": m,
        "r3w": rc3["worst"], "r3m": rc3["median"], "r5w": rc5["worst"], "r5m": rc5["median"],
        "rec_dd": rec["maxdd_recovery_days"], "uw_max": rec["max_underwater_days"],
    }


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

    # 고정 평가 기준(CLAUDE.md 규칙4): 롤링 CAGR · Ulcer · 회복기간 · Martin
    h = (f"  {'variant':<15}{'롤3y최악':>9}{'롤3y중앙':>9}{'롤5y최악':>9}{'Ulcer':>8}"
         f"{'회복일':>8}{'최장UW':>8}{'Martin':>8}{'│CAGR':>8}{'MaxDD':>8}")
    print(f"\n{'=' * (len(h)+4)}\n  비교 요약 — 고정 4지표(롤링CAGR·Ulcer·회복기간·Martin), drawdown OFF 고정\n{'=' * (len(h)+4)}")
    print(h)
    print("  " + "─" * (len(h) + 2))
    for label, _, _, _ in VARIANTS:
        d = res[label]; m = d["metrics"]
        rec = "미회복" if d["rec_dd"] < 0 else f"{int(d['rec_dd'])}"
        print(
            f"  {label:<15}{d['r3w']:>9.1%}{d['r3m']:>9.1%}{d['r5w']:>9.1%}{m.get('ulcer',0):>8.2f}"
            f"{rec:>8}{int(d['uw_max']):>8}{m.get('martin',0):>8.2f}{m.get('cagr',0):>8.1%}{m.get('max_drawdown',0):>8.1%}"
        )

    # 권장: Martin(=CAGR/Ulcer, 1차 판정) 최대. 회복기간·롤3y최악은 보조 점검.
    base = res["vol_off"]
    print(f"\n  {'─' * (len(h)-2)}")
    print(f"  vol_off 기준: Martin {base['metrics'].get('martin',0):.2f}, Ulcer {base['metrics'].get('ulcer',0):.2f}, "
          f"롤3y최악 {base['r3w']:+.1%}, 회복 {int(base['rec_dd'])}d")
    best = max(
        (res[label] for label, vol_on, _, _ in VARIANTS if vol_on),
        key=lambda d: d["metrics"].get("martin", 0),
    )
    bm = best["metrics"]
    rec = "미회복" if best["rec_dd"] < 0 else f"{int(best['rec_dd'])}d"
    print(f"  ▶ 권장: {best['label']} — Martin {bm.get('martin',0):.2f} (최대), Ulcer {bm.get('ulcer',0):.2f}, "
          f"롤3y최악 {best['r3w']:+.1%}, 회복 {rec}")
    print("  고정기준 해석: Martin 1차 판정, 동률·근소차는 회복기간·롤3y최악으로 가른다. CAGR·MaxDD는 보조참고.")


if __name__ == "__main__":
    main()
