# -*- coding: utf-8 -*-
"""stats 응답 구조 디버깅."""
import os, sys, json
from pathlib import Path
ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from dotenv import load_dotenv
from lib.naver_api import NaverAdAPI

load_dotenv(ROOT / ".env")
api = NaverAdAPI(
    os.getenv("NAVER_AD_API_KEY"),
    os.getenv("NAVER_AD_SECRET_KEY"),
    os.getenv("NAVER_AD_CUSTOMER_ID"),
)

# 임의 캠페인 1개로 테스트
cid = "cmp-a001-01-000000010432490"  # 로얄 고비용 (P)
params = {
    "id": cid,
    "fields": '["impCnt","clkCnt","salesAmt"]',
    "timeUnit": "day",
    "timeRange": json.dumps({"since": "2026-05-11", "until": "2026-05-17"}),
}
res = api._request("GET", "/stats", params=params)
print(json.dumps(res, ensure_ascii=False, indent=2))
