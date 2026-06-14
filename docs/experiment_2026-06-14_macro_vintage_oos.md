# 매크로 point-in-time(vintage) 검증 — 수정값 누수는 작다, walk-forward 결론 유지 (처방 b)

> **요약**: ① 비판 리뷰 처방 (b)("라벨을 point-in-time으로 검증")를 실측 — 현재 백테스트는 FRED *최신 수정값*을 쓰는데(publication lag는 적용됐으나 그건 *언제* 알았나만 보정, *값*은 최종 수정값이라 그 당시 몰랐던 정보가 레짐 라벨에 새어듦), ALFRED `get_series_all_releases`로 CPI·실업률·M2·Fed자산의 *첫 발표값*(vintage)을 재구성해 동일 변환·동일 pub-lag·동일 엔진(core30·vol·hmm 전부 ON)에 흘려 최신값 백테스트와 1:1 비교했다(NFCI는 API 10만행 상한으로 vintage 불가 → 양쪽 최신값 고정). ② **검증창(OOS 2019~2025) Martin이 3.80→3.70(ΔMartin −0.10·CAGR −0.2%p)로 거의 안 떨어지고, 레짐 라벨(acting=rule) 일치율이 전체 98.2%·검증창 98.0%** — M2 YoY가 평균 0.42로 가장 크게 수정됐는데도 라벨·지표가 미동(불일치 71일도 대부분 Goldilocks↔Slowdown 인접 전환). 게다가 첫 발표값은 *가장 발산이 큰 하한*(이후 알게 된 소수정 미반영)이라 **진짜 point-in-time 누수는 −0.10보다도 작다**. ③ **결론: 데이터 수정 누수는 무시 가능 — 백테스트·walk-forward 결론 강건(vintage OOS Martin 3.70도 정적 B 2.83·B2 2.87·모든 shrink λ를 여전히 압도).** 라이브는 이미 실시간(=vintage)값을 쓰므로 누수는 *백테스트에만* 있고 그 과대평가폭이 작다 → 라이브 변경 불필요, config 변경 없음(진단). 주의: 첫 run은 NFCI가 vintage서 통째 누락돼 −0.43으로 오인 → NFCI 통제 후 −0.10이 진짜 값.

- 날짜: 2026-06-14
- 코드: `scripts/macro_vintage_oos.py` (진단 — 엔진/config 변경 없음, FRED 원시값만 vintage로 monkeypatch)
- 기간: 2010-01-01 ~ 2025-04-30, split=2019-01-01, USD 단일통화, drift 리밸, core30·vol·hmm ON
- 동기: 비판 리뷰 처방 (b) 실측 — 사용자 "B 해줘"(2026-06-14)
- 선행: [[experiment_2026-06-14_walkforward_shrink_oos]](처방 c 기각·스택 OOS 우위) · [[experiment_2026-06-14_regime_target_shrink]]

## 방법 (vintage 격리)

- REVISABLE = {CPIAUCSL, UNRATE, M2SL, WALCL}만 `get_series_all_releases`에서 각 reference date의 *첫 발표값*(min realtime_start) 재구성 → `fetcher._fred_get_series` monkeypatch → 실제 `fetch_fred_history`가 동일 변환(YoY·3m변화·z-score)·동일 pub-lag를 그대로 적용. 변환 코드 중복 없음.
- 시장기반 일별(T5YIE·BAA10Y·T10Y2Y)은 사실상 무수정 → 원본 그대로(차이 0.0000 확인).
- **NFCI 제외**: `get_series_all_releases`가 10만행 상한에 걸려 1976년까지만 반환(주별 전체이력 수정으로 vintage 폭증) → 백테스트 구간 미포함. 양쪽 다 최신값 고정(차이 0.0000)해 4대 경제지표만 깨끗이 비교.

## 결과

### 데이터 차이 (vintage vs 최신값, 평균 절대차)
| 컬럼 | 평균 절대차 |
|---|--:|
| m2_yoy | **0.4226** |
| cpi_mom_zscore | 0.2667 |
| unrate_chg_3m | 0.0828 |
| cpi_yoy | 0.0700 |
| fed_bs_yoy | 0.0145 |
| breakeven_5y·hy_spread·nfci·curve_10y2y | 0.0000 (무수정/통제) |

### 학습창 TRAIN 2010-01 ~ 2018-12 (in-sample)
| 데이터 | Martin | CAGR | Ulcer | 최장UW | 롤3y최악 | MaxDD | tx |
|---|--:|--:|--:|--:|--:|--:|--:|
| 최신수정(현행) | 1.58 | 8.8% | 3.03 | 342 | 4.8% | -10.8% | 1.99% |
| vintage(첫발표) | 1.45 | 8.5% | 3.10 | 628 | 4.3% | -11.1% | 1.99% |

