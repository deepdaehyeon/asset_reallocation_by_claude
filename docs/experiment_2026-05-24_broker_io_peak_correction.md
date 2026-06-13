# 입출금 explicit 트래킹 — peak_krw 보정

> **요약**: ① drawdown 계산의 peak_krw를 휴리스틱(±10% 변동 추정) 대신 CSV 로그·KIS profits 역산·휴리스틱 폴백의 3단계 계층으로 명확히 보정하는 방식을 구현하고 검증했다. ② 34건 단위·시나리오·KIS profits 테스트 전부 PASS, 5% 출금·-10% 폭락 등 핵심 시나리오에서 기존 휴리스틱의 오인·미감지 결함이 해소됨을 확인했다. ③ 코드(deposit_log.py 신규·executor.py 수정)를 라이브에 채택했으며, CSV 파일을 생성해 주는 것이 권장 운영 방식이고 실 운영 적용 시 로그 확인이 필요하다.

**일자**: 2026-05-24
**목적**: 기존 휴리스틱 기반 입출금 추정의 결함 해결.

## 배경

`KisRebalancer._compute_position_snapshot`(executor.py)는 peak_krw 대비 drawdown을 계산해 defensive 모드를 트리거한다. 기존 휴리스틱:

```
Δ = (현재 자산 - 직전 자산) / 직전 자산
if |Δ| > 10%  AND  age < 30h:
    peak *= (1 + Δ)   # 입출금으로 추정
```

이 휴리스틱의 결함 두 가지:

| # | 문제 | 결과 |
|---|---|---|
| 1 | 10% 미만 출금 미감지 | 5% 출금 → peak 그대로 → drawdown −5% 잘못 표시 → 불필요한 defensive 트리거 |
| 2 | 1일 시장 폭락이 입출금으로 오인 | COVID 2020-03-12 −10% 같은 폭락 → peak를 끌어내려 drawdown 0% → defensive mode가 작동 안 함 |

특히 2번은 **위기 구간에서 방어가 무력화**되는 시나리오라 위험.

## 해결 — explicit 이벤트 + 자동 역산 + 휴리스틱 fallback

`trading/deposit_log.py` 모듈 신설. 조회 우선순위:

1. **CSV 로그 (primary, deterministic)** — `trading/logs/deposits.csv`
   사용자가 입출금 발생 시 한 줄 append. 파일이 존재하면 (비어 있어도) "이 시스템을 사용 중"으로 간주.
2. **KIS profits 역산 (auto)** — `pykis account().profits(start, end)` 사용
   공식: `입출금 = Δ(매입금액 + 예수금) − 실현손익`
   - pykis가 `account().profits()`로 기간 실현손익을 직접 노출
   - state에 `last_principal_krw`(직전 실행 매입금액+예수금) 캐싱
   - `kis_profits_processed_through`(마지막 처리일) 캐싱 → 같은 날 중복 호출 시 매도손익 이중 계산 방지
   - 한계: 모의투자 미지원, 매수 수수료 ±수천원 오차, 같은 날 정밀도↓ (1만원 미만은 노이즈로 간주)
3. **휴리스틱 fallback** — 위 둘이 모두 사용 불가일 때 |Δ|>10% / age<30h 로직 (기존 동작). 경고 로그에 "deposits.csv 사용 권장" 안내 출력.

### CSV 포맷

```
ts,acc_name,amount_krw,kind,note,id
2026-05-01T10:00:00,KRW_1,1000000,deposit,월급 입금,
2026-05-15T14:30:00,KRW_1,300000,withdrawal,생활비,
```

| 컬럼 | 설명 |
|---|---|
| ts | ISO8601 timestamp |
| acc_name | 계좌 식별자 (정보용) |
| amount_krw | KRW 환산 금액. **항상 양수** |
| kind | `deposit` 또는 `withdrawal` |
| note | 자유 메모 |
| id | 고유 식별자 (비우면 ts+kind+amount로 자동 생성) |

`trading/logs/deposits.csv`는 `.gitignore`로 제외(개인 금융 데이터). 템플릿은 `deposits.csv.example`.

### `_correct_peak_for_io` 로직

`executor.py:KisRebalancer._correct_peak_for_io`:

```
events, source = fetch_deposit_withdrawal_events(since=prev_total_at, ...)

if events is not None:
    net_flow = sum(deposits) - sum(withdrawals)
    peak += net_flow                # explicit 보정
    # 출금으로 peak가 음수 되면 total_all_krw로 리셋 (안전망)
else:
    # 휴리스틱 fallback (기존 동작)
    if |Δ| > 10% AND age < 30h:
        peak *= (1 + Δ)
```

이후 `peak = max(peak, total_all_krw)`은 그대로 유지(고점 갱신).

## 비교

