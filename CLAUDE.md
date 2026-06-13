# CLAUDE.md — 프로젝트 협업 규칙

## 규칙

1. **실험 결과 저장** — 실험(벤치마크, 피처 비교, 모델 성능 측정 등)을 수행한 경우 결과를 `docs/` 아래 마크다운 파일에 저장한다. 파일 맨 앞에 반드시 **3문장 요약**을 넣는다: ① 무엇을 실험했는지, ② 핵심 수치/발견, ③ 채택 여부·결론. 이 요약은 Claude Code가 나중에 관련 실험을 빠르게 검색·파악할 수 있도록 파일 첫 블록에 위치해야 한다.

2. **코드 수정 후 커밋·푸시** — 코드 변경이 완료되면 반드시 `git commit` 및 `git push`를 수행한다.

3. **불확실할 땐 먼저 물어보기** — 실행 결과가 확실하지 않은 작업(주문 실행, 상태 변경, 되돌리기 어려운 조작 등)은 진행 전에 사용자에게 확인한다.

4. **평가 기준 고정 (2026-06-12 결정)** — 전략·실험·변형을 비교·판단할 때는 아래 4개 지표를 기준으로 고정한다. CAGR 단독·Sharpe·종점수익 같은 다른 지표로 결론 내지 않는다(보조 참고는 가능).
   - **롤링 CAGR** — 단일 종점 수익이 아니라 롤링 윈도우(3y·5y) CAGR의 최악/중앙값. 경로의존성·진입시점 운을 본다.
   - **Ulcer Index** — 드로우다운의 깊이×지속을 합친 하락 고통 지표.
   - **회복 기간(recovery period)** — 최대 낙폭에서 원금 회복까지 걸린 기간(underwater duration). 장기보유자의 체감 고통.
   - **Martin Ratio** — CAGR / Ulcer (Ulcer 조정 수익). 위험조정 효율의 1차 판정 지표.
   - 근거: 본 포트폴리오의 1순위 목표는 하락 회피이고 사용자는 3~5년 장기보유자라, 수익 절댓값보다 *하락의 깊이·길이와 그에 대비한 수익 효율*이 의사결정 기준이다.

5. **새 기능 실험 전 상호작용 확인 (2026-06-13 결정)** — 새로운 기능을 실험·설계할 때는 먼저 `docs/trading_features_and_interactions.md`(트레이딩 부수 기능 지도 + 상호작용 맵)를 확인한다. 실험 대상과 상호작용해 결과를 교란·희석할 수 있는 기능(예: core30이 vol targeting을 희석, 백테스트 리밸 모드가 floor 결론을 뒤집음)을 **실험 시작 전에 사용자에게 미리 알리고**, 어떤 기능을 고정할지/함께 토글할지 실험 범위를 사용자와 합의한 뒤 진행한다. 기능을 추가·변경했으면 이 문서도 함께 갱신한다.



Large files policy:

Never read:
- *.parquet
- *.csv
- *.feather
- *.pkl
- *.joblib

Unless explicitly requested by the user.
- Open log file only when needed.

Documentation policy:
- Use docs/*.md as project knowledge.

1. Read only the SUMMARY section first.
2. Read the full document only if the summary is insufficient.
3. Prefer summaries over full document reads.
