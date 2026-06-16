# vol targeting 반응속도(ewma_lambda) 스윕 — 1차 null(배선 결함, 라이브·백테스트 양쪽 死), 엔진 충실화 후 재실험: 작동하나 약한 레버(현행 유지)

## SUMMARY
- **무엇을**: 비중 조정이 흡수되는 고원 상태에서 진짜 레버는 vol targeting의 손잡이라는 가설([[project-voltarget-blend-defense-engine]]) 하에, 그중 반응속도 `ewma_lambda`(EWMA 감쇠, 낮을수록 빠름)를 0.97·0.94(현행)·0.90·0.85·0.80으로 스윕해 빠른 디리스킹이 4지표(Ulcer·Martin·회복)를 개선하는지 엔드투엔드 워크포워드 OOS로 검증. core30·drift·drawdown_scaling off 등 라이브 config 고정, ewma_lambda만 교체. `scripts/sweep_voltarget_lambda_oos.py`.
- **핵심 수치**: 5개 λ 전부 **완전 동일**(TEST Martin 3.31·CAGR 18.0%·Ulcer 4.23·MaxDD −15.4%·회복 564, TRAIN Martin 1.58 — 소수점까지 byte-identical). Δ 전부 +0.00. → λ가 백테스트 결과에 **영(0) 영향**.
- **채택 여부·결론**: **가설 미검증(실험 무효) — null의 원인이 배선 결함이라 "반응속도는 무효"로 결론할 수 없음.** 원인: 백테스트는 `_target_weights`에 `signal_px_slice=signal_px.tail(65)`를 넘기는데(engine.py:662·585), `compute_portfolio_ewma_vol`의 weights는 *유니버스 티커*(379800·VTV…) 키이고 `signal_px`는 *신호 티커*(SPY·^VIX·TLT·HYG…)뿐이라 **교집합이 공집합 → port_vol=0 → eff_vol=realized_vol(SPY 기반, features.py `_ewma_vol`의 고정 λ=0.94)로 폴백**. 즉 config의 `ewma_lambda`는 **백테스트에서 죽은 손잡이**(완전 동일 결과가 그 증거).
- **정정(2026-06-17, 이전 '괴리' 주장 철회)**: 라이브(run.py:647-653)도 **동일하게 죽어 있었다.** `market["prices"]`는 `fetch_signal_prices(signal_cfg["tickers"])` = *신호 티커*뿐(SPY·^VIX·TLT·HYG·DX-Y·DJP·^VIX9D)인데 `ticker_w`는 *유니버스 티커* 키 → `compute_portfolio_ewma_vol`에서 교집합 공집합 → `port_vol=0` → `eff_vol=realized_vol`(SPY, 고정 λ=0.94)로 폴백. config로 확인: `set(signal.tickers) & set(universe) = ∅`. **∴ `use_portfolio_vol` 경로는 라이브·백테스트 양쪽 모두 비활성이며, 둘 사이 괴리는 없었다(이전 SUMMARY/한계의 '괴리' 서술은 오류).**

## 엔진 충실화 후 재실험 (2026-06-17, 사용자 승인: "백테스트만 먼저 살려 검증")
- **수정**: `backtest/engine.py` `_target_weights`에 `universe_px_slice` 인자 추가 → portfolio vol을 *실제 보유(유니버스) 가격*으로 계산(드리프트·캘린더 양 경로 모두 `universe_px[:date].tail(65)` 주입). 라이브 코드(run.py)는 미수정(승인 범위 외). 추가 인자라 미패치 호출부는 구 동작 유지(회귀 없음).
- **결과(이제 λ가 살아있음 — byte-identical 깨짐)**: TEST(OOS) Martin이 느린 λ→빠른 λ로 **단조 개선**하나 폭이 작다. λ=0.97 Martin 3.12 / 0.94(현행) 3.26 / 0.90 3.28 / 0.85 3.31 / 0.80 3.33. Ulcer 4.33→4.11. **MaxDD(−15.4%)·회복(564→563) 사실상 불변**. 대가는 회전율 tx 1.13%→1.37%(빠를수록 ↑). TRAIN(2010~2018)은 여전히 5개 λ 동일(1.58) — 초기 유니버스 ETF 미상장으로 port_vol=0 폴백이 그 구간엔 남아서.
- **채택 여부·결론**: **반응속도는 이제 작동하는 진짜 손잡이지만 *약한 레버*다.** 현행(0.94)→최속(0.80) 이득은 ΔMartin **+0.07**·ΔUlcer −0.08뿐인데 회전율은 +0.21%p(약 +18%) 증가. 깊이 방어(MaxDD)·회복엔 영향 없음. 라이브 회전이 백테스트의 ~10배라([[project-live-turnover-vs-backtest-gap]]) 이 미미한 위험조정 개선은 **라이브 회전 비용을 정당화하지 못함 → 현행 λ=0.94 유지 권장.** 즉 "vol targeting이 가장 강한 레버"는 맞지만(on/off는 방어엔진 [[project-voltarget-blend-defense-engine]]), 그 *반응속도 하위손잡이*는 고원을 못 넘는다(비중 조정과 같은 결).
- **기준선 이동 주의**: 이 엔진 수정으로 *모든 향후 백테스트*의 portfolio vol 입력이 SPY프록시→실포트로 바뀜. 현행 λ=0.94 기준선이 구(舊) Martin 3.31→3.26, Ulcer 4.23→4.19로 소폭 이동. 과거 floor·ladder·shrink 결론은 방향성은 유지될 가능성 높으나 정밀 재확인 필요.

