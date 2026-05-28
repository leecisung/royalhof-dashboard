# -*- coding: utf-8 -*-
"""
버거리(보승에프앤비) 네이버 계정 대행사 진입 감사.

목적:
  1. 최근 생성된 캠페인/그룹 식별 (대행사가 어제 5/28부터 운영 → regTm 기준)
  2. 우리 기존 키워드와 신규(대행사) 키워드 중복 검출 (자기경쟁/CPC 상승 위험)
  3. 파워컨텐츠 캠페인 상세 확인

스캔 대상: 버거리 NEW(4265143) + OLD(694291) 두 계정.
'최근' 기준: regTm >= CUTOFF (기본 2026-05-27T00:00Z, KST 5/27 09시 = 어제 전후 여유)
"""

import os, sys, json, logging
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

try: sys.stdout.reconfigure(encoding="utf-8")
except: pass

from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))
from lib.naver_api import NaverAdAPI

# 대행사 진입 시점. 5/28 KST 운영 시작 → 5/27 UTC 15시 이후가 5/28 KST.
# 여유롭게 5/27 00:00 UTC 부터 '최근'으로 본다.
CUTOFF = datetime(2026, 5, 27, 0, 0, 0, tzinfo=timezone.utc)


def parse_tm(s: str) -> datetime:
    if not s:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def kst(dt: datetime) -> str:
    from datetime import timedelta
    return (dt + timedelta(hours=9)).strftime("%m/%d %H:%M")


def list_campaigns(api):
    r = api._request("GET", "/ncc/campaigns")
    return r if isinstance(r, list) else r.get("items", [])


def list_adgroups(api, cid):
    r = api._request("GET", "/ncc/adgroups", params={"nccCampaignId": cid})
    return r if isinstance(r, list) else r.get("items", [])


