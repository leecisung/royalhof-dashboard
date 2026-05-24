# 매주 월요일 보고서 작업지시서

> 이 파일을 클로드코드에 던지면 매주 월요일 운영 사이클 실행.
> CLAUDE.md 먼저 읽었다고 가정함.

---

## 작업 목표

1. 지난주(월~일) 키워드별 성과 조회
2. 운영 룰에 따라 키워드 분류 (컷 / 입찰↑ / 유지 / 우수)
3. 컷 키워드 DELETE 실행
4. 예비 풀에서 동량 보충 등록
5. 마크다운 주간 보고서 생성
6. 슬랙 전송 (옵션)

---

## 실행 명령

```bash
# 기본 (조회만 - dry run)
python scripts/weekly_pruner.py --dry-run

# 실제 실행
python scripts/weekly_pruner.py --execute --report

# 특정 기간 재실행 (장애 복구용)
python scripts/weekly_pruner.py --execute --since 2026-05-11 --until 2026-05-17
```

---

## 작업 순서 (스크립트가 해야 할 일)

### Step 1. 기간 결정
- 기본: 지난주 월~일 (실행 시점 기준)
- `user_time_v0` 또는 `datetime.now(tz=KST)` 사용
- 한국 시간 기준 (Asia/Seoul)

### Step 2. 성과 데이터 조회
- `/stats` 호출, fields: `impCnt, clkCnt, salesAmt, ctr, cpc, avgRnk`
- 모든 활성 광고그룹 순회
- 그룹당 키워드 1,000개 → 분할 호출 필요
- 결과를 `data/last_week_stats.json`에 임시 저장 (재시작 대비)

### Step 3. 키워드 분류

| 조건 | 액션 | 카운트 |
|---|---|---|
| 노출 0 & 입찰 70원 & 등록 7일 이상 | 입찰 100원 ↑ | `bid_up_count` |
| 노출 0 & 입찰 100원 & 등록 14일 이상 | DELETE | `cut_zero_count` |
| CPC > 200원 | DELETE | `cut_high_cpc_count` |
| 노출 10,000+ & CTR < 1% | DELETE | `cut_low_ctr_count` |
| CPC 70~100원 & 노출 1,000+ & CTR 2%+ | 우수 트래킹 | `top_count` |
| 그 외 | 유지 | `keep_count` |

### Step 4. 실행
- DELETE: `/ncc/keywords/{id}` DELETE (100개 batch 권장, rate limit 5/s)
- 입찰가 변경: `/ncc/keywords/{id}` PUT, `bidAmt` 변경
- 컷된 키워드는 `reserve_pool.db`에 `cooldown_until=오늘+30일` 기록

### Step 5. 보충
- 컷한 수량 = N
- `reserve_pool.db`에서 `cooldown_until < today` 조건으로 N개 추출
- 그룹별 잔량 확인 → 슬롯 있는 그룹에 분배
- `/ncc/keywords` POST batch 100개씩 등록
- 예비 풀 잔량 < 10만개면 경고 출력 (재펼침 필요)

### Step 6. 보고서 생성
- 아래 템플릿에 따라 `reports/weekly_YYYYMMDD.md` 생성
- YYYYMMDD = 실행일 (월요일)
- 슬랙 웹훅 있으면 요약 섹션만 전송

---

## 보고서 템플릿

`reports/weekly_YYYYMMDD.md` 형식:

