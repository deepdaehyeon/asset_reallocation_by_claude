# 레짐 판단 모델 비교 연구

_작성일: 2026-05-08 | 환경: Mac Mini M1 (arm64), Python 3.10_

---

## 1. 배경 및 문제 인식

### 현재 시스템
- **규칙 기반** (`detect_regime`) + **GaussianHMM** (5-state) 앙상블
- HMM: hmmlearn `GaussianHMM`, lookback 500일, predict_lookback 60일

### 문제점
| 문제 | 원인 | 영향 |
|------|------|------|
| 소수 레짐(Crisis/Stagflation) 과소 탐지 | HMM의 MLE 목적함수가 다수 클래스(Goldilocks ~65%)에 지배됨 | 위험 구간에서 과도한 주식 편입 → 낙폭 확대 |
| 분류 품질 측정 불가 | 단순 accuracy는 불균형 데이터에서 무의미 | 모델 개선 효과를 정량 비교 불가 |

### 레이블 분포 (역사적 추정)
```
Goldilocks   ██████████████████████  ~65%
Slowdown     ████████                ~18%
Reflation    ████                    ~10%
Stagflation  ██                      ~5%
Crisis       █                       ~2%
```
→ Goldilocks 65%, Crisis 2%: **32:1 불균형**

---

## 2. 평가 메트릭 설계

단순 accuracy 대신 불균형 데이터에 적합한 지표 도입 (`backtest/metrics.py`):

| 메트릭 | 특성 | 선택 이유 |
|--------|------|-----------|
| **MCC** (Matthews Correlation Coefficient) | -1 ~ +1, 혼동행렬 전체 반영 | 단일 숫자로 다중 클래스 불균형 품질 요약 |
| **Macro-F1** | 클래스별 F1 단순 평균 | Goldilocks 빈도 무관, 소수 클래스 동등 반영 |
| **Balanced Accuracy** | 클래스별 accuracy 평균 | 직관적 해석, 불균형 보정 |
| **Crisis/Stagflation Recall** | 위험 레짐 탐지율 | 놓치면 손실, precision보다 recall 우선 |
| **경제적 오판 비용** | 위험 레짐→Goldilocks 오판 시 일평균 수익률 | recall 희생의 실제 포트폴리오 비용 |

---

## 3. 모델 후보 조사

### 3.1 실측 벤치마크 (M1 Mac Mini, 500일×6피처, 5회 평균)

| 모델 | 학습 시간 | 추론 1회 | 백테스트 전체¹ | 비고 |
|------|:---------:|:--------:|:-------------:|------|
| GaussianHMM (현재) | 153ms | 0.14ms | ~2분 | hmmlearn |
| Markov Switching | 215ms | 즉시 | ~3분 | statsmodels, 3-state AR(2) |
| PELT (change point) | 119ms | 즉시 | ~2분 | ruptures, 변화점 탐지만 |
| **RandomForest balanced** | **187ms** | **즉시** | **~2.4분** | sklearn, class_weight='balanced' |
| GMM (sklearn) | 47ms | 즉시 | ~0.6분 | 시간 정보 없음 |
| GradientBoosting | 454ms | 0.5ms | ~6분 | sklearn |
| pomegranate DenseHMM | 1,301ms | 1.8ms | ~17분 | Bayesian HMM, torch 기반 |
| LSTM 200ep (CPU) | ~3,000ms | 즉시 | ~39분 | PyTorch |
| LSTM 200ep (MPS) | ~12,600ms | 즉시 | ~163분 | M1 GPU — 소모델에서 역효과 |
| PyMC Bayesian HMM | 수십초/회 | — | 수시간 | MCMC 샘플링 |

> ¹ 주간 리밸런싱 15년 ≈ 780회 재학습 기준

**M1 MPS(GPU) 주의사항**: 소규모 모델은 CPU→GPU 텐서 전송 오버헤드가 지배적이라 CPU보다 8배 느림.  
MPS 가속은 배치 수백 이상의 대형 모델에서만 유리하다.

### 3.2 핵심 기준별 비교

