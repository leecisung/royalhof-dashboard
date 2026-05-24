# -*- coding: utf-8 -*-
"""
3계정 통합 주간 보고서 v2 (2026-05-11 ~ 2026-05-17)
- 로얄호프치킨 (4328346)
- 버거리 신버 (2436096, 보승에프앤비 법인) — 키 인증 시 자동 포함
- 버거리 구버 (1861348, 미사용) — 키 인증 시 자동 포함

캠페인 단위 집계 + 캠페인별 키워드 상위 분석 + 전환(ccnt) 포함
출력: reports/weekly_20260511_0517.md
"""

import os
import sys
import json
import logging
from pathlib import Path
from datetime import date, datetime, timedelta

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

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

SINCE = "2026-05-11"
UNTIL = "2026-05-17"
BATCH_SIZE = 100

# 키워드 분석 임계치
MAX_KEYWORDS_PER_CAMPAIGN = 2000   # 이 이상이면 키워드 분석 스킵 (예: 70원전략 984K)
TOP_KEYWORD_LIMIT = 15             # 상위 키워드 표시 개수


def fetch_campaign_stats(api: NaverAdAPI, cid: str) -> tuple[int, int, int, int, int]:
    """캠페인 단위 (imp, clk, cost, ccnt, ctr%, cpc 원) 반환."""
    params = {
        "id": cid,
        "fields": '["impCnt","clkCnt","salesAmt","ctr","cpc","ccnt"]',
        "timeUnit": "day",
        "timeRange": json.dumps({"since": SINCE, "until": UNTIL}),
    }
    try:
        res = api._request("GET", "/stats", params=params)
    except Exception as e:
        logger.warning("  · stats 실패 %s: %s", cid, e)
        return 0, 0, 0, 0
    imp = clk = cost = ccnt = 0
    for row in res.get("data", []) if isinstance(res, dict) else []:
        if isinstance(row, dict):
            imp += int(row.get("impCnt", 0) or 0)
            clk += int(row.get("clkCnt", 0) or 0)
            cost += int(row.get("salesAmt", 0) or 0)
            ccnt += int(row.get("ccnt", 0) or 0)
    return imp, clk, cost, ccnt


def fetch_keyword_stats_batch(api: NaverAdAPI, keyword_ids: list[str]) -> dict[str, dict]:
    """키워드 ID들의 5/11-17 stats. {kw_id: {imp,clk,cost,ccnt}} 반환.

    응답 구조: {"data": [{"id":..., "impCnt":..., "clkCnt":..., "salesAmt":..., "ccnt":...}, ...]}
    (집계값이 한 row로 옴 — daily breakdown 아님)
    """
    stats: dict[str, dict] = {}
    for i in range(0, len(keyword_ids), BATCH_SIZE):
        batch = keyword_ids[i : i + BATCH_SIZE]
        params = {
            "ids": ",".join(batch),
            "fields": '["clkCnt","impCnt","salesAmt","ccnt"]',
            "timeUnit": "day",
            "timeRange": json.dumps({"since": SINCE, "until": UNTIL}),
        }
        try:
            res = api._request("GET", "/stats", params=params)
        except Exception as e:
            logger.warning("  · 키워드 stats 배치 실패: %s", e)
            continue
        for row in res.get("data", []) if isinstance(res, dict) else []:
            if not isinstance(row, dict):
                continue
            kw_id = row.get("id")
            if not kw_id:
                continue
            stats[kw_id] = {
                "imp": int(row.get("impCnt", 0) or 0),
                "clk": int(row.get("clkCnt", 0) or 0),
                "cost": int(row.get("salesAmt", 0) or 0),
                "ccnt": int(row.get("ccnt", 0) or 0),
            }
    return stats


def fetch_campaign_keywords(api: NaverAdAPI, campaign_id: str) -> list[dict]:
    """캠페인 내 모든 키워드 메타 반환. [{id, text, group_name, bid}, ...]"""
    # 1) 캠페인 → 그룹들
    groups = api._request("GET", "/ncc/adgroups", params={"nccCampaignId": campaign_id})
    groups = groups if isinstance(groups, list) else groups.get("items", [])

    keywords = []
    for g in groups:
        gid = g.get("nccAdgroupId", "")
        gname = g.get("name", "")
        kws = api.get_keywords_by_group(gid)
        for kw in kws:
            keywords.append({
                "id": kw.get("nccKeywordId", ""),
                "text": kw.get("keyword", ""),
                "group": gname,
                "bid": kw.get("bidAmt", 0),
                "user_lock": kw.get("userLock", False),
            })
    return keywords


