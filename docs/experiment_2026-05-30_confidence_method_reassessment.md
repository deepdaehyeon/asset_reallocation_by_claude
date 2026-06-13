# confidence_method 재평가 — G 작업 검증 결과 (2026-05-30)

> **요약**: ① anomaly 패널티(-50%)에서 min+0.20 조합의 stuck 위험을 해결하기 위해 product+0.10으로 전환 가능성을 백테스트로 검증했다. ② 백테스트 Sharpe/MaxDD는 mean·min·product 모두 동일했으나, product 전환 시 fallback rate가 86.3%로 급증해 G가 우려한 "stuck 빈도"가 오히려 악화됨을 발견했다. ③ min + threshold 0.20 유지 결정 — product의 단조성 우위(+0.036)가 fallback 1.5배 증가를 정당화하지 못하며, stuck 근본 원인인 anomaly 패널티 강도 재조정은 라이브 데이터 누적 후 별도 결정한다.

## 배경

이전 분석 (2026-05-29)에서 G 항목으로 "`confidence_method=min` + `confidence_threshold=0.20`이 anomaly 패널티(-50%)에 취약 → stuck 가능"이라 식별. config 주석 권장값(`product` + 0.10)으로 전환 검증.

## 실험

스크립트: `scripts/compare_confidence_methods.py`
구간: 2020-01-01 ~ 2025-04-30 (5.3년, 1339 리밸런싱)
설정: drift 모드 (라이브 정합), FRED 매크로 포함

## 결과

```
method        Sharpe     MaxDD    Calmar         ρ    fallback@0.40
mean           0.784    -9.98%     1.110    -0.119       47.9%
min            0.784    -9.98%     1.110    +0.847       68.6%
product        0.784    -9.98%     1.110    +0.883       86.3%
```

### 핵심 발견

1. **Sharpe/MaxDD/CAGR 셋 다 동일** — `confidence_method`는 백테스트 엔진에 fallback 로직이 없어 거래 결과에 직접 영향 없음. 라이브 `run.py`의 "임계 미달 시 이전 확정 레짐 유지" 분기에서만 의미.

2. **단조성 ρ**: `mean -0.119` → `min +0.847` → `product +0.883`. min·product 둘 다 신뢰도-정확도 단조 회복. **product가 min 대비 +0.036 우위 (미세)**.

3. **fallback rate**: mean 48% → min 69% → product 86%. **product는 신뢰도가 훨씬 더 낮은 분포** (rule×hmm 곱 산식의 자연 결과).

### bin별 accuracy 비교 (ensemble == rule_regime 비율)

| bin | min n / acc | product n / acc |
|---|---|---|
| (0.0, 0.1] | 344 / 12% | **538 / 38%** |
| (0.1, 0.2] | 107 / 73% | 341 / 75% |
| (0.2, 0.3] | 435 / 77% | 206 / 80% |
| (0.5, 0.6] | 119 / 87% | 28 / 100% |
| (0.9, 1.0] | 78 / 100% | 78 / 100% |

**product의 0.0-0.1 bin이 538회로 가장 큰 덩어리**. min은 같은 구간이 344회. product로 가면 "매우 낮은 신뢰도" 회차가 훨씬 많이 생성됨.

## 해석

G의 원래 가설은 "anomaly가 높으면 min×0.5 = 임계 0.20 미달 → stuck 위험". 이걸 product로 풀려고 했는데, **product는 fallback rate를 26%(min@0.20 추정) → 40%(product@0.10 추정)으로 늘림** → G가 우려한 "stuck 빈도"가 정반대 방향으로 악화.

product의 단조성 우위 +0.036은 너무 미세해 fallback 빈도 1.5배 증가를 정당화하지 못함.

## 결정

**`confidence_method=min` 유지, `confidence_threshold=0.20` 유지.** product 전환은 G의 의도(stuck 빈도 감소)와 반대 방향으로 작용.

G의 진짜 stuck 위험은 anomaly 패널티(0.5)가 너무 강한 데서 기인. 해결 후보:
- **G-alt-1**: `anomaly.confidence_penalty: 0.5 → 0.3` (패널티 약화)
- **G-alt-2**: `confidence_threshold: 0.20 → 0.15` (임계 완화)
- **G-alt-3**: 둘 다 (보수적 약화)

이번 백테스트로는 위 셋의 영향을 측정 불가(엔진에 fallback 로직 없음). 라이브 monitor.log를 일정 기간 모은 후 anomaly 분포·실제 stuck 빈도를 본 다음 결정이 적절.

## 후속

- G는 큐에서 **"라이브 데이터 누적 후 anomaly 패널티/임계 재조정"**으로 변경.
- product 전환 시도는 본 문서로 마무리 (베네핏 미미, 코스트 명확).
