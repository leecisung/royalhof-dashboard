# 로얄호프치킨 70원 전략 + 버거리 Meta 광고 운영

> 클로드코드가 부팅할 때 이 파일을 먼저 읽고 컨텍스트 파악할 것.
>
> 이 저장소는 **2개 채널·2개 브랜드**의 광고 자동화를 함께 관리한다:
> - **로얄호프치킨 / 네이버 검색광고**: 70원 캡 롱테일 (섹션 1~8)
> - **버거리 / Meta(FB) 광고**: 기존 LLA 1%/3% 자동 운영 (섹션 9)
> 네이버 코드와 Meta 코드는 같은 lib(`reporter.py` 등)와 같은 `.env`, 같은 로그 파일을 공유한다.

---

## 1. 프로젝트 개요

- **브랜드 1 / 채널 1**: 로얄호프치킨 (보승에프앤비) → 네이버 검색광고
- **브랜드 2 / 채널 2**: 버거리 (burgerry.co.kr) → Meta(FB) Conversion 광고
- **운영자**: TIGER (마케팅팀장)
- **랜딩**:
  - 네이버: royalhofchicken.com (창업 페이지)
  - Meta: burgerry.co.kr (Pixel 이벤트 = Lead)
- **전략**: 70원 캡 롱테일 다량 등록 + 주간 자연선택 (네이버) / LLA 1%·3% 자동 예산조정 (Meta)
- **규모**: 100만개 키워드 + Meta 일 8만원 (2개 ad set × 4만원)
- **측정**: GA4 + Meta Pixel (기존세팅)
- **시작일**: 2026년 5월

### 전략 핵심
- 입찰가 70원 캡으로 자동 필터링
- 검색량 적어도 키워드 수로 노출 만회 (강남BAR 패턴 확장)
- 매주 노출 0인 키워드 컷 → 예비 풀에서 동량 보충
- 살아남는 8~15만개가 알맹이

---

## 2. API 자격증명

`.env` 파일 필수:
```
NAVER_AD_API_KEY=...
NAVER_AD_SECRET_KEY=...
NAVER_AD_CUSTOMER_ID=...
SLACK_WEBHOOK_URL=... (옵션, 보고서 전송용)
GA4_PROPERTY_ID=... (옵션, 전환 매칭용)
```

### Naver Search Ad API
- Base URL: `https://api.searchad.naver.com`
- 인증: HMAC-SHA256 서명 (`X-Signature` 헤더)
- Rate limit: **5 calls/sec, 100 keywords/batch**
- 핵심 엔드포인트:
  - `/keywordstool` GET - 연관키워드 조회
  - `/ncc/keywords` POST - 키워드 등록 (batch 100)
  - `/ncc/keywords/{id}` PUT - 입찰가 수정
  - `/ncc/keywords/{id}` DELETE - 키워드 삭제
  - `/stats` GET - 성과 데이터

### 알려진 함정 (이전 작업에서 학습)
- `datePreset` 파라미터는 동작 안 함 → `dateRange.since/until` 명시
- 한글 키워드 URL 인코딩 시 `quote(kw, safe='')` 사용
- 키워드 stat 응답은 nested - `data.statList[].statData[]` 순회

---

## 3. 디렉토리 구조

```
royalhof-70won/
├── CLAUDE.md                    # 이 파일
├── .env                         # 자격증명 (gitignore)
├── data/
│   ├── seeds.json               # 시드 150개 (5축 분류)
│   ├── regions.json             # 행정동 3,500개
│   ├── competitors.json         # 경쟁사 사전
│   ├── suffixes.json            # 변형 접미사 (창업비용/문의/본사 등)
│   ├── reserve_pool.db          # SQLite, 예비 키워드 풀
│   └── ad_groups.json           # 그룹ID ↔ 그룹명 매핑
├── scripts/
│   ├── 01_generate_seeds.py     # 1회성: 100~150만 조합 생성
│   ├── 02_expand_keywords.py    # 1회성: keywordstool 펼침
│   ├── 03_register.py           # 1회성: 70만개 초기 등록
│   ├── weekly_pruner.py         # ★ 매주 월요일 새벽 cron
│   ├── adhoc_query.py           # 임시 조회용
│   └── lib/
│       ├── naver_api.py         # API 클라이언트 (HMAC, 재시도)
│       ├── reserve_pool.py      # 예비 풀 DB 인터페이스
│       └── reporter.py          # 마크다운 보고서 생성
├── reports/
│   └── weekly_YYYYMMDD.md       # 매주 보고서
└── logs/
    └── api_calls.log
```

---

## 4. 광고그룹 구조 (6축)

