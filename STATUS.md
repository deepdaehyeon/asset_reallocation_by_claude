# 트레이딩 상태 스냅샷

> 생성: **2026-07-01 10:00 KST** · 마지막 실행: 2026-07-01 04:00

> ⚠️ 자동 생성 파일. 수동 편집 금지 (`scripts/snapshot_state.py`).

## 레짐

| 항목 | 값 |
|---|---|
| 확정 레짐 | **Goldilocks** |
| 마지막 전환일 | 2026-05-12 |
| 신뢰도 | 13.76% |
| HMM 매핑 | unsupervised (실행 46회, legacy 폴백 0회) |

**blend 확률 분포**

| 레짐 | 확률 |
|---|---|
| Slowdown | 55.3% |
| Goldilocks | 24.4% |
| Reflation | 14.7% |
| Stagflation | 5.3% |
| Crisis | 0.3% |

## 자산

| 항목 | 값 |
|---|---|
| 총자산 | ₩223,559,390 |
| 원금 | ₩222,873,711 |
| 누적 손익 | ₩685,678 (+0.31%) |
| 고점 | ₩224,657,863 |
| 드로우다운 | -0.49% |
| 이번 달 회전액 | ₩210,838,307 (2026-06) |
| USD/KRW | 1,530.9 (2026-06-24 07:08) |

## 리밸런싱 트리거

| 계좌 | drift | 트리거 | 사유 | 마지막 리밸 |
|---|---|---|---|---|
| KRW | 2.70% | ⚪ | no_trigger(drift=2.7%) | 2026-06-30 10:04 |
| USD | 2.70% | ⚪ | no_trigger(drift=2.7%) | 2026-06-30 04:00 |

지연 매수: 없음

## 목표 비중 (블렌딩 결과)

| 자산군 | 비중 |
|---|---|
| equity_etf | 23.3% |
| bond_krw | 16.3% |
| gold | 13.3% |
| equity_individual | 11.1% |
| cash | 7.6% |
| equity_factor | 6.3% |
| managed_futures | 6.1% |
| bond_tips | 5.2% |
| commodity | 3.9% |
| equity_developed | 2.7% |
| equity_sector | 2.0% |
| equity_emerging | 2.0% |

## 매크로 피처 (마지막 실행)

| 지표 | 값 |
|---|---|
| 모멘텀 1M | -1.3% |
| 모멘텀 3M | 15.2% |
| 실현변동성 | 15.9% |
| VIX | 18.04 |
| VIX 기간구조 | -1.18 |
| 크레딧 신호 | -0.03 |
| HY 스프레드 | 1.56 |
| HY z-score | -0.72 |
| 10Y-2Y 커브 | 0.28 |
| CPI YoY | 4.27 |
| CPI MoM z | 1.17 |
| 실업률 3M 변화 | -0.10 |
| BEI 5Y | 2.24 |
| M2 YoY | 5.58 |
| Fed BS YoY | 1.10 |
| NFCI | -0.52 |
| DXY 1M | 0.02 |
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

