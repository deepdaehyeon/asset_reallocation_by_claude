# 실험: HMM 입력 노이즈 피처 5일 평활 + fit 시드 고정 (2026-06-17)

## 요약 (3문장)
1. **무엇을** — HMM 매일 재학습이 라이브에서 레짐을 일별로 흔들어(회전율 ~300%/월) 발생하는 churn의 근원 두 가지를 제거했다: (a) 빠른 시장 노이즈 피처 6종(vix_term_structure·vix·credit_signal·momentum_1m·commodity_mom_1m·dxy_mom_1m)을 5일 rolling 평균으로 평활, (b) 다중 fit 시드[42,7,13]를 단일 시드[42]로 고정.
2. **핵심 수치** — 고정 4지표 백테스트(drift·tx·2010~2025) 결과 평활 ON은 OFF 대비 Martin 2.32→**2.42**(개선), Ulcer 3.68→**3.63**(개선), 롤3y최악 5.9→5.7%·롤5y최악 6.0→6.1%(보합), tx 2.97→2.85%. 4지표 손상 없음.
3. **결론** — 두 변경 모두 **라이브 config에 채택**. 백테스트는 리밸일에만 _get_regime을 재계산하므로 회전 감소 효과가 라이브보다 작게 잡힌다(이 표의 tx 감소는 하한선); 본 효과는 매일 재계산하는 라이브에서 발생한다. 백테스트의 역할은 "평활·시드고정이 4지표를 해치지 않는가" 확인이며 — 해치지 않음(오히려 Martin·Ulcer 소폭 개선).

## 배경 — churn의 근원 진단
- 라이브 run.py는 매일 레짐을 재계산 → 타깃 비중이 일별로 흔들림 → 거래 발생. 백테스트 _run_drift는 타깃을 고정하고 가격 drift에만 리밸 → churn 미재현(라이브 회전 ~10배 갭).
- HMM은 매 호출마다 rolling ~500일 윈도우로 처음부터 비지도 재학습(영구 가중치 없음). 일별 흔들림의 3대 원인:
  1. **빠른 노이즈 피처 지배** — 일별 변동성(std(daily diff)/std) 상위가 전부 빠른 시장 피처: vix_term_structure 0.61, vix 0.42, credit_signal 0.37, momentum_1m 0.36, commodity_mom_1m 0.35, dxy_mom_1m 0.29. 느린 거시(cpi·unrate 등)는 0.04~0.13. 레짐은 거시적으로 느려야 하는데 빠른 피처가 분산을 지배.
  2. **매일 재학습** — 윈도우가 하루씩 굴러가며 센트로이드가 미세 이동.
  3. **시드 승자 교체** — 3개 시드 중 '최고 점수' 모델을 고르는데, 전날과 다음날 승자 시드가 달라지면 센트로이드가 점프 → 앵커 매핑(state→regime)이 흔들림.

## 변경 내용
### (a) 노이즈 피처 5일 평활
- `trading/features.py`: `compute_features(..., smooth_window, smooth_features)` 및 `compute_feature_matrix(..., smooth_window, smooth_features)`에 평활 인자 추가. 지정 피처를 5일 rolling 평균으로 대체. 매트릭스 경로에서 해당 피처 일별 변동성 ~55~59% 감소 확인.
- `trading/config.yaml` `feature_smoothing:` 블록(enabled·window:5·features 6종) 신설.
- `backtest/engine.py`·`trading/run.py`에서 config를 읽어 두 함수에 전달.

### (b) fit 시드 고정
- `trading/regime.py`: `HmmRegimeClassifier(..., fit_seeds=None)` 추가. None이면 기존 [42,7,13], 지정 시 그 목록만 사용. fit 루프가 `self._fit_seeds`를 순회.
- `trading/config.yaml` `hmm.fit_seeds: [42]` — 단일 시드로 고정해 일별 재학습을 결정적으로 만들고 시드 승자 교체 churn을 제거.
- `backtest/engine.py`·`trading/run.py`에서 `fit_seeds`를 분류기에 전달.

## 백테스트 결과 (drift·tx·USD단일·2010~2025)
| 전략 | 롤3y최악 | 롤3y중앙 | 롤5y최악 | Ulcer | 회복일 | 최장UW | Martin | CAGR | MaxDD | tx |
|---|---|---|---|---|---|---|---|---|---|---|
| OFF 평활없음 | 5.9% | 11.9% | 6.0% | 3.68 | 82 | 564 | 2.32 | 12.5% | -15.4% | 2.97% |
| **ON 5일평활** | 5.7% | 11.9% | 6.1% | **3.63** | 84 | 592 | **2.42** | 12.8% | -15.7% | 2.85% |

- Martin(1차 판정)·Ulcer 소폭 개선, 롤링 CAGR 보합, tx 소폭 감소. 최장 underwater 564→592일은 소폭 악화이나 4지표 종합 판단 시 채택 무방.
- 재현: `scripts/feature_smoothing_ab.py`. 시드 고정 효과는 라이브 회전 모니터링으로 별도 추적.
