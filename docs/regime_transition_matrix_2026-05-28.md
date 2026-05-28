# 레짐 전환 matrix & 실제 매매 빈도

*작성일: 2026-05-28*
*기간: 2010-01-01 ~ 2025-04-30 (15.3년, 3856일)*
*대상: 현재 라이브 config 백테스트 (`6d86b79` 이후)*

## 1. 전환 절대 빈도 (15년간 총 190회)

대각선 = 유지(전환 아님)는 제외. from→to 발생 횟수만.

|              | →Goldilocks | →Reflation | →Slowdown | →Stagflation | →Crisis | 총 |
|--------------|-----------:|---------:|--------:|-----------:|------:|----:|
| **Goldilocks→** | - | 6 | **42** | 10 | 3 | 61 |
| **Reflation→** | **8** | - | 6 | 1 | 0 | 15 |
| **Slowdown→** | **39** | 7 | - | 15 | 5 | 66 |
| **Stagflation→** | 9 | 2 | **14** | - | **8** | 33 |
| **Crisis→** | 5 | 0 | 3 | **7** | - | 15 |

## 2. 전환 확률 (from 기준 %)

|              | →Goldilocks | →Reflation | →Slowdown | →Stagflation | →Crisis |
|--------------|----------:|--------:|-------:|----------:|------:|
| **Goldilocks→** | - | 9.8% | **68.9%** | 16.4% | 4.9% |
| **Reflation→** | **53.3%** | - | 40.0% | 6.7% | 0% |
| **Slowdown→** | **59.1%** | 10.6% | - | 22.7% | 7.6% |
| **Stagflation→** | 27.3% | 6.1% | **42.4%** | - | 24.2% |
| **Crisis→** | 33.3% | 0% | 20.0% | **46.7%** | - |

## 3. 레짐별 안정성 (평균 연속 일수)

| 레짐 | 총 일수 | 평균 연속 | 진입 횟수 |
|------|------:|-----:|--------:|
| **Goldilocks** | 1834 | **30.1일** | 61 |
| Reflation | 373 | 24.9일 | 15 |
| Crisis | 243 | 16.2일 | 16 |
| Slowdown | 1048 | 15.9일 | 66 |
| **Stagflation** | 357 | **10.8일** | 33 |

## 4. 실제 매매 빈도 (분류 빈도와 다름)

| 지표 | 값 |
|------|---|
| 리밸런싱 실행 | 50.6회/년 (W-FRI 매주) |
| 실제 매매 | 50.6회/년 |
| 레짐 raw 전환 | 12.4회/년 |
| **매매당 평균 turnover** | **3.6%** |
| 누적 거래비용 (15년) | 5.54% (연 0.36%) |
| 레짐 전환일에 매매 발생률 | 100% |
| **매매일 중 레짐 전환일 비율** | **25%** |

## 주요 패턴

### (a) Goldilocks ↔ Slowdown oscillation (전체 전환의 43%)
- Goldilocks → Slowdown: **42회 (68.9%)** — 강세장 → 둔화 (압도적 1위)
- Slowdown → Goldilocks: **39회 (59.1%)** — 회복
- 두 레짐 사이 진동이 빈번

### (b) Crisis 진출 경로 (사용자 직관 검증)
| 다음 레짐 | 횟수 | 확률 |
|----------|----:|----:|
| Stagflation | 7 | **46.7%** |
| Goldilocks | 5 | 33.3% |
| Slowdown | 3 | 20.0% |
| **Reflation** | **0** | **0%** |

**Crisis → Reflation 경로는 15년간 0회**. 사용자가 짐작한 "V자 반등으로 Reflation 전환"은 detect_regime 임계 구조상 발생 안 함.

### (c) Crisis 진입 경로
| 직전 레짐 | 횟수 |
|----------|----:|
| Stagflation | **8** |
| Slowdown | 5 |
| Goldilocks | 3 |
| Reflation | 0 |

Crisis는 주로 **Stagflation/Slowdown에서 악화**. Goldilocks에서 직접 Crisis는 드묾(3회).

### (d) Stagflation의 transitory 성격
- 평균 연속 10.8일 (가장 짧음)
- 진입 33회 중 42.4%가 Slowdown으로, 24.2%가 Crisis로
- 짧은 transition state — 다른 레짐으로 빨리 빠짐

