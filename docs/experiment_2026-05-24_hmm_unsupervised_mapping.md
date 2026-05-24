# HMM Unsupervised State-Regime Mapping — 실험 결과

**일자**: 2026-05-24
**Phase**: TODO Option A — HMM/RF 자기참조 학습 제거
**관련 커밋**: 7343714 (Option C anomaly detector) 이후 진행

## 배경

기존 `HmmRegimeClassifier.fit()`은 학습 후 각 HMM state에 `detect_regime()` 결과의 다수결로 레짐 라벨을 부여했다. 결과적으로 HMM은 규칙 기반 함수의 smoothing 근사기로 동작했고, 진정한 비지도 학습 효과가 미흡했다.

**가설**: HMM state별 피처 평균(realized_vol, momentum, credit, inflation 지표)을 사용한 사후 라벨링이 detect_regime 자기참조 없이도 동등 이상의 분류 품질과 백테스트 성과를 낼 수 있다.

## 구현

### 신규 매핑 알고리즘

`_unsupervised_state_mapping(states, feature_matrix)` (trading/regime.py:306):

1. state별 피처 평균 계산
2. **Crisis 식별**: realized_vol 최댓값이 ≥0.25 또는 두번째 state 대비 ≥1.5x이면 해당 state → Crisis
3. **나머지 state 분류**:
   - growth_score = momentum_1m + momentum_3m + credit_signal (+curve_10y2y 가중)
   - infl_score = commodity_mom_1m + hy_spread_zscore × 0.03 + cpi_yoy adj + (vix-20)/200
   - 4개 state면 growth로 정렬해 하위 2 → Slowdown/Stagflation, 상위 2 → Goldilocks/Reflation
   - 각 그룹 내에서 infl_score 높은 쪽 = Stagflation/Reflation, 낮은 쪽 = Slowdown/Goldilocks
4. **품질 검증**: 분류된 distinct 레짐 < 3종 또는 빈 state ≥ 2개 → `None` 반환 → legacy 자동 폴백

### 호환성

- `HmmRegimeClassifier(unsupervised_mapping: bool = True)` 인자로 전환 가능
- config: `hmm.unsupervised_mapping: true` (기본값)
- ambiguous 매핑은 자동으로 legacy `_legacy_state_mapping`으로 폴백
- run.py / backtest/engine.py 두 진입점 모두 config 반영

## 실험 결과

워크포워드 백테스트, 주간 리밸런싱, 거래비용 0.1%, USD 30%/KRW 70%, FRED 미사용(가격 파생 피처만).

### 10년 윈도우 (2015-01-01 ~ 2024-12-31)

| metric | legacy | new (unsupervised) | Δ |
|---|---:|---:|---:|
| CAGR | +12.05% | +11.81% | **−0.24pp** |
| Volatility | 8.84% | 8.24% | −0.60pp |
| Sharpe | 0.911 | 0.949 | **+0.038** |
| MaxDD | −11.97% | −10.05% | **+1.92pp** |
| Calmar | 1.007 | 1.175 | **+0.168** |
| MCC | +0.762 | +0.774 | +0.012 |
| Macro-F1 | 0.768 | 0.760 | −0.008 |
| Balanced Acc | 0.831 | 0.824 | −0.007 |
| HMM Override율 | 14.3% | 13.5% | −0.8pp |
| 위험레짐 미감지 일수 | 0/239 | 5/239 (2%) | +5일 |

### 전체 윈도우 (2010-01-01 ~ 2025-04-30)

| metric | legacy | new (unsupervised) | Δ |
|---|---:|---:|---:|
| CAGR | +10.58% | +10.22% | −0.36pp |
| Volatility | 8.73% | 8.28% | −0.45pp |
| Sharpe | 0.753 | 0.751 | **−0.002** |
| MaxDD | −11.97% | −10.05% | **+1.92pp** |
| Calmar | 0.884 | 1.016 | **+0.132** |
| MCC | +0.806 | +0.818 | +0.012 |
| Macro-F1 | 0.806 | 0.809 | +0.003 |
| Balanced Acc | 0.862 | 0.861 | −0.001 |
| HMM Override율 | 11.5% | 10.8% | −0.7pp |
| 위험레짐 미감지 일수 | 0/373 | 5/373 (1%) | +5일 |

### 레짐 분포 비교 (2010-2025)

| 레짐 | legacy | new |
|---|---:|---:|
| Goldilocks | 2229일 | 2221일 |
| Slowdown | 860일 | 878일 |
| Stagflation | 241일 | 266일 |
| Reflation | 285일 | 265일 |
| Crisis | 241일 | 226일 |

## 해석

- **수익률**: CAGR이 0.24~0.36pp 떨어지지만, **변동성도 0.45~0.60pp 함께 줄어** Sharpe는 보존되거나 개선.
- **리스크 관리**: MaxDD가 **1.92pp 개선**(−11.97% → −10.05%), Calmar는 0.13~0.17 개선. 위기 구간 방어력 강화.
- **분류 품질**: MCC가 +0.012 개선되어 규칙 기반과의 일관성 유지. Macro-F1은 ±0.01 이내 변동.
- **위험레짐 미감지**: 5일 추가(전체의 1.3%)되었으나 절대 규모는 작음. 이 5일 평균 일수익 −0.72%는 실제로 손실 구간 → 향후 anomaly overlay(Option C)와 결합해 보강 여지.
- **Override율 감소**: 11.5% → 10.8%. 비지도 매핑이 자기참조 매핑보다 규칙 기반과 충돌이 적음 = 더 일관된 신호.

## 결론

**채택**. 기존 detect_regime 자기참조 매핑 대신 비지도 state-feature 매핑을 primary로 적용.
- 동등한 Sharpe / 개선된 MaxDD·Calmar
- 분류 품질은 보존되며 자기참조 우회 → 진정한 unsupervised 신호
- 폴백 안전망:
  1. `unsupervised_mapping: false` config 플래그로 즉시 legacy 복귀
  2. 매핑이 ambiguous할 때 (distinct regime < 3) 자동으로 legacy 매핑으로 폴백

Option C(`AnomalyDetector`)는 그대로 유지하여 두 시그널 상호보완. 향후 위험레짐 미감지 5일에 대해 anomaly_score 분석으로 후속 개선 검토.

## 재현 방법

```bash
python scripts/compare_hmm_mapping.py --start 2015-01-01 --end 2024-12-31
python scripts/compare_hmm_mapping.py --start 2010-01-01 --end 2025-04-30
```

## 변경된 파일

- `trading/regime.py` — `HmmRegimeClassifier` 매핑 분리 (`_unsupervised_state_mapping`, `_legacy_state_mapping`)
- `trading/config.yaml` — `hmm.unsupervised_mapping: true` 추가
- `trading/run.py` — config 플래그 반영
- `backtest/engine.py` — config 플래그 반영
- `scripts/compare_hmm_mapping.py` — 비교 백테스트 스크립트 신규