ΔMartin = −0.13.

### 검증창 TEST 2019-01 ~ 2025-04 (OUT-OF-SAMPLE, COVID+Bear22 포함)
| 데이터 | Martin | CAGR | Ulcer | 최장UW | 롤3y최악 | MaxDD | tx |
|---|--:|--:|--:|--:|--:|--:|--:|
| 최신수정(현행) | 3.80 | 18.4% | 3.78 | 409 | 11.6% | -14.9% | 1.14% |
| vintage(첫발표) | 3.70 | 18.2% | 3.83 | 401 | 11.1% | -15.8% | 1.25% |

ΔMartin = **−0.10**. 레짐 라벨(acting=rule) 일치율: 전체 **98.2%** · 검증창 **98.0%**.

## 해석

### 1. 수정 누수는 실재하지만 작다
M2 YoY가 평균 0.42로 가장 크게 수정되는데도(M2는 분기 벤치마크 재집계로 사후 변동 큼), 레짐 라벨이 98% 일치하고 OOS Martin이 3.80→3.70(−2.6%)로 미동. 즉 *메커니즘으론* 누수가 있으나(그 당시 몰랐던 수정값이 라벨에 반영) *크기로는* 의사결정을 바꾸지 않는다. 불일치 71일도 대부분 Goldilocks↔Slowdown처럼 비중이 비슷한 인접 레짐 간 전환이라 수익 영향이 작다.

### 2. 첫 발표값은 누수의 *상한* — 진짜 효과는 더 작다
진짜 point-in-time은 "as_of 시점의 가장 최근 vintage"(첫발표와 최신값 사이)인데, 본 실험은 *첫 발표값*만 써 가장 발산이 큰 보수적 하한을 잡았다. 실제로는 발표 후 며칠~몇 주 내 1~2차 수정까지는 알게 되므로, 현실 라이브의 데이터는 첫발표보다 최신값에 가깝다 → **실제 누수폭은 −0.10보다 작다**. 따라서 "무시 가능"은 안전한 결론.

### 3. 누수는 백테스트에만 있고, 라이브엔 없다
라이브(`fetch_fred_data`)는 조회 시점의 실시간값을 쓰므로 *그 순간엔 vintage*다 — 미래 수정값을 볼 수 없다. 최신 수정값 누수는 오직 *과거 재현(백테스트)*에서만 발생하고, 그 과대평가폭이 작다(OOS Martin 0.10). 즉 백테스트가 라이브를 *약간* 낙관하나 기만적이진 않다.

## 결론

- **데이터 수정 누수 무시 가능 — config·라이브 변경 없음.** vintage OOS Martin 3.70도 정적 B(2.83)·B2(2.87)·모든 shrink λ를 여전히 압도 → [[experiment_2026-06-14_walkforward_shrink_oos]]의 "스택은 OOS에서 강건·정적 분산 우위" 결론이 point-in-time 데이터에서도 유지.
- **처방 (b)는 메커니즘으론 타당하나 본 시스템에선 영향이 작다** — 1차 지표를 바꾸지 않으므로 즉효 처방이라기보다 "이미 충분히 견고"의 확인.
- 선택지(낮은 우선순위): 백테스트의 정직성을 위해 vintage 모드를 옵션으로 둘 수 있으나(−0.10), 어떤 결정도 바꾸지 않아 필수 아님.
- 남은 비판 처방: (d) CFNAI 외부지표 교차검증만 미실험. 회전은 실행 손질([[experiment_2026-06-14_turnover_stress]]).

## 한계

- **vintage=첫 발표값 근사** — as_of별 정확한 vintage(이후 수정 반영)가 아니라 첫 릴리즈만. 단 §2대로 이는 누수의 *상한*이라 결론(무시 가능)을 강화.
- **NFCI 미검증** — `get_series_all_releases` 10만행 상한으로 vintage 불가, 양쪽 최신값 고정. NFCI는 주별 사후수정이 있어(금융여건지수) 별도 검증 시 per-as_of 호출 필요(미실시). 단 NFCI는 위기감지 보조신호라 4대 경제지표만큼 라벨을 지배하지 않음.
- **TEST 6.3년 단일 경로**·USD 단일통화·무마찰. 첫 run의 −0.43은 NFCI 누락 교란(통제 후 −0.10이 진값).
