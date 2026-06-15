"""
레짐 발생확률 + 전이확률 — 현재 라이브 config·detect_regime 기준 재계산.

질문(2026-06-15, 사용자): 레짐별 발생확률(시간 점유율)과 전이확률(어느 레짐→어느 레짐)을
  현재 코드로 구한다. 기존 docs/regime_transition_matrix_2026-05-28.md는 6d86b79 시점이라
  최신 detect_regime으로 다시 뽑는다.

방법:
  - 레짐 = 일별 acting regime(rule, detect_regime(compute_features(...))). 라이브와 동일 로직.
  - 발생확률 = 각 레짐 라벨일수 / 전체일수(= 시간 점유율, stationary 근사).
  - 전이확률 = 인접일 (오늘→내일) 레짐쌍 카운트. 두 종류 동봉:
      (a) self 포함 = 하루 단위 마르코프 P(내일=j | 오늘=i) — 대각선이 지속확률.
      (b) self 제외 = 레짐이 바뀌는 사건만 — "다음에 어느 레짐으로 갈아타나".
  - 평균 연속일수·진입횟수도 같이.

한계: rule 레짐(라이브 RegimeFilter·blend 평활 미적용 raw). 소표본 레짐(Stag·Crisis) 추정
  불안정. 일단위 전이라 라벨 노이즈가 self-transition을 부풀릴 수 있음(둘 다 표시로 보완).
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "trading"))
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "scripts"))

warnings.filterwarnings("ignore", message="Model is not converging.*")

from data import load_all_prices  # noqa: E402
from regime_class_correlation import daily_rule_regime, START, END  # noqa: E402

REGIMES = ["Goldilocks", "Reflation", "Slowdown", "Stagflation", "Crisis", "Transition"]


def main():
    with open(ROOT / "trading" / "config.yaml") as f:
        cfg = yaml.safe_load(f)

    print(f"데이터 로딩 [{START} ~ {END}]...")
    _, signal_px = load_all_prices(config=cfg, start=START, end=END, use_cache=True)

    print("일별 레짐 분류 중 (detect_regime)...")
    reg = daily_rule_regime(signal_px)
    labels = [r for r in REGIMES if r in set(reg)]
    n = len(reg)
    span_years = (reg.index[-1] - reg.index[0]).days / 365.25

    # 1) 발생확률 (시간 점유율)
    counts = reg.value_counts().reindex(labels).fillna(0).astype(int)
    occ = counts / n

    # 2) 평균 연속일수 / 진입횟수 (run-length)
    runs = {l: [] for l in labels}
    prev, length = None, 0
    for v in reg.values:
        if v == prev:
            length += 1
        else:
            if prev is not None:
                runs[prev].append(length)
            prev, length = v, 1
    runs[prev].append(length)
    avg_run = {l: (np.mean(runs[l]) if runs[l] else 0) for l in labels}
    n_enter = {l: len(runs[l]) for l in labels}

    # 3) 전이 카운트 (오늘→내일)
    cur = reg.values[:-1]
    nxt = reg.values[1:]
    cnt = pd.DataFrame(0, index=labels, columns=labels)
    for a, b in zip(cur, nxt):
        cnt.loc[a, b] += 1

    # (a) self 포함 마르코프
    P = cnt.div(cnt.sum(axis=1), axis=0)
    # (b) self 제외 (전환 사건만)
    cnt_sw = cnt.copy()
    np.fill_diagonal(cnt_sw.values, 0)
    Psw = cnt_sw.div(cnt_sw.sum(axis=1), axis=0).fillna(0)

    def short(l):
        return {"Goldilocks": "Goldi", "Reflation": "Refl", "Slowdown": "Slow",
                "Stagflation": "Stag", "Crisis": "Crisis", "Transition": "Trans"}[l]

    print(f"\n{'='*78}\n  레짐 발생확률 (시간 점유율) — 전체 {n}일, {span_years:.1f}년"
          f"\n{'='*78}")
    print(f"  {'레짐':>12}{'일수':>8}{'발생확률':>10}{'평균연속':>10}{'진입횟수':>10}")
    print("  " + "─" * 56)
    for l in sorted(labels, key=lambda x: -occ[x]):
        print(f"  {l:>12}{counts[l]:>8}{occ[l]:>9.1%}{avg_run[l]:>9.1f}일{n_enter[l]:>9}")

    def show_matrix(title, M, note):
        print(f"\n{'='*78}\n  {title}\n{'='*78}")
        hdr = "  from\\to    " + "".join(f"{short(c):>9}" for c in labels)
        print(hdr); print("  " + "─" * (len(hdr) - 2))
        for i in labels:
            row = "".join(f"{M.loc[i, j]:>8.1%} " for j in labels)
            print(f"  {short(i):>8}  {row}")
        print(f"  ({note})")

    show_matrix("전이확률 (a) self 포함 — P(내일=j | 오늘=i), 대각선=지속확률", P,
                "대각선이 그 레짐 하루 유지 확률. 행 합=100%")
    show_matrix("전이확률 (b) self 제외 — 레짐이 바뀔 때 어디로 가나", Psw,
                "전환 사건만. 행 합=100%, 대각선=0")

    print(f"\n  요약: 발생확률 1위 {sorted(labels, key=lambda x:-occ[x])[0]} "
          f"{occ[sorted(labels, key=lambda x:-occ[x])[0]]:.0%}. "
          f"rule raw 레짐(라이브 필터·평활 미적용). 소표본 레짐 추정 주의.")


if __name__ == "__main__":
    main()
