# 트레이딩 상태 스냅샷

> 생성: **2026-07-21 03:01 KST** · 마지막 실행: 2026-07-21 03:01

> ⚠️ 자동 생성 파일. 수동 편집 금지 (`scripts/snapshot_state.py`).

## 레짐

| 항목 | 값 |
|---|---|
| 확정 레짐 | **Goldilocks** |
| 마지막 전환일 | 2026-05-12 |
| 신뢰도 | 42.02% |
| HMM 매핑 | unsupervised (실행 63회, legacy 폴백 0회) |

**blend 확률 분포**

| 레짐 | 확률 |
|---|---|
| Goldilocks | 85.2% |
| Slowdown | 14.3% |
| Reflation | 0.4% |
| Stagflation | 0.1% |

## 자산

| 항목 | 값 |
|---|---|
| 총자산 | ₩214,326,246 |
| 원금 | ₩218,470,306 |
| 누적 손익 | ₩-4,144,061 (-1.90%) |
| 고점 | ₩224,657,863 |
| 드로우다운 | -4.60% |
| S&P500였다면 | ₩213,593,093 |
| **알파(vs S&P500)** | **₩733,153 (+0.34%)** |
| 이번 달 회전액 | ₩204,095,226 (2026-07) |
| USD/KRW | 1,526.3 (2026-07-07 10:42) |

## 리밸런싱 트리거

| 계좌 | drift | 트리거 | 사유 | 마지막 리밸 |
|---|---|---|---|---|
| KRW | 4.46% | ⚪ | no_trigger(drift=4.5%) | 2026-07-20 10:02 |
| USD | 4.46% | ⚪ | no_trigger(drift=4.5%) | 2026-07-17 03:02 |

지연 매수: 없음

## 목표 비중 (블렌딩 결과)

| 자산군 | 비중 |
|---|---|
| equity_etf | 56.5% |
| equity_factor | 7.6% |
| gold | 6.9% |
| cash | 5.5% |
| managed_futures | 5.0% |
| equity_developed | 4.6% |
| commodity | 4.3% |
| bond_krw | 4.0% |
| equity_sector | 2.6% |
| equity_emerging | 2.0% |
| bond_tips | 1.0% |

## 매크로 피처 (마지막 실행)

| 지표 | 값 |
|---|---|
| 모멘텀 1M | 1.2% |
| 모멘텀 3M | 5.4% |
| 실현변동성 | 12.6% |
| VIX | 17.19 |
| VIX 기간구조 | -2.46 |
| 크레딧 신호 | 0.02 |
| 10Y-2Y 커브 | 0.37 |
| CPI YoY | 3.73 |
| CPI MoM z | -3.29 |
| 실업률 3M 변화 | -0.10 |
| BEI 5Y | 2.27 |
| M2 YoY | 5.58 |
| Fed BS YoY | 1.26 |
| NFCI | -0.54 |
| DXY 1M | 0.01 |
| 원자재 1M | 0.02 |

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