```markdown
# 로얄호프 70원 전략 주간 보고 — YYYY/MM/DD ~ YYYY/MM/DD

> 생성: YYYY-MM-DD HH:MM KST
> 실행: weekly_pruner.py

---

## 1. 핵심 요약 (3줄)

- 살아있는 키워드 **XXX,XXX개** (전주 대비 +N,NNN)
- 주간 비용 **X,XXX,XXX원** / 노출 **XX,XXX,XXX회** / 클릭 **X,XXX회**
- 평균 CPC **XX원** / CTR **X.XX%** / 노출당 비용 **X.XX원**

## 2. 키워드 변동

| 분류 | 키워드 수 | 비고 |
|---|---:|---|
| 신규 등록 (보충) | N,NNN | 예비 풀에서 충당 |
| 컷 (노출 0) | N,NNN | 14일 노출 무 |
| 컷 (CPC 과다) | NNN | 200원 초과 |
| 컷 (CTR 저조) | NNN | 노출 10K+ & CTR<1% |
| 입찰가 ↑ (70→100원) | N,NNN | 1주 노출 0 |
| 유지 | NNN,NNN | |
| **⭐ 우수 트래킹** | NNN | 별도 섹션 참조 |

**예비 풀 잔량**: NNN,NNN개 (경고: <100,000이면 재펼침 필요)

## 3. 그룹별 성과

| 그룹 | 키워드수 | 노출 | 클릭 | 비용 | CPC | CTR |
|---|---:|---:|---:|---:|---:|---:|
| G1_메인변형 | | | | | | |
| G2_지역창업 | | | | | | |
| G3_경쟁치킨 | | | | | | |
| G4_경쟁호프 | | | | | | |
| G5_상황조건 | | | | | | |
| G6_메뉴결합 | | | | | | |

## 4. ⭐ 우수 키워드 TOP 20 (강남BAR 후보)

| 키워드 | 노출 | 클릭 | CPC | CTR | 비용 | 그룹 |
|---|---:|---:|---:|---:|---:|---|
| ... | | | | | | |

> 우수 기준: CPC 70~100원 + 노출 1,000+ + CTR 2%+
> 이 키워드들이 강남BAR 같은 발견. 별도 모니터링.

## 5. 신규 키워드 흥미 후보 TOP 10

> 지난주 신규 등록 중 첫주에 클릭 5+ 발생한 키워드 (잠재 우수)

| 키워드 | 노출 | 클릭 | CPC | 그룹 |
|---|---:|---:|---:|---|
| ... | | | | |

## 6. 비용 추이 (직전 4주)

| 주차 | 비용 | 노출 | 클릭 | CPC | 키워드수 |
|---|---:|---:|---:|---:|---:|
| 4주 전 | | | | | |
| 3주 전 | | | | | |
| 2주 전 | | | | | |
| 지난주 | | | | | |

## 7. 이상 신호 (해당 시만)

- ⚠️ 단일 키워드 비용 전체의 5% 초과: ...
- ⚠️ 예비 풀 잔량 < 10만개
- ⚠️ 심사 거부 비율 > 10%
- ⚠️ API 호출 실패율 > 5%

## 8. 다음주 권고 액션

- [ ] 우수 키워드 별도 T&D 작성 검토 (현재 5개 이상이면)
- [ ] 예비 풀 재펼침 (잔량 부족 시)
- [ ] 신규 카테고리 시드 추가 (성과 정체 시)

---

## Appendix

### A. 실행 로그
- 시작: HH:MM:SS
- API 호출 총 N회 (성공 N, 실패 N)
- 소요 시간: NN분

### B. 컷 키워드 전체 목록
> reports/weekly_YYYYMMDD_cuts.csv 별도 저장

### C. 신규 등록 키워드 전체 목록
> reports/weekly_YYYYMMDD_added.csv 별도 저장
```

---

## 슬랙 전송 포맷 (요약본)

`SLACK_WEBHOOK_URL` 있으면 보고서 1번 섹션 + 2번 변동표만 전송:

```
🍗 로얄호프 70원 주간 (5/11~5/17)
━━━━━━━━━━━━━━━━━━━━
✅ 살아있는 키워드 482,193개 (+12,340)
💰 비용 2,840,510원 / 노출 18.2M / 클릭 8,420
📊 CPC 평균 84원 / CTR 0.046%
━━━━━━━━━━━━━━━━━━━━
신규 +47,820 / 컷 -35,480 / 입찰↑ 28,910
⭐ 우수 키워드 NN개 발견

📎 상세: reports/weekly_20260518.md
```

---

## 장애 처리

### API rate limit 초과
- 429 응답 시 60초 sleep 후 재시도
- 3회 연속 실패 시 작업 중단 + 슬랙 알림

### 부분 실행 중단된 경우
- `data/last_week_stats.json` 보존되어 있음
- `--resume` 플래그로 stat 조회 스킵하고 재시작
- 컷 실행은 idempotent (이미 삭제된 키워드 응답 무시)

### 예비 풀 소진
- 보충 0건이어도 보고서는 정상 생성
- 다음 작업: `02_expand_keywords.py` 재실행 필요
- 보고서 상단에 ⚠️ 표시

---

## 첫 실행 시 체크리스트

- [ ] `.env` 모든 키 세팅
- [ ] `data/ad_groups.json` 그룹ID 매핑 완료
- [ ] `reserve_pool.db` 초기화 (예비 30만개 이상)
- [ ] cron 등록: `0 6 * * 1 cd /path/to/royalhof-70won && python scripts/weekly_pruner.py --execute --report`
- [ ] 첫 주는 `--dry-run` 으로 실행해서 분류 결과 검토
- [ ] 슬랙 웹훅 테스트 메시지 전송 확인

---

## TIGER 메모

- 한 번 돌리면 그 다음부터는 손 안 대도 굴러가게 만드는 게 목표
- 보고서 봐서 의사결정 필요할 때만 개입
- 우수 키워드 발견되면 별도 T&D 작성 / 전용 그룹으로 승격 고려
- 6주 운영 후 1단계 평가 → 200만 확장 결정
