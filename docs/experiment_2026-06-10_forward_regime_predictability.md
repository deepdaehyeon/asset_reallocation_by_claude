# 실험: 미래 레짐(forward-return) 예측 가능성 — 엄격 walk-forward 프로토타입

> **요약**: ① 오라클 라벨(H=21일 최고 forward 수익 레짐)을 walk-forward RF로 예측하고, 그 예측으로 거래했을 때의 성과를 baseline과 비교했다. ② OOS 예측력이 MCC=0.038(≈랜덤)으로 features에 미래 레짐 정보가 거의 없으며, 예측으로 거래 시 MaxDD -9.4%→-16.7%로 거의 2배 악화됐다. ③ 세 번째 독립 확인으로 "forward 신호화 기각" 결론 — 천장 갭은 실현 불가능한 노이즈이며, 개선은 forward 예측이 아닌 현재 분류 정확도와 진입 적시성 쪽에 남는다.

- 날짜: 2026-06-10
- 코드: `scripts/prototype_forward_regime_predictability.py`
- 질문: 오라클 천장([[experiment_2026-06-10_oracle_regime_ceiling]])의 알파가 "현재 분류"
  대신 **"미래 레짐 예측"**으로 실현 가능한가, 아니면 과적합인가?
- 결론: **실현 불가능. 오라클 라벨의 OOS 예측력이 사실상 0(MCC 0.04)이고, 그 예측으로
  매매하면 baseline 대비 Sharpe 0.84→0.58·MaxDD -9.4→-16.7%로 강하게 열위.
  천장 갭은 더 나은 예측기로 못 닫는 노이즈다.**

## 설계 (누수 차단이 핵심)

- ground-truth 라벨 `y_t` = argmax_regime (해당 레짐 타겟 포트폴리오의 t→t+H forward 수익률)
  = 오라클 라벨. look-ahead라 t+H 이후에야 확정. H=21(오라클 sweet spot).
- 피처 `X_t` = `compute_feature_matrix` — 전부 trailing·causal, FRED는 publication-lag 적용.
- **walk-forward**: 매월 RandomForest 재학습. 학습셋은 **라벨이 완전히 실현된 표본만**
  (s+H < 그 달 시작). 그 달의 각 영업일을 OOS 예측. 미래 정보 일절 미사용.
- RF: n_estimators=300, max_depth=6, min_samples_leaf=20, class_weight=balanced.

## [1] OOS 분류 스킬 — 오라클 라벨(H=21) 예측 (평가 2789일)

| 예측기 | 정확도 | Macro-F1 | MCC |
|---|---|---|---|
| **forward-RF (OOS)** | 30.7% | 0.211 | **0.038** |
| 현재 룰 분류 | 36.0% | 0.199 | 0.022 |
| majority(최빈=Goldilocks) | 53.6% | 0.140 | 0.000 |

- forward-RF ↔ 현재룰 일치율: 28.4%
- 오라클 라벨 분포: Goldilocks 54%, Crisis 16%, Reflation 13%, Stagflation 12%, Slowdown 6%

## [2] 실현 가능 백테스트 — OOS forward 예측을 레짐으로 매매

| 전략 | CAGR | Sharpe | MaxDD | Calmar | 리밸 | tx누적 | COVID | Bear22 |
|---|---|---|---|---|---|---|---|---|
| **baseline(현행)** | 10.4% | 0.84 | -9.4% | 1.10 | 542 | 4.04% | -9.4% | -8.5% |
| forward-RF(OOS) | 9.2% | 0.58 | **-16.7%** | 0.55 | 500 | 5.88% | **-16.7%** | -8.2% |

## 해석 — 깔끔한 음성 결과

1. **오라클 라벨은 OOS로 예측 불가.** forward-RF의 MCC=0.038 ≈ 0(랜덤). 정확도 30.7%는
   최빈 추측(53.6%)보다도 낮다. 즉 "다음 H일 어느 레짐 타겟이 최적인가"에는 **현재 피처가
   담은 예측 정보가 거의 없다.** 모델 문제가 아니라 데이터에 신호가 없다.
2. **과적합 질문에 대한 답: 그렇다.** MCC가 0 근처라 더 크고 복잡한 모델을 얹으면 in-sample만
   좋아지고 OOS는 여전히 ~0이다. 예측력의 천장 자체가 0에 가까우므로 모델 추가/교체로 못 넘는다.
3. **매매하면 오히려 위험.** OOS 예측으로 거래 시 MaxDD/COVID가 -9.4→-16.7%로 거의 2배.
   부정확한 forward 예측이 폭락 직전 risk-on으로 휘둘려(one-hot·평활 없음) 방어가 무너진다.
   라이브의 다층 안전망(blend 평활·vol targeting·히스테리시스)이 없으면 forward 신호는 독.
4. **오라클 천장의 정체.** 오라클은 라벨의 54%가 Goldilocks(강세장 다수). 천장 알파는
   "대부분 공격적으로 가되 16% Crisis-best 주간에 정확히 방어"에서 나오는데, **어느 주간인지가
   바로 예측 불가능한 부분**(MCC 0). 따라서 갭은 실현 가능한 알파가 아니라 노이즈.

## 종합 — 세 번째 독립 확인

forward 신호화는 이제 세 경로에서 독립적으로 기각:
- rf_forward_window 4라운드([[experiment_2026-05-27_rf_forward_label]]) — 위기감지 약화.
- use_forward_hmm([[experiment_2026-05-27_transition_response]]) — 효과 미미.
- 본 실험 — 오라클 라벨 OOS 예측력 ≈ 0, 매매 시 MaxDD 2배.

방향 결론: **"미래 레짐 예측"으로 천장에 다가가는 길은 막혀 있다.** 직관은 옳았으나(천장은
미래에서 나옴) 그 미래는 이 라벨·피처로는 예측 불가. 개선은 forward 예측이 아니라 현재 분류
정확도·진입 적시성([[feedback-agility-over-turnover-reduction]]) 쪽에 남는다.

## 범위·한계

- 라벨 정의 1종(argmax 오라클 레짐, H=21) + 모델 1종(RF)을 테스트. 단 이는 천장을 만든
  오라클의 가장 직접적인 실현 버전이라 그 알파에 대한 결정적 검증으로 충분.
- 다른 라벨(forward 수익률 회귀, 변동성 버킷)·다른 모델로 미세 차이는 가능하나, MCC가 0
  근처라 질적 반전 가능성은 낮음. 추가 탐색은 EV 낮음.
