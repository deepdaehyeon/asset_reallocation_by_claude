# TODO
_최종 갱신: 2026-05-08_

---

## 즉시 처리 (수동)

- [ ] **TSLY 매도** — 커버드콜 ETF, -37.6% 손실. 유니버스 외 보유 중. orphan 매도 기능(`sell_orphans`)으로 자동 처리 가능하나 손실 확정 전 수동 검토 권장.
- [ ] **IAU 매도** — 411060(KRX 금현물)으로 통합 완료 후 정리.
- [ ] **VTV·AVUV·XLE 최초 매수** — 신규 유니버스 편입 종목. 현재 USD 계좌 미보유 상태. 첫 실행 시 트리거 조건 충족 여부 확인 후 리밸런싱.

---

## 현재 구현 보완

### 리스크 엔진

- [ ] **Turnover 상한** — 월간 회전율 30% 초과 방지. `executor.py`의 `rebalance()` 호출 전 총 주문 금액 합계와 portfolio 대비 비율 계산 후 초과 시 경고·차단.
- [ ] **자산 간 상관 모니터링** — 평균 롤링 상관계수 > 0.8 시 포지션 60%로 강제 축소. `features.py`에 `compute_rolling_correlation()` 추가, run.py 파이프라인에서 경고 출력.
- [ ] **공휴일 처리** — `settlement.py`의 `_next_business_day()`가 토·일만 제외하고 한국·미국 공휴일은 미고려. `holidays` 라이브러리 추가 또는 커스텀 캘린더 적용.

### 실행 레이어

- [ ] **주문 타임아웃 설정화** — `_wait_for_fill()`의 1000초가 하드코딩. `config.yaml` `rebalancing.order_timeout_sec` 키로 조정 가능하게 변경.
- [ ] **지연 매수 TTL** — `deferred_buys`에 만료 시한이 없어 오래된 항목이 무기한 누적 가능. 생성 후 N일(예: 5영업일) 초과 항목 자동 정리 로직 추가.
- [ ] **환율 캐싱** — `KisRebalancer.__init__`에서 매 실행마다 yfinance로 환율 조회. monitor/krw/usd 3회 실행 시 3번 조회됨. 실행 간 state.json 캐싱(유효기간 1시간) 고려.
- [ ] **주문 재시도 상한** — `_wait_for_fill()`의 100초마다 가격 조정이 최대 10회(1000초)까지만 허용. 재시도 횟수를 별도 파라미터로 분리하면 가독성 개선.

### 상태 관리

- [ ] **`get_portfolio_state()` 사이드 이펙트 분리** — 현재 잔고 조회 메서드 내에서 `state.json`에 `peak_krw`를 직접 쓰는 구조. 조회와 저장의 책임 분리를 위해 peak 업데이트를 `run.py` 호출 측으로 이동.
- [ ] **state.json 스키마 검증** — 재기동 시 state.json이 손상되거나 키가 누락되면 런타임 오류. 로드 시 필수 키 존재 여부 + 타입 검증 추가 (`pydantic` 또는 단순 `assert`).

### 설정

- [ ] **Slack mention 설정화** — `messenger.py`의 `_MENTION = "김대현"`이 하드코딩. `config.yaml`의 `slack.mention_user` 키로 이동.
- [ ] **server.py 인증** — 웹 컨트롤 패널(`/ws/run`)에 인증이 없어 로컬 네트워크에서 누구나 리밸런싱 실행 가능. Bearer token 또는 Basic Auth 추가 권장.
- [ ] **`config.yaml` 로드 실패 처리** — `run.py`의 `open(args.config)` 실패 시 KeyError가 아닌 명확한 오류 메시지 출력 (`FileNotFoundError` catch + 안내).

---

## Phase 3 — LLM 텍스트 신호 모듈

- [ ] 뉴스 헤드라인 수집 파이프라인 (Reuters/Bloomberg RSS 또는 News API)
- [ ] LLM 분류 프롬프트 → `sentiment`, `uncertainty`, `policy_score`, `event_tags`
- [ ] 일별 집계 + EWMA 스무딩
- [ ] `features.py`에 텍스트 피처 통합 (HMM_FEATURE_COLS 확장 또는 별도 가중치)
- [ ] `regime.py`에서 `uncertainty_score > 0.8` 시 조기 경보 트리거
- [ ] 텍스트 신호 배분 영향 상한: ±20%p 이내 제한

