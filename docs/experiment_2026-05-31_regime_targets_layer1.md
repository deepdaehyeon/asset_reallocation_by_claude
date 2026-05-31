# 층 1 — regime_targets 가치 검증 (C안 A/B 재측정 + V-shape 편향 재랭킹)

- **일자**: 2026-05-31
- **스크립트**: `scripts/compare_regime_targets_ab.py` (Step 1), `scripts/rerank_regime_targets_robust.py` (Step 2)
- **구간**: 2010-01-01 ~ 2025-04-30 (walk-forward, 유니버스 20종목)
- **코드 변경**: 없음 (진단/시뮬레이션). 결과 적용은 사용자 결정 대기.

## 배경

층 0(엔드투엔드 가치)에서 "레짐 장치의 가치는 CAGR이 아니라 **방어**(위험레짐 적시
디리스킹)에 있다"고 결론. 층 1은 그 방어가 실제 작동하도록 `regime_targets`가 각
레짐의 우위 자산과 정합하는지 검증.

`regime_targets`는 커밋 **6d86b79**에서 Phase 1 진단(`docs/experiment_2026-05-28_
regime_targets_analysis.md`)의 **forward 21일 Sharpe 랭킹**을 반영해 수동조정(C안)됨.
그러나 그 진단 문서 스스로 메타이슈 #2로 경고: *detect_regime은 후행적이라
Crisis/Stagflation 분류 시점이 폭락 바닥 근처 → forward 윈도가 V-shape 반등을 잡아
위험자산에 비현실적 우위를 준다.* 두 단계로 검증한다.

---

## Step 1 — C안의 엔드투엔드 기여 재측정 (현재 엔진 기준)

커밋 6d86b79는 당시 A/B(Sharpe 0.733→0.751)를 기록했으나, **이후 파이프라인이 크게
변경**됨(drawdown scaling 제거 b923dd2, drift 5%→1.5%, regime_change_trigger 제거
38170e5, vol floor 0.50). regime_targets만 pre-C vs 현재로 교체하고 나머지는 현재
라이브 설정으로 동일하게 두어 재측정.

| variant | CAGR | Sharpe | MaxDD | Calmar | COVID DD | Bear22 DD |
|---|---:|---:|---:|---:|---:|---:|
| pre-C (Phase1 이전) | 9.5% | **0.71** | -11.7% | 0.81 | -8.6% | -8.3% |
| current (C안 적용) | 9.1% | 0.67 | **-10.5%** | **0.87** | -8.7% | -8.2% |
| **델타 (current−pre-C)** | **-0.35pp** | **-0.041** | +1.15pp | +0.056 | -0.09pp | +0.09pp |

**위험레짐 체류일 일평균 수익률 (방어 성과)**:

| variant | Crisis | Stagflation | Slowdown |
|---|---:|---:|---:|
| pre-C | +0.031% | +0.032% | +0.014% |
| current | **+0.012%** | **+0.017%** | +0.014% |

**해석**: 현재 엔진에선 C안의 우위가 **사라졌다**.
- Sharpe·CAGR는 오히려 악화(-0.041 / -0.35pp). 개선은 MaxDD/Calmar뿐.
- 결정적으로 **Crisis·Stagflation 체류일 방어가 약화**(+0.031→+0.012%, +0.032→+0.017%).
  C안이 V-shape forward Sharpe 근거로 Crisis에 equity_etf·bond_tips, Stagflation에
  equity_factor를 넣은 것이, 정작 위험한 날 포트폴리오를 덜 방어적으로 만들었다.
  → 층 0의 경고("잘못된 시점에 베타 추가")가 데이터로 확인됨.

---

## Step 2 — V-shape 편향 제거 재랭킹

forward 21일 Sharpe(편향 有) vs **동시점(contemp) Sharpe**(레짐 체류일의 그날 수익률,
반등 선취 없음)를 같이 산출해 랭킹 역전을 찾았다. shift = (forward rank) − (contemp
rank), **음수면 forward가 과대평가**(V-shape 선취 의심).

