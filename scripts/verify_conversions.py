# -*- coding: utf-8 -*-
"""
verify_conversions.py — 전환추적 실측 검증 (읽기 전용)

trackingMode 플래그가 아니라 실제 전환수(ccnt)를 넓은 기간으로 직접 확인.
업체가 예전에 전환추적을 셋업했다면 충분히 긴 기간엔 ccnt가 잡혀야 함.

1) 3계정 전 캠페인의 2026-03-01~05-19 imp/clk/cost/ccnt 집계
2) 클릭 많은 캠페인 1개로 /stats 원본 응답 덤프 (전환 관련 필드 실제 구조 확인)
"""

import os
import sys
import json
import logging
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv
from lib.naver_api import NaverAdAPI

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(ROOT / "logs" / "api_calls.log", encoding="utf-8")],
)

SINCE = "2026-03-01"
UNTIL = "2026-05-19"
ACCOUNTS = [
    ("로얄호프치킨", "NAVER_AD"),
    ("버거리", "BURGEORI_NEW"),
    ("보승회관", "BURGEORI_OLD"),
]


def camp_stats(api: NaverAdAPI, cid: str):
    params = {
        "id": cid,
        "fields": '["impCnt","clkCnt","salesAmt","ctr","cpc","ccnt"]',
        "timeUnit": "day",
        "timeRange": json.dumps({"since": SINCE, "until": UNTIL}),
    }
    try:
        res = api._request("GET", "/stats", params=params)
    except Exception as e:
        return None
    imp = clk = cost = ccnt = 0
    for row in (res.get("data", []) if isinstance(res, dict) else []):
        if isinstance(row, dict):
            imp += int(row.get("impCnt", 0) or 0)
            clk += int(row.get("clkCnt", 0) or 0)
            cost += int(row.get("salesAmt", 0) or 0)
            ccnt += int(row.get("ccnt", 0) or 0)
    return imp, clk, cost, ccnt


def probe_raw(api: NaverAdAPI, cid: str):
    """전환 관련 확장 필드로 /stats 원본 응답 덤프."""
    for fields in (
        '["impCnt","clkCnt","ccnt","crto","convAmt","cpConv"]',
        '["impCnt","clkCnt","ccnt","drtCcnt","idrtCcnt","convAmt"]',
        '["impCnt","clkCnt","ccnt"]',
    ):
        params = {
            "id": cid,
            "fields": fields,
            "timeUnit": "allDays",
            "timeRange": json.dumps({"since": SINCE, "until": UNTIL}),
        }
        try:
            res = api._request("GET", "/stats", params=params)
            print(f"  fields={fields}")
            print(f"  → {json.dumps(res, ensure_ascii=False)[:600]}")
            print()
        except Exception as e:
            print(f"  fields={fields} → 오류: {e}")
            print()


def main():
    load_dotenv(ROOT / ".env")
    print(f"=== 전환 실측 검증  {SINCE} ~ {UNTIL} (약 11주) ===\n")

    biggest = None  # (api, campaign_id, clk)
    for label, prefix in ACCOUNTS:
        key = os.getenv(f"{prefix}_API_KEY")
        sec = os.getenv(f"{prefix}_SECRET_KEY")
        cid = os.getenv(f"{prefix}_CUSTOMER_ID")
        if not all([key, sec, cid]):
            print(f"[{label}] 자격증명 누락 — 스킵\n")
            continue
        api = NaverAdAPI(key, sec, cid)
        try:
            camps = api._request("GET", "/ncc/campaigns")
            camps = camps if isinstance(camps, list) else camps.get("items", [])
        except Exception as e:
            print(f"[{label}] 캠페인 조회 실패: {e}\n")
            continue

        print(f"### {label} (Customer {cid})")
        print(f"{'캠페인':<34}{'노출':>12}{'클릭':>9}{'비용':>12}{'전환':>8}")
        print("-" * 78)
        tot = [0, 0, 0, 0]
        for c in camps:
            r = camp_stats(api, c.get("nccCampaignId", ""))
            if r is None:
                continue
            imp, clk, cost, ccnt = r
            for i, v in enumerate(r):
                tot[i] += v
            if imp or cost:
                print(f"{c.get('name','')[:32]:<34}{imp:>12,}{clk:>9,}{cost:>12,}{ccnt:>8,}")
            if biggest is None or clk > biggest[2]:
                biggest = (api, c.get("nccCampaignId", ""), clk, f"{label} / {c.get('name','')}")
        print("-" * 78)
        print(f"{'합계':<34}{tot[0]:>12,}{tot[1]:>9,}{tot[2]:>12,}{tot[3]:>8,}")
        print(f"→ {label}: 11주간 전환수 합계 = {tot[3]:,}건\n")

    if biggest:
        print(f"=== /stats 원본 응답 덤프 — {biggest[3]} (클릭 {biggest[2]:,}) ===\n")
        probe_raw(biggest[0], biggest[1])


if __name__ == "__main__":
    main()
