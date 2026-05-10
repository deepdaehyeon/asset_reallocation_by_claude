# 실험 리포트 — Drawdown 현금 재배분 / 261220 프록시 / HMM 안정화
_작성일: 2026-05-10_

## 목표

- **실거래 리스크 제어의 일관성 확보**: drawdown 스케일다운 시 equity 축소분이 현금성 자산으로 재배분되어 타깃 합이 유지되는지 확인.
- **백테스트 데이터 공백 제거**: `261220`(KRX) 데이터 미존재로 인한 백테스트 경고/누락 제거.
- **레짐 모델 로그/안정성 개선**: HMM 학습의 수렴 경고 스팸을 줄이고 학습을 더 안정적으로 수행.

---

## 변경 사항 요약

### 1) Drawdown 축소분 현금 재배분 (실거래)
- drawdown 구간에서 equity만 곱해 줄이면 타깃 합이 1.0보다 작아져 drift/주문 계산이 흔들릴 수 있음.
- 개선: 축소된 비중(reduction)을 현금성 티커로 이동.
  - KRW: `settlement.buffer_tickers`(기본 `469830`)
  - USD: `SHY`

### 2) `261220` 백테스트 프록시
- `backtest/data.py`에서 `261220 → USO` 프록시 매핑.

### 3) HMM 안정화
- `trading/regime.py:HmmRegimeClassifier.fit()`
  - NaN/Inf 정리, 표준화 후 클리핑
  - `min_covar`로 수치 안정성 강화, `tol`/`n_iter` 조정
  - seed 재시도 후 최선 모델 선택
  - 학습 구간 stderr 출력 억제(수렴 로그 스팸 방지)

---

## 실험/검증 커맨드

### (A) crisis 모드 (전체기간)

```bash
python backtest/run_backtest.py --mode crisis
```

**관측(2026-05-10 실행 결과):**
- COVID Crash (2020)
  - 전략 MaxDD -10.55% vs 60/40 -19.13%
- Bear 2022
  - 전략 MaxDD -13.19% vs 60/40 -20.67%
- SVB (2023 Q1)
  - 전략 total_return +8.87% vs 60/40 +4.88%

### (B) robustness 모드 (전체기간)

```bash
python backtest/run_backtest.py --mode robustness
```

**관측(요약):**
- 서브기간 Sharpe 양수: 4/5 (2014-16 구간 Sharpe < 0)
- 레짐 의도 달성: 4/5 (Reflation만 FAIL)

### (C) drift 민감도 (전체기간)

```bash
python backtest/run_backtest.py --mode drift
```

**관측(요약):**
- 주간(기준): CAGR 10.7%, 누적비용 6.333%, MaxDD -16.1%
- drift 5% (현재): CAGR 10.5%, 누적비용 1.316%, MaxDD -15.2%
- drift 8~10%: 비용은 더 줄지만 CAGR 하락폭이 커짐

---

## 추가 확인 (빠른 스모크)

```bash
python backtest/run_backtest.py --mode crisis --start 2020-01-01 --end 2021-12-31
```

**관측:**
- `261220` 데이터 없음 경고가 사라짐 (프록시 `USO` 적용 확인)
- HMM “Model is not converging” 로그 스팸이 출력되지 않음 (stderr 억제 확인)

