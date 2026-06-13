# 실험: A안 — Transition phase 비중 (채택 보류)

> **요약**: ① 레짐 전환 후 7~21일 동안 보수적 Transition 비중(cash 24%, equity 25%)을 적용하는 A안을 백테스트로 검증했다. ② 모든 N(7/14/21일)에서 Sharpe가 -0.09~-0.13, CAGR -0.96~-1.48pp로 baseline 대비 명백히 열위였으며, 주 원인은 Goldilocks 진입 시 7일간 equity 25% 고수에 따른 강세장 기회 손실이었다. ③ 채택 보류 — 시스템에 이미 transition을 흡수하는 다층 안전망이 있어 추가 Transition 비중은 over-protection이지만, 코드는 옵트인으로 영구 보존돼 향후 per_regime gating 등 변형 재시도가 가능하다.

*작성일: 2026-05-28*
*상태: 모든 N에서 baseline 대비 명백한 열위. **A안 단순 형태 채택 보류**. 코드는 옵트인으로 보존.*

## 배경

사용자 framework: **레짐 transition 시 보수 비중, 안정 후 적극 비중**.
- 진입 전~진입 후 7-14일 = transition phase (안전·저위험)
- 그 이후 = 본 레짐 비중 (수익률 최대화)

이론적 정당화:
- 분류 불확실성 + 시장 변동성이 transition 시점에 가장 큼
- whipsaw 57.6% (진단) — 분류가 빈번히 뒤집힘
- transition cost(turnover, 슬리피지)도 변곡점에 가장 큼

## 구현 (A안 단순 형태)

- `regime_targets.Transition` 비중 추가 (cash 24, bond 29, gold 12, commodity/MF 10, equity 25)
- `regime_filter.transition_days` config (기본 0)
- `portfolio.blend_regime_targets(..., transition_phase=...)` 인자 추가 — True면 Transition 비중 반환
- `backtest/engine.py`: 직전 confirmed regime 변경 시점 추적, N일 동안 transition_phase=True로 호출

## 결과 (2010~2025)

| N | Sharpe | MaxDD | Calmar | CAGR | 위험 미감지 |
|---|-------:|------:|-------:|-----:|----------:|
| **0 (baseline)** | **0.751** | -10.90% | **0.926** | **+10.10%** | 10 |
| 7 | 0.661 | -10.61% | 0.861 | +9.14% | 10 |
| 14 | 0.630 | -11.17% | 0.786 | +8.77% | 10 |
| 21 | 0.624 | -10.73% | 0.803 | +8.62% | 10 |

baseline 대비 Δ: Sharpe **-0.09 ~ -0.13**, CAGR **-0.96 ~ -1.48pp** — 명백한 열위.

## 실패 원인 분석

### 1. 시스템이 이미 transition을 잘 흡수
다층 안전망이 이미 작동 중:
- `blend_smoothing_alpha=0.5` (점진적 전환)
- `vol_targeting` (변동성 기반 자동 축소)
- Crisis 비대칭 hysteresis (빠른 진입)
- `drawdown scale-down` (-10/-15/-25% 자동 보호)
- 최근 적용한 `regime_targets` 조정 (V-shape 흡수)

추가 Transition 비중은 **이미 처리된 위험에 대한 over-protection**.

### 2. 모든 transition에 일률 보수화는 기회 비용 큼
- **Goldilocks 진입 직후 7일 = equity 25%만** (Transition 비중) vs 평상 Goldilocks equity 70%
- 강세장 진입 시점이 곧 강한 상승 시점이라 7일 손실이 큼
- 위험 진입(Crisis, Slowdown)에는 보호가 작동하지만, 강세장 진입(Goldilocks, Reflation)의 기회 손실이 압도

### 3. confirmed regime 통과 후 실제 transition 빈도 적음
- 진단의 raw whipsaw 57.6%는 `detect_regime` 직접 출력
- `RegimeFilter` (3회 확인 + 5일 쿨다운) + `blend_smoothing_alpha=0.5` 통과 후 confirmed transition은 훨씬 적음
- 적은 transition에 강한 보호 = 비용 누적 > 보호 이익

### 4. MaxDD는 미세 개선
- N=7에서 MaxDD -10.61% (vs baseline -10.90%, +0.29pp)
- 위험 진입 보호는 부분적으로 작동
- 다만 Sharpe -0.09 손실이 압도적

## 보존 정책

코드는 **옵트인으로 영구 보존**:
- `regime_targets.Transition` 비중 정의 그대로
- `regime_filter.transition_days: 0` (기본 비활성)
- `portfolio.blend_regime_targets`의 `transition_phase` 옵션
- `backtest.engine._check_transition` 헬퍼

향후 다음과 같은 변형 재시도 시 즉시 활성 가능:
- 위험 레짐 진입에만 적용 (per_regime gating)
- Transition 비중 더 적극적으로 (equity 50%, 5개 레짐 평균 등)
- 진입 전 신호 (confidence 하락) 추가
- 점진 ramp (B안 — 첫 7일 70:30, 둘째 7일 50:50, 셋째 7일 30:70)

## 학습된 인사이트

1. **사용자 framework는 이론적으로 타당**. 다만 현재 시스템에 이미 transition 흡수 메커니즘이 다층 누적되어 있어 추가 도입 효과 없음.
2. **MaxDD 미세 개선**은 framework의 부분 작동 증거. 다른 변형(per_regime gating, Transition 비중 재설계)에서 효과 가능성 남아있음.
3. **본질적 한계**: detect_regime이 contemporaneous라 "진입 전 인식"이 불가능. 진입 후 시점은 이미 시장이 움직인 후 — transition 보호 자체가 후행적.

## 후속 작업 후보

| 옵션 | 효과 가능성 | 작업 |
|------|----------|------|
| per_regime gating (위험 진입만) | 중간 | 0.5일 |
| Transition 비중 적극적 재설계 | 낮음 (이미 baseline이 적정) | 0.5일 |
| 진입 전 신호 활용 (confidence 기반) | 낮음 (noise 클 가능성) | 1일 |
| 점진 ramp (B안) | 낮음 (이미 blend_smoothing이 흡수) | 1일 |

대부분 효과 불확실. **현재 baseline이 sweet spot에 가까움**.
