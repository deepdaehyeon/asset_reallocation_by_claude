# PROJECT STATE
_최종 갱신: 2026-05-08_

> 아래 계좌 잔고는 2026-05-05 실행 기준 스냅샷이다. 현재 잔고는 `python trading/run.py --mode monitor --dry-run`으로 확인한다.

---

## 현재 레짐 상태 (2026-05-08)

| 항목 | 값 |
|---|---|
| 규칙 기반 레짐 | Goldilocks |
| HMM 앙상블 | Goldilocks (100%) |
| 신뢰도 | 83% (규칙기반 67% + HMM 100%) |
| **확정 레짐** | **Slowdown (전환 대기 중)** |
| 전환 후보 | Goldilocks (2/3회 확인, 쿨다운 4일 남음) |
| SPY 1M 모멘텀 | +8.22% |
| SPY 3M 모멘텀 | +6.22% |
| 실현 변동성 | 10.22% |
| VIX | 17.1 |
| 크레딧 신호 | +1.21% |

---

## 포트폴리오 스냅샷 (2026-05-05 기준)

| 항목 | 값 |
|---|---|
| 유니버스 기준 자산 | 184,555,920 원 (약 1.85억) |
| 전체 자산 (orphan 포함) | 205,603,597 원 (약 2.06억) |
| 드로우다운 | -0.01% |

### 계좌별 보유 현황 (2026-05-05)

**KRW_1 (64378890-01 KRW)**
| 종목 | 이름 | 평가금액 |
|---|---|---|
| 379800 | KODEX S&P500 | 6,376,020 원 |
| 411060 | ACE KRX금현물 | 3,509,580 원 |

**KRW_2 (64521213-01 KRW)**
| 종목 | 이름 | 평가금액 |
|---|---|---|
| 379800 | KODEX S&P500 | 37,704,810 원 |
| 379810 | KODEX 나스닥100 | 38,881,635 + 6,682,360 원 |
| 305080 | TIGER 미국채10년 | 11,572,015 + 1,939,665 원 |
| 411060 | ACE KRX금현물 | 20,603,655 원 |
| 469830 | SOL 초단기채권 | 11,459,400 + 1,990,600 원 |

**USD (64378890-01 USD)**
| 종목 | 이름 | 평가금액 (USD) |
|---|---|---|
| TSLA | Tesla | $4,325 |
| PLTR | Palantir | $10,849 |
| IEF | iShares 7-10Y | $4,251 |
| SHY | iShares 1-3Y | $4,272 |

### 정리 완료 예정 (Orphan Holdings, 2026-05-05 기준)

| 종목 | 평가금액 | 비고 |
|---|---|---|
| **TSLY** | 12,646,251 원 (6.2%) | 커버드콜 ETF, -37.6% 손실. 수동 정리 권장 |
| **IAU** | 8,391,007 원 (4.1%) | 411060(KRX 금현물)으로 통합 후 정리 예정 |

---

## 현재 설정 기준 목표 비중 (Goldilocks 기준, 순수 배분)

HMM 연속 블렌딩 적용 전 수치. 실제 실행 시 HMM 확률로 가중 평균됨.

| 자산군 | 목표 | 세부 종목 |
|---|---|---|
| equity_etf | 40% | 379800(64%) + 379810(36%) |
| equity_factor | 5% | VTV(60%) + AVUV(40%) |
| equity_sector | 0% | — |
| equity_individual | 20% | TSLA(36%) + PLTR(64%) |
| commodity | 5% | DBC(100%) |
| managed_futures | 5% | DBMF(100%) |
| bond_usd | 0% | — |
| bond_krw | 0% | — |
| gold | 10% | 411060(100%) |
| cash | 15% | 469830(100%) |

---

## 유니버스 변경 이력

| 날짜 | 변경 |
|---|---|
| 2026-05 이전 | Risk-On/Off/Neutral/High-Vol 4레짐 체계 |
| 2026-05-07 | Goldilocks/Reflation/Slowdown/Stagflation/Crisis 5레짐 전환 |
| 2026-05-07 | equity_individual: USMV 제거, TSLA 36%+PLTR 64% 유지 |
| 2026-05-07 | equity_factor: VTV(60%) + AVUV(40%) 추가 |
| 2026-05-07 | equity_sector: XLE 신규 추가 (Reflation·Stagflation 수혜) |
| 2026-05-07 | managed_futures base 비중 12% → 레짐별 5~12%로 세분화 |

---

## 구현 완료 범위

- [x] yfinance 기반 레짐 신호 수집
- [x] 모멘텀·변동성·VIX·크레딧 피처 계산
- [x] 규칙 기반 5레짐 감지 (Goldilocks·Reflation·Slowdown·Stagflation·Crisis)
- [x] 레짐별 자산군 목표 비중 + 자산 라우팅 (config.yaml)
- [x] GaussianHMM(5상태) 앙상블 — 500일 학습, 최근 60일 시퀀스 추론
- [x] 레짐 연속 블렌딩 (Continuous Exposure) — HMM 사후 확률 가중 평균
- [x] 레짐 신뢰도 출력 + 40% 미달 시 Slowdown 폴백
- [x] 레짐 전환 히스테리시스 필터 (3회 연속 + 5일 쿨다운)
- [x] FRED API 연동 (HY 스프레드, 장단기 금리차)
- [x] 변동성 타겟팅 (target_vol 10%, floor 0.65)
- [x] 자산군별 최대 비중 상한 (class_max_weight)
- [x] 드로우다운 제어 (equity 단계적 축소, 채권·금·현금 유지)
- [x] KIS 멀티 계좌 잔고 조회 + 유니버스/orphan 분리
- [x] 유니버스 외 보유 종목 자동 매도 (sell_orphans)
- [x] 매도 우선 주문 실행
- [x] 직전 고점 기반 드로우다운 추적 (state.json)
- [x] T+2 결제 지연 대응: Pre-Funding Buffer (469830 ≥ 7%)
- [x] T+2 결제 지연 대응: Synthetic Exposure (USD 지연 → KRW 합성)
- [x] SettlementTracker — pending_sells / deferred_buys 영속화
- [x] 환율 자동 조회 (yfinance KRW=X, 실패 시 config 폴백)
- [x] 주문 결과 로깅 (logs/orders.csv)
- [x] Slack 알림 (시작·완료·오류, 레짐·신뢰도·비중·주문내역 포함)
- [x] 웹 컨트롤 패널 (FastAPI + WebSocket, http://\<IP>:8080)
- [x] Prometheus + Grafana 모니터링 대시보드 (Docker Compose)
- [x] monitor/krw/usd 3모드 분리 실행 — 계좌별 드리프트·트리거 독립 관리
- [x] 미결제 폴링 루프 추출 (`_wait_for_fill`) — 코드 중복 제거
- [x] 모든 내부 지연 import 제거 — 모듈 상단 통합

---

## 미결 설계 결정

| 항목 | 현황 | 옵션 |
|---|---|---|
| TSLY·IAU 정리 | 수동 보유 중 | 즉시 수동 매도 권장 |
| KRW_2 리밸런싱 | exec_account 배분으로 종목별 단일 계좌 지정 | 두 계좌 잔고 비례 분배 방식으로 개선 가능 |
| FRED 미연동 시 | yfinance proxy로 credit_signal 계산 | FRED 연동 권장 (더 정확한 HY 스프레드) |
| 주문 타임아웃 | 1000초 하드코딩 | config.yaml에서 조정 가능하도록 변경 고려 |
