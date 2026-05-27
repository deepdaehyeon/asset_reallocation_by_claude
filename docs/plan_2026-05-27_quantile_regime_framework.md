# 계획: forward return quantile binning 기반 레짐 재설계 (C안)

*작성일: 2026-05-27*
*상태: 계획 단계. 코드 변경 없음. 별도 작업으로 진행 결정 시 단계적 PR.*

## 배경

`docs/experiment_2026-05-27_detect_thresholds.md` 시뮬레이션에서 확인된 본질 문제:

> `detect_regime`은 "현재 시장 상태"를 분류하는 시점 식별기일 뿐 "미래 N일 수익률"을 예측하지 않는다.
> Goldilocks 정의가 데이터의 58%를 흡수해 사실상 시장 평균이 됨.
> 임계 미세 조정만으로는 분리도가 본질적으로 회복되지 않음.

C안은 **레짐 라벨 자체를 미래 N일 수익률 분포로 정의**해 "분류가 곧 예측이 되도록" 재설계한다.

## 목표

1. 레짐별 forward return 분리도 회복: 각 레짐의 평균 forward N일 수익률이 의미 있게 다르도록.
2. 라벨링의 자기참조 완전 제거: HMM·RF 모두 데이터 기반 라벨로 학습.
3. 기존 `regime_targets` 자산 배분 framework와의 호환성 유지 (또는 명시적 매핑).

## 핵심 설계 결정

### 1. 라벨 정의

두 가지 옵션을 검토:

**옵션 1 — 직접 quantile bin (6~9개 bin)**
- 라벨 = (forward 21일 SPY 수익률 quantile) × (forward 21일 SPY 변동성 quantile)
- 예: (수익률 H/M/L) × (변동성 L/H) = 6 bins
- 장점: 데이터 기반, 자기참조 완전 제거
- 단점: 5개 레짐 → 6 bins 매핑 필요. 기존 `regime_targets` 호환성 깨짐.

**옵션 2 — 5개 레짐 이름 유지 + 정의를 quantile로 매핑 (권장)**
- 학습 데이터 분위 기준 직접 매핑:
  - `Crisis`: 변동성 top 10% (극단 위험)
  - `Stagflation`: 수익률 bottom 30% + 변동성 상위
  - `Slowdown`: 수익률 bottom 30% + 변동성 하위
  - `Reflation`: 수익률 top 30% + 변동성 상위
  - `Goldilocks`: 수익률 top 30% + 변동성 하위
- 나머지 시점은 가장 가까운 bin에 할당 (Manhattan distance)
- 장점: 기존 `regime_targets` 그대로 사용 가능. 운영 호환성 유지.
- 단점: 매핑 규칙에 임의성 약간 (다만 quantile 기반이라 임계는 데이터에서 추정).

→ **옵션 2 권장**. 운영 안정성 우선.

### 2. Forward window N

- 후보: 21일 (1M) / 63일 (3M) / 252일 (1Y)
- 짧으면 노이즈 큼. 길면 라벨 손실(마지막 N일 학습 제외) + 학습 데이터 부족.
- 권장: **N=21**부터 시작. 백테스트로 21 vs 63 비교.

### 3. 추론 시 라벨 결정 방법

학습 데이터로 quantile threshold 결정 후, 추론 시점에는 미래를 모르므로 별도 분류기가 필요:

**A. supervised 분류기** (현재 BalancedRFClassifier 발전형)
- 학습: features → forward quantile label (옵션 2의 5개 레짐)
- 추론: 현재 features → 5개 레짐 확률
- 이미 RF forward 라벨 옵션 1·2 실험에서 비슷한 시도 있었음 (당시 채택 보류, Round 3 백테스트에서도 baseline 우월).

**B. HMM 비지도 매핑 + supervised 보강** (현재 구조 유지)
- HMM의 unsupervised state mapping을 forward quantile 통계 기반으로 재정의
- RF는 supervised 분류기로 보강

→ **B 권장**. 현재 ensemble 구조를 살리면서 라벨만 forward 기반으로 교체.

### 4. 기존 `regime_targets`과의 관계

`config.yaml`의 `regime_targets`는 5개 레짐별 자산군 비중 dict. 옵션 2로 가면 이름 유지되어 그대로 사용 가능. 다만 정의가 바뀐 후에도 비중이 합리적인지 검증 필요:
- 새 Goldilocks(수익률 top 30% + 변동성 하위) → 현재의 risk-on 비중(equity 47%, cash 8%)이 여전히 적합?
- 새 Crisis(변동성 top 10%) → 현재 보수적 비중 유지 OK?

