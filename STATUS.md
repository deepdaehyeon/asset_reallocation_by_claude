# 트레이딩 상태 스냅샷

> 생성: **2026-07-09 03:01 KST** · 마지막 실행: 2026-07-09 03:01

> ⚠️ 자동 생성 파일. 수동 편집 금지 (`scripts/snapshot_state.py`).

## 레짐

| 항목 | 값 |
|---|---|
| 확정 레짐 | **Goldilocks** |
| 마지막 전환일 | 2026-05-12 |
| 신뢰도 | 35.83% |
| HMM 매핑 | unsupervised (실행 53회, legacy 폴백 0회) |

**blend 확률 분포**

| 레짐 | 확률 |
|---|---|
| Goldilocks | 76.7% |
| Slowdown | 11.3% |
| Stagflation | 7.6% |
| Reflation | 4.4% |

## 자산

| 항목 | 값 |
|---|---|
| 총자산 | ₩218,518,703 |
| 원금 | ₩220,289,392 |
| 누적 손익 | ₩-1,770,690 (-0.80%) |
| 고점 | ₩224,657,863 |
| 드로우다운 | -2.73% |
| 이번 달 회전액 | ₩138,654,316 (2026-07) |
| USD/KRW | 1,526.3 (2026-07-07 10:42) |

## 리밸런싱 트리거

| 계좌 | drift | 트리거 | 사유 | 마지막 리밸 |
|---|---|---|---|---|
| KRW | 11.09% | 🔔 | drift(11.1%) | 2026-07-08 10:23 |
| USD | 11.09% | ⚪ | — | 2026-07-09 03:01 |

지연 매수: 없음

## 목표 비중 (블렌딩 결과)

| 자산군 | 비중 |
|---|---|
| equity_etf | 52.8% |
| equity_factor | 7.7% |
| gold | 7.6% |
| cash | 6.2% |
| managed_futures | 5.5% |
| commodity | 5.0% |
| bond_krw | 4.3% |
| equity_developed | 4.3% |
| equity_sector | 3.0% |
| equity_emerging | 2.0% |
| bond_tips | 1.8% |

## 매크로 피처 (마지막 실행)

| 지표 | 값 |
|---|---|
| 모멘텀 1M | -1.1% |
| 모멘텀 3M | 10.4% |
| 실현변동성 | 14.2% |
| VIX | 16.24 |
| VIX 기간구조 | -2.89 |
| 크레딧 신호 | 0.00 |
| HY 스프레드 | 1.54 |
| HY z-score | -0.86 |
| 10Y-2Y 커브 | 0.36 |
| CPI YoY | 4.27 |
| CPI MoM z | 1.17 |
| 실업률 3M 변화 | -0.10 |
| BEI 5Y | 2.28 |
| M2 YoY | 5.58 |
| Fed BS YoY | 0.98 |
| NFCI | -0.52 |
| DXY 1M | 0.02 |
| 원자재 1M | -0.09 |

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

