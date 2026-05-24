# GA4 Data API 연동 가이드

대시보드에서 GA4 트래픽·전환 데이터를 가져오려면 GCP Service Account 인증이 필요. 10분 작업, 한 번만 하면 됨.

## 1. GCP 프로젝트 + Service Account 생성

1. https://console.cloud.google.com 접속 (회사 Google 계정)
2. 상단 프로젝트 선택 → **새 프로젝트** → 이름 `burgerry-dashboard` (또는 원하는 거)
3. 좌측 메뉴 → **API 및 서비스 → 사용 설정된 API 및 서비스**
4. **+ API 및 서비스 사용 설정** → "Google Analytics Data API" 검색 → 사용 설정
5. 좌측 메뉴 → **IAM 및 관리자 → 서비스 계정** → **+ 서비스 계정 만들기**
   - 이름: `dashboard-reader`
   - 역할: 없음 (다음 단계에서 GA4 쪽에서 권한 부여)
6. 서비스 계정 만들어진 후 → 해당 행 클릭 → **키** 탭 → **키 추가 → 새 키 만들기** → JSON 선택 → 다운로드
   - 다운로드된 JSON 파일을 안전한 곳에 보관 (예: `C:\Users\damho\Desktop\royalhof-70won\.ga4-credentials.json`)
   - ⚠️ 이 파일은 절대 git에 커밋 금지 (`.gitignore`에 이미 .env 포함되어 있다면 같은 줄에 추가)

## 2. GA4 속성에 Service Account 권한 부여

1. https://analytics.google.com 접속
2. 좌측 하단 톱니바퀴(관리) → 속성 열 → **속성 액세스 관리**
3. **+** 버튼 → 사용자 추가
4. 이메일: 위에서 만든 서비스 계정 이메일 (`dashboard-reader@burgerry-dashboard.iam.gserviceaccount.com` 형식)
5. 역할: **뷰어** (Viewer)
6. **추가**

## 3. GA4 Property ID 확인

1. https://analytics.google.com → 관리 → 속성 → **속성 세부정보**
2. 우측 상단 "속성 ID" 숫자 9자리 (예: `123456789`)
3. 또는 URL `https://analytics.google.com/analytics/web/#/p123456789/...`에서 `p` 다음 숫자

## 4. .env 설정

```env
GA4_PROPERTY_ID=123456789
GOOGLE_APPLICATION_CREDENTIALS=C:\Users\damho\Desktop\royalhof-70won\.ga4-credentials.json
```

⚠️ Windows 경로는 `\\` 또는 `/` 둘 다 가능. 절대경로 권장.

## 5. 검증

```powershell
python scripts/lib/ga4_api.py
```

→ 출력에 "✅ GA4 연결 성공" + 최근 7일 세션 수 나오면 OK.

오류 시 자주 보는 것:
- `403 PERMISSION_DENIED` → 2단계 권한 부여 미완료
- `503 invalid_grant` → JSON 키 경로 잘못. `GOOGLE_APPLICATION_CREDENTIALS` 확인
- `Property not found` → `GA4_PROPERTY_ID` 잘못

## 6. UTM 추적 권장 (네이버 광고 측)

메타 광고는 이미 UTM 박혀있음. 네이버도 광고 랜딩 URL에 UTM 박으면 GA4에서 채널 통합 비교 가능:

```
https://royalhofchicken.com/?utm_source=naver&utm_medium=cpc&utm_campaign=70원전략&utm_term={KEYWORD}
```

`{KEYWORD}`는 네이버 검색광고 매크로. 광고그룹/캠페인별로 url 박을 때 UTM 같이.

## 7. 보안

- `.ga4-credentials.json`은 `.gitignore`에 추가
- 또는 환경변수로 직접 박기: `setx GOOGLE_APPLICATION_CREDENTIALS "C:\..."`
- 토큰 만료 없음 (Service Account 키는 영구). 키 회전 원하면 GCP에서 삭제 후 재발급.
