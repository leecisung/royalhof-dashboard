# -*- coding: utf-8 -*-
"""
diagnose_powercontents.py — 파워컨텐츠 캠페인 키워드·소재 검수 상태 진단 (읽기 전용)

파워컨텐츠 노출 저조 원인(키워드 반려 의심) 파악:
- POWER_CONTENTS 캠페인의 키워드 상태(status/statusReason/inspectStatus) 집계
- 소재(ad)의 검수 상태(inspectStatus) 집계
- 반려/검토중/정상 분포 + 반려 키워드 예시 출력

출력: 콘솔 + reports/diagnose_powercontents_YYYYMMDD.md
"""

import os
import sys
import json
import logging
from collections import Counter
from pathlib import Path
from datetime import date

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

ACCOUNTS = [
    ("로얄호프치킨", "NAVER_AD"),
    ("버거리", "BURGEORI_NEW"),
    ("신규 버거리", "BURGEORI_OLD"),
]

# 검수 거절/문제로 간주할 상태 키워드 (대소문자 무시 부분일치)
BAD_HINTS = ["REJECT", "반려", "거절", "DISAPPROV", "INELIGIBLE", "RESTRICT", "LIMITED"]


def is_bad(value: str) -> bool:
    v = str(value).upper()
    return any(h.upper() in v for h in BAD_HINTS)


def items(res):
    return res if isinstance(res, list) else (res.get("items", []) if isinstance(res, dict) else [])


def main():
    load_dotenv(ROOT / ".env")
    out = []
    out.append(f"# 파워컨텐츠 검수 상태 진단 ({date.today().isoformat()})")
    out.append("")
    out.append("> 파워컨텐츠 노출 저조 원인 점검 — 키워드/소재 반려(검수 거절) 현황. 읽기 전용.")
    out.append("")

    dumped = False
    for label, prefix in ACCOUNTS:
        key = os.getenv(f"{prefix}_API_KEY")
        sec = os.getenv(f"{prefix}_SECRET_KEY")
        cid = os.getenv(f"{prefix}_CUSTOMER_ID")
        if not all([key, sec, cid]):
            continue
        api = NaverAdAPI(key, sec, cid)
        try:
            camps = items(api._request("GET", "/ncc/campaigns"))
        except Exception as e:
            print(f"[{label}] 캠페인 조회 실패: {e}")
            continue
        pc = [c for c in camps if c.get("campaignTp") == "POWER_CONTENTS"]
        if not pc:
            continue

        for c in pc:
            cname = c.get("name", "")
            groups = items(api._request("GET", "/ncc/adgroups",
                                        params={"nccCampaignId": c.get("nccCampaignId", "")}))
            kw_status = Counter()
            kw_reason = Counter()
            kw_inspect = Counter()
            ad_inspect = Counter()
            bad_kws = []
            total_kw = total_ad = 0

            for g in groups:
                gid = g.get("nccAdgroupId", "")
                gname = g.get("name", "")
                kws = api.get_keywords_by_group(gid)
                for kw in kws:
                    total_kw += 1
                    st = kw.get("status", "(없음)")
                    rs = kw.get("statusReason", "(없음)")
                    ins = kw.get("inspectStatus", "(없음)")
                    kw_status[st] += 1
                    kw_reason[rs] += 1
                    kw_inspect[ins] += 1
                    if is_bad(st) or is_bad(rs) or is_bad(ins):
                        bad_kws.append((kw.get("keyword", ""), gname, st, rs, ins))
                    if not dumped and kws:
                        print("=== 키워드 객체 원본 샘플 ===")
                        print(json.dumps(kw, ensure_ascii=False, indent=1))
                        print()
                        dumped = True
                for ad in api.get_ads_by_group(gid):
                    total_ad += 1
                    ad_inspect[ad.get("inspectStatus", "(없음)")] += 1

            out.append(f"## {label} — {cname}")
            out.append("")
            out.append(f"- 키워드 {total_kw}개 · 소재 {total_ad}개")
            out.append(f"- 키워드 status: `{dict(kw_status)}`")
            out.append(f"- 키워드 statusReason: `{dict(kw_reason)}`")
            out.append(f"- 키워드 inspectStatus: `{dict(kw_inspect)}`")
            out.append(f"- 소재 inspectStatus: `{dict(ad_inspect)}`")
            if bad_kws:
                out.append(f"- ⚠️ **반려/제한 의심 키워드 {len(bad_kws)}개** (상위 15개):")
                out.append("")
                out.append("  | 키워드 | 그룹 | status | statusReason | inspectStatus |")
                out.append("  |---|---|---|---|---|")
                for kw, gname, st, rs, ins in bad_kws[:15]:
                    out.append(f"  | {kw} | {gname[:16]} | {st} | {rs} | {ins} |")
            out.append("")
            print(f"[{label}] {cname}: 키워드 {total_kw}, 소재 {total_ad}, "
                  f"반려의심 {len(bad_kws)}")

    md = "\n".join(out)
    path = ROOT / "reports" / f"diagnose_powercontents_{date.today().strftime('%Y%m%d')}.md"
    path.write_text(md, encoding="utf-8-sig")
    print(f"\n{md}\n")
    print(f"✓ 저장: {path}")


if __name__ == "__main__":
    main()