비중 자체는 큰 변경 없을 가능성 — 라벨의 의미가 본질적으로 바뀌진 않음.

## 단계별 작업

### Phase 1: 라벨 정의 + 분리도 시뮬레이션 (코드 변경 최소)

`scripts/compare_quantile_regimes.py`:
- walk-forward로 features + forward 21일 (수익률, 변동성) 수집
- 옵션 2 매핑 규칙으로 새 quantile 라벨 시리즈 생성
- 기존 `detect_regime` 라벨과 비교 (분리도, 분포, 일관성)
- 백테스트 없이 분리도만 검증

기대: Goldilocks 분리도가 기존 -1.19pp → 양수로 회복. 그렇지 않으면 매핑 규칙 조정 (예: top 30% → top 20%).

**소요: 0.5일.**

### Phase 2: 새 라벨 함수 + 분류기 통합

- `regime.py`에 `detect_regime_quantile(features, fitted_thresholds)` 함수 추가
  - fitted_thresholds = 학습 데이터에서 추정한 forward stats quantile
- `BalancedRFClassifier`에 `label_mode='forward_quantile_v2'` 추가 (옵션 2 매핑)
- `HmmRegimeClassifier._unsupervised_state_mapping`에서 detect_regime 호출 부분을 forward quantile 매핑으로 옵션화
- config: `regime_framework: rule_based | quantile_based` 토글

**소요: 1일.**

### Phase 3: 백테스트 + 본격 비교

`scripts/compare_regime_frameworks.py`:
- baseline (현재 rule_based) vs quantile_based
- 각각 백테스트 + regime_diagnostics 실행
- 비교: Sharpe / MaxDD / 레짐별 forward return / 위험 진입 적시성

채택 기준:
- Sharpe 동등 이상 (-0.05 허용)
- MaxDD 악화 ≤2pp
- Goldilocks 분리도 양수 회복
- 위험 진입 적시성 개선

**소요: 0.5일 (백테스트 실행 + 분석).**

### Phase 4: 채택 결정 + 문서화 + 커밋

- 채택 시: `config.yaml`의 `regime_framework: quantile_based` 활성화 + 운영 가이드 갱신
- 보류 시: 코드는 옵트인으로 보존, 후속 작업 메모

**소요: 0.5일.**

## 위험과 trade-off

1. **운영 안정성**: 라이브 시스템 핵심 분류기 변경. Phase 3 백테스트에서 충분히 검증 후 단계적 활성.
2. **자기참조 제거가 백테스트 성과로 이어진다는 보장 없음**: 이전 RF forward 라벨 실험 (Round 2/3)에서 옵션 2 quantile은 baseline 대비 Sharpe -0.05~-0.07. 본 작업은 라벨 정의 자체를 바꾸는 더 본질적 변경이지만 결과는 미지.
3. **regime_targets 의미 변동**: 라벨 정의가 바뀌면 같은 자산 비중이 다른 시점에 적용 → 백테스트로 검증 필수.
4. **HMM unsupervised 매핑과의 통합 복잡도**: forward quantile 기반 매핑이 HMM의 hidden state 의미와 잘 정렬되는지 검증 필요.

## 채택 의사결정 흐름

```
Phase 1 시뮬레이션
  ├─ Goldilocks 분리도 회복 X → STOP. 매핑 규칙 재검토 (Phase 0 반복)
  └─ 회복 O → Phase 2 코드 통합
              └─ Phase 3 백테스트
                    ├─ Sharpe/MaxDD 채택 기준 만족 → Phase 4 채택
                    └─ 미달 → 옵트인 보존, 보류
```

## 본 계획의 범위 밖

- 5개 레짐 → 다른 개수(예: 6 bins)로 교체 (옵션 1 — 더 큰 재설계)
- `regime_targets` 자산 매핑 자체 재최적화
- forward window N 자동 튜닝 (현재는 21로 고정)

## 결정 요청

이 계획대로 단계적으로 진행할지, 또는 다른 우선순위가 있는지 결정 필요.
- "진행" → Phase 1 시뮬레이션부터 다음 작업으로 진행
- "보류" → 본 계획 문서만 보존, 미래에 참조
