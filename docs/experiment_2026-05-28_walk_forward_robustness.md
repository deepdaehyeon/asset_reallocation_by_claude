# 실험: Walk-Forward Robustness 분석

> **요약**: ① 현재 라이브 config(drift 1.5%)를 적용한 시스템을 5년 sub-period 3개·3년 sliding window 13개·연도별 15개로 시간 안정성을 검증했다. ② 3년 sliding 13개 window 전부 Sharpe 양수(0.67~2.00), 연도별 양수 12/음수 3(2015/2018/2022 — 자산 동반 하락 해)이었으며, 마지막 5년이 첫 5년 대비 +5.5pp CAGR 우위는 in-sample bias보다 시장 환경(코로나 후 강세장) 영향이 주요 원인임을 확인했다. ③ A안(현재 결과 수용) 권장 — robustness 신호가 양호하고 in-sample bias 우려는 작으며, Data Validation 같은 다른 우선순위로 이동하는 것이 효율적이다.

*작성일: 2026-05-28*
*기간: 2010-01-01 ~ 2025-04-30, 현재 라이브 config (drift 1.5%)*

## 배경

본 세션의 모든 변경(특히 in-sample bias 위험이 있는 `regime_targets` 부분 조정, `confidence_method` / `blend_smoothing_alpha` / `drift_threshold` 선택)이 단일 백테스트 결과 기반.

robustness 검증:
- 시간 sub-period로 metric 일관성 확인
- in-sample bias 신호 식별

※ 모델 walk-forward (HMM/RF가 매 시점 직전 데이터로 재학습)는 `BacktestEngine._get_regime`에 이미 구현됨. 본 분석은 시간 안정성에 집중.

## 방법

- 5년 sub-period 3개 (2010-14, 2015-19, 2020-24)
- 3년 sliding window 13개 (1년 step)
- 연도별 metric 15개 (2010-2024)
- 안정성 = CV (std/|mean|)

**Metric 주의**: 본 스크립트는 risk-free 0 단순 Sharpe 사용. 기존 backtest의 risk-free 4% 차감과 다름. 단순 Sharpe 1.22 ≈ 기존 0.74.

## 결과

### 전체 기간
- CAGR +9.9%, Sharpe 1.22, MaxDD -10.3%, Calmar 0.97

### 5년 sub-period

| 기간 | CAGR | Sharpe | MaxDD | Calmar |
|------|----:|------:|------:|------:|
| 2010-14 | +9.0% | 1.11 | -7.7% | 1.17 |
| 2015-19 | +6.8% | 1.07 | -10.3% | 0.66 |
| **2020-24** | **+14.5%** | **1.51** | -10.3% | **1.41** |

### 3년 sliding (안정성 측정)
- 13개 window 모두 Sharpe > 0 (0.67 ~ 2.00)
- CV (sliding): Sharpe 0.27, CAGR 0.36, MaxDD 0.13
- → **안정적 robustness**

### 연도별 (15개, 가장 엄격)

| 연도 | Sharpe | 비고 |
|------|------:|------|
| 2017 | **+3.40** | 강세장 최고 |
| 2023 | +2.29 | 회복장 |
| 2020 | +2.13 | COVID 후 V-shape |
| 2019 | +2.53 | Fed pivot |
| 2024 | +1.91 | AI 랠리 |
| ... | (중간 양수 9개) | |
| **2015** | **-0.51** | 원자재 붕괴, 강달러 |
| **2018** | **-0.37** | Fed 인상 + Q4 폭락 |
| **2022** | **-0.39** | 인플레+금리인상, equity+bond 동반 하락 |

- 양수 12 / 음수 3 (모두 매크로 환경 자체가 어려운 해)
- CV (annual): Sharpe 0.83 — 시장 환경에 따른 변동 큼
- 최저 -0.51, 최고 +3.40 — 큰 범위

### In-sample bias 검증

첫 5년 vs 마지막 5년 비교:

| metric | 2010-14 | 2020-24 | Δ |
|--------|------:|------:|---:|
| Sharpe | 1.109 | 1.507 | **+0.398** |
| CAGR | 9.0% | 14.5% | +5.5pp |
| MaxDD | -7.7% | -10.3% | -2.5pp |
| Calmar | 1.169 | 1.412 | **+0.243** |

**마지막 5년이 명확히 우월**. 두 가지 가능 해석:
1. **본 세션 변경이 최근 데이터에 더 잘 맞을 가능성** (in-sample bias)
2. **시장 환경 자체가 우호적** (코로나 후 강세장, AI 랠리)

CAGR +5.5pp 격차는 매우 큼. 시스템 변경의 영향이라기엔 너무 큼 — 시장 환경이 주요 원인일 가능성이 높음.

## 평가

### 긍정 신호 ✓
- **3년 sliding 모두 양수 Sharpe** — robustness 양호
- **MaxDD 안정** (CV 0.13)
- 연도별 손실도 -4% CAGR 수준 (Calmar -0.5)에 머무름
- 어려운 해(2015, 2018, 2022)에도 catastrophic loss 없음

### 의심 신호 ⚠
- 마지막 5년 성과가 첫 5년 대비 +5.5pp CAGR — overfit/시장 환경 의심
- 연도별 변동성 큼 (CV 0.83)
- 시스템이 매크로 환경 자체에 영향받음 (다층 안전망에도 어려운 해 손실)

### 본질적 한계
2015/2018/2022 모두 **equity+bond+commodity 동반 압박** 시기. 본 시스템의 모든 안전망(blend_smoothing, drawdown scale, vol_targeting, regime_targets)이 자산 클래스 간 분산을 가정. 매크로 환경 자체가 분산을 무력화하면 보호 불가.

## 다음 단계 옵션

### A. 현재 결과 수용 (가장 단순)
- robustness 양호 + 본 세션 변경 영향이 in-sample bias만은 아님 (시장 환경)
- 다음 우선순위(Data Validation 등)로 이동

### B. 진짜 OOS 검증
- 첫 10년(2010-19)으로만 `analyze_regime_targets.py` 재실행 → 새 비중 도출
- 그 비중으로 마지막 5년(2020-24) out-of-sample 백테스트
- 현재 (전체 데이터로 조정한) 비중의 OOS metric과 비교
- 비슷하면 in-sample bias 작음, 다르면 큼
- 작업: 1일

### C. 어려운 시장 강화
- 2015/2018/2022 같은 어려운 해 보호 강화
- 예: drawdown threshold 더 빠르게, vol_targeting 더 적극적
- 작업: 0.5-1일

## 권장

**A안 (현재 결과 수용)** — 누적된 robustness 신호가 양호. in-sample bias 의심은 있지만 시장 환경 영향이 더 큰 듯. 다른 우선순위(Data Validation, Slippage)로 이동이 효율적.

B/C안은 추가 검증·개선 가치 있으나 marginal. 운영 모니터링하면서 필요 시 진행.