def scan(label, cid, key, secret):
    print(f"\n{'#'*82}")
    print(f"# 계정: {label} (CID {cid})")
    print('#'*82)
    api = NaverAdAPI(key, secret, cid)

    camps = list_campaigns(api)
    # keyword_text -> list of (campaign_name, group_name, keyword_id, recent_bool)
    kw_index = defaultdict(list)
    recent_campaigns = []
    recent_groups = []
    powercontent_camps = []

    print(f"\n[캠페인 {len(camps)}개 — regTm 순]")
    print(f"  {'생성(KST)':<12} {'타입':<14} {'상태':<10} {'예산':>8}  campaign")
    for c in sorted(camps, key=lambda x: parse_tm(x.get("regTm")), reverse=True):
        reg = parse_tm(c.get("regTm"))
        is_recent = reg >= CUTOFF
        tp = c.get("campaignTp", "")
        mark = " ★신규" if is_recent else ""
        budget = c.get("dailyBudget", 0) if c.get("useDailyBudget") else 0
        print(f"  {kst(reg):<12} {tp:<14} {c.get('status',''):<10} {budget:>8,}  {c.get('name','')}{mark}")
        if is_recent:
            recent_campaigns.append(c)
        if tp == "POWER_CONTENTS" or "파워컨텐츠" in c.get("name", ""):
            powercontent_camps.append(c)

    # 그룹 + 키워드 인덱싱 (전 캠페인)
    print(f"\n[그룹 스캔 — 신규(★) 표시 + 키워드 수집 중...]")
    for c in camps:
        cname = c.get("name", "")
        try:
            groups = list_adgroups(api, c["nccCampaignId"])
        except Exception as e:
            print(f"  그룹 조회 실패 ({cname}): {e}")
            continue
        for g in groups:
            greg = parse_tm(g.get("regTm"))
            g_recent = greg >= CUTOFF
            if g_recent:
                recent_groups.append((cname, g))
            # 키워드 수집
            try:
                kws = api.get_keywords_by_group(g["nccAdgroupId"])
            except Exception:
                kws = []
            for k in kws:
                kw = k.get("keyword", "")
                if kw:
                    kw_index[kw].append({
                        "campaign": cname,
                        "group": g.get("name", ""),
                        "kid": k.get("nccKeywordId"),
                        "recent_group": g_recent,
                        "campaign_tp": c.get("campaignTp", ""),
                    })

    # 신규 캠페인 상세
    print(f"\n{'='*82}")
    print(f"[① 신규 캠페인] regTm >= {CUTOFF.date()} (대행사 추정)")
    print('='*82)
    if recent_campaigns:
        for c in recent_campaigns:
            print(f"  • {c.get('name','')} | {c.get('campaignTp','')} | {c.get('status','')} | "
                  f"생성 {kst(parse_tm(c.get('regTm')))} KST | 예산 {c.get('dailyBudget',0):,}")
    else:
        print("  (없음 — 이 계정엔 신규 캠페인 추가 안 됨)")

    # 신규 그룹 상세
    print(f"\n[② 신규 그룹] regTm >= {CUTOFF.date()}")
    if recent_groups:
        for cname, g in recent_groups:
            print(f"  • [{cname}] {g.get('name','')} | {g.get('status','')} | 생성 {kst(parse_tm(g.get('regTm')))} KST")
    else:
        print("  (없음)")

    # 키워드 중복 (같은 키워드가 2개+ 그룹/캠페인에 존재)
    print(f"\n[③ 키워드 중복] 같은 키워드가 2곳 이상 → 자기경쟁/CPC상승 위험")
    overlaps = {kw: locs for kw, locs in kw_index.items() if len(locs) >= 2}
    if overlaps:
        for kw, locs in sorted(overlaps.items(), key=lambda x: -len(x[1])):
            has_recent = any(l["recent_group"] for l in locs)
            flag = " ⚠️신규그룹포함" if has_recent else ""
            print(f"\n  '{kw}' — {len(locs)}곳{flag}")
            for l in locs:
                rm = " ★신규" if l["recent_group"] else ""
                print(f"      [{l['campaign']}] {l['group']}{rm}")
    else:
        print("  (계정 내 중복 없음)")

    # 파워컨텐츠
    print(f"\n[④ 파워컨텐츠 캠페인]")
    if powercontent_camps:
        for c in powercontent_camps:
            print(f"  • {c.get('name','')} | {c.get('status','')} | 생성 {kst(parse_tm(c.get('regTm')))} KST | "
                  f"예산 {c.get('dailyBudget',0):,}")
            try:
                groups = list_adgroups(api, c["nccCampaignId"])
                for g in groups:
                    greg = parse_tm(g.get("regTm"))
                    rm = " ★신규" if greg >= CUTOFF else ""
                    kws = api.get_keywords_by_group(g["nccAdgroupId"])
                    print(f"      그룹: {g.get('name','')} ({len(kws)}kw) {g.get('status','')}{rm}")
            except Exception as e:
                print(f"      그룹/키워드 조회 실패: {e}")
    else:
        print("  (파워컨텐츠 캠페인 없음)")

    return kw_index, recent_campaigns, recent_groups


def main():
    logging.basicConfig(level=logging.WARNING)
    accounts = [
        ("버거리 현재 NEW", os.getenv("BURGEORI_NEW_CUSTOMER_ID"), os.getenv("BURGEORI_NEW_API_KEY"), os.getenv("BURGEORI_NEW_SECRET_KEY")),
        ("버거리 예전 OLD", os.getenv("BURGEORI_OLD_CUSTOMER_ID"), os.getenv("BURGEORI_OLD_API_KEY"), os.getenv("BURGEORI_OLD_SECRET_KEY")),
    ]
    all_indexes = {}
    for label, cid, k, s in accounts:
        if not (cid and k and s):
            print(f"[건너뜀] {label} — 환경변수 없음")
            continue
        try:
            idx, _, _ = scan(label, cid, k, s)
            all_indexes[label] = idx
        except Exception as e:
            print(f"[에러] {label}: {e}")
            import traceback; traceback.print_exc()

    # 계정 간 중복 (NEW vs OLD)
    if len(all_indexes) == 2:
        labels = list(all_indexes.keys())
        a, b = all_indexes[labels[0]], all_indexes[labels[1]]
        cross = set(a.keys()) & set(b.keys())
        print(f"\n{'='*82}")
        print(f"[⑤ 계정 간 키워드 중복] {labels[0]} ↔ {labels[1]}")
        print('='*82)
        if cross:
            for kw in sorted(cross):
                print(f"  '{kw}'")
                for lbl in labels:
                    for l in all_indexes[lbl][kw]:
                        print(f"      [{lbl}] {l['campaign']} / {l['group']}")
        else:
            print("  (계정 간 중복 키워드 없음)")


if __name__ == "__main__":
    main()
