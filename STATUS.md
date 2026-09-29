# 트레이딩 상태 스냅샷

> 생성: **2026-09-29 09:45 KST** · 마지막 실행: 2026-09-29 03:02

> ⚠️ 자동 생성 파일. 수동 편집 금지 (`scripts/snapshot_state.py`).

## 레짐

| 항목 | 값 |
|---|---|
| 확정 레짐 | **Goldilocks** |
| 마지막 전환일 | 2026-08-03 |
| 신뢰도 | 46.66% |
| HMM 매핑 | unsupervised (실행 120회, legacy 폴백 0회) |

**blend 확률 분포**

| 레짐 | 확률 |
|---|---|
| Goldilocks | 68.9% |
| Reflation | 16.7% |
| Stagflation | 11.6% |
| Slowdown | 2.9% |

## 자산

| 항목 | 값 |
|---|---|
| 총자산 | ₩231,045,441 |
| 원금 | ₩237,155,849 |
| 누적 손익 | ₩-6,110,408 (-2.58%) |
| 고점 | ₩239,236,409 |
| 드로우다운 | -3.42% |
| S&P500였다면 | ₩234,866,068 |
| **알파(vs S&P500)** | **₩-3,820,628 (-1.63%)** |
| 이번 달 회전액 | ₩154,698,222 (2026-09) |
| USD/KRW | 1,416.5 (2026-08-17 10:00) |

## 리밸런싱 트리거

| 계좌 | drift | 트리거 | 사유 | 마지막 리밸 |
|---|---|---|---|---|
| KRW | 4.15% | ⚪ | no_trigger(drift=4.2%) | 2026-09-28 10:01 |
| USD | 4.15% | ⚪ | no_trigger(drift=4.2%) | 2026-09-26 03:03 |

지연 매수: 없음

## 목표 비중 (블렌딩 결과)

| 자산군 | 비중 |
|---|---|
| equity_etf | 50.3% |
| equity_factor | 7.9% |
| gold | 7.4% |
| commodity | 6.8% |
| cash | 6.6% |
| managed_futures | 6.4% |
| equity_developed | 4.1% |
| equity_sector | 3.8% |
| bond_krw | 2.5% |
| bond_tips | 2.2% |
| equity_emerging | 2.0% |

## 매크로 피처 (마지막 실행)

| 지표 | 값 |
|---|---|
| 모멘텀 1M | 0.9% |
| 모멘텀 3M | 3.0% |
| 실현변동성 | 10.7% |
| VIX | 15.18 |
| VIX 기간구조 | -1.80 |
| 크레딧 신호 | 0.01 |
| HY 스프레드 | 1.39 |
| HY z-score | -1.87 |
| 10Y-2Y 커브 | 0.36 |
| CPI YoY | 3.71 |
| CPI MoM z | 0.66 |
| 실업률 3M 변화 | -0.20 |
| BEI 5Y | 2.34 |
| M2 YoY | 5.66 |
| Fed BS YoY | 2.11 |
| NFCI | -0.56 |
| DXY 1M | 0.02 |
| 원자재 1M | 0.05 |

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

