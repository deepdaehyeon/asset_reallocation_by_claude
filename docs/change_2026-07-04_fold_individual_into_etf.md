# 개별주(satellite) 접기 — equity_individual → equity_etf 통합

> **요약**: ① 개별주(NVDA/TSLA/PLTR/LLY = equity_individual)는 USD 전용·KRW 대체재 없음이라, 방어/USD과잉 국면에서 USD 계좌 여력(≈37%)을 초과해 오버플로(→KRW equity_etf)됐다가 USD 여유가 생기면 되돌아가는 통화 왕복 wash를 유발할 구조였다. ② 개별주 비중을 전 레짐에서 equity_etf(지수)로 이관하면 core30(30% Goldilocks 고정) 덕에 **USD 수요 최악이 47%→35.5%로 눌려 계좌(37%) 안에 갇히고**(모의: Reflation 블렌드 USD 배정 36.6%, SGOV 여유 3%, 오버플로 없음), 개별주↔지수(다른 자산, 상계 불가) churn이 지수 KRW↔USD(같은 자산, equivalence 상계)로 전환돼 wash 가능성이 구조적으로 제거된다. ③ **채택** — 개별주 satellite 알파를 포기하는 전략 변경이나, 사용자 1순위(하락 회피·장기보유·마찰 제거)에 부합하고 알파는 미검증(hindsight). 개별주는 **별도 계좌에서 수동 관리**하며 이 시스템은 미관리. 백테스트는 usd_ratio 고정이라 이 이득을 못 봐 판정 부적절.

## 배경

- 이전 진단: 계좌분리(KRW/USD)에서 USD 수요 > 계좌면 waterfall이 저우선 USD자산을 KRW equity_etf로
  오버플로. USD 여유가 다시 생기면 되돌림 → 통화 왕복 churn. 개별주(satellite)는 그중에서도
  KRW 대체재가 전무해 wash의 핵심 소지였다([[feedback-illiquid-fix-over-swap]] 관련 논의).
- 사용자 통찰: 개별주를 지수로 접으면 **core30(30% Goldilocks 고정, USD 25%)** 때문에 블렌드가
  Reflation(40%)로 쏠려도 `0.3×25 + 0.7×40 = 35.5% < 37%` → 오버플로 자체가 안 생김.
- 부분 축소로는 불가(37% 밑으로 넣으려면 individual ≤ ~1.5%) → 사실상 전량 접기(all-or-nothing).

## 변경 (config.yaml + portfolio.py + run.py)

- regime_targets 5개: equity_individual 비중을 equity_etf로 이관
  (G 0.15→etf 0.62 / Refl 0.10→0.32 / Slow 0.10→0.25 / Stag 0.08→0.13 / Crisis 0.05→0.15). 합계 1.0 유지.
- universe에서 NVDA/TSLA/PLTR/LLY 제거. asset_routing.equity_individual 제거.
  synthetic_pairs에서 4종목 제거. class_max_weight.equity_individual 제거.
  vol_targeting·risk.equity_asset_classes에서 equity_individual 제거.
- portfolio.py derive_account_weights: USD waterfall Priority 2a `core_eq=("equity_factor",)`
  (individual 제거), all_eq_classes·usd_eq_allocated에서 equity_individual 제거.
  apply_dynamic_class_caps VIX 축소 대상에서 equity_individual 제거.
- run.py: risk.equity_asset_classes fallback 기본값에서 equity_individual 제거.

## 검증 (모의)

- 5개 레짐 목표 합계 = 1.000 ✓. 개별주 종목·참조 잔존 0 ✓. routing↔universe 정합 ✓. 코드 컴파일 ✓.
- Reflation 100% 블렌드 + core30, USD37/KRW63: **USD 실제배정 36.6% < 37%**, SGOV 여유 0.031,
  VEA/VWO는 USD에 정상 배정(오버플로 없음), `[USD 부족 대체]` 미발동 ✓. → 오버플로·wash 구조 제거 확인.

## 전환(라이브)

- 라이브는 로컬 config.yaml 직접 read → 변경 즉시 유효. 현재 보유 NVDA/TSLA/PLTR/LLY(≈13%·2,800만원)는
  다음 USD 실행(2026-07-07 화 03:00)에 **유니버스 외 종목으로 자동 전량 매도 → 지수 재배치**.
  사용자 A안 채택(2026-07-04): 시스템이 팔고 재배치, 개별주는 별도 계좌에서 새 자금으로 수동 운용.

## 한계

- USD 계좌가 ≈35% 이상 유지돼야 오버플로 방지 성립(현재 37%, 여유 ~1.5%p로 빠듯). 환전으로 USD를
  과도히 낮추면 오버플로 재발 가능 — USD 35%+ 유지 권장.
- 알파 포기: 과거 실험서 탈알파는 Martin −0.16(hindsight, [[docs/experiment_krw_substitution]] 계열).
- 백테스트 판정 부적절: usd_ratio=0.30 고정이라 오버플로·wash(라이브 전용)를 못 봄
  ([[project-live-turnover-vs-backtest-gap]]).
