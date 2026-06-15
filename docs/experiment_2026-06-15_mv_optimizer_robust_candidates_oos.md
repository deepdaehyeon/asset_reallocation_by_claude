# 레짐별 MV 최적화기 진단 → 견고 후보 워크포워드 OOS 검증

## SUMMARY
- **무엇을**: 레짐별 자산군 평균-분산(MV) 최적화기를 진단용으로 돌려 방향을 보고(`scripts/regime_mv_optimizer.py`), MV·동시점 진단이 *함께* 가리킨 견고 후보 2건을 워크포워드 OOS(TRAIN 2010–2018 / TEST 2019–2025, 4지표)로 검증(`scripts/walkforward_robust_candidates_oos.py`).
- **핵심 수치**: C2(Stagflation commodity 18%→10%, 차이 bond로)는 **학습창 ΔMartin +0.22**로 좋아 보였으나 **검증창 ΔMartin −0.27**, 회복기간 409→**560일**(+151일 악화) — 전형적 과적합. C1(Crisis bond_tips 10%→bond)은 학습 +0.00·검증 −0.01로 엔진이 완전 흡수(무변화).
- **채택 여부·결론**: **둘 다 미채택, 현행 유지**. MV가 in-sample에서 가리킨 방향은 OOS에서 무가치(C1) 또는 역효과(C2). per-regime 비중 미세튜닝은 노이즈라는 결론([[feedback-regime-targets-no-tuning]])을 OOS로 재확인. 최적화기 출력은 처방이 아니라 방향 진단으로만 유효.

## 배경
사용자 요청 흐름: 레짐별 자산군 수익률·비중·상관관계를 모두 확보한 뒤 "이를 종합해 최적
포트폴리오를 뽑자". AskUserQuestion에서 "둘 다 (최적화기 → OOS 검증)" 선택 — 먼저 MV
최적화기로 방향을 보고, 그중 견고한 후보만 walk-forward OOS로 검증하는 경로.

## 1단계: MV 최적화기 진단 (`scripts/regime_mv_optimizer.py`)
- 자산군 단위 long-only MV: maximize μ'w − λ·w'Σw − γ·||w−w_current||² s.t. w≥0, Σw=1, w≤cap.
- 강한 정규화: 가중치 캡 30%, Σ 대각 수축 δ=0.3, 현재비중 L2 앵커 γ=5.0. 개별주식 제외(생존편향).
- μ는 추정오차에 극단 반응(error maximizer)이라 정규화 필수. 그래도 처방 아님.

**관찰된 방향(앵커로 현재비중 근처에서만 넛지)**:
- Stagflation commodity: 연수익 −63%인데 현재 23%(정규화) → MV 일관 축소(19~21%). **축소 방향**.
- Crisis tips→bond: tips 연수익 −2%·주식상관 −0.07(약한 분산자), bond +13%·−0.44(강한 분산자) → MV가 bond를 25~26%로 넛지. **tips→bond 방향**.
- Goldilocks gold: 연수익 ~0%인데 MV는 17%로 *상향* — 분산효과(낮은 상관) 때문. 그러나 4지표
  Martin은 −0.14(낙폭 벌점). **MV(분산기반) ≠ 우리 판단기준(Martin)** → MV가 처방이 못 되는 직접 증거.

동시점 진단([[experiment_2026-06-13_regime_targets_contrast]])과 MV가 *동시에* 가리킨 2건만 OOS로 승격:
- **C1** [Crisis] bond_tips 10% → bond_krw
- **C2** [Stagflation] commodity 18% → 10% (차이 8%p를 bond_krw로)

## 2단계: 워크포워드 OOS (`scripts/walkforward_robust_candidates_oos.py`)
하니스 = `walkforward_shrink_oos.py` 재사용. 각 config 2010~2025 1회 실행(전구간 고정 →
2019~ 수익은 config 선택의 진짜 OOS), drift 모드(라이브 동일), 수익을 TRAIN/TEST로 슬라이스.

| 구간 | 전략 | Martin | CAGR | Ulcer | 최장UW | 롤3y최악 | MaxDD |
|---|---|---|---|---|---|---|---|
| TRAIN | 현행 | 1.58 | 8.8% | 3.03 | 342 | 4.8% | −10.8% |
| TRAIN | C1 | 1.58 | 8.8% | 3.03 | 342 | 4.8% | −10.8% |
| TRAIN | C2 | **1.80** | 9.2% | 2.86 | 339 | 5.5% | −9.7% |
| TRAIN | C1+C2 | 1.81 | 9.2% | 2.86 | 339 | 5.5% | −9.7% |
| TEST | 현행 | **3.80** | 18.4% | 3.78 | **409** | 11.6% | −14.9% |
| TEST | C1 | 3.79 | 18.3% | 3.78 | 409 | 11.6% | −14.7% |
| TEST | C2 | **3.53** | 17.9% | 3.95 | **560** | 11.2% | −14.6% |
| TEST | C1+C2 | 3.53 | 17.9% | 3.95 | 560 | 11.2% | −14.4% |

## 해석
- **C2 = 과적합**: 학습창 ΔMartin +0.22로 매력적이나 검증창 ΔMartin −0.27 + 회복기간
  409→560일(+151일 더 물밑). 1지표(Martin)·회복기간 동시 악화 → 명백한 in-sample 적합 착시.
- **C1 = 엔진 흡수**: TRAIN +0.00·TEST −0.01. vol targeting·class cap·core30·drift가 비중차를
  완전히 흡수. MaxDD만 −14.9→−14.7로 미세 개선이나 Martin·회복기간 무변화 → 회전 들일 가치 없음.
- 두 결과 모두 [[feedback-regime-targets-no-tuning]]의 OOS 재확인: 정밀 per-regime 비중은
  파이프라인을 통과하며 희석되거나, in-sample 개선이 OOS에서 역전된다.

## 한계
- TEST 6.3년 단일 경로(COVID·Bear22 각 1회). Crisis·Stag은 TEST 내 일부 구간만 발생.
- MV는 자산군 단위·long-only·동시점 in-sample(평균회귀 꼬리 포함). 개별주식 생존편향으로 제외.
- 진단이지 처방 아님 — 가치는 "현행이 OOS에서 견고하다"는 음성 확인.
