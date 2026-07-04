# vol targeting 축소분 행선지 A/B/C — cash vs 방어분배 vs 전체분배

> **요약**: ① vol targeting이 equity를 깎을 때 축소분이 전량 cash(469830)로 가는데, 다른 방어자산으로 분배하는 게 나은지(de-risk 약화 vs 방어수익 트레이드오프)를 `reduction_dest` 옵션(cash/defensive/nonequity)으로 2010~2025 drift 백테스트·규칙4 4지표 비교했다(레짐 경로 1회 캐싱 후 3변형 재사용). ② **A_cash가 롤링CAGR최악(1.83% vs 1.65~1.66%)·Ulcer(3.98 최저)·위기낙폭(MaxDD/COVID/Bear22 전부 최고)에서 우위**이고, B_defensive는 Martin +0.012·CAGR +0.12%p로 미세 우위나 그 대가로 Ulcer·롤링최악·COVID(-0.12%)·Bear22(-0.41%)가 악화, C_nonequity는 Martin −0.008로 방어·효율 둘 다 열위 — 전 변형 차이가 노이즈 수준(수익 <0.2%p·Ulcer <0.1·Martin ±0.01). ③ **현행 cash 유지** — 분배는 현금 대신 채권/금으로 CAGR을 소폭 벌지만 vol targeting의 목적(de-risk)을 갉아 하락 방어를 미세 악화시키고, 1순위(하락 회피)·규칙4 종합에서 순이득이 없다. `reduction_dest` knob은 재실험용 보존(기본 cash=라이브 무변화).

## 배경·설계

- 계기: 사용자(2026-07-04) — vol 축소분이 현금으로만 가는데 다른 자산 분배가 나을 것 같다, 백테스트로 확인하자.
- 변형(`config.vol_targeting.reduction_dest`, `portfolio.apply_vol_targeting`):
  - `cash` (현행): 469830 100%.
  - `defensive`: bond_krw·bond_tips·gold·cash에 현재비중 비례.
  - `nonequity`: 위 + commodity·managed_futures 비례.
- 범위 합의(규칙5): 나머지 전부 현행 고정(drift 5%·floor 0.65·core30·blend·평활), 분배처만 토글.
  drift 리밸 모드·2010~2025. 판정=규칙4 4지표. **reduction_dest는 레짐계산 무관(비중 후처리)**이라
  `precompute_regime_path()`로 HMM 1회 학습 후 3변형 재사용(비중은 `_target_weights`가 변형별 재계산).

## 결과 (2010-01 ~ 2025-04, drift 모드)

| 전략 | Martin | 롤3y최악 | 롤3y중앙 | Ulcer | 회복일 | UW최대 | CAGR | MaxDD | COVID | Bear22 | tx |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A_cash | 1.269 | 1.83% | 8.72% | 3.98 | 123 | 750 | 9.06% | −17.33% | −17.33% | −13.29% | 8.63% |
| B_defensive | 1.281 | 1.65% | 8.78% | 4.04 | 123 | 750 | 9.18% | −17.45% | −17.45% | −13.70% | 8.47% |
| C_nonequity | 1.261 | 1.66% | 8.73% | 4.07 | 748 | 748 | 9.13% | −17.54% | −17.54% | −13.79% | 8.71% |

Δ vs A_cash: B(ΔMartin +0.012·Δ롤최악 −0.18%·ΔUlcer +0.06·ΔMaxDD −0.12%·ΔCOVID −0.12%·ΔBear22 −0.41%),
C(ΔMartin −0.008·Δ롤최악 −0.17%·ΔUlcer +0.08·ΔMaxDD −0.21%·ΔCOVID −0.22%·ΔBear22 −0.50%).

## 해석

- **트레이드오프 그대로 실현**: 분배(B/C)는 현금(수익 0) 대신 채권/금으로 CAGR +0.1%p, 하지만
  de-risk 약화로 Ulcer↑·롤링최악↓·위기낙폭↑. 축소분을 위험자산으로 보내면 vol targeting 목적을 상쇄.
- **차이 전부 미미**(노이즈 수준). 1순위 하락회피 기준에선 A가 4지표 중 3개(롤최악·Ulcer·회복 동률)와
  위기낙폭 전부에서 우위. B의 Martin +0.012는 Ulcer·위기낙폭 악화와 상쇄돼 순이득 없음. C는 명백 열위.
- 회복기간·UW는 사실상 불변(vol targeting은 진입 방어라 회복 국면 영향 작음 + core30 희석).

## 결론

**cash 유지(config 변경 없음).** `reduction_dest` knob(기본 cash) 보존 — 재실험/향후 조합 검토용
([[feedback-regime-targets-no-tuning]] 정신: 미미한 차이는 노이즈, 튜닝 지양). 산출:
`docs/_voltarget_reduction_dest_abc.csv`, `scripts/compare_voltarget_reduction_dest.py`.
