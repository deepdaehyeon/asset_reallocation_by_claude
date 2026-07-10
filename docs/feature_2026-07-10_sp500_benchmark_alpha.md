# S&P500 벤치마크 대비 알파 — 매 실행 리포트

> **요약**: ① 사용자 요청(2026-07-10) — "같은 돈을 S&P500(SPY)에 넣었더라면 총자산이 얼마였을지, 실제 대비 얼마 이득/손해인지"를 매 트레이딩 결과마다 표시해 시스템이 잘 돌고 있는지 판단. ② money-weighted 방식(입출금 반영): 벤치마크가 SPY 주식을 보유한다고 보고 입금하면 SPY를 더 사고 출금하면 파는 것으로 처리(입출금 net은 peak 보정과 동일 소스 재사용, processed_through로 중복 방지) → 입금 많다고 유리하게 왜곡되는 것 차단. 앵커는 사용자 A안대로 "오늘부터"(최초 실행 총자산=출발점, 알파 0). ③ Slack 리포트(send_monitor·send_complete)·STATUS.md·`docs/_benchmark_history.csv`에 표시. **순수 리포트 — 매매 로직 미영향**(알파 마이너스여도 시스템은 아무것도 안 바꿈).

## 계산

- 상태: `bench_spy_shares`(벤치마크 보유 SPY주), `bench_inception_at`, `bench_start_value`.
- 최초: `bench_spy_shares = 총자산 / SPY_KRW`(앵커, 알파 0).
- 이후: 입출금 감지 시 `bench_spy_shares += net_flow / SPY_KRW`.
- 매 실행: `벤치마크 = bench_spy_shares × SPY_KRW`, `알파 = 실제총자산 − 벤치마크`.
- SPY_KRW = SPY(signal, yfinance) 종가 × usd_krw.

## 구현 (파일)

- `trading/benchmark.py`(신설): `update_benchmark`·`format_alpha_line`·CSV 기록.
- `trading/executor.py`: `_correct_peak_for_io`가 감지한 입출금 net을 `self._last_net_flow_krw`로 노출.
- `trading/run.py`: `_report_benchmark_alpha` 헬퍼 — monitor(market["prices"]의 SPY 재사용)·execute(SPY
  재조회) 양쪽에서 호출, 콘솔 출력 + state 저장 + alpha_line 반환.
- `trading/messenger.py`: `send_monitor`·`send_complete`에 `alpha_line` 파라미터 추가(자산 줄 아래 표시).
- `scripts/snapshot_state.py`: STATUS.md 자산 표에 "S&P500였다면 / 알파" 행 추가.

## 검증 (모의)

| 시나리오 | 알파 |
|---|---|
| 앵커(최초) | 0 (추적 시작) |
| SPY +10% / 포트 +3% | **−6.4%** (상승장에선 방어형이 덜 벌어 마이너스 = 정상) |
| SPY −20% / 포트 −5% | **+18.8%** (하락장에서 방어로 알파 플러스) |
| +3천만 입금 | 벤치 SPY 100주 추가(740→840), 알파 왜곡 없음 |

## 해석 가이드

이 포트폴리오 목표는 **S&P보다 덜 벌더라도 덜 빠지는 것**(하락 방어). 그래서 **상승장에선 알파가
마이너스, 하락장에서 플러스로 벌어지는 게 정상 패턴**이다. 알파 절댓값보다 "하락장에서 얼마나
방어했나"가 이 전략의 성공 지표. (원하면 낙폭 비교도 추가 가능.)

## 앵커 재설정

지금부터 추적이라 알파는 0에서 시작해 벌어진다. 과거 시점부터 보고 싶으면 state의
`bench_spy_shares`를 (그날 총자산 / 그날 SPY_KRW)로 수동 설정하면 그 시점 앵커로 소급 가능.
