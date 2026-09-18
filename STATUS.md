# 트레이딩 상태 스냅샷

> 생성: **2026-09-18 10:00 KST** · 마지막 실행: 2026-09-18 03:02

> ⚠️ 자동 생성 파일. 수동 편집 금지 (`scripts/snapshot_state.py`).

## 레짐

| 항목 | 값 |
|---|---|
| 확정 레짐 | **Goldilocks** |
| 마지막 전환일 | 2026-08-03 |
| 신뢰도 | 20.21% |
| HMM 매핑 | unsupervised (실행 111회, legacy 폴백 0회) |

**blend 확률 분포**

| 레짐 | 확률 |
|---|---|
| Goldilocks | 87.6% |
| Slowdown | 6.5% |
| Reflation | 3.4% |
| Stagflation | 2.5% |

## 자산

| 항목 | 값 |
|---|---|
| 총자산 | ₩229,887,209 |
| 원금 | ₩236,168,005 |
| 누적 손익 | ₩-6,280,796 (-2.66%) |
| 고점 | ₩236,744,255 |
| 드로우다운 | -2.90% |
| S&P500였다면 | ₩234,640,056 |
| **알파(vs S&P500)** | **₩-4,752,847 (-2.03%)** |
| 이번 달 회전액 | ₩85,112,720 (2026-09) |
| USD/KRW | 1,416.5 (2026-08-17 10:00) |

## 리밸런싱 트리거

| 계좌 | drift | 트리거 | 사유 | 마지막 리밸 |
|---|---|---|---|---|
| KRW | 1.36% | ⚪ | no_trigger(drift=1.4%) | 2026-09-17 10:00 |
| USD | 1.36% | ⚪ | no_trigger(drift=1.4%) | 2026-09-17 03:03 |

지연 매수: 없음

## 목표 비중 (블렌딩 결과)

| 자산군 | 비중 |
|---|---|
| equity_etf | 57.3% |
| equity_factor | 7.8% |
| gold | 6.3% |
| cash | 5.5% |
| managed_futures | 5.3% |
| commodity | 5.1% |
| equity_developed | 4.6% |
| equity_sector | 3.0% |
| bond_krw | 2.2% |
| equity_emerging | 2.0% |
| bond_tips | 0.9% |

## 매크로 피처 (마지막 실행)

| 지표 | 값 |
|---|---|
| 모멘텀 1M | -1.8% |
| 모멘텀 3M | 2.1% |
| 실현변동성 | 10.6% |
| VIX | 16.69 |
| VIX 기간구조 | -1.70 |
| 크레딧 신호 | 0.00 |
| HY 스프레드 | 1.48 |
| HY z-score | -1.24 |
| 10Y-2Y 커브 | 0.27 |
| CPI YoY | 3.71 |
| CPI MoM z | 0.66 |
| 실업률 3M 변화 | -0.20 |
| M2 YoY | 5.41 |
| Fed BS YoY | 2.04 |
| NFCI | -0.56 |
| DXY 1M | -0.00 |
| 원자재 1M | 0.09 |

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

