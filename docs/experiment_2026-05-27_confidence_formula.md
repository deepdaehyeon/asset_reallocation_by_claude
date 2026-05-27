# 실험: 신뢰도 결합 산식 비교 (외부 비평 #4 본질 해결)

*작성일: 2026-05-27*
*상태: 비교 완료. **min 또는 product 채택 권장**, fallback threshold 동시 조정 필요.*

## 배경

이전 진단(`docs/experiment_2026-05-27_regime_observability.md`)에서:
- 기존 `combined_conf = (rule_conf + hmm_conf) / 2`의 단조성 Spearman ρ = **-0.325 (음수)**.
- 즉 confidence가 높아져도 ensemble↔rule 일치율이 단조 증가하지 않음.
- 외부 비평 #4: "rule_conf와 hmm_conf는 스케일·의미가 다른 값. 산술평균이 정당한지 불분명."

본 실험은 단조성 회복 후보인 두 산식과 baseline을 동일 백테스트로 비교.

## 구현

- `trading/regime.py`에 `compute_combined_confidence(rule, hmm, method)` 추가
  - `method`: `mean` | `min` | `product`
- `regime_filter.confidence_method` config 키 (기본 `mean` 호환)
- `trading/run.py` · `backtest/engine.py`가 이 함수 사용
- 비교 스크립트: `scripts/compare_confidence_methods.py`

## 결과 — 2010-01-01 ~ 2025-04-30, FRED 매크로 포함

### 요약

| method     | Sharpe | MaxDD  | Calmar | **Spearman ρ** | fallback @0.40 |
|------------|-------:|-------:|-------:|---------------:|---------------:|
| **mean**   |  0.70  | -9.4%  |  1.02  | **-0.325**     |  38.8%         |
| **min**    |  0.70  | -9.4%  |  1.02  | **+0.595**     |  72.4%         |
| **product**|  0.70  | -9.4%  |  1.02  | **+0.681**     |  83.2%         |

**핵심 관찰**:

1. **단조성이 극적으로 회복** — Δρ ≈ +0.9~+1.0. 비평이 정확했음. 두 점수의 단순 평균은 비단조 산식이고, min/product는 단조 회복.
2. **백테스트 성과(Sharpe/MaxDD/Calmar)는 세 method 모두 동일**. 이유: 백테스트 엔진은 confidence_threshold 폴백 로직을 적용하지 않고 단지 confidence를 시계열로 기록만 함. 따라서 산식 변경이 백테스트 metric에 직접 영향 X.
   - **라이브(run.py)에서는 confidence < threshold일 때 "이전 확정 레짐 유지" 폴백이 작동**하므로 실제 라이브 영향은 백테스트 결과와 다를 수 있다.
3. **fallback rate가 급증** — mean 38.8% → min 72.4% → product 83.2%. 현재 `confidence_threshold=0.40`은 mean 스케일 기준으로 잡힌 값이라 min/product에 그대로 쓰면 폴백이 너무 빈번.

### bin별 accuracy (mean의 비단조 → min·product의 단조 회복)

mean (-0.325):
- (0.3, 0.4] 1139일에 **48%** (동전 던지기), (0.8, 0.9] 131일에 **44%** — 비단조 outlier 다수

min (+0.595):
- 하위 bin에서 낮음 (0.1대 acc 9-28%), 상위 bin에서 높음 (90-100%) — 단조 회복
- 단 (0.6, 0.7] 83일에서 **0%** outlier — 특정 상황 누설

product (+0.681):
- min과 유사한 단조 회복. (0.6, 0.7] 78일에서 **12%** 동일 outlier

→ min과 product 둘 다 같은 outlier bin이 있음 → 산식과 무관한 특정 시장 상황의 잘못된 ensemble override. 산식 자체의 단조성은 둘 다 양호.

## 권장 채택안

| 안 | 산식 | 새 threshold 권장 | 장점 | 단점 |
|---|------|-----------------|------|------|
| A | **min** | **~0.20** | 단조성 양호(+0.60), fallback rate 적정 | product보다 약한 단조성 |
| B | **product** | **~0.15** | 단조성 가장 강함(+0.68), 가장 보수적 결합 | fallback 너무 잦으면 ensemble 의미 약화 가능 |
| C | mean 유지 | 0.40 | 호환성 | **#4가 그대로 남음** (권고 X) |

**A(min) 우선 권장 이유**:
- 단조성 회복이 충분히 강함 (+0.595, 양수)
- "두 신호 중 약한 쪽" 해석이 직관적 — Crisis 같은 강한 signal은 rule_conf=1.0이라도 hmm_conf가 낮으면 보수적으로 작동
- product의 multiplicative effect는 양쪽이 0.5씩만 되어도 0.25로 떨어져 fallback 과잉 가능

**새 threshold 결정 가이드**:
- mean 0.40에서 fallback 38.8%였음 → 같은 fallback 비율을 유지하려면 min에서는 약 0.20 근처.
- 정확한 값은 운영 정책에 따라 결정. 일단 0.20부터 시작해 fallback rate 보고 조정.

## 적용 방법

`trading/config.yaml`:
```yaml
regime_filter:
  confidence_method: min          # mean → min
  confidence_threshold: 0.20      # 0.40 → 0.20 (min 스케일에 맞춤)
```

## 한계

- 백테스트가 confidence 폴백을 시뮬레이션하지 않아 실제 라이브 효과 측정 불완전.
  - 후속: `backtest/engine.py`에 confidence < threshold일 때 "직전 confirmed regime 유지" 로직 추가하면 산식별 실제 성과 차이 측정 가능.
- (0.6, 0.7] bin의 outlier(min·product 둘 다 acc≤12%)는 산식과 무관한 ensemble override 문제 — 별도 분석 가치 있음.