### V-shape 함정 의심 (forward 과대평가 + 현재 비중 ≥8%)

| 레짐 | 클래스 | forward rank | contemp rank | contempSR | 현재 비중 | C안에서 추가? |
|---|---|:--:|:--:|---:|---:|:--:|
| Crisis | equity_etf | #2 | #6 | **+0.06** | 10% | **예 (0→10)** |
| Crisis | bond_tips | #3 | #8 | -0.31 | 10% | **예 (0→10)** |
| Stagflation | equity_factor | #3 | #11 | **-2.37** | 8% | **예 (3→8)** |
| Reflation | managed_futures | #1 | #10 | +0.52 | 12% | **예 (5→12)** |
| Stagflation | gold | #2 | #5 | +1.13 | 18% | 아니오(기존) |
| Slowdown | equity_etf | #5 | #9 | -0.55 | 15% | 아니오(기존) |
| Slowdown | equity_individual | #2 | #6 | +0.31 | 10% | 아니오 |
| Reflation | equity_individual | #4 | #8 | +0.62 | 10% | 아니오 |

**핵심**: C안이 추가한 risk/trend 비중 4건(Crisis equity_etf·bond_tips,
Stagflation equity_factor, Reflation MF)이 **모두** forward 편향 의심 목록 상단에 있다.
- Crisis equity_etf: forward #2(+2.83)였지만 **contemp #6(+0.06) — Crisis 중엔 사실상 무방어**.
  forward 우위는 100% 반등 선취였다.
- Stagflation equity_factor: forward #3(+1.41)였지만 **contemp #11(-2.37) — Stagflation 중 적극적으로 손실**.

### 방어 자산의 일관된 우위 (contemp 기준)

Slowdown·Stagflation·Crisis 세 방어 레짐 모두 contemp 상위가 **cash / bond_usd /
bond_krw / bond_tips / gold**로 수렴. 특히:
- Stagflation **bond_krw**: contemp #3(+2.65)인데 현재 비중 **0%** (★ 저비중 방어 자산).
- Crisis contemp 상위 = cash #1, bond_usd #2, bond_krw #3 — 방어자산이 동시점에서 진짜로 보호.

### MF 제거는 옳았다

C안의 다른 변경 중 **Crisis MF 12→0% 제거는 정당**: MF는 Crisis contemp #13(-2.19)·
forward #13(-0.32) 모두 최하위. Slowdown MF 12→5% 축소도 contemp #7로 방어 효과 약함.
→ C안이 전부 틀린 게 아니라, **risk/trend "추가"가 V-shape 편향, "제거"는 타당**.

---

## 종합 결론

1. **C안은 현재 엔진에서 더 이상 Sharpe를 개선하지 않고, 위험레짐 일별 방어를 약화시킨다**
   (Step 1). 개선은 집계 MaxDD/Calmar뿐.
2. **원인은 forward-21일 Sharpe 랭킹의 V-shape 편향**(Step 2). C안이 추가한 위험/트렌드
   비중 4건이 모두 동시점 기준으론 우위가 사라지거나 음수다.
3. **방어 레짐은 contemp 기준 cash/국채/금이 일관 우위.** 시스템 목표가 "리스크 회피"이므로
   방어 레짐은 이쪽으로 더 기울어야 하며, V-shape 기반 equity 추가는 역효과.
4. **C안의 "제거"(Crisis/Slowdown MF)는 타당** — 전면 revert가 아니라 선별 조정이 맞다.

## 한계

- **contemp Sharpe도 만능 아님**: 저변동 자산(cash)이 분모 효과로 항상 상위
  (Slowdown cash SR +7.50 등). 절대 랭킹이 아니라 **forward와의 괴리(shift)** 가 신호.
  "전부 현금화"가 아니라 "위험자산의 forward 우위가 동시점에 사라지는가"를 본다.
- 단일 구간 in-sample. selection/regime-lag 편향 잔존. 적용 전 서브기간 교차확인 권장.

---

## Step 3 — C-v2 후보 검증 (가설 기각)

