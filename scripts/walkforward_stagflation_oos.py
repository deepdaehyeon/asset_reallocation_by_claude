"""
워크포워드 OOS: 스태그플레이션 commodity 축소분을 *에피소드-강건* 행선지(cash·tips)로 옮기면
  미지 구간에서 4지표 개선되는가? — C2(→bond)가 깨진 뒤 대안 검증.

질문(2026-06-15): stagflation_episode_split.py 결과 = 채권·금·tips는 2010년대↔2022 부호 역전,
  cash만 양 시대 양수. C2(commodity→bond)는 채권의 에피소드 의존성 탓에 OOS 기각(-0.27).
  → 에피소드에 안 휘둘리는 cash(및 소량 tips)로 옮기면 살아남는지 측정.
   S1 commodity 18→10 → cash 14→22
   S2 commodity 18→10 → tips 8→10(+2, cap) + cash 14→20(+6)
   S3 commodity 18→13 → cash 14→19 (완화판)
제약: gold는 cap 18%로 이미 만석(행선지 불가). tips cap 10%(8→10만 가능).
방법(규칙4): walkforward_shrink_oos 하니스. TRAIN/TEST 4지표. drift 모드.
한계: 스태그는 TEST 내 2022 집중·소표본. 엔진 흡수 가능.
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


def s1(cfg):
    s = cfg["regime_targets"]["Stagflation"]
    s["commodity"] = 0.10
    s["cash"] = round(s["cash"] + 0.08, 4)
    return cfg


def s2(cfg):
    s = cfg["regime_targets"]["Stagflation"]
    s["commodity"] = 0.10
    s["bond_tips"] = 0.10
    s["cash"] = round(s["cash"] + 0.06, 4)
    return cfg


def s3(cfg):
    s = cfg["regime_targets"]["Stagflation"]
    s["commodity"] = 0.13
    s["cash"] = round(s["cash"] + 0.05, 4)
    return cfg


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}], split={SPLIT}...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    runs = [
        ("현행(baseline)", copy.deepcopy(base)),
        ("S1 comm18→10→cash", s1(copy.deepcopy(base))),
        ("S2 comm→tips+cash", s2(copy.deepcopy(base))),
        ("S3 comm18→13→cash", s3(copy.deepcopy(base))),
    ]

    print("\n실행 중 (각 config 2010~2025 1회, 수익 슬라이스)...")
    train_rows, test_rows = {}, {}
    for label, cfg in runs:
        print(f"  [{label}]")
        tr, te = run_config(cfg, universe_px, signal_px, fred_history)
        train_rows[label] = tr
        test_rows[label] = te

    def print_table(title, rows):
        print(f"\n{'='*108}")
        print(f"  {title}")
        print(f"{'='*108}")
        h = (f"  {'전략':>22}{'Martin':>9}{'CAGR':>9}{'Ulcer':>8}{'최장UW':>8}"
             f"{'롤3y최악':>10}{'MaxDD':>9}{'tx':>8}")
        print(h)
        print("  " + "─" * (len(h)))
        base_m = rows["현행(baseline)"]["Martin"]
        for label, r in rows.items():
            d = r["Martin"] - base_m
            mark = "" if label == "현행(baseline)" else f"  ΔMartin {d:+.2f}"
            print(f"  {label:>22}{r['Martin']:>9.2f}{r['CAGR']:>9.1%}{r['Ulcer']:>8.2f}"
                  f"{int(r['uw_max']):>8}{r['r3w']:>10.1%}{r['MaxDD']:>9.1%}{r['tx']:>8.2%}{mark}")

    print_table("학습창 TRAIN 2010-01 ~ 2018-12 (in-sample)", train_rows)
    print_table(f"검증창 TEST {SPLIT[:7]} ~ {END} (OUT-OF-SAMPLE, 2022 스태그 포함)", test_rows)

    print("\n  판정: TEST ΔMartin>0 + 회복(UW)·MaxDD 동반개선이면 채택검토. 미미하면 엔진흡수→현행유지.")
    print("  주의: 스태그는 TEST 내 2022 집중. cash는 양 에피소드 강건했던 유일 자산. 라이브는 확인 후.")


if __name__ == "__main__":
    main()