| 기준 | GaussianHMM | MarkovSwitching | pomegranate HMM | **RF balanced** | RL (PPO+LSTM) |
|------|:-----------:|:---------------:|:---------------:|:---------------:|:-------------:|
| 소수 레짐 Recall | ❌ 낮음 | △ 보통 | ✅ 우수 | ✅ 우수 | ✅ 우수 |
| 불확실성 정량화 | ❌ 없음 | △ 신뢰구간 | ✅ 사후확률 | △ 캘리브레이션 필요 | ❌ 없음 |
| 시간 구조 학습 | ✅ HMM | ✅ 전환행렬 | ✅ HMM | ❌ 없음 | ✅ LSTM |
| M1 실용 속도 | ✅ 빠름 | ✅ 빠름 | △ 느림 | ✅ 빠름 | ❌ 매우 느림 |
| 해석 가능성 | △ 보통 | ✅ 전환확률 직독 | △ 보통 | ✅ feature importance | ❌ 블랙박스 |
| 과적합 위험 (500샘플) | △ 보통 | △ 보통 | △ 보통 | ✅ 낮음 | ❌ 매우 높음 |
| 구현 복잡도 | ✅ 현재 | ✅ 낮음 | △ 중간 | ✅ 낮음 | ❌ 매우 높음 |

---

## 4. RL 접근법 평가

레짐 분류에 RL을 쓰는 두 방식:

### 방식 A — RL로 직접 레짐 분류 (DQN/PPO)
- 보상 = 리밸런싱 후 포트폴리오 수익률
- **문제**: 에피소드 1회 = 15년 시뮬레이션 → 수렴까지 수천 에피소드 → 며칠 소요
- 500샘플에서 LSTM은 극심한 과적합 위험

### 방식 B — 직접 포트폴리오 최적화 (암묵적 레짐)
- 레짐 레이블 없이 feature → 비중을 RL로 직접 학습
- 연구에서는 유망하나 현 시스템의 **"레짐을 명시적으로 알고 비중 결정"** 구조와 충돌
- 기존 규칙 기반 / 검증 체계 재사용 불가

**결론**: RL은 이 프로젝트 요건(데이터 규모, 속도, 해석 가능성)에서 실용적이지 않다.

---

## 5. 구현 결정

### Stage 1 — BalancedRF 앙상블 (완료, 2026-05-08)

**선택 이유**:
1. 속도: 187ms → 백테스트 영향 최소
2. 불균형 처리: `class_weight='balanced'` → Crisis(2%) 레이블이 Goldilocks(65%)와 동등한 결정 경계 영향력
3. 드롭인 통합: 기존 `ensemble_regime()` 파이프라인 재사용
4. 즉시 측정 가능: 방금 추가한 MCC/Macro-F1 지표로 개선 효과 정량 확인 가능

**블렌딩 공식**:
```
blend_probs = 0.6 × HMM_probs + 0.4 × RF_probs
final_regime = ensemble_regime(rule_regime, blend_probs, threshold=0.60)
```

**HMM vs RF 역할 분담**:
- HMM: 시계열 전이 패턴 (어제 Goldilocks → 오늘 Crisis 확률)
- RF: 피처 공간의 소수 클래스 경계 (어떤 feature 조합이 Crisis인가)

**변경 파일**:
- `trading/regime.py` — `BalancedRFClassifier` 클래스 추가
- `backtest/engine.py` — `_get_regime()`에 RF 블렌딩 통합
- `trading/run.py` — 실거래 파이프라인에 RF 추가 (RF 예측 로그 출력)
- `trading/config.yaml` — `hmm.rf_enabled: true`, `hmm.rf_weight: 0.40`

### Stage 2 — Markov Switching Model (TODO)

**선택 이유**:
- 금융 레짐 모델의 학문적 표준 (Hamilton 1989, Hamilton/Raj 2002)
- 전환 확률 행렬 직접 해석 가능: "Goldilocks → Crisis 월간 전환 확률 X%"
- M1에서 215ms, 현재 HMM과 동등한 속도
- statsmodels에서 바로 지원, 추가 의존성 없음

**제약 및 구현 방향**:
- 다변량 MS는 복잡하므로 PCA 2~3 성분으로 피처 축약 후 학습
- `MarkovSwitchingClassifier` 클래스로 `HmmRegimeClassifier`와 동일 인터페이스 구현
- 블렌딩: HMM + RF + MS 3-way 앙상블 (가중치 config에서 조정)

---

## 6. 참고

- 실측 벤치마크 코드: 2026-05-08 인터랙티브 세션에서 직접 측정
- Hamilton (1989) "A New Approach to the Economic Analysis of Nonstationary Time Series"
- M1 MPS 참고: torch.backends.mps.is_available() = True, 그러나 소모델에서 역효과 확인
