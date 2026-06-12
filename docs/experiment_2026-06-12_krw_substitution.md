# 실험: USD→KRW ETF 대체의 수익 영향

- 날짜: 2026-06-12
- 코드: `scripts/experiment_krw_substitution.py`
- 대체(사용자 결정): VTIP→468370(TIPS), XLE→218420(에너지), NVDA→381180(반도체 SOX)
- 백테스트는 자산군 단위·USD 단일통화라 통화 라우팅은 수익에 무관 — 차이는 기초자산(프록시)뿐.
  프록시: 468370→TIP(broad TIPS), 218420→XLE(동일 지수), 381180→SOXX(반도체 바스켓).

## 결과 (2010-01-01~2025-04-30)

| 전략 | CAGR | MaxDD | Sharpe | Ulcer | Martin | 3y최악 | 3y중앙 |
|---|--:|--:|--:|--:|--:|--:|--:|
| baseline(현행) | 10.1% | -9.9% | 0.81 | 2.88 | 2.13 | 3.2% | 8.9% |
| sub_clean(E+TIPS) | 10.1% | -10.2% | 0.80 | 2.88 | 2.11 | 3.2% | 8.6% |
| sub_all(+NVDA) | 9.6% | -10.0% | 0.75 | 2.83 | 1.97 | 2.2% | 8.3% |

## 해석

1. **에너지(218420)+TIPS(468370)는 사실상 수익 동등 — 깨끗한 배관 교체.**
   CAGR 10.1%→10.1%, Sharpe 0.81→0.80, MaxDD -9.9%→-10.2%(TIPS broad 듀레이션이 VTIP short보다
   약간 길어서 생긴 미미한 차이). 218420은 XLE와 같은 S&P500 에너지 지수라 0 영향. 채택 안전.

2. **NVDA→반도체바스켓(381180/SOX)은 ~0.5%p CAGR 비용 — 공짜 아님.**
   CAGR 10.1%→9.6%, Sharpe 0.81→0.75, Martin 2.13→1.97, 롤링3y최악 3.2%→2.2%. 원인: 백테스트
   구간(2010~2025) NVDA가 역사적 폭등주라, 30종 반도체 바스켓으로 바꾸면 그 *개별 알파*를 잃는다.
   **단 이건 후행(hindsight) 효과** — NVDA의 실현 초과수익은 사후적으로 큰 것이고, *앞으로도*
   반복된다는 보장은 없다. 바스켓은 단일종목 집중위험을 크게 낮춘다. 따라서 이 -0.5%p는
   "과거에 NVDA가 운 좋게 폭등했던 몫을 분산으로 맞바꾼 비용"이지, 미래 손실 예측이 아니다.
   → **분산 vs NVDA 개별 확신**의 선택. 확신 없으면 바스켓이 위험조정상 합리적.

## 라이브 적용 시 구조적 이슈 (백테스트로는 안 보임)

백테스트는 프록시만 바꾸면 되지만, **라이브에서 진짜 KRW-native로 만들려면**
`derive_account_weights`의 통화 라우팅(자산군 *하드코딩*) 수정이 필요하다:
- bond_tips·equity_sector는 단일티커 자산군이라 KRW 라우팅으로 이동 가능(중간 난이도).
- **equity_individual은 4종목(NVDA+LLY+PLTR+TSLA) 혼합 자산군** — NVDA만 KRW로 빼려면 자산군을
  분할(예: equity_individual 3종목 + 신규 KRW 반도체 클래스)해야 함. regime_targets 6개 레짐
  구조 변경 수반 = 큰 수술.
- universe의 currency 필드만 바꿔선 **라우팅이 안 바뀐다**(클래스 기반). 반드시 함수 수정 동반.

## USD 40% 선충전과의 상호작용 (주의)

사용자가 USD를 40%로 끌어올리면 현행 Goldilocks USD 수요(0.40)와 일치 → 합성 거의 소멸.
그런데 위 대체가 USD 수요를 *줄이면*(NVDA 0.06 등 KRW화) 수요<공급 → **USD 초과분 발생**.

