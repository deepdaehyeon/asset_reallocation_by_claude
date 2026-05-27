# 실험: #1 Phase 1 — regime_targets 격차 진단

*작성일: 2026-05-28*
*상태: 분석 완료. 격차 큰 레짐 식별. Phase 2(walk-forward 최적화/shrinkage)는 사용자 결정.*

## 배경

`config.yaml`의 `regime_targets`는 5개 레짐 × 자산 클래스별 비중을 손정의한 룩업 테이블. 데이터로 검증되지 않은 가설. Phase 1은 각 레짐의 현재 비중 vs historical optimal을 비교해 격차를 식별.

## 방법

- 13개 자산 클래스 (`equity_etf/factor/sector/individual/developed/emerging`, `bond_usd/tips/krw`, `gold`, `commodity`, `managed_futures`, `cash`)
- 자산 클래스별 일별 수익률 = 해당 클래스 종목 단순 평균
- walk-forward로 매일 `detect_regime` 적용 (in-sample, 진단 목적)
- 각 레짐 시점의 자산 클래스 forward 21일 수익률 평균/Sharpe 측정
- 현재 `regime_targets[regime][class]` 비중과 비교

## 결과 (2010~2025)

### Crisis (n=164일) — 가장 큰 격차

| 클래스 | fwd_mean | Sharpe | rank | 현재 비중 | 비고 |
|--------|---------:|-------:|----:|---------:|------|
| equity_individual | +10.60% | **+3.55** | 1 | 5% | 매우 저비중 |
| equity_etf | +4.95% | **+2.85** | 2 | **0%** | ★ 완전 누락 |
| bond_tips | +0.78% | **+2.28** | 3 | **0%** | ★ 완전 누락 |
| bond_usd | +0.44% | +1.60 | 4 | 10% | 적정 |
| bond_krw | +0.77% | +1.59 | 5 | 10% | 적정 |
| equity_factor | +3.66% | +1.50 | 6 | 3% | 약간 저비중 |
| cash | +0.01% | +0.52 | 10 | **36%** | 과중 |
| gold | +0.76% | +0.45 | 11 | 15% | ✗ 하위 고비중 |
| managed_futures | -0.43% | **-0.48** | 13 | **12%** | ✗ 음수인데 고비중 |

### Stagflation (n=302일)
- equity_factor Sharpe +1.34 (3위) but 3% (★ 상위 저비중)
- managed_futures Sharpe +0.44 (12위) but 12% (✗ 하위 고비중)

### Slowdown (n=824일)
- bond_tips Sharpe +1.19 (3위) but 0% (★ 상위 저비중)
- managed_futures Sharpe +0.07 (12위) but 12% (✗ 하위 고비중)

### Reflation (n=321일)
- managed_futures Sharpe +2.26 (1위) but 5% (★ 상위 저비중)
- gold Sharpe +0.01 (11위) but 15% (✗ 하위 고비중)

### Goldilocks (n=2195일)
- 현재 비중이 비교적 align (equity_etf 42%, equity_individual 20% 등)
- managed_futures Sharpe +0.90 (3위) but 5% (저비중이지만 분포 다양)
- equity_developed/emerging 음수 Sharpe는 비중 작아서 큰 문제 X

## 핵심 패턴

1. **managed_futures의 레짐별 비대칭 필요**:
   - Reflation에서 +2.26 (1위) — 5% 너무 작음
   - Crisis -0.48, Slowdown +0.07, Stagflation +0.44 — 12%로 균등 과중
   - 레짐별 비중 폭이 5%~25% 정도로 차등 필요

2. **bond_tips 활용도 부족**:
   - Slowdown +1.19, Crisis +2.28 모두 상위
   - 현재 거의 모든 레짐에서 0~8% — 위기·둔화 레짐에서 강화 여지

3. **Crisis 보수 자산(cash/gold/MF) 과중**:
   - 합 63%가 보수. 그러나 Crisis 분류 후 21일은 V-shape 반등 시점
   - 진단의 transition 후행과 align: 분류 시점에 이미 폭락 후

4. **gold의 Reflation 비중 과중**:
   - Reflation Sharpe 0.01 (11위)인데 15%
   - Stagflation에서는 +1.50 (2위)라 비중 18% 적정

## 주의해야 할 메타 이슈

본 분석은 **진단용 in-sample 측정**이며 그대로 적용 시 다음 위험:

1. **In-sample bias**: 같은 데이터로 라벨링 + Sharpe 계산. 미래에 같은 패턴 보장 X.
2. **Crisis +3.84% fwd_mean의 본질**: detect_regime이 후행적이라 분류 시점 = V-shape 직전. 진짜 Crisis 위험이 아닌 "분류된 시점의 시장"이라는 점.
3. **equity_individual 4종목 selection bias**: TSLA/PLTR/NVDA/LLY는 후행적으로 골라진 winner. survivorship bias 매우 큼. 미래 성과 보장 X.
4. **자산 클래스 합성 단순화**: 종목 단순 평균. 실제 운영 비중과 다를 수 있음.

## Phase 2 옵션

격차가 명확하나 적용은 신중해야:

### A. Walk-forward Markowitz (정통, 위험 큼)
- 매 리밸런싱마다 직전 3년 데이터로 재최적화
- look-ahead 없음, overfit 위험
- 작업: 2-3일

### B. Shrinkage 적용 (현재 비중 prior, 안전)
- `final = (1-λ)·현재 + λ·data`, λ=0.2부터 점진적
- baseline에 가까운 변화, 운영 안정성
- 작업: 1-2일

### C. 부분 수동 조정 (가장 안전)
- 본 진단의 명확한 격차만 수동 조정:
  - Crisis: cash 36→25%, MF 12→0%, bond_tips 0→10%, equity_etf 0→10%
  - Reflation: gold 15→8%, MF 5→15%
  - Slowdown: MF 12→5%, bond_tips 0→8%
  - Stagflation: MF 12→5%, equity_factor 3→8%
- 백테스트로 변화 효과 검증
- 작업: 0.5-1일

### D. 보류
- 현재 비중 유지. in-sample bias가 너무 크다고 판단
- 또는 detect_regime 본질 문제(Goldilocks 흡수, Crisis 후행)가 먼저 해결되어야 의미 있다고 판단

## 권장

**C안 (부분 수동 조정)**이 가장 균형. 진단 결과의 명확한 신호만 활용, overfitting 위험 최소, 작업량 작음.

A안은 위험 대비 효과 불확실. B안은 균형 좋지만 어떤 λ가 적정인지 추가 결정 필요.
