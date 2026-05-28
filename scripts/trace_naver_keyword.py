# -*- coding: utf-8 -*-
"""
버거리 네이버 NEW 계정 5/26 캠페인별 → 그룹별 → 키워드별 클릭 까서
가맹문의 유력 키워드 후보 식별.

(breakdown=pcMobile 은 응답에 device 필드 안 박혀서 사용 불가 — 디바이스 분리는 포기,
 GA4의 m.search.naver.com 매칭으로 모바일임은 확정. 캠페인 성격으로 가맹 의도 추정.)
"""

import os, sys, json, logging
from pathlib import Path
from datetime import datetime, date

try: sys.stdout.reconfigure(encoding="utf-8")
except: pass

from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))
from lib.naver_api import NaverAdAPI


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def stats_for_day(api, ids, target):
    if not ids:
        return {}
    out = {}
    BATCH = 100
    for i in range(0, len(ids), BATCH):
        batch = ids[i:i+BATCH]
        params = {
            "ids": ",".join(batch),
            "fields": '["clkCnt","impCnt","salesAmt"]',
            "timeRange": json.dumps({"since": str(target), "until": str(target)}),
        }
        res = api._request("GET", "/stats", params=params)
        for r in res.get("data", []) if isinstance(res, dict) else []:
            out[r.get("id")] = {
                "clk": int(r.get("clkCnt", 0) or 0),
                "imp": int(r.get("impCnt", 0) or 0),
                "cost": int(r.get("salesAmt", 0) or 0),
            }
    return out


def main():
    target = parse_date(sys.argv[1] if len(sys.argv) > 1 else "2026-05-26")
    logging.basicConfig(level=logging.WARNING)

    cid = os.getenv("BURGEORI_NEW_CUSTOMER_ID")
    api = NaverAdAPI(os.getenv("BURGEORI_NEW_API_KEY"), os.getenv("BURGEORI_NEW_SECRET_KEY"), cid)

    print(f"\n계정: 버거리 NEW (CID {cid}) — {target}")
    print("="*80)

    # 1. 캠페인
    camps = api._request("GET", "/ncc/campaigns")
    camp_list = camps if isinstance(camps, list) else camps.get("items", [])
    camp_stats = stats_for_day(api, [c["nccCampaignId"] for c in camp_list], target)

    print(f"\n[캠페인 클릭 분포 — 5/26]")
    print(f"  {'campaign':<55} {'clk':>5} {'imp':>7} {'cost':>7}")
    sorted_camps = sorted(camp_list, key=lambda c: -camp_stats.get(c["nccCampaignId"], {}).get("clk", 0))
    for c in sorted_camps:
        s = camp_stats.get(c["nccCampaignId"], {})
        if s.get("clk", 0) == 0 and s.get("imp", 0) == 0:
            continue
        print(f"  {c.get('name','')[:55]:<55} {s.get('clk',0):>5} {s.get('imp',0):>7} {s.get('cost',0):>7,}")

    # 2. 모든 캠페인 그룹·키워드 drilldown (클릭 있는 캠페인만)
    for c in sorted_camps:
        cid_ = c["nccCampaignId"]
        cname = c.get("name", cid_)
        s = camp_stats.get(cid_, {})
        if s.get("clk", 0) == 0:
            continue
        print(f"\n  ▶ 캠페인: {cname} (클릭 {s['clk']})")
        try:
            groups = api._request("GET", "/ncc/adgroups", params={"nccCampaignId": cid_})
        except Exception as e:
            print(f"    그룹 조회 실패: {e}")
            continue
        glist = groups if isinstance(groups, list) else groups.get("items", [])
        gstats = stats_for_day(api, [g["nccAdgroupId"] for g in glist], target)

        for g in sorted(glist, key=lambda x: -gstats.get(x["nccAdgroupId"], {}).get("clk", 0)):
            gs = gstats.get(g["nccAdgroupId"], {})
            if gs.get("clk", 0) == 0:
                continue
            print(f"\n    그룹: {g.get('name','')[:50]:<50} clk={gs['clk']} imp={gs['imp']}")
            # 키워드까지 확장 (클릭 ≥ 1 만)
            try:
                kws = api.get_keywords_by_group(g["nccAdgroupId"])
            except Exception as e:
                print(f"      키워드 조회 실패: {e}")
                continue
            kid_to_kw = {k.get("nccKeywordId"): k.get("keyword", "") for k in kws}
            kstats = stats_for_day(api, list(kid_to_kw.keys()), target)
            rows = [(kid_to_kw.get(kid, kid), v) for kid, v in kstats.items() if v.get("clk", 0) > 0]
            rows.sort(key=lambda x: -x[1]["clk"])
            print(f"      {'keyword':<35} {'clk':>4} {'imp':>5}")
            for kw, v in rows[:30]:
                print(f"      {kw[:35]:<35} {v['clk']:>4} {v['imp']:>5}")


if __name__ == "__main__":
    main()
