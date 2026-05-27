# 실험: C안 Phase 2a — supervised 분류기에서 forward_quantile_v2 검증

*작성일: 2026-05-27*
*상태: 백테스트 결과 baseline 대비 열위. **C안 전체에 대한 본질적 의문 제기. Phase 2b(HMM 통합) 보류 권장.***

## 배경

Phase 1에서 옵션 2 매핑 라벨 자체가 분리도 +4.85pp 회복(Goldilocks 5/5 → 1/5)을 보였음. 그러나 in-sample 측정 한계가 있어, Phase 2에서는 **supervised 분류기(BalancedRF)에 라벨을 가르치고 features로 예측** 검증.

## 구현

`BalancedRFClassifier.label_mode='forward_quantile_v2'` 추가 (`trading/regime.py`):

```python
@staticmethod
def _compute_quantile_labels_v2(feature_matrix, forward_window):
    # t의 라벨 = t+N 시점 (momentum_1m, realized_vol)의 옵션 2 매핑
    # Crisis: rvol p90, Goldilocks: ret p70 + rvol<med, Reflation: ret p70 + rvol>=med
    # Slowdown: ret p30 + rvol<med, Stagflation: ret p30 + rvol>=med
    # 나머지 ~40%는 가장 가까운 코어에 Manhattan 거리
```

기존 'quantile' 모드와의 차이:
- Crisis 임계 p80 → p90 (더 엄격)
- 코어 4-quadrant 명시 (default Slowdown 대신)
- 나머지 시점은 가장 가까운 코어에 할당

비교 시나리오 확장 (`scripts/compare_rf_label.py`):
- `rule` (baseline)
- `forward_rule_21`
- `forward_q_21` (v1)
- `forward_qv2_21` (v2)
- `forward_qv2_63` (v2)

## 결과 (2010~2025, FRED 포함)

| 시나리오           |  CAGR | Sharpe |  MaxDD  | Calmar |  MCC  | 위험감지 미감지 |
|--------------------|------:|-------:|--------:|-------:|------:|---------------:|
| **rule (baseline)**| +9.55%| **0.69**| **-10.60%** | **0.90** | **+0.647** | **10일** |
| forward_rule_21    | +9.70%|  0.69  | -11.64% |  0.83  | +0.565| 15일 |
| forward_q_21 (v1)  | +8.83%|  0.62  | -10.72% |  0.82  | +0.510| 10일 |
| **forward_qv2_21 (v2)** | +8.76% | **0.61** | -11.54% | 0.76 | +0.534| 10일 |
| forward_qv2_63 (v2)| +8.71%|  0.60  | -12.25% |  0.71  | +0.536| 10일 |

## 해석

1. **Phase 1의 분리도 회복이 supervised 학습으로 보존되지 못함**.
   - Phase 1 (라벨 자체): Goldilocks +4.48% Sharpe 10.64
   - Phase 2 (RF 학습 후 features 예측): baseline 대비 Sharpe -0.08

2. **본질 원인 — features → forward stats 예측 능력의 한계**.
   - 라벨 정의는 forward 21일 (수익률, 변동성)의 분포 기반
   - 그러나 RF가 현재 features만으로 그 forward stats를 예측해야 함
   - 시장 효율성으로 인해 features → forward stats 예측은 본질적으로 어려움
   - 학습 라벨의 좋은 분리도가 추론 단계에서 보존되지 않음

3. **v2가 v1보다도 약간 더 열위**.
   - 더 엄격한 매핑이 학습 데이터에서 더 의미 있게 분리되지만, 그 만큼 generalize도 어려워짐
   - RF가 더 noise를 학습할 수 있음

4. **모든 forward 라벨 변형이 일관되게 baseline 열위**.
   - Round 2/3 (forward_rule): -0.01~-0.07
   - Round 2/3 (forward_q v1): -0.05~-0.07
   - Phase 2a (forward_qv2): -0.08

## C안 전체에 대한 평가

라벨 정의 자체는 좋지만, **시장 effects 때문에 features 기반 예측이 baseline rule 분류를 능가하지 못함**. 본 결과는 일회성이 아니라 여러 실험에서 일관된 패턴 — C안 framework 자체가 다음 두 가지 한계를 안고 있음:

- **시장 단기 forward return의 본질적 unpredictability** (특히 21-63일 horizon)
- **features의 forward 예측 정보량 부족** (현재 features는 contemporaneous 시장 상태에 더 적합)

## 권장 결정

**A안: C안 보류** (강력 권장)
- Phase 2b (HMM 통합) 진행해도 같은 본질적 한계에 부딪힐 가능성 매우 높음
- 코드는 옵트인으로 보존 (label_mode='forward_quantile_v2'는 config로 활성화 가능)
- 다른 방향으로 ensemble override / regime_targets 자체 최적화 등 검토

**B안: Phase 2b 강행**
- HMM 통합이 RF의 한계를 흡수할 수 있다는 가설 — 그러나 본 결과로 봤을 때 가능성 낮음

**C안: 더 짧은 forward window 또는 더 강력한 모델로 재시도**
- forward N=5/10일이 더 예측 가능할 수도. 다만 매매 주기와 mismatch.
- gradient boosting / neural net이 더 잘 generalize할 수도. 그러나 라벨 자체의 unpredictability가 더 큰 제약일 가능성.

## 보존된 코드 자산

C안이 보류되더라도 다음은 영구 보존:
- `BalancedRFClassifier.label_mode='forward_quantile_v2'` (config로 활성)
- Phase 1 시뮬레이션 도구 (`scripts/compare_quantile_regimes.py`)
- 본 실험 결과 문서들

후속 작업에서 라벨 정의·예측력 관련 실험이 필요하면 즉시 재사용 가능.
