# 트레이딩 상태 스냅샷

> 생성: **2026-10-02 10:00 KST** · 마지막 실행: 2026-10-02 03:01

> ⚠️ 자동 생성 파일. 수동 편집 금지 (`scripts/snapshot_state.py`).

## 레짐

| 항목 | 값 |
|---|---|
| 확정 레짐 | **Goldilocks** |
| 마지막 전환일 | 2026-08-03 |
| 신뢰도 | 29.71% |
| HMM 매핑 | unsupervised (실행 123회, legacy 폴백 0회) |

**blend 확률 분포**

| 레짐 | 확률 |
|---|---|
| Goldilocks | 78.7% |
| Reflation | 15.4% |
| Slowdown | 3.6% |
| Stagflation | 2.2% |

## 자산

| 항목 | 값 |
|---|---|
| 총자산 | ₩231,541,809 |
| 원금 | ₩237,881,200 |
| 누적 손익 | ₩-6,339,391 (-2.66%) |
| 고점 | ₩239,717,027 |
| 드로우다운 | -3.41% |
| S&P500였다면 | ₩234,698,816 |
| **알파(vs S&P500)** | **₩-3,157,007 (-1.35%)** |
| 이번 달 회전액 | ₩12,256,485 (2026-10) |
| USD/KRW | 1,416.5 (2026-08-17 10:00) |

## 리밸런싱 트리거

| 계좌 | drift | 트리거 | 사유 | 마지막 리밸 |
|---|---|---|---|---|
| KRW | 1.37% | ⚪ | no_trigger(drift=1.4%) | 2026-10-01 10:00 |
| USD | 1.37% | ⚪ | no_trigger(drift=1.4%) | 2026-10-01 03:01 |

지연 매수: 없음

## 목표 비중 (블렌딩 결과)

| 자산군 | 비중 |
|---|---|
| equity_etf | 54.9% |
| equity_factor | 7.9% |
| commodity | 6.5% |
| gold | 6.2% |
| managed_futures | 6.1% |
| cash | 5.8% |
| equity_developed | 4.4% |
| equity_sector | 3.5% |
| equity_emerging | 2.0% |
| bond_krw | 1.4% |
| bond_tips | 1.3% |

## 매크로 피처 (마지막 실행)

| 지표 | 값 |
|---|---|
| 모멘텀 1M | -0.1% |
| 모멘텀 3M | 1.9% |
| 실현변동성 | 10.0% |
| VIX | 15.94 |
| VIX 기간구조 | -2.02 |
| 크레딧 신호 | 0.03 |
| HY 스프레드 | 1.46 |
| HY z-score | -1.34 |
| 10Y-2Y 커브 | 0.41 |
| CPI YoY | 3.71 |
| CPI MoM z | 0.66 |
| 실업률 3M 변화 | -0.20 |
| BEI 5Y | 2.36 |
| M2 YoY | 5.66 |
| Fed BS YoY | 2.11 |
| NFCI | -0.55 |
| DXY 1M | 0.02 |
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

