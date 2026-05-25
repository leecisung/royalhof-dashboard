# -*- coding: utf-8 -*-
"""
GA4 Data API 1회성 OAuth 발급 스크립트.

목적: 본인(boseungfc@gmail.com)이 GA4 관리자이므로, Service Account 우회 없이
본인 계정의 토큰으로 GA4 Data API 직접 호출. GA4 admin SA 이메일 거부 이슈를
완전히 우회.

실행:
    python scripts/ga4_oauth_setup.py

흐름:
    1. .ga4-oauth-client.json (데스크톱 OAuth 클라이언트) 읽기
    2. 브라우저 자동 오픈 → 본인 계정 로그인 → analytics.readonly 권한 승인
    3. refresh_token + access_token을 .ga4-user-token.json에 저장
    4. 이후 ga4_api.py가 자동으로 이 토큰 사용

토큰 만료: refresh_token은 영구 (revoke 안 하는 한). access_token은 자동 갱신.
"""

import os
import sys
import json
import logging
from pathlib import Path

ROOT = Path(__file__).parents[1]

CLIENT_JSON = ROOT / ".ga4-oauth-client.json"
TOKEN_JSON = ROOT / ".ga4-user-token.json"

SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if not CLIENT_JSON.exists():
        print(f"❌ {CLIENT_JSON} 없음. GCP에서 OAuth 클라이언트(데스크톱 앱) 다운받아 이 경로에 두세요.")
        sys.exit(1)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("❌ google-auth-oauthlib 미설치. pip install google-auth-oauthlib")
        sys.exit(1)

    print(f"🔐 OAuth flow 시작 — 브라우저가 열립니다.")
    print(f"   본인 GA4 관리자 계정(boseungfc@gmail.com 등)으로 로그인 + Analytics 권한 승인.")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_JSON), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    # creds.to_json()은 access/refresh/expiry 모두 포함
    TOKEN_JSON.write_text(creds.to_json(), encoding="utf-8")
    print()
    print(f"✅ 토큰 저장 완료: {TOKEN_JSON}")
    print(f"   refresh_token 보유: {bool(creds.refresh_token)}")
    print(f"   scopes: {creds.scopes}")
    print()
    print("이제 .env 의 GA4_OAUTH_TOKEN_PATH 를 위 경로로 설정하거나,")
    print("ga4_api.py 가 기본 경로(.ga4-user-token.json)를 자동으로 찾습니다.")


if __name__ == "__main__":
    main()
