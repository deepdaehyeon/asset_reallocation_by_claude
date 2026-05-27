# 실험: RF forward-looking 라벨링 도입 (자기참조 제거)

*작성일: 2026-05-27*
*상태: 코드 도입 완료, 백테스트 검증 대기*

## 배경

`BalancedRFClassifier`는 학습 시 각 시점 `t`의 피처 벡터에 동일 시점의 `detect_regime()`(룰 기반) 결과를 라벨로 부여해 왔다. 외부 리뷰가 지적한 문제:

> RF는 detect_regime()으로 레이블을 받는다. 즉 RF는 룰의 근사기일 뿐, 독립적인 정보원이 아니다. blend가 0.6·HMM + 0.4·RF인데, RF가 룰을 모방한다면 blend는 사실상 "HMM 60% + 룰의 부드러운 버전 40%"이고, ensemble 단계에서 다시 룰과 비교한다. 룰이 세 번 영향을 준다(레이블, blend, override 기준).
> ⇒ 룰의 체계적 편향(예: curve 역전을 항상 bearish로 보는 것)을 RF가 학습하면 교정 대신 확신을 증폭시킨다.

## 개선안

RF의 라벨을 **t+N 시점의 `detect_regime()` 결과**로 바꾼다.

- 의미: "현재 피처가 N영업일 후 어떤 레짐과 연관되는가"를 학습.
- 자기참조 끊김: t 시점 라벨이 t 시점 피처에서 직접 계산되지 않으므로 룰의 즉시적 결정 경계를 단순히 모방할 수 없다.
- 미래 레짐을 예측하는 진정한 forward-looking 모델로 동작.

## 구현 (이미 반영)

### config (`trading/config.yaml`)
```yaml
hmm:
  ...
  # 0         → 동일 시점 detect_regime() (기존, 룰 근사기)
  # N (>0)    → t+N 시점 detect_regime() (forward-looking)
  rf_forward_window: 0    # 기본 0 = 옵트인. 검증 후 21 등으로 변경 권장.
```

### `BalancedRFClassifier(forward_window=...)`
- `forward_window=0`: 기존 동작 (룰 라벨).
- `forward_window=N>0`:
  - `labels[t] = detect_regime(feature_matrix.iloc[t + N])`
  - 학습 셋은 마지막 N영업일 제외 (라벨링 불가).
  - 표본 부족(`len(fm) <= N+1`) 시 안전하게 룰 라벨로 폴백.
- `label_method` property로 실제 사용된 라벨 방식을 외부에서 조회 가능.

### 호출 측
- `trading/run.py`: 라이브 트레이딩 — `hmm.rf_forward_window`를 `BalancedRFClassifier`에 주입.
- `backtest/engine.py`: 워크포워드 백테스트도 동일 인자 전달.

## 검증 방법

비교 백테스트 스크립트:

```bash
python scripts/compare_rf_label.py --start 2010-01-01 --end 2025-04-30 --windows 0,21,63
```

3가지 시나리오를 동일 데이터·동일 설정에서 실행:
- `w=0`: 기존 룰 라벨 baseline
- `w=21`: 약 1개월 forward
- `w=63`: 약 3개월 forward

각 시나리오마다 백테스트 metric(CAGR/Vol/Sharpe/MaxDD/Calmar)과 레짐 분류 metric(MCC/Macro-F1/Override율/위험레짐 미감지일수) 출력. 마지막에 Sharpe·MaxDD 사전사전 정렬로 권장 N 자동 판정.

## 채택 기준

- baseline 대비 **Sharpe 동등 이상 + MaxDD 악화 없음**이 우선.
- 룰 라벨 대비 분류 metric(특히 위험 레짐 미감지일수)이 개선되면 의미 있음.
- 둘 다 만족하는 N이 있으면 `config.yaml`의 `hmm.rf_forward_window`를 그 값으로 설정.

## 한계 (남는 비평)

- forward 라벨 자체가 `detect_regime()` 출력이라 **룰의 한계는 그대로** 상속한다. 룰이 잘못 분류하는 구간은 forward 라벨도 잘못된 정답으로 학습된다.
- 진짜 ground-truth는 forward N일의 SPY 수익률/변동성 quantile일 수 있으나 (옵션 2), 그 경우 blend 수식과 출력 해석 자체가 재설계 대상이 된다 — 별도 후속 실험으로 남김.
- blend 수식(0.6·HMM + 0.4·RF)은 그대로 유지. RF 출력의 의미가 "현재"에서 "N일 후"로 살짝 시점 이동하지만, 두 신호의 결합 자체는 부드러운 가중평균이라 실무적으로 큰 충돌은 없다고 본다.

## 결과

(백테스트 실행 후 채워 넣을 것)
