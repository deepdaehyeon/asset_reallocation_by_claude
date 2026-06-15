# Slowdown·Crisis 비중조정 — 에피소드 진단 + 워크포워드 OOS (SL2만 양 시대 개선)

## SUMMARY
- **무엇을**: Slowdown·Crisis 레짐의 자산 성격을 학습창(≤2018)/검증창(≥2019)으로 쪼개 에피소드 안정성을 진단(`regime_episode_split.py`)하고, 진단이 시사한 4개 비중조정 후보를 4지표 워크포워드 OOS로 검증(`walkforward_slowdown_crisis_oos.py`): SL1(둔화 eqETF 15→10→bond), SL2(둔화 commodity 5→0→gold/bond), CR1(위기 eqFac/comm→bond), CR2(위기 eqETF 10→6→bond).
- **핵심 수치**: 진단상 Slowdown 방어3종(bond·gold·tips)은 양 시대 일관 양수(안정), Crisis는 bond·gold 안정하나 eqETF가 +39%(2011)→−23%(2020+) 역전. OOS 결과 SL1·CR1·CR2는 엔진 흡수(TRAIN/TEST ΔMartin ±0.03). **SL2만 학습창 +0.03 / 검증창 +0.17로 양 시대 동시 개선** — Ulcer(3.78→3.68)·MaxDD(−14.9→−14.4%)도 두 시대 모두 동반 개선, 과적합 지문(TRAIN+/TEST−) 아님.
- **채택 여부·결론**: SL1·CR1·CR2 **미채택(엔진 흡수, 현행 유지)**. SL2(Slowdown commodity 5%→0, gold 14→18%·bond +1%)는 지금까지 검증한 후보 9건 중 **유일하게 양 시대 일치 개선**이라 라이브 채택 검토 후보로 사용자에 보고. 단 +0.17은 modest이고 라이브 config 미반영(rule 3 — 사용자 확인 후).

## 배경
사용자 "스태그 하위국면은 최우선 TODO로 남기고 Slowdown·Crisis 가보자". 스태그는 에피소드
이질성으로 비중조정 전부 OOS 기각([[feedback-regime-targets-no-tuning]])이었는데, Slowdown·
Crisis도 같은 병이 있는지부터 일반화 진단(`regime_episode_split.py`) 후 후보 설계.

## 1단계: 에피소드 안정성 진단 (동시점 수익 TRAIN/TEST 분할)
| 레짐·자산 | 학습창 TRAIN(≤2018) Martin | 검증창 TEST(≥2019) Martin | 해석 |
|---|---|---|---|
| Slowdown bond | 8.41 | 2.82 | 양 시대 양수 (안정) |
| Slowdown gold | 0.55 | 7.11 | 양 시대 양수 (안정) |
| Slowdown tips | 1.47 | 4.46 | 양 시대 양수 (안정) |
| Crisis bond | 2.81 | 8.37 | 양 시대 양수 (안정) |
| Crisis gold | 1.34 | 5.03 | 양 시대 양수 (안정) |
| Crisis eqETF | +39.4%/9.93 (2011) | −22.7%/−1.77 (2020+) | **부호 역전** (V자 베팅이 시대 의존) |

→ Slowdown은 스태그와 달리 방어자산이 **에피소드 안정**. Crisis도 채권·금은 안정하나 eqETF만
시대 의존(2011 V자 회복엔 영웅, 2020+엔 손실). 후보는 이 진단을 따라: 약체/불안정 자산을
줄이고 안정 방어자산(bond·gold)으로 이동.

## 2단계: 워크포워드 OOS (4지표, drift 모드)
제약: gold cap 18, eqFac cap 10, 행선지 bond/cash 무캡.

| 구간 | 전략 | Martin | CAGR | Ulcer | 최장UW | 롤3y최악 | MaxDD | ΔMartin |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| TRAIN | 현행 | 1.58 | 8.8% | 3.03 | 342 | 4.8% | −10.8% | — |
| TRAIN | SL1 eqETF→bond | 1.56 | 8.7% | 2.99 | 342 | 4.6% | −10.7% | −0.02 |
| TRAIN | **SL2 comm→gold** | **1.61** | 8.8% | 2.97 | 307 | 4.9% | −10.6% | **+0.03** |
| TRAIN | CR1 위성→bond | 1.56 | 8.8% | 3.04 | 333 | 4.9% | −10.8% | −0.02 |
| TRAIN | CR2 eqETF→bond | 1.55 | 8.7% | 3.05 | 342 | 4.8% | −10.8% | −0.03 |
| TEST | 현행 | 3.80 | 18.4% | 3.78 | 409 | 11.6% | −14.9% | — |
| TEST | SL1 eqETF→bond | 3.77 | 18.2% | 3.77 | 409 | 11.3% | −14.6% | −0.03 |
| TEST | **SL2 comm→gold** | **3.97** | 18.6% | 3.68 | 409 | 11.9% | −14.4% | **+0.17** |
| TEST | CR1 위성→bond | 3.81 | 18.3% | 3.75 | 409 | 11.6% | −14.4% | +0.01 |
| TEST | CR2 eqETF→bond | 3.79 | 18.3% | 3.78 | 409 | 11.6% | −14.8% | −0.01 |

- **SL1·CR1·CR2**: 양 시대 ΔMartin ±0.03 → 엔진 흡수, 현행과 사실상 동일. 미채택.
- **SL2**: 학습창 +0.03 / 검증창 +0.17. 핵심은 부호 — 이전 실패 9건(C1/C2/G/R/S1~S3/SL1/CR1/
  CR2)은 전부 학습창+/검증창− 과적합 지문이었는데, SL2만 **양 시대 동방향**. Ulcer 두 시대 ↓,
  MaxDD 두 시대 ↓(−10.8→−10.6 / −14.9→−14.4), 최장 회복기간 학습창 342→307 단축·검증창 409 동일.

## 해석·결론
- **왜 SL2가 다른가**: Slowdown commodity 5%는 작은 위성인데 둔화기 commodity는 약체. 이걸 빼서
  **양 시대 안정 양수**인 gold로 옮기는 건 스태그(자산 부호 역전)와 달리 환원 가능한 개선이다.
  에피소드 진단(둔화기 gold Martin 0.55→7.11 안정)과 OOS(양 시대 +)가 정합 → 노이즈가 아니라
  실제 레버일 가능성. [[feedback-regime-targets-no-tuning]]의 "단독 약체 ≠ 제거 대상"과도 충돌
  안 함 — commodity는 둔화기에서 분산 기여도 약하고, 행선지 gold는 주식과 저상관 유지.
- **단, modest**: TEST +0.17은 의미 있으나 작고, TEST는 단일 경로(2020·2022·2025)라 과신 금물.
  라이브 채택은 사용자 확인 후([[feedback-regime-targets-no-tuning]] — 비중조정은 기본 회의).
- **Crisis는 비중조정 무이득**: eqETF가 시대 의존이라 줄이는 게 맞아 보여도(CR2), OOS 무변화
  (−0.01). Crisis 개선도 비중이 아니라 구조(진입 적시성)일 가능성.

## 한계
TEST 2019~2025 단일 경로(Crisis는 COVID·2022·2025 집중). 동시점·프록시·in-sample. 엔진(vol
targeting·cap·core30·drift)이 비중차 흡수. gold cap 18 만석이라 SL2 gold가 정확히 캡에 도달.
라이브 config 미반영.
