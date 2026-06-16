# vol targeting 반응속도(ewma_lambda) 스윕 — 결과 null이나 '신호 부재'가 아니라 백테스트 배선 결함(라이브↔백테스트 괴리) 발견

## SUMMARY
- **무엇을**: 비중 조정이 흡수되는 고원 상태에서 진짜 레버는 vol targeting의 손잡이라는 가설([[project-voltarget-blend-defense-engine]]) 하에, 그중 반응속도 `ewma_lambda`(EWMA 감쇠, 낮을수록 빠름)를 0.97·0.94(현행)·0.90·0.85·0.80으로 스윕해 빠른 디리스킹이 4지표(Ulcer·Martin·회복)를 개선하는지 엔드투엔드 워크포워드 OOS로 검증. core30·drift·drawdown_scaling off 등 라이브 config 고정, ewma_lambda만 교체. `scripts/sweep_voltarget_lambda_oos.py`.
- **핵심 수치**: 5개 λ 전부 **완전 동일**(TEST Martin 3.31·CAGR 18.0%·Ulcer 4.23·MaxDD −15.4%·회복 564, TRAIN Martin 1.58 — 소수점까지 byte-identical). Δ 전부 +0.00. → λ가 백테스트 결과에 **영(0) 영향**.
- **채택 여부·결론**: **가설 미검증(실험 무효) — null의 원인이 배선 결함이라 "반응속도는 무효"로 결론할 수 없음.** 원인: 백테스트는 `_target_weights`에 `signal_px_slice=signal_px.tail(65)`를 넘기는데(engine.py:662·585), `compute_portfolio_ewma_vol`의 weights는 *유니버스 티커*(379800·VTV…) 키이고 `signal_px`는 *신호 티커*(SPY·^VIX·TLT·HYG…)뿐이라 **교집합이 공집합 → port_vol=0 → eff_vol=realized_vol(SPY 기반, features.py `_ewma_vol`의 고정 λ=0.94)로 폴백**. 즉 config의 `ewma_lambda`는 **백테스트에서 죽은 손잡이**(완전 동일 결과가 그 증거). **부수 발견(중요)**: 라이브(run.py:647-653)는 `market["prices"]`=실제 보유(유니버스) 가격으로 port_vol을 계산해 config λ가 살아있음 → **백테스트는 vol targeting 입력을 SPY 광역 프록시로, 라이브는 실제 포트폴리오 EWMA vol로 쓰는 괴리**. 가설을 제대로 검증하려면 백테스트를 라이브와 일치(유니버스 가격 주입)시켜야 하며, 이는 *모든 과거 백테스트의 vol targeting 입력을 바꾸는 엔진 변경*이라 기준선이 이동 → 사용자 승인 필요.

## 결과 (참고 — λ 무관 동일)
| 창 | 설정 | Martin | CAGR | Ulcer | 최장UW | 롤3y최악 | MaxDD | tx |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| TRAIN | λ 전부(0.80~0.97) | 1.58 | 9.1% | 3.21 | 339 | 5.9% | −11.2% | 1.81% |
| TEST | λ 전부(0.80~0.97) | 3.31 | 18.0% | 4.23 | 564 | 10.9% | −15.4% | 1.20% |

(이 수치는 이번 세션의 라이브 비중 수정[Stagflation·Goldilocks] 반영 후 값 — 이전 문서들의 Martin 3.97/Ulcer 3.68과 다른 건 비중 변경 탓이지 λ 탓 아님.)

## 진단 (왜 죽은 손잡이인가)
- `backtest/engine.py:324` 게이트: `if use_portfolio_vol and signal_px_slice is not None:` → 통과(slice 있음).
- `:329` `compute_portfolio_ewma_vol(signal_px_slice, ticker_w, lam=lam)`.
- `portfolio.py:103` `tickers = [t for t in weights if t in prices.columns and weights[t]>0]`. weights=ticker_w(유니버스 키), prices=signal_px(신호 티커) → **tickers=[] → return 0.0**.
- `:330` `eff_vol = port_vol if port_vol>0 else realized_vol` → 항상 realized_vol.
- realized_vol = `compute_features(sig)["realized_vol"]` = `_ewma_vol`(features.py, EWMA_VOL_LAMBDA=0.94 **하드코딩**). config ewma_lambda 미참조.
- ∴ λ를 어떻게 바꿔도 입력 vol 불변 → 결과 byte-identical. 라이브(run.py:653)는 prices=유니버스라 정상 작동 → 괴리.

## 함의 / 다음 단계 (사용자 결정 필요)
1. **백테스트 충실화(권장)**: 엔진이 vol 계산에 유니버스 가격 슬라이스를 쓰도록 수정 → 라이브와 일치 + ewma_lambda 스윕 가능. 단 *모든 백테스트의 vol targeting 입력이 SPY프록시→실포트로 바뀌어* 기준선 이동(과거 floor·ladder 결론 재확인 필요). 규칙3·5 대상 — 승인 후 진행.
2. **대안(오염)**: features.py `_ewma_vol` 고정 λ를 스윕 — 그러나 realized_vol은 HMM/RF 레짐 피처이기도 해 레짐 분류까지 흔듦(반응속도 단독 검증 불가).
3. **백테스트 프록시 수용**: SPY realized_vol을 vol targeting 입력으로 받아들이고 반응속도 실험은 보류.

## 한계
- 본 실험은 null이지만 *신호 부재 증거가 아님* — 배선상 손잡이가 비활성. 반응속도 가설은 여전히 미검증.
- 백테스트 vol targeting 입력 ≠ 라이브 입력(SPY프록시 vs 실포트 EWMA). 회복/Ulcer 등 vol 관련 모든 백테스트 결론에 이 괴리가 잠재 영향.
- 단일통화(USD)·단일경로. 라이브 미반영(코드 변경 없음, 진단만).
