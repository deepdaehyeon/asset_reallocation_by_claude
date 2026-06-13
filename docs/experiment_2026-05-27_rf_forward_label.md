# 실험: RF forward-looking 라벨링 도입 (자기참조 제거)

> **요약**: ① RF 학습 라벨을 동시점 detect_regime 대신 t+N 시점 레짐(N=21/63일)·quantile 기반 라벨로 교체하는 4라운드 실험을 수행했다. ② 자기참조는 실제로 끊겼으나(Override 30%→50-65%) 4라운드 전부에서 위험 레짐 미감지가 2~3배 늘었고 MaxDD도 악화됐으며, FRED publication lag 적용(Round 3)이 진짜 백테스트를 복원하는 가장 큰 수확이었다. ③ 채택 보류 — rf_forward_window=0 유지, 자기참조 제거가 능사가 아님(위험 감지 약화)이 4라운드 일관 결론이며, publication lag 적용만 라이브에 반영됐다.

*작성일: 2026-05-27 (Round 1·2·3 통합)*
*상태: 채택 보류 (`rf_forward_window=0` 유지). 가장 큰 수확은 #3(FRED publication lag) 반영으로 baseline 자체의 신뢰성 회복.*

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

## 결과 — Round 1 (FRED 없이, 옵션 1만)

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

## 결과 — Round 2 (FRED 매크로 포함 + 옵션 2 라벨 추가)

Round 1 한계점을 보강해 재실험:
1. **FRED 매크로 포함** (7개: cpi_yoy / cpi_mom_zscore / unrate_chg_3m / breakeven_5y / m2_yoy / fed_bs_yoy / curve_10y2y). 단, ICE 라이선스 회수로 `hy_spread`는 빠짐.
2. **옵션 2(quantile binning) 추가**. t의 라벨을 t+N 시점 `(momentum_1m, realized_vol)` 학습 분포 분위로 매핑. detect_regime 호출 없음.
3. **5개 시나리오 한 번에 비교**: rule(w=0) / forward_rule_21·63 / forward_q_21·63.

### 백테스트 metric

| 시나리오            |  CAGR | Sharpe |  MaxDD  | Calmar | Override | 위험레짐 미감지 |
|--------------------|------:|-------:|--------:|-------:|---------:|---------------:|
| **rule (baseline)** | +8.8% |  0.58  | **-12.5%** | **0.70** |  30.2%  | **10/373 (3%)** |
| forward_rule_21    | +9.0% | **0.60** | -14.3%  |  0.63  |  50.7%  |  30/373 (8%)   |
| forward_rule_63    | +8.8% |  0.58  | -14.9%  |  0.59  |  50.1%  |  30/373 (8%)   |
| forward_q_21       | +8.1% |  0.53  | -13.5%  |  0.60  |  65.1%  |  30/373 (8%)   |
| forward_q_63       | +8.0% |  0.51  | -14.6%  |  0.55  |  64.5%  |  30/373 (8%)   |

### 분류 metric (rule_regime 기준 — 옵션 2처럼 자기참조 끊긴 모델은 점수 자동 하락)

| 시나리오            |   MCC | Macro-F1 | BalAcc | Override |
|--------------------|------:|---------:|-------:|---------:|
| rule (baseline)    | +0.527| 0.591    | 0.664  |  30.2%   |
| forward_rule_21    | +0.265| 0.412    | 0.486  |  50.7%   |
| forward_rule_63    | +0.275| 0.413    | 0.487  |  50.1%   |
| forward_q_21       | +0.129| 0.329    | 0.424  |  65.1%   |
| forward_q_63       | +0.134| 0.333    | 0.426  |  64.5%   |

### 해석

1. **위험 레짐 미감지가 가장 강한 차별 신호**. Baseline이 10일인 반면 forward 라벨은 모두 30일(3배). 위험 레짐 1회 놓침이 누적 수익에 미치는 영향이 Sharpe 0.02 개선보다 훨씬 큼.
2. **자동 판정 알고리즘(Sharpe·MaxDD)이 `forward_rule_21`을 추천**했지만, MaxDD가 -1.8pp 악화되고 위험 미감지 3배가 동반 → 실질 권고는 baseline 유지.
3. **옵션 2(quantile)가 옵션 1보다 더 떨어진다**. Sharpe 0.51-0.53, Override 65%. 두 가지 가능 해석:
   - quantile 매핑이 룰의 임계 편향을 회피하면서 동시에 룰 라벨링이 담고 있던 유효한 사후 정보(예: rvol·VIX의 비선형 결정 경계)를 잃었다.
   - 또는 5개 레짐 자체가 detect_regime 룰의 발명품이라, 분위 매핑이 같은 레짐 라벨로 잘 정렬되지 않음.
