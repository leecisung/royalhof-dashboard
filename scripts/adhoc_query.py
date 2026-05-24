# -*- coding: utf-8 -*-
"""
adhoc_query.py — 임시 조회용
실행: python scripts/adhoc_query.py campaigns
     python scripts/adhoc_query.py groups --campaign cmp-xxx
"""

import os
import sys
import json
import argparse
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import load_dotenv
from lib.naver_api import NaverAdAPI


def main():
    load_dotenv(ROOT / ".env")
    api = NaverAdAPI(
        os.getenv("NAVER_AD_API_KEY"),
        os.getenv("NAVER_AD_SECRET_KEY"),
        os.getenv("NAVER_AD_CUSTOMER_ID"),
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=["campaigns", "groups"])
    parser.add_argument("--campaign", default="", help="그룹 조회 시 캠페인 ID")
    args = parser.parse_args()

    if args.cmd == "campaigns":
        # 전체 캠페인 목록 조회
        result = api._request("GET", "/ncc/campaigns")
        campaigns = result if isinstance(result, list) else result.get("items", [])
        print(f"\n{'='*60}")
        print(f"{'캠페인 ID':<30} {'유형':<10} {'이름'}")
        print(f"{'='*60}")
        for c in campaigns:
            cid   = c.get("nccCampaignId", "")
            ctype = c.get("campaignTp", "")
            name  = c.get("name", "")
            print(f"{cid:<30} {ctype:<10} {name}")
        print(f"{'='*60}")
        print(f"총 {len(campaigns)}개 캠페인\n")
        print("★ 기존 파워링크 캠페인 ID를 .env의 PROTECTED_CAMPAIGN_IDS에 입력하세요")

    elif args.cmd == "groups":
        if not args.campaign:
            print("--campaign 옵션 필요. 예: python scripts/adhoc_query.py groups --campaign cmp-xxx")
            sys.exit(1)
        result = api._request("GET", "/ncc/adgroups", params={"nccCampaignId": args.campaign})
        groups = result if isinstance(result, list) else result.get("items", [])
        print(f"\n{'='*60}")
        print(f"{'그룹 ID':<30} {'이름'}")
        print(f"{'='*60}")
        for g in groups:
            print(f"{g.get('nccAdgroupId',''):<30} {g.get('name','')}")
        print(f"{'='*60}")
        print(f"총 {len(groups)}개 그룹\n")


if __name__ == "__main__":
    main()