## 결과 A — 엔진 수정 전 (배선 死, λ 무관 동일)
| 창 | 설정 | Martin | CAGR | Ulcer | 최장UW | 롤3y최악 | MaxDD | tx |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| TRAIN | λ 전부(0.80~0.97) | 1.58 | 9.1% | 3.21 | 339 | 5.9% | −11.2% | 1.81% |
| TEST | λ 전부(0.80~0.97) | 3.31 | 18.0% | 4.23 | 564 | 10.9% | −15.4% | 1.20% |

## 결과 B — 엔진 충실화 후 (실포트 vol, λ 작동) — TEST(OOS) 2019-01~2025-04
| 설정 | Martin | CAGR | Ulcer | 최장UW | 롤3y최악 | MaxDD | tx | ΔMartin |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| λ=0.97 (hl≈23d, 느림) | 3.12 | 17.5% | 4.33 | 564 | 10.4% | −15.4% | 1.13% | −0.15 |
| **λ=0.94 (현행)** | **3.26** | **17.7%** | **4.19** | **564** | **10.8%** | **−15.4%** | **1.16%** | — |
| λ=0.90 (hl≈7d) | 3.28 | 17.6% | 4.14 | 563 | 10.7% | −15.4% | 1.25% | +0.02 |
| λ=0.85 (hl≈4d) | 3.31 | 17.6% | 4.11 | 563 | 10.7% | −15.4% | 1.33% | +0.05 |
| λ=0.80 (hl≈3d, 빠름) | 3.33 | 17.7% | 4.11 | 563 | 10.8% | −15.4% | 1.37% | +0.07 |

(TRAIN은 수정 후에도 5개 λ 동일 1.58 — 초기 유니버스 ETF 미상장 구간 port_vol=0 폴백 잔존. TEST는 전 종목 데이터 존재라 λ 활성.)

(위 수치는 이번 세션의 라이브 비중 수정[Stagflation·Goldilocks] 반영 후 값 — 이전 문서들의 Martin 3.97/Ulcer 3.68과 다른 건 비중 변경 탓이지 λ 탓 아님.)

## 진단 (왜 죽은 손잡이인가)
- `backtest/engine.py:324` 게이트: `if use_portfolio_vol and signal_px_slice is not None:` → 통과(slice 있음).
- `:329` `compute_portfolio_ewma_vol(signal_px_slice, ticker_w, lam=lam)`.
- `portfolio.py:103` `tickers = [t for t in weights if t in prices.columns and weights[t]>0]`. weights=ticker_w(유니버스 키), prices=signal_px(신호 티커) → **tickers=[] → return 0.0**.
- `:330` `eff_vol = port_vol if port_vol>0 else realized_vol` → 항상 realized_vol.
- realized_vol = `compute_features(sig)["realized_vol"]` = `_ewma_vol`(features.py, EWMA_VOL_LAMBDA=0.94 **하드코딩**). config ewma_lambda 미참조.
- ∴ λ를 어떻게 바꿔도 입력 vol 불변 → 결과 byte-identical. **라이브(run.py:653)도 prices=신호티커라 동일하게 폴백 → 괴리 없음(2026-06-17 정정).**

## 함의 / 다음 단계 (사용자 결정 필요)
1. **백테스트 충실화(권장)**: 엔진이 vol 계산에 유니버스 가격 슬라이스를 쓰도록 수정 → 라이브와 일치 + ewma_lambda 스윕 가능. 단 *모든 백테스트의 vol targeting 입력이 SPY프록시→실포트로 바뀌어* 기준선 이동(과거 floor·ladder 결론 재확인 필요). 규칙3·5 대상 — 승인 후 진행.
2. **대안(오염)**: features.py `_ewma_vol` 고정 λ를 스윕 — 그러나 realized_vol은 HMM/RF 레짐 피처이기도 해 레짐 분류까지 흔듦(반응속도 단독 검증 불가).
3. **백테스트 프록시 수용**: SPY realized_vol을 vol targeting 입력으로 받아들이고 반응속도 실험은 보류.

## 한계
- 본 실험은 null이지만 *신호 부재 증거가 아님* — 배선상 손잡이가 비활성. 반응속도 가설은 여전히 미검증.
- **(정정)** 백테스트·라이브 vol targeting 입력 *동일*(둘 다 port_vol=0 폴백 → SPY realized_vol). 이전 '괴리' 서술 철회. 단, vol targeting 자체는 폴백 경로(SPY realized_vol, λ=0.94)로 정상 작동 중 — 죽은 것은 *config ewma_lambda 손잡이*와 *실포트 vol 사용*뿐.
- 단일통화(USD)·단일경로. 라이브 미반영(코드 변경 없음, 진단만).
