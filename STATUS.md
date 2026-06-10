# 트레이딩 상태 스냅샷

> 생성: **2026-06-10 23:30 KST** · 마지막 실행: 2026-06-10 23:30

> ⚠️ 자동 생성 파일. 수동 편집 금지 (`scripts/snapshot_state.py`).

## 레짐

| 항목 | 값 |
|---|---|
| 확정 레짐 | **Goldilocks** |
| 마지막 전환일 | 2026-05-12 |
| 신뢰도 | 20.42% |
| HMM 매핑 | unsupervised (실행 21회, legacy 폴백 0회) |

**blend 확률 분포**

| 레짐 | 확률 |
|---|---|
| Slowdown | 48.1% |
| Goldilocks | 36.7% |
| Stagflation | 13.9% |
| Reflation | 0.9% |
| Crisis | 0.5% |

## 자산

| 항목 | 값 |
|---|---|
| 총자산 | ₩216,977,186 |
| 원금 | ₩216,229,981 |
| 누적 손익 | ₩747,204 (+0.35%) |
| 고점 | ₩225,885,767 |
| 드로우다운 | -3.99% |
| 이번 달 회전액 | ₩247,851,212 (2026-06) |
| USD/KRW | 1,515.9 (2026-05-22 23:00) |

## 리밸런싱 트리거

| 계좌 | drift | 트리거 | 사유 | 마지막 리밸 |
|---|---|---|---|---|
| KRW | 7.93% | 🔔 | drift(7.9%) | 2026-06-10 10:10 |
| USD | 19.15% | ⚪ | — | 2026-06-10 23:30 |

지연 매수: 없음

## 목표 비중 (블렌딩 결과)

| 자산군 | 비중 |
|---|---|
| equity_etf | 23.6% |
| equity_individual | 13.4% |
| gold | 13.0% |
| cash | 8.9% |
| bond_krw | 7.3% |
| commodity | 6.9% |
| bond_usd | 6.5% |
| equity_factor | 5.4% |
| managed_futures | 5.3% |
| bond_tips | 4.6% |
| equity_developed | 2.4% |
| equity_emerging | 2.0% |
| equity_sector | 0.8% |

## 매크로 피처 (마지막 실행)

| 지표 | 값 |
|---|---|
| 모멘텀 1M | -0.5% |
| 모멘텀 3M | 10.8% |
| 실현변동성 | 13.2% |
| VIX | 20.22 |
| VIX 기간구조 | 2.76 |
| 크레딧 신호 | 0.00 |
| HY 스프레드 | 1.52 |
| HY z-score | -1.01 |
| 10Y-2Y 커브 | 0.40 |
| CPI YoY | 4.27 |
| CPI MoM z | 1.17 |
| 실업률 3M 변화 | -0.10 |
| BEI 5Y | 2.44 |
| M2 YoY | 4.72 |
| Fed BS YoY | 0.58 |
| NFCI | -0.51 |
| DXY 1M | 0.02 |
| 원자재 1M | -0.08 |

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

