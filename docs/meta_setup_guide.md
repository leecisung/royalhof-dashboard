# Meta(Facebook) Marketing API 자격증명 발급 가이드

> 대상: TIGER
> 소요시간: 약 30~60분
> 목적: weekly_meta_pruner.py가 사용할 API access token 확보

---

## 0. 사전 확인

다음이 이미 있어야 합니다 (없으면 광고 운영 자체가 안 되므로 있을 것):
- Facebook 개인 계정
- Meta Business Manager 계정 (business.facebook.com)
- Ad Account (광고 계정) — LLA 1%/3% 만든 그 계정
- Page (페이스북 페이지)
- Pixel (royalhofchicken.com / 버거리 사이트에 설치된)

---

## 1. Meta for Developers App 생성

1. https://developers.facebook.com 접속 → 우상단 **My Apps** → **Create App**
2. App 유형 선택: **Business** (Other도 됨)
3. 정보 입력:
   - **App name**: `royalhof-marketing-automation` (자유)
   - **Contact email**: TIGER 이메일
   - **Business Account**: 본인 Business Manager 선택
4. Create 완료 → App Dashboard 진입

### 1-1. App ID / App Secret 복사

App Dashboard 좌측 메뉴 → **App Settings → Basic**
- **App ID** (상단에 표시) → `.env`의 `META_APP_ID`에 저장
- **App Secret** (Show 클릭, 비밀번호 재입력) → `META_APP_SECRET`에 저장

---

## 2. Marketing API 추가

App Dashboard 좌측 메뉴 → **Add Product** → **Marketing API** 찾아서 **Set up**

설정 끝나면 좌측 메뉴에 "Marketing API" 가 추가됨.

---

## 3. System User + Access Token 발급

**왜 System User**: 개인 토큰은 60일 후 만료. System User 토큰은 영구 사용 가능.

### 3-1. System User 생성

1. https://business.facebook.com → 우상단 톱니바퀴 (**Business Settings**)
2. 좌측 메뉴 **Users → System Users → Add**
3. 정보 입력:
   - **System User name**: `royalhof-automation`
   - **System User role**: **Admin**
4. Create

### 3-2. System User에 Ad Account 권한 부여

1. 방금 만든 System User 클릭 → **Add Assets**
2. **Ad Accounts** 탭 → 광고 계정 선택 → **Manage campaigns** (전체 권한) 체크
3. Save

같은 방식으로 **Pages** 탭에서 페이지도 권한 부여.

### 3-3. Access Token 생성

1. System User 화면에서 **Generate New Token** 클릭
2. 정보 선택:
   - **App**: 1단계에서 만든 `royalhof-marketing-automation`
   - **Token expiration**: **Never** (System User라서 가능)
   - **Permissions** (체크):
     - `ads_management`
     - `ads_read`
     - `business_management`
     - `pages_show_list`
     - `pages_read_engagement`
3. Generate Token → 표시된 토큰 즉시 복사 (재표시 안 됨)
4. `.env`의 `META_ACCESS_TOKEN`에 저장

⚠️ **이 토큰은 비밀번호와 동급**. 노출되면 광고 계정 탈취당함. `.env`는 `.gitignore`에 있음 확인.

---

## 4. Ad Account ID 확인

방법 A (Business Manager):
1. Business Settings → **Accounts → Ad Accounts**
2. 광고 계정 클릭 → 상단에 `Account ID: 1234567890` 형식으로 표시
3. **`act_` 프리픽스 붙여서** 저장: `META_AD_ACCOUNT_ID=act_1234567890`

방법 B (Ads Manager URL):
- adsmanager.facebook.com 접속 → URL의 `act=숫자` 부분이 ID
- 예: `https://adsmanager.facebook.com/.../?act=1234567890` → `act_1234567890`

---

## 5. Page ID 확인

