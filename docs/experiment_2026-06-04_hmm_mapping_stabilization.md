# HMM label-switching 정렬(stabilize_mapping) — 가짜 레짐 플립 차단

> **요약**: ① 매 실행 HMM 재학습 시 state→regime 매핑이 뒤집혀(Slowdown↔Goldilocks 100% 극단 플립) 불필요한 왕복 회전이 발생하는 원인을 추적하고, Hungarian 최적 매칭으로 직전 군집 anchor에 정렬하는 stabilize_mapping을 구현해 deadband(0.3~1.0) 스윕으로 검증했다. ② db=0.3에서 Sharpe +0.053·Calmar +0.033·tx누적 4.87%→4.57% 개선을 달성하면서 MaxDD·COVID·Bear22는 ±0.07pp 불변으로, 가짜 플립만 제거하고 진짜 방어 전환은 보존했다. ③ db=0.3 채택(`hmm.stabilize_mapping: true, hmm.mapping_deadband: 0.3`) — 리스크 무손실 + 회전·비용 개선이 dominant하며, db 상향은 Sharpe는 오르지만 낙폭이 크게 붕괴돼 기각됐다.

- **일자**: 2026-06-04
- **스크립트**: `scripts/validate_mapping_stabilization.py`
- **구간**: 2010-01-01 ~ 2025-04-30 (current config, timing_source=rule)
- **코드 변경**: regime.py(정렬 로직)·run.py·engine.py·config.yaml

## 배경 — 진단(approach A)

라이브에서 같은 날 아침/저녁 HMM 예측이 **Slowdown 100% ↔ Goldilocks 100%**로 극단적으로
뒤집히는 현상을 추적. 원인:

- HMM fit은 시드 고정(42/7/13)이라 **입력이 같으면 결정적**이나, 매 실행 `fetch_signal_prices`로
  최신 데이터를 새로 받아 feature_matrix가 미세하게 달라지고 **5-state HMM을 처음부터 재학습**.
- 재학습하면 비지도 state→regime 매핑(`_unsupervised_state_mapping`)이 성장 score **중앙값 분할**로
  라벨을 다시 부여 → 경계 근처 state가 Goldilocks↔Slowdown으로 뒤집힘(label-switching).
- monitor.log "매핑: state 변화 3~5/5"가 대부분 실행에서 관측 — 매핑이 매번 갈아엎어짐.
- 결과: blend 비중이 흔들리고(blend α=0.5 평활로도 잔여 스윙이 drift 1.5%를 넘김), 379800 같은
  종목이 며칠 새 매수→매도 **왕복 회전**. 시장이 움직인 게 아니라 **라벨이 뒤집힌 것**.
- 백테스트(`_run_drift`)도 리밸 시점마다 재학습하므로 day-to-day churn은 in-sample. 백테스트가 못 보는 건
  라이브의 **하루 2회(아침+저녁) 재학습**(종가 1회만 읽음).

## 처방 — approach 2: anchor 정렬

재학습은 그대로 두되 **라벨만 안정화**. 재학습 후 새 state 군집 중심(raw 피처 평균)을 직전 실행의
군집(anchor)에 1:1 매칭(Hungarian, scipy). 정규화 거리(피처 std 단위 RMS) ≤ `mapping_deadband`면
**직전 라벨 물려받음** → 데이터가 거의 안 변했을 때 가짜 플립 차단. 군집이 크게 이동하면 새 비지도
라벨 채택. **Crisis는 정렬 제외**(변동성 기반, 위기 감지 보존). 비지도 매핑 성공 시에만 적용(legacy 폴백 무관).
anchor는 run.py=state.json, engine=일별 루프 인스턴스 속성으로 누적.

## 결과 — deadband sweep

| cell | CAGR | Sharpe | MaxDD | Calmar | 리밸 | tx누적 | COVID | Bear22 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| off (현행) | 9.8% | 0.75 | -9.9% | 0.99 | 541 | 4.87% | -9.1% | -8.2% |
| **on db=0.3** ◀ 채택 | 10.1% | **0.80** | **-9.9%** | **1.02** | 534 | 4.57% | -9.2% | -8.2% |
| on db=0.5 | 10.5% | 0.84 | -11.1% | 0.95 | 527 | 4.34% | -11.1% | -8.3% |
| on db=0.75 | 11.3% | 0.90 | -11.1% | 1.01 | 519 | 4.18% | -11.1% | -9.4% |
| on db=1.0 | 10.4% | 0.80 | -11.9% | 0.88 | 508 | 4.00% | -11.9% | -9.4% |

**델타(on−off)**:

| cell | ΔSharpe | ΔMaxDD | ΔCalmar | ΔCOVID | ΔBear22 |
|---|---:|---:|---:|---:|---:|
| **db=0.3** | **+0.053** | **-0.06pp** | **+0.033** | **-0.07pp** | **-0.00pp** |
| db=0.5 | +0.092 | -1.26pp | -0.040 | -1.98pp | -0.07pp |
| db=0.75 | +0.150 | -1.26pp | +0.023 | -1.98pp | -1.20pp |
| db=1.0 | +0.058 | -2.02pp | -0.114 | -2.74pp | -1.16pp |

## 핵심 발견

1. **db=0.3이 sweet spot.** Sharpe +0.053·Calmar +0.033·리밸 541→534·tx누적 4.87→4.57%인데
   **MaxDD·COVID·Bear22가 사실상 불변(±0.07pp)**. 가짜 플립만 제거하고 진짜 방어 전환은 보존 →
   리스크 회피 목표에 부합하는 거의 dominant 개선.
2. **deadband를 키우면 Sharpe/CAGR는 더 오르지만 낙폭이 붕괴.** db=0.75는 Sharpe +0.150이나
   MaxDD -1.26pp·COVID -1.98pp·Bear22 -1.20pp. 큰 deadband는 **방어 전환(Goldilocks→Slowdown)까지
   얼려** equity 노출을 과하게 유지 → vol targeting/blend 방어 엔진과 같은 트레이드오프
   ([[project-voltarget-blend-defense-engine]]). Sharpe 극대화가 목표가 아니므로 기각.
3. **백테스트 한계.** `_run_drift`는 종가 1회만 읽어 라이브의 아침/저녁 2회 재학습 플립을 직접 측정 못 함 →
   여기 수치는 day-to-day 효과만. 라이브의 실제 왕복 억제 효과는 이보다 클 것으로 기대(직접 측정은 라이브 로그 추적 필요).

## 채택

- `hmm.stabilize_mapping: true`, `hmm.mapping_deadband: 0.3`.
- 근거: 리스크(MaxDD/위기 낙폭) 무손실 + Sharpe·Calmar·회전·비용 개선. 백테스트↔라이브 정합 유지(둘 다 적용).

## 한계

- 단일 구간 in-sample sweep. anchor가 한 번 틀어지면 그 오류가 이어질 수 있음(닻을 직전 실행으로 둔 설계 선택).
- 라이브 아침/저녁 2회 재학습 자체는 그대로 — 정렬은 그 사이 라벨 일관성만 회복. 2회 재학습을 1회로 줄이는
  것(approach 3)은 별도 사안.
