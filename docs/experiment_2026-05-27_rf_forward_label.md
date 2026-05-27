# 실험: RF forward-looking 라벨링 도입 (자기참조 제거)

*작성일: 2026-05-27*
*상태: 백테스트 검증 완료 — 현재 형태로는 채택 보류 (`rf_forward_window=0` 유지)*

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

실행 조건:
- 기간: 2010-01-01 ~ 2025-04-30 (15년)
- 리밸런싱: W-FRI, tx_cost 0.001
- FRED 매크로 없이 가격 파생 7개 피처만 사용 (`FRED_API_KEY` 미설정 환경)
- 비교: `w=0` (rule baseline) / `w=21` (~1M forward) / `w=63` (~3M forward)

### 백테스트 성과

| metric        |    w=0    |   w=21    |   w=63    |
|---------------|----------:|----------:|----------:|
| CAGR          | **+10.38%** |  +9.84% | +10.34% |
| Volatility    |    8.18%  |   8.26%  |   8.31%  |
| Sharpe        | **0.779** |   0.707  |   0.763  |
| MaxDD         | **-10.96%** | -12.60% | -12.53% |
| Calmar        | **0.947** |   0.781  |   0.825  |

### 분류 metric (rule_regime 기준)

| metric        |    w=0    |   w=21    |   w=63    |
|---------------|----------:|----------:|----------:|
| MCC           | **+0.821** |  +0.421 |  +0.427  |
| Macro-F1      | **0.814** |  0.534   |  0.534   |
| BalancedAcc   | **0.859** |  0.580   |  0.583   |
| Override율     |   10.7%   | **37.1%** | **36.9%** |
| 위험레짐 미감지일수 |     0     |    0     |    0     |

### 해석

1. **백테스트 성과는 forward 라벨이 약간 열위**. w=21에서 Sharpe -0.07, MaxDD -1.6pp 악화. w=63은 차이가 더 작지만 baseline 대비 우월하진 않다.
2. **분류 metric 큰 폭 하락은 측정 도구의 한계**. metric이 `rule_regime`을 정답으로 보고 측정하므로, "자기참조를 끊은" forward 라벨 RF가 룰과 다른 결정을 내릴수록 자동으로 점수가 떨어진다. 이 metric으로 forward 모델의 실제 우월성을 판단할 수 없다.
3. **Override 비율이 10.7% → 37%로 급증**. RF가 룰과 자주 충돌해 ensemble이 RF를 채택. 비평이 우려한 "RF가 룰을 모방해 확신을 증폭시킨다"의 정반대 — RF가 룰과 다른 시그널을 내고 있다. 자기참조는 실제로 끊겼다. 하지만 그 새로운 시그널이 백테스트 성과로 이어지지 못함.
4. **MaxDD 악화(-1.5~-1.6pp)가 가장 문제**. forward 라벨로 RF의 결정 경계가 매끄러워졌지만, 그 결과가 위기 구간에서 보호 시그널을 약화시킨 듯하다 (Override 빈도가 늘어 ensemble이 룰의 보수성을 덜 반영).

### 판정

현재 형태로는 채택 보류. `config.yaml`의 `hmm.rf_forward_window`는 **0(기존 룰 라벨)**을 유지.

코드 자체는 옵트인 형태로 보존 (롤백 비용 0). 후속 실험에서 재평가 가능.

### 남은 한계와 후속 작업 제안

- **평가 metric의 자기참조 문제**: forward 모델의 진짜 우월성은 forward-looking ground truth(예: 다음 N일 SPY 실현 수익률/변동성 quantile)로 평가해야 한다. 비평이 처음에 제안한 "옵션 2: Forward 수익률 quantile binning"이 본질적인 비교가 가능한 길.
- **FRED 매크로 부재**: 본 실험은 가격 파생 7개 피처만으로 진행. FRED 매크로(특히 hy_spread_zscore, cpi_yoy)가 있을 때 forward 라벨이 더 유리할 가능성이 있다. `FRED_API_KEY` 설정 후 재실행 권장.
- **blend 가중치 미조정**: 0.6·HMM + 0.4·RF를 그대로 썼다. forward 라벨 RF는 의미가 살짝 다르므로 blend 가중치를 함께 튜닝해 비교하는 것이 공정.

이 세 가지를 보강한 옵션 2 실험을 후속으로 두고, 본 안(옵션 1: forward+detect_regime)은 옵트인 옵션으로 보존한다.

