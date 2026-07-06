# 트레이딩 상태 스냅샷

> 생성: **2026-07-07 03:02 KST** · 마지막 실행: 2026-07-07 03:01

> ⚠️ 자동 생성 파일. 수동 편집 금지 (`scripts/snapshot_state.py`).

## 레짐

| 항목 | 값 |
|---|---|
| 확정 레짐 | **Goldilocks** |
| 마지막 전환일 | 2026-05-12 |
| 신뢰도 | 33.31% |
| HMM 매핑 | unsupervised (실행 51회, legacy 폴백 0회) |

**blend 확률 분포**

| 레짐 | 확률 |
|---|---|
| Goldilocks | 71.1% |
| Reflation | 17.5% |
| Slowdown | 10.9% |
| Stagflation | 0.3% |
| Crisis | 0.2% |

## 자산

| 항목 | 값 |
|---|---|
| 총자산 | ₩222,291,229 |
| 원금 | ₩221,762,797 |
| 누적 손익 | ₩528,433 (+0.24%) |
| 고점 | ₩224,657,863 |
| 드로우다운 | -1.06% |
| 이번 달 회전액 | ₩111,110,857 (2026-07) |
| USD/KRW | 1,530.9 (2026-06-24 07:08) |

## 리밸런싱 트리거

| 계좌 | drift | 트리거 | 사유 | 마지막 리밸 |
|---|---|---|---|---|
| KRW | 8.83% | 🔔 | drift(8.8%) | 2026-07-06 10:06 |
| USD | 8.83% | ⚪ | — | 2026-07-07 03:01 |

지연 매수: 없음

## 목표 비중 (블렌딩 결과)

| 자산군 | 비중 |
|---|---|
| equity_etf | 52.5% |
| equity_factor | 7.7% |
| gold | 7.0% |
| commodity | 6.4% |
| managed_futures | 6.2% |
| cash | 5.9% |
| equity_developed | 4.1% |
| equity_sector | 3.4% |
| bond_krw | 3.1% |
| equity_emerging | 2.0% |
| bond_tips | 1.7% |

## 매크로 피처 (마지막 실행)

| 지표 | 값 |
|---|---|
| 모멘텀 1M | -1.1% |
| 모멘텀 3M | 14.1% |
| 실현변동성 | 14.7% |
| VIX | 16.56 |
| VIX 기간구조 | -2.97 |
| 크레딧 신호 | -0.01 |
| HY 스프레드 | 1.54 |
| HY z-score | -0.86 |
| 10Y-2Y 커브 | 0.35 |
| CPI YoY | 4.27 |
| CPI MoM z | 1.17 |
| 실업률 3M 변화 | -0.10 |
| BEI 5Y | 2.24 |
| M2 YoY | 5.58 |
| Fed BS YoY | 0.98 |
| NFCI | -0.50 |
| DXY 1M | 0.02 |
| 원자재 1M | -0.10 |

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

