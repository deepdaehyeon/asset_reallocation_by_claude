# 트레이딩 상태 스냅샷

> 생성: **2026-09-11 09:45 KST** · 마지막 실행: 2026-09-11 03:02

> ⚠️ 자동 생성 파일. 수동 편집 금지 (`scripts/snapshot_state.py`).

## 레짐

| 항목 | 값 |
|---|---|
| 확정 레짐 | **Goldilocks** |
| 마지막 전환일 | 2026-08-03 |
| 신뢰도 | 19.90% |
| HMM 매핑 | unsupervised (실행 105회, legacy 폴백 0회) |

**blend 확률 분포**

| 레짐 | 확률 |
|---|---|
| Goldilocks | 65.7% |
| Reflation | 25.4% |
| Slowdown | 7.0% |
| Stagflation | 1.9% |

## 자산

| 항목 | 값 |
|---|---|
| 총자산 | ₩227,845,464 |
| 원금 | ₩236,684,270 |
| 누적 손익 | ₩-8,838,806 (-3.73%) |
| 고점 | ₩234,981,644 |
| 드로우다운 | -3.04% |
| S&P500였다면 | ₩225,685,235 |
| **알파(vs S&P500)** | **₩2,160,228 (+0.96%)** |
| 이번 달 회전액 | ₩69,695,767 (2026-09) |
| USD/KRW | 1,416.5 (2026-08-17 10:00) |

## 리밸런싱 트리거

| 계좌 | drift | 트리거 | 사유 | 마지막 리밸 |
|---|---|---|---|---|
| KRW | 3.43% | ⚪ | no_trigger(drift=3.4%) | 2026-09-10 10:01 |
| USD | 3.43% | ⚪ | no_trigger(drift=3.4%) | 2026-09-09 03:02 |

지연 매수: 없음

## 목표 비중 (블렌딩 결과)

| 자산군 | 비중 |
|---|---|
| equity_etf | 50.9% |
| equity_factor | 7.8% |
| commodity | 7.5% |
| gold | 6.9% |
| managed_futures | 6.8% |
| cash | 6.1% |
| equity_developed | 4.0% |
| equity_sector | 3.8% |
| bond_krw | 2.2% |
| equity_emerging | 2.0% |
| bond_tips | 2.0% |

## 매크로 피처 (마지막 실행)

| 지표 | 값 |
|---|---|
| 모멘텀 1M | -0.7% |
| 모멘텀 3M | 3.1% |
| 실현변동성 | 10.2% |
| VIX | 15.90 |
| VIX 기간구조 | -2.34 |
| 크레딧 신호 | 0.00 |
| CPI YoY | 3.54 |
| CPI MoM z | -0.86 |
| 실업률 3M 변화 | -0.20 |
| Fed BS YoY | 2.05 |
| NFCI | -0.56 |
| DXY 1M | -0.01 |
| 원자재 1M | 0.12 |

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

