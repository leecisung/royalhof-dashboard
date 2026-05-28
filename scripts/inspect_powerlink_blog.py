# -*- coding: utf-8 -*-
"""파워링크_블로그 vs 파워링크_홈페이지 그룹 구조·목적지 URL·상태 비교 (NEW 계정)."""
import os, sys
from pathlib import Path
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
from dotenv import load_dotenv; load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))
from lib.naver_api import NaverAdAPI
import logging; logging.basicConfig(level=logging.WARNING)

api = NaverAdAPI(os.getenv("BURGEORI_NEW_API_KEY"), os.getenv("BURGEORI_NEW_SECRET_KEY"), os.getenv("BURGEORI_NEW_CUSTOMER_ID"))
camps = api._request("GET", "/ncc/campaigns")
camps = camps if isinstance(camps, list) else camps.get("items", [])

for target in ["파워링크_블로그", "파워링크_홈페이지"]:
    c = next((x for x in camps if x.get("name") == target), None)
    if not c:
        print(f"[{target}] 없음"); continue
    print(f"\n{'='*72}\n캠페인: {target} | {c.get('status')} | 목적: {c.get('campaignTp')}")
    groups = api._request("GET", "/ncc/adgroups", params={"nccCampaignId": c["nccCampaignId"]})
    groups = groups if isinstance(groups, list) else groups.get("items", [])
    for g in groups:
        kws = api.get_keywords_by_group(g["nccAdgroupId"])
        locked = sum(1 for k in kws if k.get("userLock"))
        active = len(kws) - locked
        print(f"\n  [{g.get('name')}]")
        print(f"    그룹상태: {g.get('status')} / userLock(그룹): {g.get('userLock')} / statusReason: {g.get('statusReason','')}")
        print(f"    키워드: 총 {len(kws)} (활성 {active} / OFF {locked})")
        # 광고 소재 목적지 URL
        try:
            ads = api.get_ads_by_group(g["nccAdgroupId"])
            for ad in ads[:3]:
                adobj = ad.get("ad", {})
                pc = (adobj.get("pc") or {}).get("final", "")
                mo = (adobj.get("mobile") or {}).get("final", "")
                print(f"    소재 [{ad.get('status','')}] PC목적지: {pc}")
                print(f"                       모바일목적지: {mo}")
        except Exception as e:
            print(f"    소재 조회 실패: {e}")
