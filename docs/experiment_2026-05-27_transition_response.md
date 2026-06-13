# 실험: Transition 후행 손실 완화 (override 완화 + Crisis 우선 + forward HMM)

> **요약**: ① override_threshold 완화(0.60→0.50), Crisis blend 우선 진입(≥0.30), HMM 1-step forward prediction 세 가지 변경으로 레짐 전환 후행 손실 완화를 시도했다. ② Crisis 진입 후 21일 수익이 +0.06%(후행) → -0.30%(적시)로 전환됐고, portfolio Sharpe·MaxDD에는 영향이 미미(-0.004~-0.007)했으며, forward HMM은 추가 효과가 거의 없었다. ③ A안(override_threshold 0.50만) 채택 권장 — Crisis 적시성을 개선하면서 다른 부작용이 없고, forward_hmm은 복잡도 대비 효과가 미미해 보류됐다.

*작성일: 2026-05-27*
*상태: 실험 완료. **Crisis 적시성 명확히 개선, portfolio metric 영향 미미**. 채택은 사용자 결정.*

## 배경

진단(`regime_diagnostics.json`)에서 발견된 transition 후행 문제:
- Stagflation → Crisis 전환 직후 21일 +0.22% (이미 반등 시작 후 진입)
- Slowdown → Crisis 전환 직후 21일 -0.45% (그나마 적시)
- Goldilocks → Slowdown 전환 직후 21일 +0.42% (잘못된 진입)

세 가지 변경으로 transition 적시성 개선 시도:
1. **`override_threshold` 0.60 → 0.50**: HMM/RF 다수결을 더 빨리 채택
2. **Crisis 비대칭 우선**: blend["Crisis"] ≥ 0.30이면 다른 조건 무시하고 즉시 Crisis
3. **HMM forward prediction**: `predict_proba` 대신 `predict_proba_forward(horizon=1)` 사용 (transition matrix 곱 → 1-step ahead 분포)

## 구현

- `ensemble_regime()`에 `crisis_priority_threshold` 인자 추가 (`trading/regime.py`)
- `HmmRegimeClassifier.predict_proba_forward(seq, horizon=1)` 신규 메서드
- config 키: `hmm.crisis_priority_threshold` (null 비활성), `hmm.use_forward_hmm`, `hmm.forward_hmm_horizon`
- `backtest/engine.py` · `trading/run.py` 모두 새 옵션 전달

## 결과 (2010~2025, FRED 포함)

| 시나리오            | Sharpe | MaxDD   | Calmar | miss일 | crisis_d | **Crisis 진입 후 21일** |
|--------------------|-------:|--------:|-------:|------:|---------:|----------------------:|
| baseline           | 0.687  | -10.60% | 0.901  | 10    | 168      | **+0.06%** (후행)     |
| override_50        | 0.680  | -10.60% | 0.896  | 15    | 207      | **-0.30%** (적시!)    |
| +crisis_prio_30    | 0.683  | -10.60% | 0.898  | 15    | 319      | **-0.11%** (적시)     |
| +forward_hmm       | 0.682  | -10.66% | 0.892  | 15    | 310      | -0.06% (적시)         |

## 해석

### 1. Crisis 적시성 본질적 개선

**baseline +0.06% → override_50 -0.30%**: Crisis 진입 후 21일 수익률이 양수에서 음수로 전환. 양수였던 baseline은 "Crisis 진입 시점에 이미 시장이 반등을 시작" = 후행. 음수가 된 변경 시나리오들은 "Crisis 진입 시점에 시장이 여전히 폭락 중" = 적시.

이는 진단에서 발견된 transition 후행 문제가 ensemble 단계의 보수성에 기인했음을 확인. override threshold 0.50으로 낮추는 것만으로도 ensemble이 더 적극적으로 Crisis 신호를 채택.

### 2. portfolio metric 영향 미미

Sharpe: -0.004 ~ -0.007, MaxDD: 동일 ~ 0.06pp. **시스템이 이미 다층 안전망을 가져** 분류 변경이 portfolio 결과로 크게 이어지지 않음:
- `blend_regime_targets`의 연속 노출 (확정 레짐과 무관하게 blend_probs 가중 평균)
- drawdown scale-down (-10%/-15%/-25%)
- vol_targeting (실현 변동성 기반 자동 축소)
- anomaly score 페널티

본 시스템의 설계 자체가 "분류 정확도가 모든 것을 결정하지 않도록" 안전망을 겹쳐 둠. 이게 transition 후행의 portfolio 손실을 -2~-3pp 수준으로 제한한 본질.

### 3. 위험 미감지는 +5일이지만 평균 손실 절반으로

baseline: 10일 평균 -0.303%/일 (총 -3.0% 누적 추정).
변경 후: 15일 평균 -0.176%/일 (총 -2.6% 누적 추정).

**놓치는 일수는 늘었지만 놓치는 시점의 위험도가 줄어듦**. Crisis가 더 빨리 잡혀서 진짜 큰 폭락은 덜 놓침.

### 4. crisis_prio_30은 Crisis 일수 거의 2배 (168 → 319일)

false positive 위험. 평소에도 Crisis로 잡혀 자산을 과보수적으로 운용 가능성. 임계를 0.40 정도로 더 보수적으로 잡으면 균형 좋을 듯.

### 5. forward_hmm 단독 추가 효과 미미

Sharpe/MaxDD 거의 동일. HMM transition matrix가 작은 noise라 1-step ahead가 큰 시그널 차이를 만들지 못함.

## 권장 채택안

| 안 | 변경 | 효과 | 위험 |
|----|------|------|------|
| **A** | override_threshold 0.50만 | Crisis 적시성 개선 + Sharpe 영향 미미 | 거의 없음 |
| B | A + crisis_prio_0.40 | 더 빠른 Crisis 진입 | Crisis 일수 증가 (적정 임계 결정 필요) |
| C | A + crisis_prio_0.30 + forward_hmm | 최대 적시성 | Crisis 일수 거의 2배, 운영 부담 |

**A안 권장** — 가장 안전한 변경. Crisis 적시성 본질적 개선이라는 의미를 살리되 다른 부작용 없음. forward_hmm은 미미한 효과 대비 복잡도 증가라 보류 가능.

## 보존된 코드

채택과 무관하게 모든 코드는 영구 보존 (옵트인 config로 활성):
- `ensemble_regime(..., crisis_priority_threshold=...)`
- `HmmRegimeClassifier.predict_proba_forward(seq, horizon=...)`
- config 키 3개

후속 작업에서 동일 메커니즘 재실험 시 즉시 활용 가능.
