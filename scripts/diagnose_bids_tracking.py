# -*- coding: utf-8 -*-
"""
diagnose_bids_tracking.py — 진단 전용 (읽기 전용, 아무것도 수정 안 함)

#2  고비용 캠페인 입찰가 점검
    : 이름에 '고비용' 들어간 캠페인의 그룹 입찰가 + 키워드 실효 입찰가(bidAmt) 분석.
      관측 CPC가 2~5만원대인 원인이 입찰가에 있는지 확인.

#3  전환추적 점검
    : 3계정 전 캠페인의 trackingMode 점검. ccnt(전환수)=0 의 원인 규명.

출력: reports/diagnose_YYYYMMDD.md  (+ 콘솔)
"""

import os
import sys
import logging
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
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(ROOT / "logs" / "api_calls.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# (라벨, .env 변수 prefix)
ACCOUNTS = [
    ("로얄호프치킨", "NAVER_AD"),
    ("버거리", "BURGEORI_NEW"),
    ("보승회관", "BURGEORI_OLD"),
]


def fetch_items(api: NaverAdAPI, path: str, params: dict = None) -> list:
    r = api._request("GET", path, params=params)
    return r if isinstance(r, list) else r.get("items", [])


def effective_bid(kw: dict, group_bid: int) -> int:
    """키워드 실효 입찰가 — useGroupBidAmt면 그룹 입찰가, 아니면 키워드 입찰가."""
    if kw.get("useGroupBidAmt", False):
        return int(group_bid or 0)
    return int(kw.get("bidAmt", 0) or 0)


def analyze_account(label: str, prefix: str) -> dict:
    key = os.getenv(f"{prefix}_API_KEY")
    sec = os.getenv(f"{prefix}_SECRET_KEY")
    cid = os.getenv(f"{prefix}_CUSTOMER_ID")
    if not all([key, sec, cid]):
        logger.warning("[%s] 자격증명 누락 — 스킵", label)
        return None

    api = NaverAdAPI(key, sec, cid)
    logger.info("[%s] 캠페인 조회…", label)
    camps = api._request("GET", "/ncc/campaigns")
    camps = camps if isinstance(camps, list) else camps.get("items", [])

    tracking = []
    highcost = []
    for c in camps:
        name = c.get("name", "")
        tracking.append({
            "name": name,
            "type": c.get("campaignTp", ""),
            "trackingMode": c.get("trackingMode", "(없음)"),
            "userLock": c.get("userLock", False),
        })
        if "고비용" not in name:
            continue
        camp_id = c.get("nccCampaignId", "")
        groups = fetch_items(api, "/ncc/adgroups", {"nccCampaignId": camp_id})
        grp_rows = []
        for g in groups:
            gid = g.get("nccAdgroupId", "")
            gbid = int(g.get("bidAmt", 0) or 0)
            kws = api.get_keywords_by_group(gid)
            kw_rows = []
            for k in kws:
                kw_rows.append({
                    "keyword": k.get("keyword", ""),
                    "eff_bid": effective_bid(k, gbid),
                    "use_group": k.get("useGroupBidAmt", False),
                    "user_lock": k.get("userLock", False),
                })
            grp_rows.append({
                "name": g.get("name", ""),
                "group_bid": gbid,
                "user_lock": g.get("userLock", False),
                "keywords": kw_rows,
            })
        highcost.append({
            "name": name,
            "user_lock": c.get("userLock", False),
            "groups": grp_rows,
        })
        logger.info("  · 고비용 캠페인 [%s] 그룹 %d개", name, len(grp_rows))

    return {"label": label, "cid": cid, "tracking": tracking, "highcost": highcost}


def fmt_won(n) -> str:
    return f"{int(n or 0):,}원"


def render(results: list) -> str:
    L = []
    L.append(f"# 광고 진단 — 입찰가 · 전환추적 ({date.today().isoformat()})")
    L.append("")
    L.append("> 읽기 전용 진단. 입찰가·캠페인 변경 없음.")
    L.append("")

    # ── #2 입찰가 점검
    L.append("## #2. 고비용 캠페인 입찰가 점검")
    L.append("")
    L.append("관측 CPC 2~5만원의 원인 = 키워드 실효 입찰가. (실효 입찰가 = 그룹입찰가 사용 시 그룹값, 아니면 키워드 개별값)")
    L.append("")
    for r in results:
        if not r:
            continue
        if not r["highcost"]:
            continue
        L.append(f"### {r['label']} (Customer {r['cid']})")
        L.append("")
        for hc in r["highcost"]:
            state = "🔴 ON" if not hc["user_lock"] else "⚪ OFF(정지)"
            L.append(f"#### · {hc['name']}  —  {state}")
            L.append("")
            for g in hc["groups"]:
                kws = g["keywords"]
                bids = [k["eff_bid"] for k in kws]
                active_kws = [k for k in kws if not k["user_lock"]]
                if bids:
                    bmin, bmax = min(bids), max(bids)
                    bavg = sum(bids) / len(bids)
                else:
                    bmin = bmax = bavg = 0
                gstate = "OFF" if g["user_lock"] else "ON"
                L.append(
                    f"- **그룹: {g['name']}** ({gstate}) · 그룹 입찰가 {fmt_won(g['group_bid'])} · "
                    f"키워드 {len(kws)}개(ON {len(active_kws)})")
                L.append(
                    f"  - 실효 입찰가 — 최저 {fmt_won(bmin)} / 최고 {fmt_won(bmax)} / "
                    f"평균 {fmt_won(bavg)}")
                top = sorted(kws, key=lambda k: -k["eff_bid"])[:10]
                top = [k for k in top if k["eff_bid"] > 0]
                if top:
                    L.append("")
                    L.append("  | 키워드 | 실효 입찰가 | 입찰 방식 | 상태 |")
                    L.append("  |---|---:|---|---|")
                    for k in top:
                        mode = "그룹입찰가" if k["use_group"] else "개별입찰가"
                        st = "OFF" if k["user_lock"] else "ON"
                        L.append(
                            f"  | {k['keyword']} | {fmt_won(k['eff_bid'])} | {mode} | {st} |")
                    L.append("")
            L.append("")

    # ── #3 전환추적 점검
    L.append("## #3. 전환추적 점검 (ccnt=0 원인)")
    L.append("")
    L.append("Naver 검색광고의 전환수(ccnt)는 캠페인 `trackingMode` + 프리미엄 로그분석 연동이 있어야 집계됨.")
    L.append("`TRACKING_DISABLED` = 전환추적 꺼짐 → 전환이 발생해도 0으로 기록됨.")
    L.append("")
    for r in results:
        if not r:
            continue
        L.append(f"### {r['label']} (Customer {r['cid']})")
        L.append("")
        L.append("| 캠페인 | 유형 | trackingMode | ON/OFF |")
        L.append("|---|---|---|---|")
        for t in r["tracking"]:
            tm = t["trackingMode"]
            flag = " ⚠️" if tm == "TRACKING_DISABLED" else ""
            st = "OFF" if t["userLock"] else "ON"
            L.append(f"| {t['name']} | {t['type']} | {tm}{flag} | {st} |")
        L.append("")
        disabled_on = [t for t in r["tracking"]
                       if t["trackingMode"] == "TRACKING_DISABLED" and not t["userLock"]]
        total_on = [t for t in r["tracking"] if not t["userLock"]]
        L.append(
            f"→ ON 캠페인 {len(total_on)}개 중 **{len(disabled_on)}개가 전환추적 꺼짐(TRACKING_DISABLED)**.")
        L.append("")

    return "\n".join(L)


def main():
    load_dotenv(ROOT / ".env")
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)

    results = []
    for label, prefix in ACCOUNTS:
        try:
            results.append(analyze_account(label, prefix))
        except Exception as e:
            logger.exception("[%s] 진단 실패: %s", label, e)
            results.append(None)

    md = render(results)
    out = ROOT / "reports" / f"diagnose_{date.today().strftime('%Y%m%d')}.md"
    out.write_text(md, encoding="utf-8-sig")
    logger.info("✓ 진단 보고서 저장: %s", out)
    print(f"\n{md}\n")
    print(f"✓ 저장: {out}")


if __name__ == "__main__":
    main()