**해결(2026-06-12 채택):** 기존 `잔여 USD 비례 확대`(초과분을 리스크자산에 비례 부풀림)를
제거하고, **초과 USD를 SGOV(iShares 0-3M T-Bill)로 보존**하도록 변경
(`derive_account_weights`). 새 자산군 `cash_usd`(regime_targets 미포함, USD waterfall 잔여
흡수, routing SGOV 1.00, 백테스트 프록시 BIL). 효과: USD를 40%로 과충전해도 초과분이
리스크자산을 부풀리지 않고 단기 T-Bill로 단기금리(~연4%)를 수취하며 대기. USD 계좌는
여전히 99% 투자(1% reserve). 검증: Goldilocks USD 50% 충전 시 SGOV가 USD 계좌의 19% 흡수,
NVDA·DBC는 레짐 수요 그대로(부풀림 0). 이로써 "대체로 USD 수요↓"와 "USD 충전"의 충돌이
해소 — 목표 USD를 정밀 재산정하지 않아도 초과분은 안전 대기한다.

## 권고

1. **에너지(218420)+TIPS(468370): 채택 진행** — 수익 동등, 단일티커 클래스라 라우팅 이동 깔끔.
   백테스트 동등성 확인됨([[experiment-2026-06-11-bond-krw-consolidation]] 패턴).
2. **NVDA→381180: 비용(-0.5%p) 확인 후 사용자 재확인 권장** — 분산이 목적이면 합리적이나
   공짜가 아님. core+satellite(core50)에서 NVDA를 satellite 알파로 유지하는 선택지와 비교.
3. **USD 목표는 대체 후 수요에 맞춰 재산정** — 40% 고정이 아니라 대체 반영한 레짐별 수요로.

## 적용 결과 (2026-06-12 라이브 반영 — 에너지+TIPS만, NVDA 유지)

사용자 결정: **에너지(218420)+TIPS(468370)만** KRW-native화, **NVDA는 satellite 알파로 USD 유지**.
라우팅 수술까지 포함해 라이브 config·코드에 반영 완료:

- `trading/config.yaml`: universe XLE→218420·VTIP→468370(둘 다 currency/exec_account KRW),
  asset_routing equity_sector→{218420:1.00}·bond_tips→{468370:1.00}, synthetic_pairs에서
  XLE·VTIP 합성쌍 제거(KRW-native라 합성 불필요).
- `trading/portfolio.py` `derive_account_weights`: equity_sector를 USD 2a waterfall에서,
  bond_tips를 USD 3순위에서 제외 → KRW 직접 라우팅 블록(gold·bond_krw·cash와 동렬)으로 이동.
- `backtest/data.py` PROXY_MAP: 218420→XLE, 468370→TIP 영구 추가.

라이브 config 풀 백테스트(2010~2025): **CAGR 10.2% / MaxDD -10.7% / Sharpe 0.81 / Martin 2.04 /
3y최악 2.7%** — baseline(10.1%/-9.9%/0.81/2.13) 대비 수익·Sharpe 동등, MaxDD만 broad-TIPS
듀레이션으로 ~0.8%p 깊음. **깨끗한 배관 교체로 확인.** Reflation 타깃 라우팅 점검도 통과
(218420·468370이 KRW 계좌로, XLE/VTIP 누수 0).

## 한계

- 218420 합성형 괴리·유동성, 468370 헤지여부는 매수 전 재확인.
- NVDA→SOX 비용은 표본구간 NVDA 실현수익에 민감(hindsight). 미래 보장 아님 — 그래서 NVDA는 USD 유지.
- USD 40% 선충전 시 대체로 USD 수요가 줄어 초과분 발생 가능(§USD 40% 선충전과의 상호작용) —
  목표 USD는 대체 후 레짐별 수요로 재산정 권장.