| 그룹 | 키워드 예시 | 입찰가 시작 |
|---|---|---|
| G1_메인변형 | 치킨창업, 호프창업 + 비용/방법/본사 | 70원 |
| G2_지역창업 | 강남구치킨창업, 역삼동호프집창업… (지역×카테고리) | 70원 |
| G3_경쟁치킨 | BBQ창업, 노랑통닭창업, 굽네창업 + 변형 | 70원 |
| G4_경쟁호프 | 가르텐비어창업, 역전할머니맥주, 압구정생활맥주 | 70원 |
| G5_상황조건 | 소자본창업, 퇴직창업, 부부창업, 1억창업 | 70원 |
| G6_메뉴결합 | 생맥주창업, 수제맥주창업, 안주창업 | 70원 |

> 그룹당 1,000개 한도. G2(지역창업)는 시·도 단위로 또 쪼개야 함.

`data/ad_groups.json` 형식:
```json
{
  "G1_메인변형": { "group_id": "grp-a001-...", "campaign_id": "cmp-a001-..." },
  "G2_지역창업_서울": { "group_id": "grp-a002-...", ... },
  ...
}
```

---

## 5. 운영 룰

### 입찰가 단계
- **시작**: 전 키워드 70원
- **7일 노출 0** → 100원 ↑
- **14일 노출 0 (100원에서도)** → 컷 (DELETE)
- **CPC 200원 초과** → 즉시 컷
- **CTR 1% 미만 + 노출 10,000+** → 컷
- **우수 (CPC 70~100원 + 노출 안정)** → 별도 트래킹, 그대로 유지

### 컷 vs OFF
- 노출 0 → **DELETE** (계정 슬롯 회복)
- 성과 애매·검토 필요 → **userLock=true** (OFF 유지)

### 쿨다운
- DELETE된 키워드는 **30일 동안 재등록 금지**
- 예비 풀 DB에 `cooldown_until` 컬럼으로 관리

### 보충 룰
- 컷한 수량만큼 예비 풀에서 즉시 보충
- 예비 풀 잔량 < 10만개면 → keywordstool 재펼침 트리거
- 그룹별 키워드 수가 1,000개에 근접하면 신규 그룹 자동 생성

---

## 6. 보고서 (매주 월요일)

`scripts/weekly_pruner.py` 실행 시 자동 생성:
- 파일: `reports/weekly_YYYYMMDD.md`
- 슬랙: `SLACK_WEBHOOK_URL` 있으면 자동 전송
- 포맷: `weekly_report_task.md` 참조

---

## 7. 코딩 규칙 (TIGER 선호)

- **모든 코드 변경은 풀파일로 전달** - diff/patch 형식 금지
- 한국어 인코딩: `utf-8-sig` (엑셀 호환)
- 응답 dict 순회 시 `.get()` 사용 (KeyError 방지)
- 모든 API 호출에 logging (`logs/api_calls.log`)
- 실패 시 exponential backoff 3회 재시도 (1s, 2s, 4s)
- `print` 보다 `logging.info` 선호
- 비밀키는 절대 코드에 하드코딩 금지

---

## 8. 다음 단계 (현재 시점)

- [ ] 1회성: `01_generate_seeds.py` 작성 → 행정동/경쟁사/접미사 사전 구축
- [ ] 1회성: `02_expand_keywords.py` 작성 → 100만 키워드 풀 생성
- [ ] 1회성: `03_register.py` 작성 → 70만개 초기 등록 (예비 30만은 reserve_pool.db)
- [ ] 영구: `weekly_pruner.py` 작성 ★ **우선순위 최상**
- [ ] 영구: 매주 월요일 06:00 KST cron 등록

**작업 시작 시 weekly_pruner.py 먼저 짜는 게 안전함.** 등록부터 하면 다음주에 손으로 못 정리.

---

## 9. Meta(FB) 광고 운영 룰 (버거리)

### 9.1 기본 정보
- **브랜드**: 버거리 (boseung F&B 별도 브랜드)
- **랜딩**: https://burgerry.co.kr/
- **광고 목적**: Conversion — 사이트 방문 + Pixel `Lead` 이벤트 최적화
- **자격증명**: `META_*` (.env). 발급법은 `docs/meta_setup_guide.md`
- **SDK**: `facebook-business` (Python). 모든 호출은 `scripts/lib/meta_api.py` 경유.

### 9.2 자동화 대상
- 기존 LLA 1%, LLA 3% ad set 2개. 각 일 40,000원.
- 크리에이티브 A/B 동영상(점주 창업 영상)은 이미 등록됨. AI는 ad 생성/업로드 안 함.
- `data/meta_ad_sets.json`의 `managed` 영역에서 enabled=true인 ad set만 처리.

