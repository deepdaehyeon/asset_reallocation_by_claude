# 트레이딩 상태 스냅샷

> 생성: **2026-06-15 23:30 KST** · 마지막 실행: 2026-06-15 23:30

> ⚠️ 자동 생성 파일. 수동 편집 금지 (`scripts/snapshot_state.py`).

## 레짐

| 항목 | 값 |
|---|---|
| 확정 레짐 | **Goldilocks** |
| 마지막 전환일 | 2026-05-12 |
| 신뢰도 | 20.08% |
| HMM 매핑 | unsupervised (실행 29회, legacy 폴백 0회) |

**blend 확률 분포**

| 레짐 | 확률 |
|---|---|
| Stagflation | 57.4% |
| Goldilocks | 34.9% |
| Slowdown | 6.8% |
| Crisis | 0.5% |
| Reflation | 0.4% |

## 자산

| 항목 | 값 |
|---|---|
| 총자산 | ₩221,713,956 |
| 원금 | ₩220,066,333 |
| 누적 손익 | ₩1,647,624 (+0.75%) |
| 고점 | ₩222,340,710 |
| 드로우다운 | -0.32% |
| 이번 달 회전액 | ₩40,943,282 (2026-06) |
| USD/KRW | 1,519.2 (2026-06-12 12:35) |

## 리밸런싱 트리거

| 계좌 | drift | 트리거 | 사유 | 마지막 리밸 |
|---|---|---|---|---|
| KRW | 8.49% | 🔔 | deferred_buys | 2026-06-15 10:13 |
| USD | 4.09% | ⚪ | — | 2026-06-15 23:30 |

지연 매수: 없음

## 목표 비중 (블렌딩 결과)

| 자산군 | 비중 |
|---|---|
| equity_etf | 18.7% |
| gold | 15.1% |
| equity_individual | 12.3% |
| commodity | 12.2% |
| cash | 11.5% |
| equity_factor | 6.7% |
| managed_futures | 6.2% |
| bond_tips | 5.1% |
| bond_krw | 4.9% |
| equity_sector | 2.9% |
| equity_developed | 2.3% |
| equity_emerging | 2.0% |

## 매크로 피처 (마지막 실행)

| 지표 | 값 |
|---|---|
| 모멘텀 1M | 0.7% |
| 모멘텀 3M | 12.6% |
| 실현변동성 | 15.9% |
| VIX | 16.34 |
| VIX 기간구조 | -0.91 |
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