def fetch_account_report(label: str, api_key: str, secret_key: str, customer_id: str) -> dict:
    """한 계정 — 캠페인 단위 집계 + 노출 있는 캠페인은 키워드 분석까지."""
    api = NaverAdAPI(api_key, secret_key, customer_id)
    logger.info("[%s] 캠페인 목록 조회…", label)
    camps = api._request("GET", "/ncc/campaigns")
    camps = camps if isinstance(camps, list) else camps.get("items", [])

    rows = []
    totals = {"imp": 0, "clk": 0, "cost": 0, "ccnt": 0}

    for c in camps:
        cid = c.get("nccCampaignId", "")
        name = c.get("name", "")
        ctype = c.get("campaignTp", "")
        daily_budget = c.get("dailyBudget", 0)
        use_daily_budget = c.get("useDailyBudget", False)

        imp, clk, cost, ccnt = fetch_campaign_stats(api, cid)
        ctr = (clk / imp * 100) if imp else 0
        cpc = (cost / clk) if clk else 0
        conv_rate = (ccnt / clk * 100) if clk else 0

        kw_section = None
        if imp > 0:
            # 캠페인 키워드 분석
            try:
                kw_list = fetch_campaign_keywords(api, cid)
                logger.info("  · [%s] 키워드 %d개 수집", name, len(kw_list))
                if 0 < len(kw_list) <= MAX_KEYWORDS_PER_CAMPAIGN:
                    kw_ids = [k["id"] for k in kw_list if k["id"]]
                    kw_stats = fetch_keyword_stats_batch(api, kw_ids)
                    # merge
                    enriched = []
                    for k in kw_list:
                        s = kw_stats.get(k["id"], {"imp": 0, "clk": 0, "cost": 0, "ccnt": 0})
                        enriched.append({**k, **s})
                    kw_section = {
                        "total": len(enriched),
                        "with_imp": sum(1 for k in enriched if k["imp"] > 0),
                        "with_clk": sum(1 for k in enriched if k["clk"] > 0),
                        "with_conv": sum(1 for k in enriched if k["ccnt"] > 0),
                        "items": enriched,
                    }
                elif len(kw_list) > MAX_KEYWORDS_PER_CAMPAIGN:
                    kw_section = {
                        "total": len(kw_list),
                        "skipped": True,
                        "items": [],
                    }
            except Exception as e:
                logger.exception("  · [%s] 키워드 분석 실패: %s", name, e)

        rows.append({
            "id": cid,
            "name": name,
            "type": ctype,
            "daily_budget": daily_budget,
            "use_daily_budget": use_daily_budget,
            "imp": imp,
            "clk": clk,
            "cost": cost,
            "ccnt": ccnt,
            "ctr": ctr,
            "cpc": cpc,
            "conv_rate": conv_rate,
            "kw_section": kw_section,
        })
        totals["imp"] += imp
        totals["clk"] += clk
        totals["cost"] += cost
        totals["ccnt"] += ccnt

        logger.info("  · %-40s imp=%-8d clk=%-5d cost=%-8d conv=%d", name[:40], imp, clk, cost, ccnt)

    return {"label": label, "customer_id": customer_id, "campaigns": rows, "totals": totals}


# ─────────────────────────────────────────────
# 마크다운 렌더링
# ─────────────────────────────────────────────

def fmt_won(n: int) -> str:
    return f"{n:,}원"


def fmt_int(n: int) -> str:
    return f"{n:,}"


