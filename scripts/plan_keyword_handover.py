# -*- coding: utf-8 -*-
"""
대행사 중복 키워드 OFF 계획 (NEW 계정 4265143).

방침:
  - 대행사 캠페인(#01.파워링크, #00.상호명) = 건드리지 않음 (키워드 집합만 수집)
  - 우리 파워컨텐츠(버거리_파워컨텐츠_가맹광고01) = 건드리지 않음
  - 우리 나머지 캠페인(파워링크_홈페이지, 파워링크_블로그, 버거리_자상호, (입찰기용)버거리_고비용)
    에서 대행사와 '겹치는 키워드'만 OFF 대상으로 분류
  - 우리 고유(대행사에 없는) 키워드는 KEEP

출력만 함 (실행 안 함). --execute 주면 OFF(userLock=true) 실행.
"""

import os, sys, json, logging
from pathlib import Path
from collections import defaultdict

try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
from dotenv import load_dotenv; load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))
from lib.naver_api import NaverAdAPI
logging.basicConfig(level=logging.WARNING)

AGENCY_CAMPS = {"#01.파워링크", "#00.상호명"}
KEEP_CAMPS = {"버거리_파워컨텐츠_가맹광고01"}  # 우리 파워컨텐츠 = 건드리지 않음
OUR_TARGET_CAMPS = {"파워링크_홈페이지", "파워링크_블로그", "버거리_자상호", "(입찰기용) 버거리_고비용"}
# 겹침은 아니지만 사용자 지시로 함께 OFF: 고비용의 5만원 외톨이 키워드
EXTRA_OFF = {"(입찰기용) 버거리_고비용": {"소자본1인창업"}}

EXECUTE = "--execute" in sys.argv


def main():
    api = NaverAdAPI(os.getenv("BURGEORI_NEW_API_KEY"), os.getenv("BURGEORI_NEW_SECRET_KEY"), os.getenv("BURGEORI_NEW_CUSTOMER_ID"))
    camps = api._request("GET", "/ncc/campaigns")
    camps = camps if isinstance(camps, list) else camps.get("items", [])
    cmap = {c["nccCampaignId"]: c.get("name", "") for c in camps}

    def kws_of_campaign(cid):
        groups = api._request("GET", "/ncc/adgroups", params={"nccCampaignId": cid})
        groups = groups if isinstance(groups, list) else groups.get("items", [])
        out = []  # (group_name, group_status, keyword_obj)
        for g in groups:
            ks = api.get_keywords_by_group(g["nccAdgroupId"])
            for k in ks:
                out.append((g.get("name", ""), g.get("status", ""), k))
        return out

    # 1. 대행사 키워드 집합 수집
    agency_kw = set()
    for cid, name in cmap.items():
        if name in AGENCY_CAMPS:
            for _, _, k in kws_of_campaign(cid):
                agency_kw.add(k.get("keyword", "").strip())
    print(f"대행사 키워드 집합: {len(agency_kw)}개 ( #01.파워링크 + #00.상호명 )\n")

    # 2. 우리 대상 캠페인 분류
    off_targets = []  # (campaign, group, keyword, keyword_id, current_userLock)
    keep_unique = []  # (campaign, group, keyword)
    for cid, name in cmap.items():
        if name not in OUR_TARGET_CAMPS:
            continue
        print(f"{'='*70}\n캠페인: {name}")
        rows = kws_of_campaign(cid)
        c_off, c_keep = [], []
        for gname, gstatus, k in rows:
            kw = k.get("keyword", "").strip()
            kid = k.get("nccKeywordId")
            locked = k.get("userLock", False)
            extra = kw in EXTRA_OFF.get(name, set())
            if kw in agency_kw or extra:
                c_off.append((gname, kw, kid, locked))
                off_targets.append((name, gname, kw, kid, locked))
            else:
                c_keep.append((gname, kw))
                keep_unique.append((name, gname, kw))
        print(f"  대행사와 겹침(OFF 대상): {len(c_off)}개 / 우리 고유(KEEP): {len(c_keep)}개")
        if c_keep:
            print(f"  ▶ KEEP(우리만 가진 키워드):")
            for gname, kw in c_keep:
                print(f"      [{gname}] {kw}")
        if c_off:
            already = sum(1 for _,_,_,lk in c_off if lk)
            print(f"  ▶ OFF 대상(겹침){' (이미 OFF '+str(already)+'개 포함)' if already else ''}:")
            for gname, kw, kid, lk in c_off:
                print(f"      [{gname}] {kw}{'  (이미 OFF)' if lk else ''}")

    # 3. 요약
    to_lock = [t for t in off_targets if not t[4]]  # 아직 안 잠긴 것만
    print(f"\n{'#'*70}")
    print(f"# 요약")
    print(f"#  OFF 실행 대상(아직 켜져있는 겹침 키워드): {len(to_lock)}개")
    print(f"#  KEEP(우리 고유): {len(keep_unique)}개")
    print(f"#  파워컨텐츠 / 대행사 캠페인: 손 안 댐")
    print('#'*70)

    if not EXECUTE:
        print(f"\n[DRY-RUN] 실행하려면: python scripts/plan_keyword_handover.py --execute")
        return

    print(f"\n[EXECUTE] {len(to_lock)}개 키워드 OFF(userLock=true) 시작...")
    ok = 0
    for name, gname, kw, kid, _ in to_lock:
        try:
            api.lock_keyword(kid)
            ok += 1
        except Exception as e:
            print(f"  실패 [{name}/{gname}] {kw}: {e}")
    print(f"완료: {ok}/{len(to_lock)} OFF 처리")


if __name__ == "__main__":
    main()