1. https://business.facebook.com → 좌측 메뉴 **Pages**
2. 페이지 클릭 → **Settings → Page Info** 스크롤하면 **Page ID** 표시
3. `.env`의 `META_PAGE_ID=숫자` 저장

또는 페이지 URL에서: `facebook.com/숫자` 형식이면 그 숫자가 Page ID.

---

## 6. Pixel ID 확인

1. https://business.facebook.com → 좌측 메뉴 **Events Manager**
2. royalhofchicken.com / 버거리 사이트에 연결된 Pixel 선택
3. **Settings** 탭 → 상단에 **Pixel ID** 표시
4. `.env`의 `META_PIXEL_ID=숫자` 저장

---

## 7. (선택) 기존 캠페인 보호

이미 운영 중인 다른 캠페인이 있다면, 자동 pruner가 실수로 건드리지 않도록 ID를 등록:

1. Ads Manager → **Campaigns** 탭 → 기존 캠페인 클릭
2. URL의 `selected_campaign_ids=숫자` 또는 캠페인 행의 ID 열 (열 표시 설정 필요)
3. 쉼표로 구분해서 `.env`에 저장:
   ```
   META_PROTECTED_CAMPAIGN_IDS=23851234567890,23851234567891
   ```

⚠️ **LLA 1%/3% 자동화 대상 캠페인은 여기 넣지 말 것** — 보호되면 pruner가 못 건드림.

---

## 8. .env 파일 최종 상태

`C:\Users\damho\Desktop\royalhof-70won\.env` 에 아래 형식으로 추가:

```env
# === 기존 (네이버) ===
NAVER_AD_API_KEY=...
NAVER_AD_SECRET_KEY=...
NAVER_AD_CUSTOMER_ID=...
PROTECTED_CAMPAIGN_IDS=cmp-...,cmp-...
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

# === Meta Marketing API ===
META_APP_ID=1234567890123456
META_APP_SECRET=abcdef0123456789abcdef0123456789
META_ACCESS_TOKEN=EAABxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
META_AD_ACCOUNT_ID=act_1234567890
META_PAGE_ID=1234567890
META_PIXEL_ID=1234567890
META_PROTECTED_CAMPAIGN_IDS=

# === Meta 운영 룰 (기본값 유지 권장) ===
META_DAILY_BUDGET_TOTAL_KRW=80000
META_CPA_PAUSE_THRESHOLD=50000
META_CPA_REDUCE_THRESHOLD=30000
META_CPA_BOOST_THRESHOLD=15000
META_FREQUENCY_FATIGUE=3.0
META_BUDGET_CAP_PER_ADSET_KRW=50000
META_PIXEL_EVENT=Lead
META_LEARNING_PROTECT_DAYS=14
META_LANDING_URL=https://burgerry.co.kr/
```

---

## 9. 검증

자격증명 입력 끝나면 디렉토리에서 실행:

```bash
python -c "from scripts.lib.meta_api import MetaAdsAPI; m = MetaAdsAPI.from_env(); print(m.get_account_summary(1))"
```

성공 시 어제 spend/impressions가 출력됩니다. 실패 시 에러 메시지에 따라:
- `OAuthException` → 토큰 만료 또는 권한 부족 → 3단계 다시
- `act_xxx not found` → Ad Account ID 잘못됨 → 4단계 다시
- `Permission denied` → System User에 Ad Account 권한 안 줌 → 3-2단계 다시

---

## 10. 다음 단계

자격증명 OK면 TIGER가 알려주세요. 그러면:
1. `scripts/meta_00_discover.py` 실행 → 계정의 캠페인/ad set/ad 자동 스캔, `meta_ad_sets.json`에 저장
2. 자동화 대상 ad set (LLA 1%, LLA 3%) 확인 후 `meta_weekly_pruner.py --dry-run` 으로 룰 시뮬레이션
3. 문제 없으면 cron 등록 (매주 월요일 06:00 KST)