| 시나리오 | 기존 휴리스틱 | 신규 (CSV / KIS profits) |
|---|---|---|
| 5% 출금 | **미감지** → drawdown −5% 잘못 표시 | 정확히 5% peak 감소 |
| −10% 시장 폭락 | **오인** → peak 끌어내려 drawdown 0% → defensive 미작동 | 입출금 0건 인식 → peak 유지 → defensive 정상 작동 |
| 매도 실현이익 500k (입출금 0) | 자산 변동 그대로 → 부정확 | KIS profits로 실현손익 분리 → 입출금 0건 |
| 입금 250만 + 매도 50만 | "총 +300만 = 입출금" 오인 | Δprincipal +300만 − 실현 50만 = 입금 250만 (정확) |
| CSV·KIS 둘 다 불가 (모의/초기 실행) | 동일 | 휴리스틱으로 자동 fallback + 경고 메시지 |

## 테스트

`scripts/test_broker_io_peak.py` — **34건 모두 PASS**.

```
[단위] deposit_log 모듈 (12건)
  CSV 기본 파싱 / since 필터 / bad row 스킵 / 파일 부재
  fetch 라우팅 (csv / 빈 csv / 미존재→none)

[시나리오] _correct_peak_for_io (9건)
  5% 출금 → peak 5% 감소
  시장 −10% 폭락 → peak 유지
  CSV 미존재 → 휴리스틱 fallback 작동
  동일 이벤트 prev_total_at 기준으로 자연 중복 방지
  과도 출금 시 peak<0 보호 (total_all_krw로 리셋)

[KIS profits 역산] (13건)
  순수 입금 / 순수 출금 정확 분리
  매도 실현이익만 (입출금 0)
  매도 + 입금 동시 발생 정확 분리
  초기 실행 (last_principal 없음) → None
  같은 날 중복 호출 → 빈 결과 (이중 계산 방지)
  CSV 존재 시 KIS profits 호출 안 됨
  1만원 미만 노이즈 → 0건
```

실행: `python scripts/test_broker_io_peak.py`

## 변경된 파일

- `trading/deposit_log.py` (신규) — CSV 백엔드 + KIS API stub + `fetch_deposit_withdrawal_events`
- `trading/executor.py` — `_correct_peak_for_io` 메서드 추가, `_compute_position_snapshot`에서 사용
- `trading/logs/deposits.csv.example` (신규) — 사용자 템플릿
- `.gitignore` — `trading/logs/*.csv` 패턴 추가
- `scripts/test_broker_io_peak.py` (신규) — 모킹 기반 검증

## 남은 작업 (human verification required)

### 1. 첫 운영 적용 시 검증
- 첫 실행에서 `last_principal_krw`가 state에 저장되는지 확인 (이후 실행에서 KIS profits 백엔드 가동).
- 입금/출금 미발생 일에 `[peak 보정/kis_profits]` 메시지가 안 떠야 정상 (net_flow ≈ 0 → 0건).
- 입금 발생 다음 실행에서 `[peak 보정/kis_profits] ... net +XXX → peak XXX→YYY` 메시지로 정확히 잡히는지 확인.
- 매도 거래 직후 실행에서 입출금으로 오인되지 않는지 확인.

### 2. CSV vs KIS profits 어느 쪽을 primary로?
현재 default 우선순위는 **CSV → KIS profits → 휴리스틱**.
- CSV는 결정적(deterministic) — 사용자가 명시. 가장 안전.
- KIS profits는 자동(no user action) — 편리하지만 ±수천원 오차.
- 둘 다 사용 가능. CSV 파일을 만들면 KIS profits는 자동 비활성화.

### 3. 모의투자 환경
KIS profits API는 모의투자 미지원. 모의투자 환경에서는 자동으로 휴리스틱 fallback으로 떨어진다.

### 4. USD 입출금 처리
- CSV: 사용자가 환율 적용한 KRW 금액으로 기록 (note에 USD 원금 표기 권장).
- KIS profits 백엔드: USD profits는 코드에서 환율 적용 후 KRW로 환산해 합산. pykis `deposit.exchange_rate` 사용. 환산 실패 시 1380 KRW/USD 폴백 — 환율 변동 분 작은 오차 가능.

### 5. 매수 수수료 오차
공식의 정확한 형태는 `입출금 = Δprincipal − (실현손익 − 매도수수료) − 매수수수료`. 현재 코드는 `(profit - fees)`로 매도 수수료만 분리하고, **매수 수수료는 그대로 net_flow에 반영**된다. 1억원 거래에 수수료 ~15,000원 정도라 1만원 미만 노이즈 임계값을 살짝 넘을 수 있음. 정밀도가 필요하면 `daily_orders` 사용해 매수 수수료 별도 차감 가능 (향후 개선).

## 재현 / 검증

```bash
python scripts/test_broker_io_peak.py
```

실 운영 적용 전:
1. `trading/logs/deposits.csv.example`을 `deposits.csv`로 복사하고 헤더만 남긴다.
2. 다음 입출금부터 한 줄씩 append.
3. monitor / cron 실행 시 `[peak 보정/csv]` 또는 `[peak 보정/휴리스틱]` 로그 확인.
4. `[peak 보정/csv]`가 떠야 정상 (휴리스틱 fallback이 뜨면 CSV 경로 확인).