def render_markdown(reports: list[dict], failed_accounts: list[str]) -> str:
    lines = []
    lines.append(f"# 주간 광고 보고서 — {SINCE} ~ {UNTIL}")
    lines.append("")
    lines.append(f"> 작성일: 2026-05-20 (TIGER)  ")
    lines.append(f"> 범위: 로얄호프치킨 / 버거리(신버·구버) 3개 네이버 검색광고 계정  ")
    lines.append(f"> 포함: 캠페인 집계 · 캠페인별 상위 키워드 · 전환수")
    lines.append("")
    if failed_accounts:
        lines.append(f"> ⚠️ **데이터 누락**: {', '.join(failed_accounts)} — API 키 인증 실패")
        lines.append("")

    # ── 요약
    grand_imp = sum(r["totals"]["imp"] for r in reports)
    grand_clk = sum(r["totals"]["clk"] for r in reports)
    grand_cost = sum(r["totals"]["cost"] for r in reports)
    grand_ccnt = sum(r["totals"]["ccnt"] for r in reports)
    grand_ctr = (grand_clk / grand_imp * 100) if grand_imp else 0
    grand_cpc = (grand_cost / grand_clk) if grand_clk else 0
    grand_cvr = (grand_ccnt / grand_clk * 100) if grand_clk else 0

    lines.append("## 1. 전체 요약")
    lines.append("")
    lines.append("| 지표 | 값 |")
    lines.append("|---|---:|")
    lines.append(f"| 총 노출 | {fmt_int(grand_imp)} |")
    lines.append(f"| 총 클릭 | {fmt_int(grand_clk)} |")
    lines.append(f"| 총 비용 | {fmt_won(grand_cost)} |")
    lines.append(f"| 총 전환 | {fmt_int(grand_ccnt)} |")
    lines.append(f"| 평균 CTR | {grand_ctr:.2f}% |")
    lines.append(f"| 평균 CPC | {grand_cpc:,.0f}원 |")
    lines.append(f"| 평균 전환율 | {grand_cvr:.2f}% |")
    lines.append("")

    # ── 계정별 요약
    lines.append("## 2. 계정별 요약")
    lines.append("")
    lines.append("| 계정 | Customer ID | 노출 | 클릭 | 비용 | 전환 | CTR | CPC | 전환율 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in reports:
        t = r["totals"]
        ctr = (t["clk"] / t["imp"] * 100) if t["imp"] else 0
        cpc = (t["cost"] / t["clk"]) if t["clk"] else 0
        cvr = (t["ccnt"] / t["clk"] * 100) if t["clk"] else 0
        lines.append(
            f"| {r['label']} | {r['customer_id']} | "
            f"{fmt_int(t['imp'])} | {fmt_int(t['clk'])} | "
            f"{fmt_won(t['cost'])} | {fmt_int(t['ccnt'])} | "
            f"{ctr:.2f}% | {cpc:,.0f}원 | {cvr:.2f}% |"
        )
    lines.append("")

    # ── 이슈
    lines.append("## 3. 이번 주 이슈")
    lines.append("")
    lines.append("### 3-1. 버거리 법인 이전")
    lines.append("- **구버(1861348)** : 법인 이전 전 사용하던 버거리 파워링크 계정. 현재 미사용.")
    lines.append("- **신버(2436096)** : 보승에프앤비 법인으로 옮긴 신규 계정. 5/11-17 운영.")
    lines.append("- 이전 직후 **입찰가 설정 이슈로 노출이 평소 대비 저조**. → 노출 회복 작업 필요.")
    lines.append("")
    lines.append("### 3-2. 페이스북 광고 예산 분배")
    lines.append("- 페이스북 광고 본격 집행으로 검색광고 예산 일부 이관.")
    lines.append("- **버거리 신버 일예산 3만원으로 축소 (2026-05-19부터 적용)**.")
    lines.append("- 다음주 보고서부터는 축소된 예산 기준 성과 반영.")
    lines.append("")
    lines.append("### 3-3. 로얄호프치킨 70원전략")
    lines.append("- 5/23(토) 오픈 예정. 5/11-17 기간에는 미런칭 → 키워드 등록 완료(984K) 단계.")
    lines.append("- 다음주 보고서부터 신규 캠페인 성과 포함.")
    lines.append("")

    # ── 계정별 캠페인 + 키워드 상세
    lines.append("## 4. 계정별 캠페인 + 키워드 상세")
    lines.append("")
    for idx, r in enumerate(reports, start=1):
        lines.append(f"### 4-{idx}. {r['label']} (Customer {r['customer_id']})")
        lines.append("")
        active = [x for x in r["campaigns"] if x["imp"] > 0 or x["cost"] > 0]
        zero = [x for x in r["campaigns"] if x["imp"] == 0 and x["cost"] == 0]

        if active:
            lines.append("**캠페인 단위**")
            lines.append("")
            lines.append("| 캠페인 | 유형 | 노출 | 클릭 | CTR | CPC | 전환 | 전환율 | 비용 |")
            lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
            for c in sorted(active, key=lambda x: -x["cost"]):
                lines.append(
                    f"| {c['name']} | {c['type']} | "
                    f"{fmt_int(c['imp'])} | {fmt_int(c['clk'])} | "
                    f"{c['ctr']:.2f}% | {c['cpc']:,.0f}원 | "
                    f"{fmt_int(c['ccnt'])} | {c['conv_rate']:.2f}% | "
                    f"{fmt_won(c['cost'])} |"
                )
            lines.append("")

            # 키워드 분석
            lines.append("**캠페인별 상위 키워드 (비용 기준)**")
            lines.append("")
            for c in sorted(active, key=lambda x: -x["cost"]):
                ks = c.get("kw_section")
                if not ks:
                    continue
                lines.append(f"#### · {c['name']}")
                lines.append("")
                if ks.get("skipped"):
                    lines.append(f"_키워드 {fmt_int(ks['total'])}개 — 분석 스킵 (임계치 {MAX_KEYWORDS_PER_CAMPAIGN:,}개 초과). 70원전략은 5/23 오픈 후 데이터 들어옴._")
                    lines.append("")
                    continue
                lines.append(
                    f"전체 키워드 **{fmt_int(ks['total'])}개** · "
                    f"노출 있음 **{fmt_int(ks['with_imp'])}개** · "
                    f"클릭 있음 **{fmt_int(ks['with_clk'])}개** · "
                    f"전환 있음 **{fmt_int(ks['with_conv'])}개**"
                )
                lines.append("")
                items = ks.get("items", [])
                top = sorted([k for k in items if k["imp"] > 0], key=lambda k: -k["cost"])[:TOP_KEYWORD_LIMIT]
                if top:
                    lines.append("| 키워드 | 그룹 | 노출 | 클릭 | CTR | CPC | 전환 | 비용 |")
                    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
                    for k in top:
                        ctr_k = (k["clk"] / k["imp"] * 100) if k["imp"] else 0
                        cpc_k = (k["cost"] / k["clk"]) if k["clk"] else 0
                        lines.append(
                            f"| {k['text']} | {k['group'][:20]} | "
                            f"{fmt_int(k['imp'])} | {fmt_int(k['clk'])} | "
                            f"{ctr_k:.2f}% | {cpc_k:,.0f}원 | "
                            f"{fmt_int(k['ccnt'])} | {fmt_won(k['cost'])} |"
                        )
                    lines.append("")
                # 비효율 키워드 — 클릭 10+ but 전환 0
                ineff = [k for k in items if k["clk"] >= 10 and k["ccnt"] == 0]
                if ineff:
                    ineff.sort(key=lambda k: -k["cost"])
                    lines.append(f"⚠️ **비효율 키워드** (클릭 10+ · 전환 0) — {len(ineff)}개. 상위 10개:")
                    lines.append("")
                    lines.append("| 키워드 | 그룹 | 클릭 | 비용 |")
                    lines.append("|---|---|---:|---:|")
                    for k in ineff[:10]:
                        lines.append(f"| {k['text']} | {k['group'][:20]} | {fmt_int(k['clk'])} | {fmt_won(k['cost'])} |")
                    lines.append("")

        if zero:
            lines.append(f"**무운영(노출 0) 캠페인** — {len(zero)}개")
            lines.append("")
            for c in zero:
                lines.append(f"- {c['name']} ({c['type']})")
            lines.append("")

        if not r["campaigns"]:
            lines.append("_캠페인 없음_")
            lines.append("")

    # ── 액션 아이템
    lines.append("## 5. 액션 아이템")
    lines.append("")
    lines.append("- [ ] 버거리 신버 입찰가 재점검 — 그룹 단위 입찰가/사용자 입찰가 mix 확인")
    lines.append("- [ ] 페이스북 광고 ROAS 별도 트래킹 (3만원 축소 후 검색 vs FB 비교)")
    lines.append("- [ ] 5/23 70원전략 캠페인 OFF→ON 전 광고소재·확장소재 최종 점검")
    lines.append("- [ ] 비효율 키워드 (클릭 10+ · 전환 0) OFF 검토 — 위 4-X 절 참조")
    lines.append("- [ ] 다음주 보고서: 70원전략 첫 주 키워드 분석 포함")
    lines.append("")

    return "\n".join(lines)


def main():
    load_dotenv(ROOT / ".env")
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)

    accounts = [
        ("로얄호프치킨",
         os.getenv("NAVER_AD_API_KEY"),
         os.getenv("NAVER_AD_SECRET_KEY"),
         os.getenv("NAVER_AD_CUSTOMER_ID")),
        ("버거리 신버",
         os.getenv("BURGEORI_NEW_API_KEY"),
         os.getenv("BURGEORI_NEW_SECRET_KEY"),
         os.getenv("BURGEORI_NEW_CUSTOMER_ID")),
        ("버거리 구버",
         os.getenv("BURGEORI_OLD_API_KEY"),
         os.getenv("BURGEORI_OLD_SECRET_KEY"),
         os.getenv("BURGEORI_OLD_CUSTOMER_ID")),
    ]

    reports = []
    failed = []
    for label, k, s, cid in accounts:
        if not all([k, s, cid]):
            logger.warning("[%s] 자격증명 누락 — 스킵", label)
            failed.append(label)
            continue
        try:
            r = fetch_account_report(label, k, s, cid)
            reports.append(r)
        except Exception as e:
            logger.exception("[%s] 보고서 생성 실패: %s", label, e)
            failed.append(label)

    md = render_markdown(reports, failed)
    out_path = ROOT / "reports" / "weekly_20260511_0517.md"
    out_path.write_text(md, encoding="utf-8-sig")
    logger.info("✓ 보고서 저장: %s", out_path)
    print(f"\n✓ 완료: {out_path}\n")


if __name__ == "__main__":
    main()
