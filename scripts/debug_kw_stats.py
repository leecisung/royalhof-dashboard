# -*- coding: utf-8 -*-
"""키워드 stats API 응답 구조 확인."""
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

# 로얄 고비용 (P) 캠페인의 그룹들
camp_id = "cmp-a001-01-000000010432490"
grps = api._request("GET", "/ncc/adgroups", params={"nccCampaignId": camp_id})
grps = grps if isinstance(grps, list) else grps.get("items", [])
print(f"그룹 {len(grps)}개")

# 첫 그룹의 키워드들
gid = grps[0].get("nccAdgroupId")
kws = api.get_keywords_by_group(gid)
print(f"키워드 {len(kws)}개")
kw_ids = [k.get("nccKeywordId") for k in kws[:5] if k.get("nccKeywordId")]
print(f"테스트 ID: {kw_ids}")

# timeRange + comma 시도
params3 = {
    "ids": ",".join(kw_ids),
    "fields": '["clkCnt","impCnt","salesAmt","ccnt"]',
    "timeUnit": "day",
    "timeRange": json.dumps({"since": "2026-05-11", "until": "2026-05-17"}),
}
try:
    res3 = api._request("GET", "/stats", params=params3)
    print("\n=== ids=comma + timeRange ===")
    print(json.dumps(res3, ensure_ascii=False, indent=2)[:3000])
except Exception as e:
    print(f"\n=== comma+timeRange 실패: {e}")

# JSON 배열 + timeRange 시도
params4 = {
    "ids": json.dumps(kw_ids),
    "fields": '["clkCnt","impCnt","salesAmt","ccnt"]',
    "timeUnit": "day",
    "timeRange": json.dumps({"since": "2026-05-11", "until": "2026-05-17"}),
}
try:
    res4 = api._request("GET", "/stats", params=params4)
    print("\n=== ids=JSON + timeRange ===")
    print(json.dumps(res4, ensure_ascii=False, indent=2)[:3000])
except Exception as e:
    print(f"\n=== JSON+timeRange 실패: {e}")
