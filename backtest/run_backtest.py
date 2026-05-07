"""
백테스팅 CLI 진입점.

사용법:
  python run_backtest.py                       # 전체 기간 백테스트
  python run_backtest.py --mode crisis         # 위기 구간 집중 분석
  python run_backtest.py --mode sensitivity    # 파라미터 민감도 검증
  python run_backtest.py --start 2015-01-01 --end 2024-12-31
  python run_backtest.py --rebal monthly       # 월별 리밸런싱
  python run_backtest.py --no-cache            # 가격 데이터 재다운로드

주의:
  - 한국 ETF(379800 등)는 동일 기초지수 미국 ETF로 프록시 처리
  - DBMF는 2019-05-10 이전 데이터 없음 → 해당 구간 비중 재배분
  - PLTR은 2020-09-30 이전 데이터 없음 → 동일
  - 환율 효과 미반영 (USD 단일 통화 수익률로 계산)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))

from data import load_all_prices
from engine import BacktestEngine
from metrics import compute_metrics, crisis_analysis, drawdown_series, regime_breakdown
from robustness import (
    run_subperiod_analysis,
    run_regime_intent_validation,
    run_weight_perturbation,
)
from sensitivity import run_all_sensitivity

CONFIG_PATH = ROOT / "trading" / "config.yaml"
DEFAULT_START = "2010-01-01"
DEFAULT_END   = "2025-04-30"

REBAL_FREQ_MAP = {
    "weekly":    "W-FRI",
    "biweekly":  "2W-FRI",
    "monthly":   "BMS",
}

# 벤치마크: 60/40 (SPY 60% + IEF 40%)
_BENCHMARK = {"SPY": 0.60, "IEF": 0.40}


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def build_benchmark(
    universe_px: pd.DataFrame,
    signal_px: pd.DataFrame,
    start: str,
    end: str,
) -> pd.Series:
    """60/40 포트폴리오 일별 수익률."""
    parts = []
    for t, w in _BENCHMARK.items():
        if t in universe_px.columns:
            px = universe_px[t]
        elif t in signal_px.columns:
            px = signal_px[t]
        else:
            continue
        parts.append(px.pct_change() * w)
    if not parts:
        return pd.Series(dtype=float)
    return pd.concat(parts, axis=1).sum(axis=1)[start:end]


def _fmt(m: dict) -> str:
    return (
        f"CAGR {m.get('cagr', 0):+.1%} | "
        f"Vol {m.get('volatility', 0):.1%} | "
        f"Sharpe {m.get('sharpe', 0):.2f} | "
        f"MaxDD {m.get('max_drawdown', 0):.1%} | "
        f"Calmar {m.get('calmar', 0):.2f}"
    )


def print_section(title: str) -> None:
    print(f"\n{'━'*54}")
    print(f"  {title}")
    print(f"{'━'*54}")


def run_full(config, universe_px, signal_px, args) -> pd.DataFrame:
    print_section(f"전체 기간 백테스트  [{args.start} ~ {args.end}]")
    print(f"  거래비용 {args.tx_cost:.2%} / 리밸런싱 {args.rebal}")

    engine = BacktestEngine(
        config=config,
        universe_px=universe_px,
        signal_px=signal_px,
        start=args.start,
        end=args.end,
        rebal_freq=REBAL_FREQ_MAP[args.rebal],
        tx_cost=args.tx_cost,
    )
    result = engine.run()
    m = compute_metrics(result["returns"])

    print(f"\n  전략:        {_fmt(m)}")
    print(f"  총 수익률:   {m.get('total_return', 0):+.1%}")
    print(f"  기간:        {m.get('n_days', 0)}일 ({m.get('n_days', 0)/252:.1f}년)")

    bm = build_benchmark(universe_px, signal_px, args.start, args.end)
    if not bm.empty:
        bm_m = compute_metrics(bm)
        print(f"  벤치마크(60/40): {_fmt(bm_m)}")

    # 레짐별 성과
    rb = regime_breakdown(result["returns"], result["regime"])
    if not rb.empty:
        print_section("레짐별 성과")
        cols = ["cagr", "volatility", "sharpe", "max_drawdown", "days", "pct_time"]
        available_cols = [c for c in cols if c in rb.columns]
        print(rb[available_cols].to_string())

    # 레짐 비중
    counts = result["regime"].value_counts()
    total = len(result)
    print_section("레짐 체류 비중")
    for regime, cnt in counts.items():
        bar = "█" * int(cnt / total * 30)
        print(f"  {regime:<12} {cnt:4d}일 ({cnt/total:.0%})  {bar}")

    # 거래 통계
    rebal_n = result["rebalanced"].sum()
    total_tx = result["tx_cost"].sum()
    print_section("거래 통계")
    print(f"  리밸런싱 횟수: {rebal_n}회")
    print(f"  누적 거래비용: {total_tx:.3%}")

    return result


def run_crisis(config, universe_px, signal_px, args) -> pd.DataFrame:
    engine = BacktestEngine(
        config=config,
        universe_px=universe_px,
        signal_px=signal_px,
        start=args.start,
        end=args.end,
        rebal_freq=REBAL_FREQ_MAP[args.rebal],
        tx_cost=args.tx_cost,
    )
    result = engine.run()
    bm = build_benchmark(universe_px, signal_px, args.start, args.end)

    print_section("위기 구간 분석")
    s_crisis = crisis_analysis(result["returns"])
    bm_crisis = crisis_analysis(bm) if not bm.empty else pd.DataFrame()

    if not s_crisis.empty:
        print("\n  전략 성과:")
        cols = ["total_return", "max_drawdown", "sharpe", "calmar"]
        available_cols = [c for c in cols if c in s_crisis.columns]
        print(s_crisis[available_cols].to_string())

    if not bm_crisis.empty:
        print("\n  벤치마크 (60/40):")
        available_cols = [c for c in cols if c in bm_crisis.columns]
        print(bm_crisis[available_cols].to_string())

    print_section("위기 구간 레짐 분포")
    crisis_periods = {
        "GFC (2008-2009)":    ("2008-01-01", "2009-03-31"),
        "COVID Crash (2020)": ("2020-02-19", "2020-04-30"),
        "Bear 2022":          ("2022-01-01", "2022-12-31"),
    }
    for name, (s, e) in crisis_periods.items():
        slc = result[s:e]
        if slc.empty:
            continue
        dist = slc["regime"].value_counts()
        dist_str = "  ".join(f"{r}:{cnt}일" for r, cnt in dist.items())
        print(f"  {name}: {dist_str}")

    return result


def run_sens(config, universe_px, signal_px, args) -> dict:
    print_section("파라미터 민감도 분석")
    print("  목적: 결과가 파라미터 변화에 과도하게 민감하지 않음을 확인")
    print("  (민감도가 낮을수록 과적합 가능성 낮음)")

    results = run_all_sensitivity(
        base_config=config,
        universe_px=universe_px,
        signal_px=signal_px,
        start=args.start,
        end=args.end,
        rebal_freq=REBAL_FREQ_MAP[args.rebal],
        tx_cost=args.tx_cost,
    )

    print_section("민감도 요약 (CAGR 변화 범위)")
    for param, df in results.items():
        if df.empty or "cagr" not in df.columns:
            continue
        rng = df["cagr"].max() - df["cagr"].min()
        bar = "█" * int(rng * 200)
        robust = "✓ 로버스트" if rng < 0.02 else ("△ 보통" if rng < 0.04 else "✗ 민감")
        print(f"  {param:<35} 범위 {rng:.1%}  {robust}  {bar}")

    return results


def run_robustness(config, universe_px, signal_px, args) -> None:
    """
    레짐별 비중 로버스트니스 검증.

    1단계: 서브기간 일관성 — 5개 시장 국면에서 성과 일관성
    2단계: 레짐 의도 달성 — 공격/방어 역할 수행 여부 (전체 기간 백테스트 재사용)
    3단계: 비중 교란 테스트 — 핵심 비중 ±25% 변화 내성 (--perturb 플래그 필요)
    """
    rebal_freq = REBAL_FREQ_MAP[args.rebal]
    bm = build_benchmark(universe_px, signal_px, args.start, args.end)

    # ── 1단계: 서브기간 일관성 ────────────────────────────────────────────────
    print_section("1단계: 서브기간 일관성 분석")
    print("  목적: 다양한 시장 국면에서 성과 일관성 확인 (과적합 비중은 특정 시기에만 잘 작동)")
    print("  기준: 모든 기간에서 Sharpe > 0, BM 대비 DD 절감 유지\n")

    subperiod_df = run_subperiod_analysis(
        base_config=config,
        universe_px=universe_px,
        signal_px=signal_px,
        bm_returns=bm,
        rebal_freq=rebal_freq,
        tx_cost=args.tx_cost,
    )
    if not subperiod_df.empty:
        fmt_cols = {
            "전략 CAGR": "{:+.1%}", "Sharpe": "{:.2f}",
            "전략 MaxDD": "{:.1%}", "BM CAGR": "{:+.1%}",
            "BM MaxDD": "{:.1%}", "초과수익": "{:+.1%}",
            "DD 절감": "{:+.1%}",
        }
        display = subperiod_df.copy()
        for col, fmt in fmt_cols.items():
            if col in display.columns:
                display[col] = display[col].map(lambda v, f=fmt: f.format(v))
        print(f"\n{display.to_string()}")

        sharpes = subperiod_df["Sharpe"]
        positive_periods = (sharpes > 0).sum()
        sharpe_range = sharpes.max() - sharpes.min()
        dd_saved = subperiod_df["DD 절감"].gt(0).sum()
        print(f"\n  일관성 요약:")
        robust_sharpe = "✓ 안정적" if sharpe_range < 0.50 else ("△ 보통" if sharpe_range < 0.80 else "✗ 불안정")
        print(f"    Sharpe 양수 기간: {positive_periods}/{len(sharpes)}  범위 {sharpes.min():.2f}~{sharpes.max():.2f}  {robust_sharpe}")
        print(f"    DD 절감 성공 기간: {dd_saved}/{len(subperiod_df)}")

    # ── 2단계: 레짐 의도 달성 검증 ────────────────────────────────────────────
    print_section("2단계: 레짐 의도 달성 검증")
    print("  목적: 각 레짐에서 설정한 비중이 의도한 역할(공격/방어)을 실제로 수행하는지")
    print("  기준: 성장 레짐 → CAGR > 0% / 방어 레짐 → |전략 DD| < |BM DD|\n")
    print("  (전체 기간 백테스트 실행 중...)")

    engine = BacktestEngine(
        config=config,
        universe_px=universe_px,
        signal_px=signal_px,
        start=args.start,
        end=args.end,
        rebal_freq=rebal_freq,
        tx_cost=args.tx_cost,
    )
    full_result = engine.run()

    intent_df = run_regime_intent_validation(full_result, bm)
    if not intent_df.empty:
        fmt_cols = {
            "전략 CAGR": "{:+.1%}", "BM CAGR": "{:+.1%}",
            "전략 MaxDD": "{:.1%}", "BM MaxDD": "{:.1%}",
        }
        display = intent_df.copy()
        for col, fmt in fmt_cols.items():
            if col in display.columns:
                display[col] = display[col].map(lambda v, f=fmt: f.format(v))
        print(f"\n{display.to_string()}")

        n_ok = (intent_df["달성"] == "✓ OK").sum()
        n_total = len(intent_df)
        verdict = "✓ 모든 레짐에서 의도 달성" if n_ok == n_total else f"△ {n_ok}/{n_total} 레짐 의도 달성"
        print(f"\n  의도 달성 요약: {n_ok}/{n_total}  {verdict}")

    # ── 3단계: 비중 교란 테스트 (선택적) ──────────────────────────────────────
    if not getattr(args, "perturb", False):
        print_section("3단계: 비중 교란 테스트")
        print("  (건너뜀 — --perturb 플래그로 활성화)")
        print("  목적: 핵심 비중을 ±25% 교란해도 전략 성격이 유지되는지")
        print("  주의: 레짐 수 × 스케일 수 = 25회 추가 백테스트 → 상당한 시간 소요")
        return

    print_section("3단계: 비중 교란 테스트 (핵심 비중 ×0.75 ~ ×1.25)")
    print("  목적: 비중이 ±25% 바뀌어도 전략의 공격/방어 성격이 유지되는지")
    print("  기준: CAGR 범위 < 2% → 로버스트 / 2~4% → 보통 / >4% → 민감\n")

    perturb_results, base_m = run_weight_perturbation(
        base_config=config,
        universe_px=universe_px,
        signal_px=signal_px,
        start=args.start,
        end=args.end,
        rebal_freq=rebal_freq,
        tx_cost=args.tx_cost,
    )

    print_section("비중 교란 요약")
    print(f"  기준 CAGR: {base_m.get('cagr', 0):+.1%}  Sharpe: {base_m.get('sharpe', 0):.2f}")
    print()
    for regime, df in perturb_results.items():
        if df.empty:
            continue
        cagr_range = df["cagr"].max() - df["cagr"].min()
        judge = "✓ 로버스트" if cagr_range < 0.02 else ("△ 보통" if cagr_range < 0.04 else "✗ 민감")
        targets = ", ".join(__import__("robustness").PERTURB_TARGETS.get(regime, []))
        print(f"  {regime:<14} ({targets})")
        for scale, row in df.iterrows():
            marker = " ← 기본값" if row["is_base"] else ""
            print(
                f"    ×{scale:.3f}  CAGR {row['cagr']:+.1%}  Sharpe {row['sharpe']:.2f}"
                f"  MaxDD {row['max_drawdown']:.1%}  차이 {row['cagr_diff']:+.1%}{marker}"
            )
        bar = "█" * int(cagr_range * 200)
        print(f"    CAGR 범위 {cagr_range:.1%}  {judge}  {bar}\n")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="자산 배분 시스템 백테스터",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--mode",
        choices=["full", "crisis", "sensitivity", "robustness"],
        default="full",
        help=(
            "full: 전체 기간 / crisis: 위기 구간 / "
            "sensitivity: 파라미터 민감도 / robustness: 레짐 비중 로버스트니스"
        ),
    )
    p.add_argument("--start",    default=DEFAULT_START, help="시작일 YYYY-MM-DD")
    p.add_argument("--end",      default=DEFAULT_END,   help="종료일 YYYY-MM-DD")
    p.add_argument(
        "--rebal",
        choices=["weekly", "biweekly", "monthly"],
        default="weekly",
        help="리밸런싱 주기 (기본 weekly)",
    )
    p.add_argument(
        "--tx-cost",
        type=float,
        default=0.001,
        metavar="COST",
        help="편도 거래비용 비율 (기본 0.001 = 0.1%%)",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="캐시 무시하고 가격 데이터 재다운로드",
    )
    p.add_argument(
        "--perturb",
        action="store_true",
        help="robustness 모드에서 3단계 비중 교란 테스트 활성화 (시간 오래 걸림)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()

    print(f"데이터 로딩 중  [{args.start} ~ {args.end}]...")
    universe_px, signal_px = load_all_prices(
        config=config,
        start=args.start,
        end=args.end,
        use_cache=not args.no_cache,
    )
    print(f"  유니버스 {len(universe_px.columns)}개 종목, {len(universe_px)}거래일")
    print(f"  신호 티커 {len(signal_px.columns)}개")

    if args.mode == "full":
        run_full(config, universe_px, signal_px, args)
    elif args.mode == "crisis":
        run_crisis(config, universe_px, signal_px, args)
    elif args.mode == "sensitivity":
        run_sens(config, universe_px, signal_px, args)
    elif args.mode == "robustness":
        run_robustness(config, universe_px, signal_px, args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
