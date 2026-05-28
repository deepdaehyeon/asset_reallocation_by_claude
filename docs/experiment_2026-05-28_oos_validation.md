# 실험: B안 OOS 검증 — 첫 10년 진단 vs 마지막 5년 백테스트

*작성일: 2026-05-28*
*상태: in-sample bias marginal로 확인. regime_targets 조정의 portfolio 영향 사실상 없음.*

## 배경

이전 walk-forward robustness 분석에서 "첫 5년 vs 마지막 5년 +5.5pp CAGR" 격차 발견. 두 가지 가능:
1. 본 세션 변경의 in-sample bias
2. 시장 환경 영향 (코로나 후 강세장 + AI 랠리)

본 실험은 둘을 분리. 첫 10년(2010-2019) 데이터만으로 `analyze_regime_targets.py` 재실행하여 격차 식별 → 그 결과가 전체 기간 진단과 일관한지 비교 → 일관하지 않은 항목만 가진 비중("Robust")으로 OOS 백테스트.

## 방법

### Step 1: 첫 10년 진단 vs 전체 진단 비교

| 항목 | 전체 진단 권장 | 첫 10년 진단 | 일관성 |
|------|--------------|----------------|-------|
| Crisis: equity_etf 0→10% | Sharpe +2.85 (2위) | +2.34 (3위) | **일관** |
| Crisis: bond_tips 0→10% | +2.28 (3위) | +16.95 (1위) | **일관** |
| Crisis: gold 15→12% | +0.45 | -0.59 (더 줄임 OK) | **일관** |
| Stagflation: equity_factor 3→8% | +1.34 (3위) | +2.22 (3위) | **일관** |
| **Reflation: MF 5→12%** | **+2.26 (1위)** | **MF 데이터 부족** | **일관 X** |
| **Slowdown: MF 12→5%** | **+0.07 (12위)** | **+1.19 (7위, 좋음)** | **일관 X** |
| **Stagflation: MF 12→7%** | +0.44 (12위) | MF 데이터 부족 | **일관 X** |

→ MF 변경은 모두 첫 10년에 검증 안 됨 (DBMF 2019-05 상장)

### Step 2: 3가지 비중을 마지막 5년에 적용

- **Original**: regime_targets 조정 전 (commit 6d86b79 이전)
- **Robust**: MF 변경 제외, Stagflation/Crisis equity 강화만 적용
- **Current**: 현재 라이브 적용 (전체 변경)

## 결과 (2020-01 ~ 2024-12, OOS)

| 비중 | CAGR | Sharpe | MaxDD | Calmar |
|------|----:|------:|------:|------:|
| **Original** | +14.35% | +1.13 | -10.13% | **1.42** |
| Robust | +14.61% | +1.14 | -11.03% | 1.32 |
| Current | +14.38% | +1.12 | -10.27% | 1.40 |

## 해석

### 1. 세 비중 모두 거의 동등
- Sharpe 차이 ±0.02 (실질 동등)
- MaxDD 차이 1pp 이내
- Calmar 모두 1.3+ 

### 2. In-sample bias가 marginal
- 본 세션 regime_targets 조정의 OOS 영향이 사실상 없음
- 이전 walk-forward의 "마지막 5년 +5.5pp CAGR" 격차는 거의 전부 **시장 환경 영향** (코로나 V-shape + AI 랠리)
- 시스템 변경 영향은 미미

### 3. Original이 OOS에서 약간 더 좋음 (Calmar 1.42)
- 조정 안 한 비중이 마지막 5년 Calmar 가장 우수
- 우리 조정이 portfolio metric에 사실상 효과 없음
- 다만 차이 작아 운영 결정은 다른 factor (단순성 / 운영 안정성)으로

### 4. 시스템 다층 안전망이 비중 차이를 흡수
- vol_targeting, drawdown scale-down, blend_smoothing 등이 portfolio 안정화
- 따라서 regime_targets에 어떤 비중을 두든 결과 비슷
- 이는 **시스템의 robust 설계 증거** — 비중 손정의 vs 데이터 기반의 차이가 portfolio metric으로 흡수됨

## 종합 결론

- **in-sample bias 우려 해소** ✓
- **본 세션 변경의 portfolio 가치는 marginal** — 전체 백테스트 Sharpe +0.018은 작은 신호
- **운영 결정 옵션**:
  - A. Current 유지 (작업 0, 운영 안정성)
  - B. Original 복귀 (단순화, OOS Calmar 미세 우수)
  - C. Robust (MF 변경만 제외)
- 세 옵션 portfolio 효과 사실상 동일. **운영 안정성 측면에서 A 유지가 가장 합리적**.

## 학습된 인사이트

1. **시스템 robustness 입증**: 다층 안전망이 regime_targets 손정의에 의존하지 않게 만듦
2. **데이터 기반 조정의 한계**: in-sample 진단으로 격차 식별 → 비중 조정 → OOS에서 차이 사라짐
3. **시장 환경이 dominant factor**: 백테스트 metric의 시기 의존성이 본 세션 모든 변경 효과보다 큼

후속 개선이 의미 있는 영역:
- 시스템 자체 강화 (vol_targeting, drawdown scale 더 적극적) — 비중 조정보다 더 영향력 있을 수 있음
- 어려운 시장 환경 (2015/2018/2022) 보호 강화 (자산 동반 압박 시기)
- 데이터 정합성 (Data Validation Layer 등)