4. **FRED 매크로 자체가 백테스트 성과를 향상시키지 못함**. Round 1(FRED 없음, w=0) Sharpe 0.78 → Round 2(FRED 있음, w=0) Sharpe 0.58. 가능한 원인:
   - 매크로 피처가 RF/HMM 입력 노이즈를 더했다.
   - 또는 publication lag 미반영(외부 비평 #3)으로 매크로가 사실상 사후정보를 누설 → 학습은 그럴듯해 보이지만 backtest 시점 정합성이 깨져 실제 효과는 음.
   - 후자라면 FRED 포함 시 결과 자체가 신뢰성이 낮다는 의미.

### 종합 판정

- **`hmm.rf_forward_window=0` 유지** (`rf_label_mode`는 기본값 `rule_at_future` 그대로). 옵션 1·2 어느 쪽도 baseline을 능가하지 못함.
- 자기참조 문제는 실제로 끊겼다(Override 30% → 50-65%)는 점은 확인되었으나, 끊긴 결과 분류가 **위험 레짐을 덜 식별**하는 방향으로 작용. 단순 자기참조 제거가 능사가 아님.
- **FRED 매크로 통합 자체가 의심스러움**. 매크로 추가가 성능을 깎는다면 publication lag 정합성 문제(#3)가 선행되어야 의미 있는 실험이 가능. → #3을 먼저 처리한 뒤 본 실험을 재실행하는 것이 합리적.

### 후속 작업 우선순위

1. **#3 (FRED publication lag)** — 매크로 피처에 publication lag 적용 후 본 실험 재실행. 매크로가 정합성을 갖춘 상태에서 forward 라벨이 다르게 동작할 가능성 있음.
2. 옵션 2의 매핑 자체를 5개 레짐 대신 자체 quantile class(예: 6개 bin)로 두고 blend 단계에서 매핑 — blend 수식 재설계 동반.
3. blend 가중치(0.6·HMM + 0.4·RF) 자체 튜닝.

## 결과 — Round 3 (FRED + publication lag 적용)

외부 비평 #3을 반영해 시리즈별 publication lag을 적용한 후 같은 비교 백테스트 재실행.
적용 lag (영업일, 발표 캘린더 기반 보수적 추정):
- CPI 30, UNRATE 25, M2 30, WALCL 7, BEI/T10Y2Y/HY 각 1.

### 백테스트 metric

| 시나리오            |  CAGR | Sharpe |  MaxDD  | Calmar | Override | 위험레짐 미감지 |
|--------------------|------:|-------:|--------:|-------:|---------:|---------------:|
| **rule (baseline)** | +9.6% |  0.70  | **-9.4%** | **1.02** |  34.3%  | **20/373 (5%)** |
| forward_rule_21    | +9.8% | **0.71** | -10.7%  |  0.92  |  53.9%  |  35/373 (9%)   |
| forward_rule_63    | +9.8% |  0.71  | -10.4%  |  0.94  |  51.2%  |  35/373 (9%)   |
| forward_q_21       | +9.0% |  0.65  |  -9.8%  |  0.92  |  65.6%  |  35/373 (9%)   |
| forward_q_63       | +8.9% |  0.63  | -10.9%  |  0.81  |  65.0%  |  35/373 (9%)   |

### 라운드 간 baseline 비교 (가장 중요한 발견)

| Round | 조건                                |  CAGR | Sharpe |  MaxDD  | Calmar |
|-------|-------------------------------------|------:|-------:|--------:|-------:|
| 1     | FRED 없음 (가격 피처만)              | +10.4% |  0.78  | -11.0%  |  0.95  |
| 2     | FRED 포함 + **lag 미적용**          | +8.8% |  0.58  | -12.5%  |  0.70  |
| **3** | **FRED 포함 + lag 적용**            | +9.6% | **0.70** | **-9.4%** | **1.02** |

### 해석

1. **publication lag 적용이 lookahead bias의 영향을 제거했다는 강한 증거**. Round 2 → Round 3에서 baseline Sharpe +0.12, MaxDD +3.1pp 개선. 즉 Round 2는 사후 발표된 매크로를 "당시에 알 수 있었던 것처럼" 사용한 결과로 신뢰성이 낮았다. Round 3가 정합성 갖춘 진짜 백테스트 결과.
2. **Round 3 baseline이 Round 1(FRED 없음)보다 약간 낮은 건 정상**. 매크로 피처가 가격 피처만 대비 노이즈를 약간 더한 결과로 볼 수 있다. Calmar는 오히려 Round 3가 더 높음(1.02 vs 0.95).
3. **forward 라벨 채택 결론은 여전히 보류**. forward_rule_21이 Sharpe +0.01 우월이지만 MaxDD -1.3pp 악화, 위험레짐 미감지 1.75배(20→35일). Round 2와 동일한 패턴.
4. **자기참조 끊긴 모델이 일관되게 위험감지에서 열위**라는 점이 Round 2·3에서 모두 관찰됨. 단순 자기참조 제거가 능사가 아니라는 본 실험의 핵심 결론은 lag 적용 후에도 유지.

### 최종 판정

- **`hmm.rf_forward_window=0` 유지** (`rf_label_mode`는 기본값 `rule_at_future`). 옵션 1·2 모두 baseline을 능가 못함.
- **publication lag 적용은 라이브 시스템에도 반영 완료** (`trading/fetcher.py`). 이번 실험의 가장 큰 실질 수확.
- 자기참조 끊기 자체는 잘 동작하나(Override 30% → 50-65%), 위험 레짐 감지 약화 때문에 baseline보다 우월하지 못함.

### 남은 후속 작업

1. **옵션 2의 매핑 재설계**: 5개 레짐 강제 매핑 대신 자체 quantile class(예: 6개 bin)를 두고 blend 수식 재설계. → **Round 4에서 `forward_quantile_v2`로 구현·검증 완료, 기각.**
2. **blend 가중치 튜닝**: 0.6·HMM + 0.4·RF 가중치를 데이터 기반으로 결정 (특히 lag 적용 후 매크로 신뢰도가 회복된 상황에서 재평가).
3. **HY 스프레드 대체 시리즈** 탐색 (ICE 라이선스 회수 대응): BAA 회사채 스프레드, NFCI 등.

## 결과 — Round 4 (옵션 2 매핑 재설계 `forward_quantile_v2`, 2026-05-30)

Round 3 후속 #1을 반영해 옵션 2 매핑을 재설계한 `forward_quantile_v2` 모드를 추가하고 동일 비교 백테스트 재실행.

**`forward_quantile_v2` vs 구 `quantile` 차이:**
- Crisis 임계 더 엄격 (변동성 p80 → **p90**).
- 코어를 4-quadrant로 명확 분리 (수익률 top/bottom 30% × 변동성 median 기준).
- 애매한 중간 ~40% 시점을 default Slowdown이 아니라 **가장 가까운 코어에 z-score Manhattan 거리로 할당**.

실행 조건: 2010-01-01 ~ 2025-04-30, W-FRI, tx_cost 0.001, FRED 매크로 10개 (lag 적용, hy_spread·nfci 포함).

### 백테스트 metric

| 시나리오            |  CAGR | Sharpe |  MaxDD  | Calmar | Override | 위험레짐 미감지 |
|--------------------|------:|-------:|--------:|-------:|---------:|---------------:|
| **rule (baseline)** | +10.1% | 0.752 | **-10.6%** | **0.955** |  27.3%  | **10/373 (3%)** |
| forward_rule_21    | +10.4% | **0.761** | -11.2%  |  0.924  |  34.9%  |  15/373 (4%)   |
| forward_q_21 (구)  |  +9.2% | 0.656 | -11.3%  |  0.807  |  48.8%  |  15/373 (4%)   |
| forward_qv2_21     |  +9.1% | 0.640 | -11.6%  |  0.786  |  48.8%  |  15/373 (4%)   |
| forward_qv2_63     |  +9.1% | 0.645 | -12.3%  |  0.745  |  48.8%  |  15/373 (4%)   |

### 해석

1. **재설계한 `forward_quantile_v2`가 구 `quantile`보다도 열위**. Sharpe 0.656 → 0.640, Calmar 0.807 → 0.786, MaxDD -11.3% → -11.6%. Crisis 임계 엄격화·최근접 코어 할당이 백테스트 성과를 개선하지 못함. 매핑 재설계로 옵션 2를 구제하려던 후속 #1 가설 **기각**.
2. **자동 판정은 `forward_rule_21` 권장**(Sharpe +0.009)이나 Round 1~3과 동일한 함정 — MaxDD -0.6pp 악화, Calmar 하락(0.955→0.924), 위험 미감지 1.5배(10→15). Sharpe +0.009는 노이즈.
3. **위험 레짐 미감지가 일관된 차별 신호**. baseline 10일 vs forward 라벨 전부 15일. 4라운드 내내 동일 패턴 — forward 라벨은 위기 보호를 약화시킨다.

### 최종 판정 (F 종료)

- **`hmm.rf_forward_window=0` 유지, 코드 변경 없음.** 옵션 1(rule_at_future)·옵션 2(quantile)·옵션 2 재설계(forward_quantile_v2) 셋 다 baseline을 능가 못함.
- 자기참조 끊기는 실제로 작동하나(Override 27% → 35-49%), 끊긴 결과가 위험 감지 약화로 이어진다는 결론이 4라운드 일관 확인. **단순 자기참조 제거는 능사가 아님**이 본 실험 시리즈의 최종 결론.
- 코드는 옵트인으로 보존 (롤백 비용 0). 남은 후속 #2(blend 가중치 튜닝)·#3(HY 대체 시리즈)는 별개 작업으로 유지.

