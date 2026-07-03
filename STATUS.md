# 트레이딩 상태 스냅샷

> 생성: **2026-07-03 10:08 KST** · 마지막 실행: 2026-07-03 10:07

> ⚠️ 자동 생성 파일. 수동 편집 금지 (`scripts/snapshot_state.py`).

## 레짐

| 항목 | 값 |
|---|---|
| 확정 레짐 | **Goldilocks** |
| 마지막 전환일 | 2026-05-12 |
| 신뢰도 | 29.56% |
| HMM 매핑 | unsupervised (실행 48회, legacy 폴백 0회) |

**blend 확률 분포**

| 레짐 | 확률 |
|---|---|
| Goldilocks | 49.4% |
| Slowdown | 32.2% |
| Reflation | 16.0% |
| Stagflation | 2.2% |
| Crisis | 0.1% |

## 자산

| 항목 | 값 |
|---|---|
| 총자산 | ₩222,474,294 |
| 원금 | ₩221,972,654 |
| 누적 손익 | ₩501,640 (+0.23%) |
| 고점 | ₩224,657,863 |
| 드로우다운 | -0.53% |
| 이번 달 회전액 | ₩66,155,085 (2026-07) |
| USD/KRW | 1,530.9 (2026-06-24 07:08) |

## 리밸런싱 트리거

| 계좌 | drift | 트리거 | 사유 | 마지막 리밸 |
|---|---|---|---|---|
| KRW | 23.08% | ⚪ | — | 2026-07-03 10:07 |
| USD | 23.08% | ⚪ | — | 2026-07-03 04:01 |

지연 매수: 없음

## 목표 비중 (블렌딩 결과)

| 자산군 | 비중 |
|---|---|
| equity_etf | 31.7% |
| equity_individual | 12.4% |
| gold | 10.0% |
| bond_krw | 9.4% |
| equity_factor | 7.0% |
| cash | 6.7% |
| managed_futures | 6.2% |
| commodity | 5.2% |
| equity_developed | 3.5% |
| bond_tips | 3.3% |
| equity_sector | 2.7% |
| equity_emerging | 2.0% |

## 매크로 피처 (마지막 실행)

| 지표 | 값 |
|---|---|
| 모멘텀 1M | -1.5% |
| 모멘텀 3M | 13.6% |
| 실현변동성 | 14.9% |
| VIX | 17.13 |
| VIX 기간구조 | -1.29 |
| 크레딧 신호 | -0.01 |
| HY 스프레드 | 1.53 |
| HY z-score | -0.93 |
| 10Y-2Y 커브 | 0.31 |
| CPI YoY | 4.27 |
| CPI MoM z | 1.17 |
| 실업률 3M 변화 | -0.10 |
| BEI 5Y | 2.26 |
| M2 YoY | 5.58 |
| Fed BS YoY | 1.10 |
| NFCI | -0.50 |
| DXY 1M | 0.02 |
| 원자재 1M | -0.10 |

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

