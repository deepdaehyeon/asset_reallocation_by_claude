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
- [x] **Slack 알림** — 리밸런싱 실행·완료·오류 시 메시지 발송 (`asset_allocator`의 `Messenger` 참고)
- [ ] **환율 자동 조회** — ecos API 또는 pykis에서 실시간 USD/KRW 환율 주입 (현재 config 폴백값 1380 고정)
- [ ] **주문 결과 로깅** — 실행된 주문 내역을 CSV/DB에 저장

### 레짐 모델
- [ ] **FRED API 연동** — HY 스프레드, 10Y-2Y 커브를 yfinance proxy 대신 FRED 직접 조회
- [ ] **HMM 레짐 모델** — `hmmlearn`으로 비지도 레짐 분류, 현재 규칙 기반과 앙상블
  ```python
  from hmmlearn import hmm
  model = hmm.GaussianHMM(n_components=3, covariance_type="full")
  model.fit(feature_matrix)
  ```
- [ ] **레짐 신뢰도 출력** — `regime_conf: float [0,1]` 계산 및 낮을 때 Neutral 폴백

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
- [ ] **Grafana 대시보드** — 포트폴리오 현황·레짐·드로우다운 실시간 시각화

---

## 미결 설계 결정

| 항목 | 현황 | 옵션 |
|---|---|---|
| 069500 목표 비중 | Risk-On 10%, Risk-Off 0% | 글로벌 레짐에 연동할지 국내 경기 신호 별도 연동할지 |
| SHY / IEF | Risk-On 0% 목표 → 현재 보유 중 | 즉시 전량 매도 vs 드리프트 자연 감소 허용 |
| TLT | Risk-On 1% 목표 (KRW 305080이 주력) | USD 계좌 TLT 완전 제거 후 305080만 유지 고려 |
| KRW_2 리밸런싱 | exec_account 배분으로 종목별 단일 계좌 지정 | 두 계좌 잔고 비례 분배 방식으로 개선 가능 |
