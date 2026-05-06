# TODO
_system_archi.md 기준 잔여 작업_

---

## 즉시 처리 (수동)

- [ ] **TSLY 매도** — -37.6% 손실 중인 커버드콜 ETF. 레짐 자동화와 구조적 불일치.
- [ ] **IAU 매도** — 411060(KRX 금현물)으로 통합 완료 후 정리.
- [ ] **069500 편입** — KODEX 200. Risk-On 목표 10% / 현재 0%. 수동 매수 또는 리밸런싱 트리거.

---

## 현재 구현 보완

### 리스크 엔진
- [ ] **변동성 타겟팅** — 포트폴리오 변동성을 연 10%로 맞추는 레버리지 스케일 (`portfolio.py` 추가)
  ```python
  target_vol = 0.10
  leverage_scale = min(target_vol / current_portfolio_vol, 1.0)
  weights = {t: w * leverage_scale for t, w in weights.items()}
  ```
- [ ] **Turnover 상한** — 월간 회전율 30% 초과 방지 (`executor.py`에 체크 추가)
- [ ] **상관 모니터링** — 자산 간 평균 상관 > 0.8 시 포지션 60%로 강제 축소

### 실행 레이어
- [x] **Slack 알림** — 리밸런싱 시작·완료·오류 시 메시지 발송. 레짐·신뢰도·비중변화·주문내역 포함.
- [x] **환율 자동 조회** — yfinance KRW=X 실시간 조회, 실패 시 config 폴백값 사용.
- [x] **주문 결과 로깅** — `logs/orders.csv`에 datetime/ticker/action/qty/price/status 누적 기록.

### 레짐 모델
- [x] **FRED API 연동** — `FRED_API_KEY` 환경변수 설정 시 BAMLH0A0HYM2(HY OAS)·T10Y2Y 조회.
  credit_signal을 HY 스프레드 1M 변화 기반으로 대체, hy_spread·curve_10y2y 피처 병합.
- [x] **HMM 레짐 모델** — GaussianHMM(4상태) 앙상블. 500일 역사 데이터로 학습,
  규칙 기반 레이블 다수결로 상태-레짐 매핑. override_threshold 60% 초과 시만 채택.
- [x] **레짐 신뢰도 출력** — `compute_rule_confidence()` + HMM 사후 확률 평균.
  신뢰도 < 40% 시 Neutral 자동 폴백. Slack 메시지에 신뢰도 포함.
- [x] **레짐 전환 히스테리시스 필터** — `RegimeFilter`: N회 연속 확인(기본 3회) +
  쿨다운(기본 5일) 두 조건 모두 충족해야 전환 확정.

---

## Phase 3 — LLM 텍스트 모듈

- [ ] 뉴스 헤드라인 수집 파이프라인 (Reuters/Bloomberg RSS 또는 News API)
- [ ] LLM 분류 프롬프트 → `sentiment`, `uncertainty`, `policy_score`, `event_tags`
- [ ] 일별 집계 + EWMA 스무딩
- [ ] `features.py`에 텍스트 피처 통합
- [ ] `regime.py`에서 `uncertainty_score > 0.8` 시 조기 경보 트리거
- [ ] 텍스트 신호 배분 영향 상한: ±20%p 이내 제한

---

## Phase 4 — 운영 자동화

- [ ] **cron 스케줄** — 매월 첫 거래일 자동 실행 (현재 `cron.sh` 없음)
  ```bash
  # crontab: 매월 1일 KST 09:30
  30 0 1 * * cd /path/to && python run.py >> logs/run.log 2>&1
  ```
- [ ] **페이퍼 트레이딩 모드** — KIS 모의투자 계좌 연동 (실거래 전 검증)
- [ ] **MLflow 모델 추적** — 레짐 판정 히스토리, 신호 IC/IR 기록
- [ ] **Walk-Forward 백테스트** — 2년 학습 / 6개월 검증 슬라이딩 윈도우
- [x] **Grafana 대시보드** — 포트폴리오 현황·레짐·드로우다운 실시간 시각화.
  `docker-compose.yml` + prometheus_client `/metrics` 엔드포인트. `docker compose up -d` 후 `http://localhost:3000` 접속 (admin/admin).

---

## 추가 완료 (system_archi.md 외)

- [x] **웹 컨트롤 패널** — FastAPI + WebSocket UI. 레짐 조회·Dry Run·리밸런싱 실행,
  실행 로그 실시간 스트리밍. `python server.py` 실행 후 `http://<IP>:8080` 접속.

---

## 미결 설계 결정

| 항목 | 현황 | 옵션 |
|---|---|---|
| 069500 목표 비중 | Risk-On 10%, Risk-Off 0% | 글로벌 레짐에 연동할지 국내 경기 신호 별도 연동할지 |
| SHY / IEF | Risk-On 0% 목표 → 현재 보유 중 | 즉시 전량 매도 vs 드리프트 자연 감소 허용 |
| TLT | Risk-On 1% 목표 (KRW 305080이 주력) | USD 계좌 TLT 완전 제거 후 305080만 유지 고려 |
| KRW_2 리밸런싱 | exec_account 배분으로 종목별 단일 계좌 지정 | 두 계좌 잔고 비례 분배 방식으로 개선 가능 |