### 9.3 운영 룰 (`meta_weekly_pruner.py`)

| 조건 | 조치 | 비고 |
|---|---|---|
| **학습기간** (생성 후 14일 이내) | pause/reduce 면제 | `META_LEARNING_PROTECT_DAYS` |
| CPA > 50,000원 | 🛑 일시정지 | `META_CPA_PAUSE_THRESHOLD` |
| CPA > 30,000원 + spend > 5만원 | 📉 예산 50% 삭감 | `META_CPA_REDUCE_THRESHOLD` |
| CPA < 15,000원 + 전환 ≥ 5건 | 📈 예산 30% 증액 (캡 5만원) | `META_CPA_BOOST_THRESHOLD`, `META_BUDGET_CAP_PER_ADSET_KRW` |
| 전환 0 + spend ≥ 5만원 | 🛑 일시정지 | CPA 자체가 ∞이므로 spend 기준 |
| frequency > 3.0 | ⚠️ flag (자동 X) | `META_FREQUENCY_FATIGUE`. 사람이 크리에이티브 교체 |
| 7일 노출 == 0 | ⚠️ flag (자동 X) | 사람이 확인 |
| 그 외 | ✅ 유지 | |

**왜 학습기간 보호**: Meta Conversion 최적화는 ad set당 50 conversions/7d를 채워야 안정화됨. 일 4만원 예산에선 첫 2주는 데이터 부족 → 섣불리 컷하면 학습 망침.

### 9.4 안전장치
- `META_PROTECTED_CAMPAIGN_IDS`에 등록된 캠페인 소속 ad set은 `meta_api.py` 내부에서 변경 차단 (`MetaProtectedError`).
- 자동화 대상 LLA 캠페인은 여기 절대 넣지 말 것 — 보호되면 pruner가 못 건드림.
- 모든 write 메서드(`pause_ad_set`, `update_ad_set_budget`)는 호출 직전 campaign_id를 다시 조회해서 검증.

### 9.5 결정 이력
- `data/meta_state.db` (SQLite). 매주 결정 + 실행 결과 누적.
- 조회: `python scripts/meta_adhoc_query.py history --limit 50`

### 9.6 디렉토리

```
scripts/
├── lib/meta_api.py             # facebook-business 래퍼 (HMAC 대신 access token, 같은 retry/rate-limit)
├── meta_00_discover.py         # 1회/필요 시: 계정 자산 발견 + managed 등록
├── meta_weekly_pruner.py       # ★ 매주 월요일 cron
└── meta_adhoc_query.py         # account / ad_sets / history 조회

data/
├── meta_ad_sets.json           # managed + discovered
└── meta_state.db               # 결정 이력 SQLite

docs/
└── meta_setup_guide.md         # 자격증명 발급법 (TIGER용)

reports/
└── weekly_meta_YYYYMMDD.md     # 매주 자동 생성
```

### 9.7 실행 순서 (처음)
1. `docs/meta_setup_guide.md` 보고 .env 채움
2. `pip install -r requirements.txt` (facebook-business 추가됨)
3. `python scripts/meta_00_discover.py` — 자격증명 검증 + 모든 ad set 콘솔 출력
4. 표에서 LLA 1%, LLA 3% ad set ID 확인 후:
   ```
   python scripts/meta_00_discover.py --enable <ID> --alias LLA_1pct --budget 40000
   python scripts/meta_00_discover.py --enable <ID> --alias LLA_3pct --budget 40000
   ```
5. `python scripts/meta_weekly_pruner.py --dry-run` — 룰 시뮬레이션
6. 결과 정상이면 cron 등록 (매주 월 06:00, 네이버와 같이)

### 9.8 첫 2주는 사람이 본다
- 학습기간 보호 룰이 작동하지만, frequency / impressions=0 등 flag는 사람이 봐야 함.
- 3주차부터 룰의 임계값이 실제와 맞는지 검증 후 .env 조정.
- claude-ads 플러그인(설치 시) `/ads:audit` 으로 추가 질적 감사 가능.

### 9.9 알려진 미해결
- Meta API는 `daily_budget`를 계정 통화 minor unit으로 받음. KRW는 minor unit 없음 → 정수가 곧 원. 다른 통화 계정엔 적용 불가 (현재 가정: 계정 통화 = KRW).
- Lead 추적은 Pixel `Lead` 이벤트 기준. 다른 이벤트로 최적화하려면 `META_PIXEL_EVENT` 변경.
- Conversion 학습기간 50건/7d 미달 시 Meta가 자체 학습불가 표시 — pruner는 그 신호는 안 봄 (단순히 CPA/spend만 봄).
