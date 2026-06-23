# 트레이딩 상태 스냅샷

> 생성: **2026-06-23 10:25 KST** · 마지막 실행: 2026-06-23 10:25

> ⚠️ 자동 생성 파일. 수동 편집 금지 (`scripts/snapshot_state.py`).

## 레짐

| 항목 | 값 |
|---|---|
| 확정 레짐 | **Goldilocks** |
| 마지막 전환일 | 2026-05-12 |
| 신뢰도 | 11.82% |
| HMM 매핑 | unsupervised (실행 39회, legacy 폴백 0회) |

**blend 확률 분포**

| 레짐 | 확률 |
|---|---|
| Goldilocks | 47.3% |
| Stagflation | 30.5% |
| Slowdown | 21.3% |
| Crisis | 0.7% |
| Reflation | 0.3% |

## 자산

| 항목 | 값 |
|---|---|
| 총자산 | ₩223,898,875 |
| 원금 | ₩223,104,813 |
| 누적 손익 | ₩794,062 (+0.36%) |
| 고점 | ₩224,657,863 |
| 드로우다운 | -0.25% |
| 이번 달 회전액 | ₩148,522,711 (2026-06) |
| USD/KRW | 1,519.2 (2026-06-12 12:35) |

## 리밸런싱 트리거

| 계좌 | drift | 트리거 | 사유 | 마지막 리밸 |
|---|---|---|---|---|
| KRW | 6.04% | ⚪ | — | 2026-06-23 10:25 |
| USD | 6.04% | ⚪ | — | 2026-06-23 05:00 |

지연 매수: 없음

## 목표 비중 (블렌딩 결과)

| 자산군 | 비중 |
|---|---|
| equity_etf | 24.7% |
| equity_individual | 14.1% |
| gold | 11.8% |
| bond_krw | 10.7% |
| cash | 8.5% |
| equity_factor | 7.3% |
| managed_futures | 5.6% |
| bond_tips | 4.6% |
| commodity | 4.3% |
| equity_developed | 3.4% |
| equity_sector | 3.0% |
| equity_emerging | 2.0% |

## 매크로 피처 (마지막 실행)

| 지표 | 값 |
|---|---|
| 모멘텀 1M | 1.1% |
| 모멘텀 3M | 13.9% |
| 실현변동성 | 15.8% |
| VIX | 17.00 |
| VIX 기간구조 | -0.88 |
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

