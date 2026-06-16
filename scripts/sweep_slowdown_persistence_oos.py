"""
Goldilocks↔Slowdown 분리 2단계 — Slowdown 진입 지속요건 K일 스윕 (엔드투엔드 워크포워드 OOS).

질문(2026-06-15, 사용자): 진단([[experiment_2026-06-15_slowdown_episode_diagnosis]])에서 짧은
  Slowdown(≤2d, 89개=진입의 44%)은 Goldilocks와 forward-MDD가 동일한 노이즈, 방어가치는 긴
  Slowdown(>10d)에 집중으로 나왔다. 그럼 Slowdown '진입'에 K일 연속 지속요건을 걸어 짧은 블립을
  Goldilocks로 흡수하면 4지표가 개선되나? 아니면 라이브 blend가 이미 흡수해 무효인가([[feedback-
  agility-over-turnover-reduction]] A/B/C 전례)? 엔드투엔드 OOS로 판정.

방법:
  - 엔진과 동일한 윈도(signal_px[as_of-(hmm_lookback+60):as_of])로 일별 raw rule 레짐을 1회 precompute.
  - K별 causal 필터: raw Slowdown이 K일 연속 지속해야 Slowdown 채택, 그 전엔 직전 채택레짐 carry
    (짧은 블립=Goldi 흡수). 긴 Slowdown은 K일 지연 후 진입(지연=비용). 다른 레짐은 즉시 통과.
  - 필터된 일별 레짐을 날짜로 엔진에 주입(detect_regime 몽키패치) → 풀 엔진(blend+vol+core+drift)
    그대로 위에 얹어 4지표 TRAIN/TEST. K=1=현행(필터=항등) 대조군.

한계: rule raw 기준 필터지만 엔진 내부는 여전히 blend/HMM 사용 → 필터 효과가 blend에 흡수될 수
  있음(그게 바로 검증 대상). K일 지연은 긴 Slowdown 진입을 늦춰 방어손실 가능. 단일경로·USD단일.
  라이브 반영은 확인 후.
"""
from __future__ import annotations

import copy
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
from features import compute_features  # noqa: E402
from regime import detect_regime as _orig_detect  # noqa: E402
from fetcher import fetch_fred_history  # noqa: E402
import engine as eng  # noqa: E402
from walkforward_shrink_oos import run_config, SPLIT, build_engine  # noqa: E402
from compare_rule_timing_ab import START, END  # noqa: E402

K_VALUES = [1, 3, 5]  # K=1 = 현행(필터=항등)

# 몽키패치용 전역: 현재 주입할 일별 레짐 dict, 현재 as_of
_FILT: dict = {}


def _patched_get_regime(self, as_of):
    eng._EXP_AS_OF = as_of
    return _patched_get_regime._orig(self, as_of)


def _patched_detect(features):
    as_of = getattr(eng, "_EXP_AS_OF", None)
    if as_of is not None and as_of in _FILT:
        return _FILT[as_of]
    return _orig_detect(features)


def install_patches():
    _patched_get_regime._orig = eng.BacktestEngine._get_regime
    eng.BacktestEngine._get_regime = _patched_get_regime
    eng.detect_regime = _patched_detect


def precompute_raw(signal_px, lookback):
    """엔진과 동일 윈도로 일별 raw rule 레짐."""
    out = {}
    win = pd.Timedelta(days=lookback + 60)
    idx = signal_px.index
    for as_of in idx:
        sig = signal_px[as_of - win:as_of]
        if len(sig) < 30:
            continue
        out[as_of] = _orig_detect(compute_features(sig))
    return pd.Series(out).sort_index()


def filter_slowdown(raw_series, K):
    """causal Slowdown 지속요건 K. K=1이면 항등."""
    out = {}
    out_prev = "Goldilocks"
    streak = 0
    for ts, r in raw_series.items():
        if r == "Slowdown":
            streak += 1
            cur = "Slowdown" if streak >= K else out_prev
        else:
            streak = 0
            cur = r
        out[ts] = cur
        out_prev = cur
    return out


def main():
    global _FILT
    with open(ROOT / "trading" / "config.yaml") as f:
        base = yaml.safe_load(f)

    print(f"데이터 로딩 [{START} ~ {END}], split={SPLIT}...")
    universe_px, signal_px = load_all_prices(config=base, start=START, end=END, use_cache=True)
    fred_history = fetch_fred_history(START, END)

    # 엔진의 hmm_lookback 확인
    probe = build_engine(base, universe_px, signal_px, fred_history)
    lookback = int(getattr(probe, "hmm_lookback", 130))
    print(f"엔진 hmm_lookback={lookback}. 일별 raw 레짐 precompute 중...")
    raw_series = precompute_raw(signal_px, lookback)
    n_slow = int((raw_series == "Slowdown").sum())
    print(f"  raw Slowdown 일수 {n_slow} / {len(raw_series)}")

    install_patches()

    train_rows, test_rows = {}, {}
    print("\n실행 중 (각 K config 2010~2025 1회)...")
    for K in K_VALUES:
        _FILT = filter_slowdown(raw_series, K)
        n_slow_f = sum(1 for v in _FILT.values() if v == "Slowdown")
        label = f"K={K}" + (" (현행)" if K == 1 else "")
        print(f"  [{label}] 필터후 Slowdown 일수 {n_slow_f}")
        tr, te = run_config(copy.deepcopy(base), universe_px, signal_px, fred_history)
        train_rows[label] = tr
        test_rows[label] = te

    def table(title, rows):
        print(f"\n{'='*92}\n  {title}\n{'='*92}")
        h = (f"  {'설정':>14}{'Martin':>9}{'CAGR':>9}{'Ulcer':>8}{'최장UW':>8}"
             f"{'롤3y최악':>10}{'MaxDD':>9}{'tx':>8}")
        print(h); print("  " + "─" * (len(h)))
        bm = rows["K=1 (현행)"]["Martin"]
        for label, r in rows.items():
            d = r["Martin"] - bm
            mark = "" if label == "K=1 (현행)" else f"  Δ{d:+.2f}"
            print(f"  {label:>14}{r['Martin']:>9.2f}{r['CAGR']:>9.1%}{r['Ulcer']:>8.2f}"
                  f"{int(r['uw_max']):>8}{r['r3w']:>10.1%}{r['MaxDD']:>9.1%}{r['tx']:>8.2%}{mark}")

    table("학습창 TRAIN 2010-01~2018-12 (in-sample)", train_rows)
    table(f"검증창 TEST {SPLIT[:7]}~{END} (OUT-OF-SAMPLE)", test_rows)

    print("\n  판정(규칙4): TEST Martin·Ulcer·회복기간이 K>1에서 개선되면 짧은블립 억제가 실효.")
    print("  K↑에 단조 악화면 blend가 이미 흡수(현행 유지). 긴 Slowdown 진입지연 비용도 함께 관찰.")
    print("  주의: rule raw 필터·blend 내부 흡수 가능·단일경로·USD단일. 라이브 반영은 확인 후.")


if __name__ == "__main__":
    main()
