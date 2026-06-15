# 트레이딩 상태 스냅샷

> 생성: **2026-06-15 10:14 KST** · 마지막 실행: 2026-06-15 10:13

> ⚠️ 자동 생성 파일. 수동 편집 금지 (`scripts/snapshot_state.py`).

## 레짐

| 항목 | 값 |
|---|---|
| 확정 레짐 | **Goldilocks** |
| 마지막 전환일 | 2026-05-12 |
| 신뢰도 | 19.43% |
| HMM 매핑 | unsupervised (실행 28회, legacy 폴백 0회) |

**blend 확률 분포**

| 레짐 | 확률 |
|---|---|
| Stagflation | 56.0% |
| Goldilocks | 33.1% |
| Slowdown | 10.0% |
| Crisis | 0.5% |
| Reflation | 0.4% |

## 자산

| 항목 | 값 |
|---|---|
| 총자산 | ₩220,914,929 |
| 원금 | ₩219,744,731 |
| 누적 손익 | ₩1,170,198 (+0.53%) |
| 고점 | ₩222,340,710 |
| 드로우다운 | -0.76% |
| 이번 달 회전액 | ₩39,575,323 (2026-06) |
| USD/KRW | 1,519.2 (2026-06-12 12:35) |

## 리밸런싱 트리거

| 계좌 | drift | 트리거 | 사유 | 마지막 리밸 |
|---|---|---|---|---|
| KRW | 28.32% | ⚪ | — | 2026-06-15 10:13 |
| USD | 4.13% | 🔔 | drift(4.1%) | 2026-06-12 23:30 |

**지연 매수 1건 대기 중**

- 379810 6,570,652원 (KRW)

## 목표 비중 (블렌딩 결과)

| 자산군 | 비중 |
|---|---|
| equity_etf | 18.3% |
| gold | 14.9% |
| commodity | 12.3% |
| equity_individual | 12.2% |
| cash | 11.5% |
| equity_factor | 6.7% |
| managed_futures | 6.1% |
| bond_krw | 5.6% |
| bond_tips | 5.2% |
| equity_sector | 2.8% |
| equity_developed | 2.3% |
| equity_emerging | 2.0% |

## 매크로 피처 (마지막 실행)

| 지표 | 값 |
|---|---|
| 모멘텀 1M | -0.1% |
| 모멘텀 3M | 11.2% |
| 실현변동성 | 15.2% |
| VIX | 17.68 |
| VIX 기간구조 | -0.42 |
| 크레딧 신호 | -0.01 |
| HY 스프레드 | 1.55 |
| HY z-score | -0.81 |
| 10Y-2Y 커브 | 0.39 |
| CPI YoY | 4.27 |
| CPI MoM z | 1.17 |
| 실업률 3M 변화 | -0.10 |
| BEI 5Y | 2.39 |
| M2 YoY | 4.72 |
| Fed BS YoY | 0.72 |
| NFCI | -0.51 |
| DXY 1M | 0.01 |
| 원자재 1M | -0.10 |

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
| vol target (기본) / floor | 0.1 / 0.65 |
| 레짐별 target vol | Goldilocks 0.13, Reflation 0.11, Slowdown 0.09, Stagflation 0.08, Crisis 0.06 |

