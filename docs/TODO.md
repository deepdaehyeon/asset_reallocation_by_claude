# TODO
_system_archi.md 기준 잔여 작업_

---

## 즉시 처리 (수동)

- [ ] **TSLY 매도** — -37.6% 손실 중인 커버드콜 ETF. 레짐 자동화와 구조적 불일치.
- [ ] **IAU 매도** — 411060(KRX 금현물)으로 통합 완료 후 정리.

---

## 현재 구현 보완

### 리스크 엔진
- [ ] **Turnover 상한** — 월간 회전율 30% 초과 방지 (`executor.py`에 체크 추가)
- [ ] **상관 모니터링** — 자산 간 평균 상관 > 0.8 시 포지션 60%로 강제 축소

### 실행 레이어
- [x] **Slack 알림** — 리밸런싱 시작·완료·오류 시 메시지 발송. 레짐·신뢰도·비중변화·주문내역 포함.
- [x] **환율 자동 조회** — yfinance KRW=X 실시간 조회, 실패 시 config 폴백값 사용.
- [x] **주문 결과 로깅** — `logs/orders.csv`에 datetime/ticker/action/qty/price/status 누적 기록.

### 레짐 모델
- [x] **FRED API 연동** — `FRED_API_KEY` 환경변수 설정 시 BAMLH0A0HYM2(HY OAS)·T10Y2Y 조회.
- [x] **HMM 레짐 모델** — GaussianHMM(4상태) 앙상블. 500일 역사 데이터로 학습.
- [x] **레짐 연속 블렌딩** — HMM 사후 확률 가중 평균 (Continuous Exposure).
- [x] **레짐 신뢰도 출력** — 신뢰도 < 40% 시 Neutral 자동 폴백. Slack 메시지에 포함.
- [x] **레짐 전환 히스테리시스 필터** — N회 연속 확인 + 쿨다운.

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

- [ ] **cron 스케줄** — 모니터링/국장/미장 각각 자동 실행 (현재 `cron.sh` 없음)
  ```bash
  # crontab -e  (서버 시간 UTC 기준)
  # 모니터링: 매일 KST 08:50 (UTC 23:50 전날)
  50 23 * * * cd /path/to && python run.py --mode monitor >> logs/monitor.log 2>&1
  # 국장 실행: 매일 KST 09:10 (UTC 00:10)
  10 0 * * * cd /path/to && python run.py --mode krw >> logs/krw.log 2>&1
  # 미장 실행: 매일 KST 23:00 (UTC 14:00, DST 무관 안전 시각)
  0 14 * * * cd /path/to && python run.py --mode usd >> logs/usd.log 2>&1
  ```
- [ ] **MLflow 모델 추적** — 레짐 판정 히스토리, 신호 IC/IR 기록
- [ ] **Walk-Forward 백테스트** — 2년 학습 / 6개월 검증 슬라이딩 윈도우

---

## 추가 완료 (system_archi.md 외)

- [x] **웹 컨트롤 패널** — FastAPI + WebSocket UI. 레짐 조회·Dry Run·리밸런싱 실행,
  실행 로그 실시간 스트리밍. `python server.py` 실행 후 `http://<IP>:8080` 접속.
- [x] **Prometheus + Grafana 모니터링** — Docker Compose로 기동.
  `docker-compose up -d` → prometheus:9090, grafana:3000.
- [x] **변동성 타겟팅** — `apply_vol_targeting()` (portfolio.py).
- [x] **자산군별 비중 상한** — `apply_class_caps()` (portfolio.py).
- [x] **모니터링/국장/미장 분리 실행** — `--mode monitor/krw/usd`. 모니터링에서 계좌별
  드리프트 계산 + 트리거 저장, 국장/미장 run이 소비. 7일 쿨다운 + 드로우다운 비상 오버라이드.

---

## 미결 설계 결정

| 항목 | 현황 | 옵션 |
|---|---|---|
| KRW_2 리밸런싱 | exec_account 배분으로 종목별 단일 계좌 지정 | 두 계좌 잔고 비례 분배 방식으로 개선 가능 |
| IEF / SHY | Risk-On 0% 목표 → 현재 보유 중 | 즉시 전량 매도 vs 드리프트 자연 감소 허용 |
| FRED 미연동 시 | yfinance proxy로 credit_signal 계산 | FRED 연동 권장 (더 정확한 HY 스프레드) |
