# 트레이딩 상태 스냅샷

> 생성: **2026-06-09 23:30 KST** · 마지막 실행: 2026-06-09 23:30

> ⚠️ 자동 생성 파일. 수동 편집 금지 (`scripts/snapshot_state.py`).

## 레짐

| 항목 | 값 |
|---|---|
| 확정 레짐 | **Goldilocks** |
| 마지막 전환일 | 2026-05-12 |
| 신뢰도 | 27.79% |
| HMM 매핑 | unsupervised (실행 19회, legacy 폴백 0회) |

**blend 확률 분포**

| 레짐 | 확률 |
|---|---|
| Slowdown | 55.7% |
| Goldilocks | 42.9% |
| Reflation | 0.8% |
| Crisis | 0.4% |
| Stagflation | 0.2% |

## 자산

| 항목 | 값 |
|---|---|
| 총자산 | ₩218,574,752 |
| 원금 | ₩216,862,030 |
| 누적 손익 | ₩1,712,722 (+0.79%) |
| 고점 | ₩225,885,767 |
| 드로우다운 | -3.11% |
| 이번 달 회전액 | ₩227,219,723 (2026-06) |
| USD/KRW | 1,515.9 (2026-05-22 23:00) |

## 리밸런싱 트리거

| 계좌 | drift | 트리거 | 사유 | 마지막 리밸 |
|---|---|---|---|---|
| KRW | 6.78% | 🔔 | drift(6.8%) | 2026-06-09 10:05 |
| USD | 11.46% | ⚪ | — | 2026-06-09 23:30 |

**지연 매수 1건 대기 중**

- VWO 2,655,188원 (USD)

## 목표 비중 (블렌딩 결과)

| 자산군 | 비중 |
|---|---|
| equity_etf | 26.6% |
| equity_individual | 14.3% |
| gold | 12.2% |
| bond_krw | 8.4% |
| cash | 8.1% |
| bond_usd | 6.7% |
| commodity | 5.1% |
| managed_futures | 5.0% |
| equity_factor | 5.0% |
| bond_tips | 4.0% |
| equity_developed | 2.4% |
| equity_emerging | 2.0% |
| equity_sector | 0.1% |

## 매크로 피처 (마지막 실행)

| 지표 | 값 |
|---|---|
| 모멘텀 1M | 0.9% |
| 모멘텀 3M | 10.3% |
| 실현변동성 | 13.8% |
| VIX | 17.63 |
| VIX 기간구조 | 0.04 |
| 크레딧 신호 | 0.01 |
| HY 스프레드 | 1.51 |
| HY z-score | -1.07 |
| 10Y-2Y 커브 | 0.41 |
| CPI YoY | 3.95 |
| CPI MoM z | 2.21 |
| 실업률 3M 변화 | -0.10 |
| BEI 5Y | 2.47 |
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

