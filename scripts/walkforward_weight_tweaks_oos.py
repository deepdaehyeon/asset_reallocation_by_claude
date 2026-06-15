"""
워크포워드 OOS: 사용자 지정 비중 미세조정(골디락스·리플레이션 가치주↑)이 미지 구간에서
  4지표를 개선하는가? — 라이브 config 수정 전 측정.

질문(2026-06-15): 사용자 "레짐별로 비중을 하나씩 고쳐나가자".
   G [Goldilocks] equity_factor 5%→7%(+2%), gold 10%→8%(−2%)
      — 진단상 금은 약(Martin -0.14·8위), 가치주는 강(13.98·2위). 약→강 교체.
   R [Reflation] equity_factor 8%→10%(+2%), managed_futures 12%→10%(−2%)
      — DBMF는 옛 Sharpe 기준 잔재(현 Martin -1.58·9위), 가치주는 강(19.64·2위). 약→강 교체.
  C1·C2(앞 실험)는 OOS에서 죽었으나, 이 둘은 진단 *방향*과 정합 → OOS 생존 여부 측정.

방법(규칙4): walkforward_shrink_oos 하니스 재사용. 각 config 2010~2025 1회(전구간 고정 →
  2019~ OOS), drift 모드, TRAIN(≤2018)/TEST(≥2019)로 4지표.
한계: TEST 6.3년 단일경로. 엔진(vol targeting·cap·core30·drift) 흡수 가능. 진단이지 자동채택 아님.
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


def apply_g(cfg):
    g = cfg["regime_targets"]["Goldilocks"]
    g["equity_factor"] = round(g["equity_factor"] + 0.02, 4)
    g["gold"] = round(g["gold"] - 0.02, 4)
    return cfg


def apply_r(cfg):
    r = cfg["regime_targets"]["Reflation"]
    r["equity_factor"] = round(r["equity_factor"] + 0.02, 4)
    r["managed_futures"] = round(r["managed_futures"] - 0.02, 4)
    return cfg


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)
    print(f"데이터 로딩 [{START} ~ {END}], split={SPLIT}...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    runs = [
        ("현행(baseline)", copy.deepcopy(base)),
        ("G 골디 factor+2/gold-2", apply_g(copy.deepcopy(base))),
        ("R 리플 factor+2/DBMF-2", apply_r(copy.deepcopy(base))),
        ("G+R 둘 다", apply_r(apply_g(copy.deepcopy(base)))),
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
        h = (f"  {'전략':>24}{'Martin':>9}{'CAGR':>9}{'Ulcer':>8}{'최장UW':>8}"
             f"{'롤3y최악':>10}{'MaxDD':>9}{'tx':>8}")
        print(h)
        print("  " + "─" * (len(h)))
        base_m = rows["현행(baseline)"]["Martin"]
        for label, r in rows.items():
            d = r["Martin"] - base_m
            mark = "" if label == "현행(baseline)" else f"  ΔMartin {d:+.2f}"
            print(f"  {label:>24}{r['Martin']:>9.2f}{r['CAGR']:>9.1%}{r['Ulcer']:>8.2f}"
                  f"{int(r['uw_max']):>8}{r['r3w']:>10.1%}{r['MaxDD']:>9.1%}{r['tx']:>8.2%}{mark}")

    print_table("학습창 TRAIN 2010-01 ~ 2018-12 (in-sample)", train_rows)
    print_table(f"검증창 TEST {SPLIT[:7]} ~ {END} (OUT-OF-SAMPLE, COVID+Bear22 포함)", test_rows)

    print("\n  판정: TEST ΔMartin>0 + 회복(UW)·MaxDD 동반개선이면 채택검토. 미미하면 엔진흡수→현행유지.")
    print("  주의: TEST 6.3년 단일경로. drift 모드(라이브 동일). 라이브 config는 확인 후 수정.")


if __name__ == "__main__":
    main()