## 5. 매매 빈도와 분류 빈도의 차이 (사용자 우려에 대한 분석)

### 사용자 우려
"1달에 한 번 레짐 변경 = 포지션을 계속 바꾸는 것 아닌가?"

### 실제 데이터
- 레짐 raw 전환: 12.4회/년 (1달에 1회)
- portfolio 매매: 50.6회/년 (매주)
- **두 빈도는 별개** — 매매의 75%는 일반 drift 보정 때문, 25%만 레짐 전환 동반

### 매매당 turnover 작음
- 매매 평균 3.6%만 갈아엎음
- 즉 portfolio 96.4%는 그대로 두고 일부만 조정
- 강세장에서 매주 매도/매수가 일어나지만 누적 비용 연 0.36%

### 라이브 환경에서는 더 적음
백테스트는 `RegimeFilter` 미적용 (engine이 ensemble 결과 직접 사용).
라이브에서는 추가 필터:
- `RegimeFilter`: 3회 연속 확인 + 5일 쿨다운 (Crisis는 1회/0일)
- `blend_smoothing_alpha=0.5`: blend EWMA 평활
- → **라이브 confirmed regime 전환은 백테스트의 1/2~1/3 수준** (추정 4-8회/년)

### 다층 흡수 메커니즘
실제 portfolio는 `confirmed regime`이 아니라 `blend_probs`(HMM+RF 가중평균)로 결정. blend_probs는 매일 부드럽게 변하고 평활 적용 후 매주 한 번 매매로 반영. 즉 분류 변화가 portfolio 변동의 일부만 만듦.

## 6. RegimeFilter 완화 + smoothing 검증 (사후 추가)

사용자 우려:
1. cooldown 5일이 매매 쿨다운(7일)에 가려 무의미
2. confirmation 3회가 Stagflation(평균 10.8일)에 비해 보수적
3. blend_smoothing 적용으로 drift trigger 자주 → 매매 잦은 것 아닌가

검증 결과:

### Calendar 모드 회귀 (confirmation 3→2, cooldown 5→0)
- Sharpe/MaxDD/Calmar/CAGR 모두 변경 전과 **동일**
- backtest engine이 RegimeFilter 미적용이라 portfolio metric에 영향 없음
- 라이브 알림·표시에만 효과

### Drift 모드 smoothing 효과 (라이브 동작 근사)

| alpha | Sharpe | MaxDD | 리밸런싱/년 | 거래비용/년 |
|------|------:|------:|---------:|----------:|
| 0.0 (off) | 0.616 | -15.64% | 4.6 | 0.093% |
| **0.5 (on, 현재)** | **0.755** | -16.00% | **5.0** | **0.068%** |

**사용자 직관 부분 정정**:
- smoothing이 drift trigger 더 자주 만듦 = **빈도 측면 맞음** (+9%)
- 그러나 매매당 turnover 작아짐 → 거래비용 더 적음
- Sharpe +0.139 큰 개선

### 라이브 매매 빈도 (drift 기반 추정)
- backtest calendar 모드: 50.6회/년 (W-FRI)
- backtest drift 모드: **5.0회/년**
- 라이브도 drift 기반이라 **실제 매매는 5회/년 수준**

사용자가 본 "1주 1회 매매"는 backtest calendar의 부산물이지 라이브 동작이 아님.

## 7. 본 데이터의 implication for A안 (Transition phase)

`docs/experiment_2026-05-28_transition_phase.md`의 실패 원인이 이 matrix로 정확히 설명됨:

- **Goldilocks 평균 연속 30.1일** → transition_days=7이면 30일 중 7일(23%)을 보수 비중으로 → 강세장 1/4 놓침
- **Crisis 평균 연속 16.2일** → transition_days=7이면 16일 중 7일(44%)을 보수로 → 위기 직후 V-shape 흡수 불가
- 모든 레짐에 일률 적용은 매우 비효율

**후속 변형 후보**: 위험 진입(→Crisis, →Stagflation)에만 transition_phase 적용. 그 경우:
- Crisis 진입 16회 + Stagflation 진입 33회 = 49회/15년 = 약 3.3회/년만 적용
- 비용 누적 적음, 보호 효과만 활용 가능
- 다만 검증 안 됨 — 향후 시도 가능
