# 실험: vol targeting 목표변동성 — 룰단계(현행) vs 확률블렌드 (2026-06-17)

## 요약 (3문장)
1. **무엇을** — vol targeting의 목표변동성을 확정 레짐 하나로 고르는 현행 대신, 비중처럼 blend 확률로 가중평균(`target_vol = Σ p[r]·vol[r]`, 연속 단계)하는 새 토글(`vol_targeting.blend_target_vol`, 기본 OFF)을 구현하고 백테스트 A/B했다. core30·평활 ON·drift·tx·2010~2025, 라이브 미적용.
2. **핵심 수치** — 고정 4지표 **사실상 중립**: Martin 2.42=2.42(동일), Ulcer 3.66→3.60·회복 84→82일(미세 개선), 최장UW 556→586일(미세 악화), 롤링CAGR·MaxDD·위기방어(COVID -15.4·Bear22 -13.1) 거의 불변. 단 **tx 2.88→2.52%로 회전 감소**(연속 목표가 단계 점프를 없앰).
3. **결론** — 우려했던 "룰 빠른진입 손실"이 **위기 구간(COVID·Bear22)에서 나타나지 않았고** 4지표도 안 해쳤다. 백테스트 이득은 작지만 회전이 줄고 방어 손상이 없어 **채택 가능한 옵션** — 다만 본 효과(연속 vol 단계 → 라이브 일별 출렁임↓)는 매일 재계산하는 라이브에서 더 큼. 사용자 확인 후 라이브 적용 결정(현재 토글 OFF 유지).

## 배경·동기
- 현행 `apply_vol_targeting`은 `target_vol = regime_vols[regime]`로 **확정 레짐 하나**(regime_timing_source=rule → 룰)를 골라 목표변동성을 단계 선택한다(`portfolio.py:198`). 레짐이 바뀔 때 목표가 계단식으로 점프.
- 사용자 제안: 비중 블렌딩과 동일 철학으로 목표변동성도 blend 확률 가중평균 → **연속적**으로 부드럽게.

## 트레이드오프(시작 전 합의)
1. **룰 빠른진입 손실 우려** — 룰은 앙상블보다 위험진입이 3~5일 빠른 게 서브기간 6/6 검증([[feedback-regime-timing-lever]]). 블렌드는 더 느릴 수 있음. → **결과적으로 위기 MaxDD에 악영향 없음**(COVID -15.4 vs -15.6, Bear22 -13.1 vs -13.2).
2. **HMM 흔들림 유입** — 블렌드 흔들림이 vol 강도에 반영(5일 평활로 완화). 라이브 블렌드 평균이 더 방어적(0.13→~0.11)이라 상시 축소↑ 우려. → tx는 오히려 감소(연속 목표가 계단 점프를 제거).

## 구현
- `trading/portfolio.py` `apply_vol_targeting(..., blend_probs=None)` 추가. `vol_cfg["blend_target_vol"]`이고 blend_probs 있으면 `target_vol = Σ p[r]·regime_vols[r] / Σ p[r]`(regime_vols에 있는 레짐만, 질량 정규화). 없으면 현행 단계 선택.
- `backtest/engine.py:350` 호출에 `blend_probs=blend_probs` 전달. **라이브 `run.py`는 미배선**(토글 기본 OFF·fails-closed: blend_probs=None이면 자동으로 단계 선택 폴백 → 라이브 안전). 라이브 채택 결정 시 run.py 배선 추가.

## 결과 (core30·평활 ON·drift·tx·USD단일·2010~2025)
| 전략 | 롤3y최악 | 롤3y중앙 | 롤5y최악 | Ulcer | 회복일 | 최장UW | Martin | CAGR | MaxDD | tx |
|---|---|---|---|---|---|---|---|---|---|---|
| OFF 룰단계 (현행) | 6.0% | 12.1% | 6.2% | 3.66 | 84 | 556 | 2.42 | 12.8% | -15.6% | 2.88% |
| **ON 확률블렌드** | 6.0% | 12.0% | 6.3% | **3.60** | **82** | 586 | 2.42 | 12.7% | -15.4% | **2.52%** |

## 해석·후속
- **4지표 중립 + 회전 감소 + 위기방어 무손상**. 우려한 빠른진입 손실은 위기 MaxDD에 안 나타남(블렌드도 위기 확률을 빠르게 키움 + crisis_priority가 별도 가속).
- 백테스트는 리밸일만 재계산이라 회전 감소가 과소반영([[project-live-turnover-vs-backtest-gap]]) — 라이브(매일 계산)에선 연속 목표가 일별 vol 강도 출렁임을 더 크게 줄일 것.
- 재현 `scripts/voltarget_blend_ab.py`. 라이브 적용은 사용자 확인 후(토글 ON + run.py 배선). 현재 보류·OFF 유지.