---

## Phase 4 — 운영 자동화

- [ ] **cron 설정** — 현재 수동 실행 중. crontab 예시 (서버 KST 기준):
  ```bash
  50 8  * * 1-5  cd /path && python trading/run.py --mode monitor >> logs/monitor.log 2>&1
  10 9  * * 1-5  cd /path && python trading/run.py --mode krw     >> logs/krw.log 2>&1
  0  23 * * 1-5  cd /path && python trading/run.py --mode usd     >> logs/usd.log 2>&1
  ```
- [ ] **MLflow 모델 추적** — 레짐 판정 히스토리, HMM 학습 파라미터, 신호 IC/IR 기록
- [ ] **Walk-Forward 백테스트** — 2년 학습 / 6개월 검증 슬라이딩 윈도우. backtest/ 모듈 활용.
- [ ] **알림 채널 다변화** — Telegram·이메일 등 Slack 의존성 해소 (messenger.py 인터페이스 추상화)
- [ ] **로그 로테이션** — `logs/orders.csv`에 보존 기간 설정 없음. 월별 아카이빙 or `logrotate` 설정 추가.

---

## Phase 5 — 백테스트 고도화

- [ ] **KRW ETF 프록시 정밀화** — 현재 379800→SPY, 379810→QQQ로 단순 대체. 실제 상장일(2021년경) 전후 수익률 차이 검증 후 보정 계수 적용.
- [ ] **환율 효과 반영** — 백테스트가 USD 단일 통화 수익률로 계산. KRW 관점 수익률(원화 환산) 추가 지표 제공.
- [ ] **거래비용 모델링** — 현재 슬리피지·수수료 0으로 가정. KIS 위탁 수수료(온라인 0.014%) + 스프레드 모델 추가.
- [ ] **AVUV·XLE 데이터 부재 구간** — AVUV 2019-09, XLE 1998-12 이전 데이터 없음. 해당 구간 비중 재배분 로직 추가.

---

## 완료 항목

- [x] **Slack 알림** — 리밸런싱 시작·완료·오류 메시지. 레짐·신뢰도·비중변화·주문내역 포함.
- [x] **환율 자동 조회** — yfinance KRW=X 실시간 조회, 실패 시 config 폴백값 사용.
- [x] **주문 결과 로깅** — `logs/orders.csv` 누적 기록.
- [x] **FRED API 연동** — HY OAS(BAMLH0A0HYM2)·T10Y2Y 조회.
- [x] **HMM 레짐 모델** — GaussianHMM(5상태) 앙상블. 500일 학습.
- [x] **레짐 연속 블렌딩** — HMM 사후 확률 가중 평균 (Continuous Exposure).
- [x] **레짐 신뢰도** — 40% 미달 시 Slowdown 폴백. Slack 메시지 포함.
- [x] **레짐 히스테리시스 필터** — 3회 연속 확인 + 5일 쿨다운.
- [x] **5레짐 체계** — Goldilocks·Reflation·Slowdown·Stagflation·Crisis.
- [x] **웹 컨트롤 패널** — FastAPI + WebSocket UI. `python trading/server.py`.
- [x] **Prometheus + Grafana** — Docker Compose. `docker-compose up -d`.
- [x] **변동성 타겟팅** — `apply_vol_targeting()` (portfolio.py).
- [x] **자산군 비중 상한** — `apply_class_caps()` (portfolio.py).
- [x] **monitor/krw/usd 분리 실행** — 계좌별 드리프트·트리거 독립 관리.
- [x] **유니버스 외 보유 종목 자동 매도** — `sell_orphans()` (executor.py).
- [x] **T+2 대응: Pre-Funding Buffer** — 469830 ≥ 7% 보장.
- [x] **T+2 대응: Synthetic Exposure** — USD 지연 매수 → KRW 합성.
- [x] **드로우다운 제어** — equity 단계적 축소, 채권·금·현금 유지.
- [x] **코드 리팩터링** — 함수 내 지연 import 제거, `_wait_for_fill()` 추출.
- [x] **레짐 로버스트니스 검증** — `--mode robustness` (backtest/robustness.py).
- [x] **HMM predict_lookback** — 단일 관측 → N일 시퀀스 추론으로 수정.
