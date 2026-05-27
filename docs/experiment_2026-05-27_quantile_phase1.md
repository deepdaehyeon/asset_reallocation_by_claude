# 실험: C안 Phase 1 — forward return quantile 라벨 분리도 검증

*작성일: 2026-05-27*
*상태: Phase 1 통과 — Goldilocks 분리도 +4.85pp 회복. Phase 2 진행 가능.*

## 배경

`docs/plan_2026-05-27_quantile_regime_framework.md` 계획에 따른 첫 단계.
새 quantile 기반 라벨이 기존 `detect_regime` 대비 분포·분리도·전환 패턴에서 의미 있는 개선을 보이는지 검증.

## 라벨 정의 (옵션 2)

5개 레짐 이름 유지, 정의를 forward 21영업일 (SPY 수익률, EWMA 변동성) quantile로 교체:

- **Crisis**      : forward 변동성 top 10%
- **Reflation**   : forward 수익률 top 30% + 변동성 ≥ median
- **Goldilocks**  : forward 수익률 top 30% + 변동성 < median
- **Stagflation** : forward 수익률 bottom 30% + 변동성 ≥ median
- **Slowdown**    : forward 수익률 bottom 30% + 변동성 < median
- **나머지 ~40%** : 가장 가까운 코어 레짐에 (z-score Manhattan 거리)

추정된 quantile threshold (2010-2025, 3835일):
- ret_hi (top 30%): +3.27%
- ret_lo (bottom 30%): -0.25%
- vol_hi (top 10%): 25.0%
- vol_med (median): 12.6%

## 결과

### 레짐별 평균 forward 21일 수익률

| | Goldilocks | Reflation | Slowdown | Stagflation | Crisis |
|---|----------:|----------:|---------:|------------:|-------:|
| baseline (rule) | **+0.81%** (Sh 0.81) | +0.89% (0.69) | +1.20% (0.87) | +2.05% (1.14) | +3.84% (2.03) |
| **quantile** (new) | **+4.48%** (Sh 10.64) | +3.49% (6.15) | -1.26% (-2.92) | -1.44% (-1.83) | -2.27% (-0.97) |

### 분리도 정량 비교

| | Goldilocks 평균 | 다른 레짐 평균 | Δ | 순위 |
|---|----------------:|---------------:|--:|----:|
| baseline | +0.81% | +2.00% | **-1.19pp** | **5/5 (최하)** |
| quantile | +4.48% | -0.37% | **+4.85pp** | **1/5 (최고)** |

### 분포 비교

| | Goldilocks | Reflation | Slowdown | Stagflation | Crisis |
|---|---:|---:|---:|---:|---:|
| baseline | 59% (2232일) | 6% (242) | 25% (943) | 6% (225) | 4% (164) |
| quantile | 21% (793) | 28% (1085) | 8% (312) | 33% (1261) | 10% (384) |

### 전환 / whipsaw

| | 전환 횟수 | whipsaw | whipsaw 비율 |
|---|---------:|--------:|------------:|
| baseline | 530 | 418 | 78.9% |
| quantile | 744 | 574 | 77.2% |

## 해석

1. **분리도 극적 회복**. Goldilocks 5위 → 1위. Δ -1.19pp → +4.85pp. 라벨 정의가 forward 분포 기반이라 by construction 잘 분리되긴 하지만, **5개 레짐 이름이 의도한 의미를 정확히 반영**하게 됨.
2. **분포 균형 회복**. baseline의 Goldilocks 59% 편중 → quantile의 8-33% 균형. 라벨이 데이터에서 추출한 자연스러운 구조를 반영.
3. **Crisis가 -2.27%로 정상화**. baseline의 Crisis +3.84%는 ensemble Crisis 진입이 후행적이라 발생한 V-shape 반등 효과였음. 새 Crisis 정의(변동성 top 10%)는 실제 위험 시점을 잡아 음수 평균 정상화.
4. **whipsaw 비율은 비슷**(78.9% vs 77.2%). 라벨 자체가 quantile 경계 근처에서 자주 바뀌어 발생. RegimeFilter / blend_smoothing으로 흡수 필요.
5. **Goldilocks Sharpe 10.64는 비현실적으로 높음** — in-sample 라벨로 forward를 정의했기 때문. 진짜 의미는 "이 quantile 정의의 라벨이 forward를 잘 나눈다"는 sanity check.

## 한계 (Phase 1 본질)

- **in-sample 측정**. quantile threshold가 2010-2025 전체에서 추정됐고, 같은 데이터의 forward로 분리도를 봤음. 이건 정의의 sanity check일 뿐.
- **진짜 검증은 Phase 2/3**: features → quantile_label을 학습한 supervised 분류기가 out-of-sample 데이터에서 얼마나 잘 예측하는가. 그 분류기의 분리도가 +4.85pp의 일부라도 보존하면 채택 가치 있음.
- 이전 RF forward 라벨 실험 (Round 2/3)에서 forward_quantile_21이 baseline Sharpe 대비 -0.05~-0.07이었던 것을 기억. 그 때는 (수익률, 변동성) 결합 매핑이 아니라 단순 quantile bin이었음. 본 옵션 2 매핑이 더 의미 있을 가능성.

## Phase 2 진행 권장 여부

**진행 권장**. 근거:
- 분리도 +4.85pp 회복은 자명한 결과지만 분포·Crisis 정상화는 정의의 sound성 입증
- baseline detect_regime의 본질 결함(5위 → 1위 이동)이 새 정의로 해결됨
- Phase 2 작업량(1일)이 채택 가치 대비 적정

**Phase 2 작업**:
1. `regime.py`에 `detect_regime_quantile(features, fitted_thresholds)` 함수
2. `BalancedRFClassifier.label_mode='forward_quantile_v2'` 추가 (옵션 2 매핑 supervised)
3. `HmmRegimeClassifier._unsupervised_state_mapping`에 quantile 옵션
4. `regime_framework: rule_based | quantile_based` config 토글

**Phase 3 작업**: 백테스트 실측으로 채택 결정.
