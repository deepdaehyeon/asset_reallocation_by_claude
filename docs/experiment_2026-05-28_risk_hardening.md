# 실험: 시스템 자체 강화 (vol_targeting / drawdown scale)

> **요약**: ① vol_targeting 20% 축소·floor 0.50·drawdown 임계 early trigger·세 개 결합 등 5개 시나리오로 어려운 해(2015/2018/2022) 보호 강화 가능성을 검증했다. ② baseline이 Sharpe 0.737·Calmar 0.969로 모든 강화 시나리오를 지배했으며, 어느 변형도 2015/2018/2022 Sharpe를 의미 있게 개선하지 못했고 오히려 전체 Sharpe가 하락했다. ③ baseline 유지 결정 — 어려운 해 손실의 본질이 자산 동반 하락으로 vol/drawdown 장치 자체로는 해결 불가능하며, 해결하려면 절대 stop-loss·correlation 모니터링·옵션 헤지 같은 별도 메커니즘이 필요하다.

*작성일: 2026-05-28*
*상태: baseline이 sweet spot으로 확인. 추가 강화 보류.*

## 배경

walk-forward 분석에서 어려운 해(2015 -0.51 / 2018 -0.37 / 2022 -0.39 Sharpe) 보호가 부족함을 발견. vol_targeting과 drawdown scale을 더 적극적으로 만들면 보호 강화될지 검증.

## 방법

5개 시나리오 비교 백테스트 (2010-2025, drift 1.5%):

1. **baseline**: 현재 (target_vol Goldilocks 0.13~Crisis 0.06, floor 0.65, dd -10/-20/-30%)
2. **vol-20%**: 모든 레짐 target_vol 20% 낮춤 (0.10~0.05)
3. **floor 0.50**: equity 50% 축소 허용 (vs 35%)
4. **dd early**: drawdown 임계 -7/-15/-22%로 일찍 trigger
5. **all 3**: 위 3개 결합

평가: 전체 Sharpe/MaxDD/Calmar + 어려운 해(2015/2018/2022) sub-Sharpe.

## 결과

| Scenario | 전체 Sharpe | MaxDD | Calmar | 2015 Sh | 2018 Sh | 2022 Sh | 매매/년 |
|----------|-----------:|------:|-------:|-------:|-------:|-------:|-------:|
| **baseline** | **+0.737** | -10.27% | **0.969** | -1.11 | -0.94 | -0.86 | 36.1 |
| vol-20% | +0.663 | -11.54% | 0.784 | -1.23 | -0.91 | -0.81 | 35.6 |
| floor 0.50 | +0.698 | **-10.00%** | 0.935 | -1.20 | -0.94 | -0.85 | 34.7 |
| dd early | +0.723 | -10.08% | 0.970 | -1.13 | -0.92 | -0.92 | 36.0 |
| all 3 | +0.608 | -10.38% | 0.800 | -1.36 | -0.85 | -0.86 | 32.5 |

## 핵심 발견

### 1. baseline이 sweet spot
- 모든 강화 시도가 Sharpe 하락
- vol-20%: -0.074 (over-tightening — 강세장 기회 손실)
- floor 0.50: -0.039 (미세 MaxDD 개선 + Sharpe 손실)
- dd early: -0.014 (거의 동등)
- all 3: -0.129 (누적 over-tightening)

### 2. 어려운 해 보호 거의 안 됨
- 2015/2018/2022 Sharpe 모두 -0.85~-1.36 범위
- **어느 시나리오도 의미 있는 개선 X** — 오히려 변경 시 더 나빠지는 경우 있음
- → vol_targeting / drawdown scale로 어려운 시장 보호 불가

### 3. 본질 한계 분석

어려운 해(2015/2018/2022)의 공통 특징:
- equity + bond + commodity **동반 하락**
- 자산 간 correlation 양의 방향으로 급증
- 폭락 속도 빨라 drawdown trigger 전에 손실 누적

vol_targeting/drawdown scale 가정:
- 자산 분산 (equity 떨어지면 bond 상승) — 분산 무력화 시 효과 X
- vol = risk 신호 — 동반 하락 시 vol_targeting 발동해도 보호 못함
- drawdown trigger는 -10% 이후 — 빠른 하락에 후행

## 결론

**현재 baseline이 최적**. 추가 강화는 net negative.

본 세션의 누적 변경들이 이미 risk-adjusted return을 잘 최적화한 상태:
- drift_threshold 1.5%로 매주 적절 재조정
- blend_smoothing_alpha 0.5로 부드러운 전환
- Crisis 비대칭 hysteresis로 빠른 위기 진입
- vol_targeting + drawdown scale 적정 균형

## 어려운 시장 보호의 진짜 해결책 (후속 검토용)

vol_targeting/drawdown scale로 풀 수 없는 본질 한계. 다른 framework 후보:

### A. 절대 stop-loss
- portfolio drawdown -15% 도달 시 강제 cash 100%
- 일정 기간 (예: 60일) 후 재진입
- 장점: 최대 손실 명확 제한 / 단점: 반등 miss 가능, 평소 영향 없음

### B. 자산 동반 압박 detect
- equity-bond 60일 correlation 모니터링
- correlation > +0.5 (정상 음의 방향에서 양의 방향으로 급변) 시 risk-off
- 새 시그널 필요

### C. 옵션 hedging
- SPY put protection (예: 3M 5%-OTM)
- 평소 비용 큼 (연 0.5~1.0% premium)
- 어려운 시장 보호 명확

세 가지 모두 새 메커니즘 + 큰 작업 + 평소 비용. 본 세션 우선순위 밖.

## 채택 결정

**baseline 유지** — 코드 변경 없음. 본 실험은 진단·검증 목적.
