# TODO
_최종 갱신: 2026-05-09_

## 현재 구현 보완 — 완료 (2026-05-09)

### 리스크 엔진
- [x] **Turnover 상한** — `rebalance()` 앞 총 주문금액/포트폴리오 비율 계산, `max_monthly_turnover`(기본 30%) 초과 시 경고·차단.
- [x] **자산 간 상관 모니터링** — `compute_rolling_correlation()` 추가. 평균 롤링 상관계수 > 0.8 시 경고 출력 (비중 축소 액션 없음).
- [x] **공휴일 처리** — `_next_business_day()`에 `holidays` 라이브러리(한·미 공휴일) 적용. 미설치 시 graceful fallback.

### 실행 레이어
- [x] **지연 매수 TTL** — `add_deferred()` 호출 시 5영업일 만료일(`expires`) 저장. `get_deferred()`에서 만료 항목 자동 정리.
- [x] **환율 캐싱** — `_fetch_usd_krw()`가 `state.json`에 환율 캐시(유효기간 1시간). monitor/krw/usd 3회 실행 시 조회 1회로 감소.
- [x] **주문 재시도 상한** — `_wait_for_fill(max_retries=10, retry_interval=100)` 파라미터 분리.

### 상태 관리
- [x] **`get_portfolio_state()` 사이드 이펙트 분리** — 메서드 내 `save_state()` 제거. 신규 peak을 `self._peak_krw`로 노출, `run.py`에서 저장.
- [x] **state.json 스키마 검증** — `load_state()`에 `JSONDecodeError` 포착 및 `peak_krw` 타입 검증 추가.

### 설정
- [x] **`config.yaml` 로드 실패 처리** — `main()`에 `FileNotFoundError`·`YAMLError` catch + 안내 메시지.

---

## 레짐 판단 모델 고도화

- [x] **레짐 분류 품질 메트릭** — MCC / Macro-F1 / Balanced Accuracy / 위험 레짐 오판 비용 (`backtest/metrics.py`)
- [x] **BalancedRF 앙상블** — `BalancedRFClassifier`. HMM 0.6 + RF 0.4 블렌딩으로 Crisis/Stagflation recall 강화.
- [ ] **Markov Switching Model** (Stage 2) — `statsmodels.tsa.MarkovAutoregression` 도입.
  - 근거: 금융 레짐 모델의 학문적 표준 (Hamilton 1989), 전환 확률 행렬 해석 가능.
  - 제약: 다변량 입력 제한 → PCA 2~3 성분 축약 후 학습.
  - 위치: `trading/regime.py` `MarkovSwitchingClassifier` 클래스, `engine.py`·`run.py` 연동.

---

## Phase 3 — LLM 텍스트 신호 모듈

- [ ] 뉴스 헤드라인 수집 파이프라인 (Reuters/Bloomberg RSS 또는 News API)
- [ ] LLM 분류 프롬프트 → `sentiment`, `uncertainty`, `policy_score`, `event_tags`
- [ ] 일별 집계 + EWMA 스무딩
- [ ] `features.py`에 텍스트 피처 통합 (PRICE_FEATURE_COLS 확장 또는 별도 가중치)
- [ ] `regime.py`에서 `uncertainty_score > 0.8` 시 조기 경보 트리거
- [ ] 텍스트 신호 배분 영향 상한: ±20%p 이내 제한

---

## Phase 4 — 운영 자동화

- [x] **cron 설정** — crontab 예시 (서버 KST):
  ```bash
  50 8  * * 1-5  cd /path && python trading/run.py --mode monitor >> logs/monitor.log 2>&1
  10 9  * * 1-5  cd /path && python trading/run.py --mode krw     >> logs/krw.log 2>&1
  0  23 * * 1-5  cd /path && python trading/run.py --mode usd     >> logs/usd.log 2>&1
  ```
- [ ] **MLflow 모델 추적** — 레짐 판정 히스토리, HMM 학습 파라미터, 신호 IC/IR 기록.
- [ ] **Walk-Forward 백테스트** — 2년 학습 / 6개월 검증 슬라이딩 윈도우. `backtest/` 모듈 활용.

---

## Phase 5 — 백테스트 고도화

- [ ] **거래비용 모델링** — KIS 위탁 수수료(온라인 0.014%) + 스프레드 모델 추가.
- [ ] **AVUV·XLE 데이터 부재 구간** — AVUV 2019-09, XLE 1998-12 이전 비중 재배분 로직 추가.
