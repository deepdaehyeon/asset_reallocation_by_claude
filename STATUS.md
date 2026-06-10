# 트레이딩 상태 스냅샷

> 생성: **2026-06-10 09:45 KST** · 마지막 실행: 2026-06-10 09:30

> ⚠️ 자동 생성 파일. 수동 편집 금지 (`scripts/snapshot_state.py`).

## 레짐

| 항목 | 값 |
|---|---|
| 확정 레짐 | **Goldilocks** |
| 마지막 전환일 | 2026-05-12 |
| 신뢰도 | 22.62% |
| HMM 매핑 | unsupervised (실행 20회, legacy 폴백 0회) |

**blend 확률 분포**

| 레짐 | 확률 |
|---|---|
| Slowdown | 40.3% |
| Goldilocks | 39.7% |
| Stagflation | 18.7% |
| Reflation | 0.9% |
| Crisis | 0.5% |

## 자산

| 항목 | 값 |
|---|---|
| 총자산 | ₩216,663,506 |
| 원금 | ₩216,182,793 |
| 누적 손익 | ₩480,713 (+0.22%) |
| 고점 | ₩225,885,767 |
| 드로우다운 | -4.08% |
| 이번 달 회전액 | ₩227,219,723 (2026-06) |
| USD/KRW | 1,515.9 (2026-05-22 23:00) |

## 리밸런싱 트리거

| 계좌 | drift | 트리거 | 사유 | 마지막 리밸 |
|---|---|---|---|---|
| KRW | 4.84% | 🔔 | deferred_buys | 2026-06-09 10:05 |
| USD | 24.00% | 🔔 | deferred_buys | 2026-06-09 23:30 |

**지연 매수 1건 대기 중**

- VWO 2,655,188원 (USD)

## 목표 비중 (블렌딩 결과)

| 자산군 | 비중 |
|---|---|
| equity_etf | 23.9% |
| equity_individual | 13.6% |
| gold | 13.1% |
| cash | 9.2% |
| commodity | 7.5% |
| bond_krw | 6.1% |
| bond_usd | 5.8% |
| equity_factor | 5.6% |
| managed_futures | 5.4% |
| bond_tips | 4.4% |
| equity_developed | 2.4% |
| equity_emerging | 2.0% |
| equity_sector | 1.0% |

## 매크로 피처 (마지막 실행)

| 지표 | 값 |
|---|---|
| 모멘텀 1M | -0.1% |
| 모멘텀 3M | 9.3% |
| 실현변동성 | 13.6% |
| VIX | 19.87 |
| VIX 기간구조 | 2.27 |
| 크레딧 신호 | 0.01 |
| HY 스프레드 | 1.52 |
| HY z-score | -1.01 |
| 10Y-2Y 커브 | 0.40 |
| CPI YoY | 3.95 |
| CPI MoM z | 2.21 |
| 실업률 3M 변화 | -0.10 |
| BEI 5Y | 2.44 |
| M2 YoY | 4.72 |
| Fed BS YoY | 0.58 |
| NFCI | -0.49 |
| DXY 1M | 0.02 |
| 원자재 1M | -0.05 |

## 핵심 설정값
> 전체 설정은 `trading/config.yaml` 참조.

| 설정 | 값 |
|---|---|
| drift 임계 | 1.5% |
| 리밸 쿨다운 | 0일 |
| 실행/월간 회전율 상한 | 0 / 0 (0=무제한) |
| 레짐 타이밍 소스 | rule |
| confirmation / cooldown | 1회 / 0일 |
| blend 평활 α | 0.5 |
| 신뢰도 산식 / 임계 | min / 0.2 |
| HMM 안정화 / deadband | True / 0.3 |
| HMM override / crisis 우선 | 0.5 / 0.4 |
| vol target (기본) / floor | 0.1 / 0.5 |
| 레짐별 target vol | Goldilocks 0.13, Reflation 0.11, Slowdown 0.09, Stagflation 0.08, Crisis 0.06 |