진단대로 C안의 V-shape 기반 추가만 타겟 리버트(MF 제거는 유지)한 C-v2를 구성해
3-way 백테스트. 변경(current 대비):
- Crisis: equity_etf 10→0, equity_factor 6→3, bond_tips 10→5 → cash 28→34, bond_usd 10→16, bond_krw 10→16
- Stagflation: equity_factor 8→3 → bond_krw 0→5
- Reflation: MF 12→5 → commodity 16→20, bond_tips 5→8
- Slowdown: 유지

**스크립트**: `scripts/compare_regime_targets_cv2.py`

| variant | CAGR | Sharpe | MaxDD | Calmar | COVID DD | Bear22 DD |
|---|---:|---:|---:|---:|---:|---:|
| pre-C | 9.5% | **0.71** | -11.7% | 0.81 | -8.6% | -8.3% |
| current (C안) | 9.1% | 0.67 | **-10.5%** | **0.87** | -8.7% | -8.2% |
| C-v2 | 9.0% | 0.66 | -11.4% | 0.79 | **-7.8%** | -8.7% |

**위험레짐 체류일 일평균 수익률**:

| variant | Crisis | Stagflation | Slowdown |
|---|---:|---:|---:|
| pre-C | +0.031% | +0.032% | +0.014% |
| current | +0.012% | +0.017% | +0.014% |
| C-v2 | +0.013% | +0.020% | +0.014% |

**C-v2는 기각.** 델타(C-v2−current): Sharpe **-0.007**, MaxDD **-0.90pp(악화)**,
Calmar **-0.081(악화)**, Crisis 일방어 **+0.001pp(노이즈)**.
- 가설("V-shape 추가를 되돌리면 Crisis 일별 방어 회복")이 **검증 안 됨** — 일방어가
  거의 움직이지 않음(+0.001pp). 집계 MaxDD/Calmar는 오히려 악화.
- **이유**: 정적 regime_targets는 포트폴리오에 닿기 전 vol targeting·class cap·drift·
  분류 후행에 의해 크게 변형된다. Crisis 타깃의 equity ±10%는 vol targeting이 고변동
  구간에서 이미 주식을 축소하므로 실제 보유 비중 변화가 작아 흡수된다.

## 종합 결론 (수정)

1~4 (Step 1·2): C안은 현재 엔진에서 Sharpe 개선 없음 + 위험레짐 일방어 약화, 원인은
   forward-21일 Sharpe의 V-shape 편향. (위 Step 1·2 참조)
5. **그러나 그 편향을 되돌려도(C-v2) 엔드투엔드 개선이 없다** — regime_targets 비중의
   미세조정은 **in-sample 노이즈 범위**(pre-C/current/C-v2 Sharpe 0.71/0.67/0.66,
   ±0.04). vol targeting·캡·drift가 비중을 지배하기 때문.
6. **메타 결론(층 0과 정합)**: 레짐 시스템의 가치는 **거친 risk-on/off 스위칭 + vol
   targeting**에 있지 per-regime 자산 비중 정밀튜닝에 있지 않다. regime_targets는
   과적합 대상이 아니며, 어느 방향 튜닝(C / C-v2)도 정당화되지 않는다.

## 권장 (코드 변경 없음)

- **current 유지.** 시스템 목표가 "리스크 회피"이고 current가 MaxDD(-10.5%)·Calmar
  (0.87) 최고 → 방어 관점에서 가장 정합. pre-C의 Sharpe 우위(0.71)는 더 깊은 낙폭이
  대가. C-v2는 dominated.
- **regime_targets 추가 튜닝 중단.** 노이즈 범위. 향후 가치는 비중이 아니라 **위험레짐
  진입/이탈 적시성**(층 2) 또는 vol targeting 파라미터에서 찾아야 한다.
- 단, 진단이 드러낸 사실(C안 Crisis equity 추가의 근거가 V-shape 편향)은 문서로 보존.
  향후 누가 "Crisis에 equity Sharpe 높으니 늘리자"는 제안을 하면 본 문서가 반례.
