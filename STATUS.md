# 트레이딩 상태 스냅샷

> 생성: **2026-06-22 10:00 KST** · 마지막 실행: 2026-06-22 05:00

> ⚠️ 자동 생성 파일. 수동 편집 금지 (`scripts/snapshot_state.py`).

## 레짐

| 항목 | 값 |
|---|---|
| 확정 레짐 | **Goldilocks** |
| 마지막 전환일 | 2026-05-12 |
| 신뢰도 | 29.44% |
| HMM 매핑 | unsupervised (실행 38회, legacy 폴백 0회) |

**blend 확률 분포**

| 레짐 | 확률 |
|---|---|
| Goldilocks | 54.1% |
| Stagflation | 30.5% |
| Slowdown | 14.5% |
| Crisis | 0.7% |
| Reflation | 0.2% |

## 자산

| 항목 | 값 |
|---|---|
| 총자산 | ₩223,092,721 |
| 원금 | ₩222,685,324 |
| 누적 손익 | ₩407,397 (+0.18%) |
| 고점 | ₩224,657,863 |
| 드로우다운 | -0.70% |
| 이번 달 회전액 | ₩136,954,026 (2026-06) |
| USD/KRW | 1,519.2 (2026-06-12 12:35) |

## 리밸런싱 트리거

| 계좌 | drift | 트리거 | 사유 | 마지막 리밸 |
|---|---|---|---|---|
| KRW | 2.38% | ⚪ | no_trigger(drift=2.4%) | 2026-06-19 10:22 |
| USD | 2.38% | ⚪ | no_trigger(drift=2.4%) | 2026-06-18 23:30 |

지연 매수: 없음

## 목표 비중 (블렌딩 결과)

| 자산군 | 비중 |
|---|---|
| equity_etf | 26.5% |
| equity_individual | 14.8% |
| gold | 10.9% |
| bond_krw | 8.8% |
| cash | 8.3% |
| equity_factor | 7.6% |
| managed_futures | 5.6% |
| commodity | 4.6% |
| bond_tips | 4.1% |
| equity_developed | 3.6% |
| equity_sector | 3.2% |
| equity_emerging | 2.0% |

## 매크로 피처 (마지막 실행)

| 지표 | 값 |
|---|---|
| 모멘텀 1M | 0.8% |
| 모멘텀 3M | 15.4% |
| 실현변동성 | 16.2% |
| VIX | 17.03 |
| VIX 기간구조 | -0.79 |
| 크레딧 신호 | -0.02 |
| HY 스프레드 | 1.50 |
| HY z-score | -1.14 |
| 10Y-2Y 커브 | 0.27 |
| CPI YoY | 4.27 |
| CPI MoM z | 1.17 |
| 실업률 3M 변화 | -0.10 |
| BEI 5Y | 2.27 |
| M2 YoY | 4.72 |
| Fed BS YoY | 0.83 |
| NFCI | -0.51 |
| DXY 1M | 0.01 |
| 원자재 1M | -0.11 |

## 핵심 설정값
> 전체 설정은 `trading/config.yaml` 참조.

| 설정 | 값 |
|---|---|
| drift 임계 | 5.0% |
| 리밸 쿨다운 | 0일 |
| 실행/월간 회전율 상한 | 0 / 0 (0=무제한) |
| 레짐 타이밍 소스 | rule |
| confirmation / cooldown | 1회 / 0일 |
| blend 평활 α | 0.5 |
| 신뢰도 산식 / 임계 | min / 0.2 |
| HMM 안정화 / deadband | True / 0.3 |
| HMM override / crisis 우선 | 0.5 / 0.4 |
| vol target (기본) / floor | 0.1 / 0.65 |
| 레짐별 target vol | Goldilocks 0.1625, Reflation 0.1375, Slowdown 0.1125, Stagflation 0.1, Crisis 0.075 |

