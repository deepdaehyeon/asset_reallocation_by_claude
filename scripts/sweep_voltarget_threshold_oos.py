"""
워크포워드 OOS: vol targeting 레짐별 목표변동성을 배수로 풀고/조이며 4지표 반응 곡선.

질문(2026-06-15, 사용자): 비중은 엔진이 흡수해 무력했지만, 진짜 조종수는 vol targeting이다
  ([[project-voltarget-blend-defense-engine]]). 그럼 레짐별 목표변동성(regime_target_vol)을
  조금씩 풀면(높이면=덜 깎음) / 조이면(낮추면=더 깎음) 4지표가 어떻게 움직이나? 현행(k=1.0)이
  스윗스폿인지, 풀거나 조이면 개선되는지 본다. 비중과 달리 vol은 흡수 안 되는 레버라 효과 기대↑.

방법:
  - 현행 regime_target_vol(G0.13/Refl0.11/Slow0.09/Stag0.08/Crisis0.06)에 배수 k를 곱함.
    k<1 조임(더 깎음·방어↑), k>1 풂(덜 깎음·수익↑). default target_vol(0.10)도 같은 k.
  - OFF 앵커(vol_targeting.enabled=false)로 "완전히 푼" 극단 동봉(ablate 참고치).
  - 각 config 2010~2025 1회(config 고정 → 2019+ 진짜 OOS), TRAIN/TEST 4지표. drift·4지표.

한계·교란(규칙5): core30이 자산 30%를 vol 면제(Goldilocks 고정)라 레버는 위성 70%에만 작용 →
  효과 희석. floor 0.65가 고변동일 클램프(주식 최대 35%만 축소)라 조임(k↓)의 효과는 floor에서 포화
  가능. 단일 경로(COVID·Bear22 각1회)·USD단일·in-sample 구성 아님(엔진내 동시점). 라이브는 확인 후.
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
from fetcher import fetch_fred_history  # noqa: E402
from walkforward_shrink_oos import run_config, SPLIT  # noqa: E402
from compare_rule_timing_ab import START, END  # noqa: E402

MULTIPLIERS = [0.75, 0.875, 1.0, 1.25, 1.5, 2.0]
VOL_CAP = 0.40  # 과도한 목표는 사실상 무클램프 — 상한


def scale_vol(cfg, k):
    vc = cfg.setdefault("vol_targeting", {})
    rtv = dict(vc.get("regime_target_vol", {}))
    vc["regime_target_vol"] = {r: min(v * k, VOL_CAP) for r, v in rtv.items()}
    if "target_vol" in vc:
        vc["target_vol"] = min(float(vc["target_vol"]) * k, VOL_CAP)
    return cfg


def vol_off(cfg):
    cfg.setdefault("vol_targeting", {})["enabled"] = False
    return cfg


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)

    print(f"데이터 로딩 [{START} ~ {END}], split={SPLIT}...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    runs = []
    for k in MULTIPLIERS:
        label = f"k={k:.3g}" + (" (현행)" if k == 1.0 else (" 조임" if k < 1 else " 풂"))
        runs.append((label, scale_vol(copy.deepcopy(base), k)))
    runs.append(("OFF (완전히 풂)", vol_off(copy.deepcopy(base))))

    print("\n실행 중 (각 config 2010~2025 1회)...")
    train_rows, test_rows = {}, {}
    for label, cfg in runs:
        print(f"  [{label}]")
        tr, te = run_config(cfg, universe_px, signal_px, fred_history)
        train_rows[label] = tr
        test_rows[label] = te

    base_label = "k=1 (현행)"

    def table(title, rows):
        print(f"\n{'='*104}\n  {title}\n{'='*104}")
        h = (f"  {'설정':>18}{'Martin':>9}{'CAGR':>9}{'Ulcer':>8}{'최장UW':>8}"
             f"{'롤3y최악':>10}{'MaxDD':>9}{'tx':>8}")
        print(h); print("  " + "─" * (len(h)))
        bm = rows[base_label]["Martin"]
        for label, r in rows.items():
            d = r["Martin"] - bm
            mark = "" if label == base_label else f"  Δ{d:+.2f}"
            print(f"  {label:>18}{r['Martin']:>9.2f}{r['CAGR']:>9.1%}{r['Ulcer']:>8.2f}"
                  f"{int(r['uw_max']):>8}{r['r3w']:>10.1%}{r['MaxDD']:>9.1%}{r['tx']:>8.2%}{mark}")

    table("학습창 TRAIN 2010-01~2018-12 (in-sample)", train_rows)
    table(f"검증창 TEST {SPLIT[:7]}~{END} (OUT-OF-SAMPLE)", test_rows)

    print("\n  판정(규칙4): TEST Martin이 k=1 근처서 최대면 현행이 스윗스폿. 조임(k<1)서 peak면 더")
    print("  방어로 개선, 풂(k>1)서 peak면 과방어였음. OFF가 크게 나쁘면 vol이 진짜 조종수 재확인.")
    print("  주의: core30 희석·floor 0.65 클램프·단일경로·USD단일. 라이브 반영은 확인 후.")


if __name__ == "__main__":
    main()
