# 실험: detect_regime 임계 시뮬레이션 (외부 비평 #6-a, Goldilocks 분리도)

> **요약**: ① Goldilocks 정의 임계(growth_bullish ≥ 2→3)와 fallback 방향(Goldilocks→Slowdown) 변형 4개 시나리오에서 레짐별 forward 21일 수익률 분리도를 시뮬레이션했다. ② strict_gold 변형에서 Goldilocks 평균 수익 +0.81%→+0.93%(+0.12pp), 순위 5/5→4/5로 미세 개선에 그쳤으며, 본질 문제는 detect_regime 임계가 아니라 ensemble·RegimeFilter 단계에 있음을 확인했다. ③ A안(보류) 채택 — 임계 조정만으로는 분리도가 의미 있게 회복되지 않아 detect_regime은 현행 유지하고 본질 해결은 ensemble 단계 개선으로 방향을 정했다.

*작성일: 2026-05-27*
*상태: 시뮬레이션 완료. **미세 개선만 확인. 채택 여부는 사용자 결정.** detect_regime 본질 재설계는 별도 큰 작업으로 남김.*

## 배경

이전 진단에서 충격적 결과: Goldilocks 평균 forward 21일 수익률 +0.71%로 5개 레짐 중 **최하**. 이론(Goldilocks = best)과 정반대.

가설: Goldilocks 정의(`growth_bullish ≥ 2 and infl_low ≥ 1`)가 너무 광범위해 평균 시장 시점을 흡수.

## 시뮬레이션 (코드 변경 없이 분리도만 검증)

`scripts/compare_detect_thresholds.py` — walk-forward로 features 시계열 생성 후 4개 시나리오로 `detect_regime` 재적용. 백테스트 1회만 (가격·FRED만 사용).

| 시나리오        | 변경                                    | 의도                          |
|----------------|-----------------------------------------|------------------------------|
| baseline       | (현재)                                  | -                            |
| strict_gold    | Goldilocks growth_min `2 → 3`           | Goldilocks를 더 엄격하게      |
| very_strict    | growth_min 3 + infl_low_min `1 → 2`    | 더 엄격                       |
| flip_fallback  | 혼재 fallback `Goldilocks → Slowdown`   | 모호한 시점은 보수적으로      |

## 결과 (2010-01-01 ~ 2025-04-30, FRED 매크로 포함)

### 레짐별 평균 forward 21일 수익률

| 시나리오       | Goldilocks      | Reflation | Slowdown | Stagflation | Crisis | Goldilocks 일수 |
|----------------|----------------:|----------:|---------:|------------:|-------:|----------------:|
| baseline       | +0.81% (Sh 0.81)| +0.89%    | +1.20%   | +2.05%      | +3.84% | 2232            |
| **strict_gold**| **+0.93%** (Sh 1.04) | +0.73% | +0.95%   | +2.05%      | +3.84% | **1902**        |
| very_strict    | +0.93%          | +0.64%    | +1.00%   | +2.05%      | +3.84% | 1663            |
| flip_fallback  | +0.48% (악화)   | +0.89%    | +1.44%   | +2.05%      | +3.84% | 1705            |

### Goldilocks 분리도 비교

| 시나리오       | Goldilocks vs 다른 레짐 평균 | Goldilocks 순위 |
|----------------|----------------------------:|----------------:|
| baseline       | Δ -1.19pp                   | **5/5 (최하)** |
| strict_gold    | Δ -0.97pp                   | 4/5            |
| very_strict    | Δ -0.95pp                   | 4/5            |
| flip_fallback  | Δ -1.58pp                   | 5/5            |

## 핵심 발견

1. **`detect_regime` 자체로는 Crisis·Stagflation이 가장 우월** (fwd_mean +3.84% / +2.05%).
   - 이전 진단(regime_diagnostics.json)의 Crisis 평균 +0.38%와 큰 차이.
   - 차이의 원인: 진단은 ensemble + RegimeFilter 통과 후의 `regime` 컬럼을 본 것 → ensemble override / 히스테리시스가 Crisis 진입을 지연시킨 결과.
   - **detect_regime의 후행 문제는 룰 자체가 아니라 ensemble 단계의 문제**.

2. **Goldilocks 임계 조정의 효과는 미세**.
   - baseline → strict_gold: Goldilocks 평균 +0.81% → +0.93% (+0.12pp). 순위 5/5 → 4/5. 의미 있지만 본질적 회복 아님.
   - very_strict는 분리도 추가 개선 없음. Goldilocks 일수만 더 감소.
   - flip_fallback은 오히려 악화 — fallback에 들어오던 케이스가 평균보다 좋은 시점이었음.

3. **본질 문제는 ensemble·RegimeFilter 단계**.
   - 이미 적용된 `blend_smoothing_alpha=0.5`가 일부 완화.
   - 더 본질적 해결: ensemble override 조건 재검토 또는 RegimeFilter의 Crisis 비대칭 hysteresis 효과 점검.

## 권장

### A안: 보류 (현재 가장 합리적)
- 임계 조정만으로 분리도가 의미 있게 회복 안 됨. detect_regime은 충분히 잘 동작.
- 본질 문제(ensemble 후행 진입)는 이미 `blend_smoothing_alpha`·Crisis hysteresis(`confirm=1, cooldown=0`)로 일부 처리됨.

### B안: strict_gold 채택
- Goldilocks 분리도 미세 개선(+0.12pp) + Sharpe 0.81→1.04.
- 코드 변경: `regime.py` 95행 `growth_bullish >= 2` → `>= 3` (1줄).
- 부작용: RF 학습 라벨 변경 → ensemble 영향. 본격 백테스트로 portfolio Sharpe·MaxDD 검증 필요.

### C안: detect_regime 본질 재설계 (별도 후속)
- 5개 레짐 framework 자체를 forward return quantile binning 기반으로 교체.
- RF forward 라벨 옵션 2 실험의 발전형. blend 수식 재설계 동반.

## 결정 안내

A안(보류) 또는 B안(strict_gold 채택 + 본격 백테스트) 중 선택 필요.
C안은 본 PR 범위 밖으로, 별도 큰 작업.
