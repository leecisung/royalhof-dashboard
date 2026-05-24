# -*- coding: utf-8 -*-
"""
report_weekly.py — 3계정 통합 주간 광고 보고서 (조치·전략 위주 + 3주 추이)

구성:
  1. 주간 KPI 추이  — 전전주 → 전주 → 이번주. 로얄은 강남BAR 포함/제외 별도 표.
  2. 이번 주 조치   — OFF 권장 키워드 + 정지·검토 권장 캠페인
  3. 전략 제언      — 입찰기·고비용 캠페인 계속 여부, 70원전략, 다음 주 할 일
  4. 계정·캠페인 실적 — 이번주 캠페인 단위 표
  5. 참고

대상 계정 (.env): 로얄호프치킨 / 버거리 / 신규 버거리(Customer 694291)

사용:
  python scripts/report_weekly.py                          # 직전 월~일 1주
  python scripts/report_weekly.py --since 2026-05-11 --until 2026-05-17
  python scripts/report_weekly.py --since 2026-05-11 --until 2026-05-17 --no-html

출력: reports/weekly_<since>_<until>.md  (+ .html)
"""

import os
import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import date, datetime, timedelta

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
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

BATCH_SIZE = 100
MAX_KEYWORDS_PER_CAMPAIGN = 2000   # 이 이상이면 키워드 분석 스킵 (예: 70원전략 984K)

# '노출용' 캠페인 — 노출 확보 전용. 실효율 KPI는 이 캠페인 포함/제외 두 버전으로 분리.
EXCLUDE_CAMPAIGN = "노출용"

# 버거리에서 아예 제외할 캠페인(브랜드/일반 트래픽) — 가맹모집 성과만 집계.
BURGEORI_EXCLUDE = ["파워링크"]

# 판정 임계값 — 비싼 키워드가 문제. 노출 많고 CPC 싼 키워드는 노출 전략상 정상.
OFF_KW_MIN_CPC = 10000   # 조치 권장 키워드: 평균 CPC 1만원 초과 (클릭 1건에 1만원+)
OFF_KW_OFF_CPC = 30000   # CPC 이 이상이면 OFF, 미만이면 입찰가 인하 권장


# ─────────────────────────────────────────────
# 데이터 수집
# ─────────────────────────────────────────────

def parse_stats_daily(res) -> list:
    """/stats 응답 → [(date_str, imp, clk, cost), ...]"""
    out = []
    for row in (res.get("data", []) if isinstance(res, dict) else []):
        if not isinstance(row, dict):
            continue
        d = row.get("dateStart") or row.get("dateEnd")
        if not d:
            continue
        out.append((
            d,
            int(row.get("impCnt", 0) or 0),
            int(row.get("clkCnt", 0) or 0),
            int(row.get("salesAmt", 0) or 0),
        ))
    return out


def bucket_by_week(daily: list, weeks: list) -> list:
    """일별 [(date,imp,clk,cost)] → 주별 버킷 [{imp,clk,cost}] (weeks 순서대로)."""
    buckets = [{"imp": 0, "clk": 0, "cost": 0} for _ in weeks]
    for d, imp, clk, cost in daily:
        try:
            dt = date.fromisoformat(d)
        except Exception:
            continue
        for i, (ws, we) in enumerate(weeks):
            if ws <= dt <= we:
                buckets[i]["imp"] += imp
                buckets[i]["clk"] += clk
                buckets[i]["cost"] += cost
    return buckets


def fetch_campaign_daily(api: NaverAdAPI, cid: str, span_since: str, span_until: str) -> list:
    """캠페인 일별 stats."""
    params = {
        "id": cid,
        "fields": '["impCnt","clkCnt","salesAmt"]',
        "timeUnit": "day",
        "timeRange": json.dumps({"since": span_since, "until": span_until}),
    }
    try:
        res = api._request("GET", "/stats", params=params)
    except Exception as e:
        logger.warning("  · 캠페인 stats 실패 %s: %s", cid, e)
        return []
    return parse_stats_daily(res)


def fetch_keyword_stats_batch(api: NaverAdAPI, keyword_ids: list, since: str, until: str) -> dict:
    """키워드 ID들의 기간 합계 stats. {kw_id: {imp,clk,cost}}"""
    stats: dict = {}
    for i in range(0, len(keyword_ids), BATCH_SIZE):
        batch = keyword_ids[i : i + BATCH_SIZE]
        params = {
            "ids": ",".join(batch),
            "fields": '["clkCnt","impCnt","salesAmt"]',
            "timeUnit": "day",
            "timeRange": json.dumps({"since": since, "until": until}),
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
            agg = stats.setdefault(kw_id, {"imp": 0, "clk": 0, "cost": 0})
            agg["imp"] += int(row.get("impCnt", 0) or 0)
            agg["clk"] += int(row.get("clkCnt", 0) or 0)
            agg["cost"] += int(row.get("salesAmt", 0) or 0)
    return stats


def fetch_campaign_keywords(api: NaverAdAPI, campaign_id: str) -> list:
    """캠페인 내 모든 키워드 메타. [{id, text, group, user_lock}, ...]"""
    groups = api._request("GET", "/ncc/adgroups", params={"nccCampaignId": campaign_id})
    groups = groups if isinstance(groups, list) else groups.get("items", [])
    keywords = []
    for g in groups:
        gid = g.get("nccAdgroupId", "")
        gname = g.get("name", "")
        for kw in api.get_keywords_by_group(gid):
            keywords.append({
                "id": kw.get("nccKeywordId", ""),
                "text": kw.get("keyword", ""),
                "group": gname,
                "user_lock": kw.get("userLock", False),
                "status_reason": kw.get("statusReason", ""),
            })
    return keywords


def fetch_account_report(label: str, api_key: str, secret_key: str,
                         customer_id: str, weeks: list) -> dict:
    """한 계정 — 3주 캠페인 추이 + 이번주 키워드 분석.

    weeks: [(이번주 s,u), (전주 s,u), (전전주 s,u)]  (date 객체)
    """
    api = NaverAdAPI(api_key, secret_key, customer_id)
    logger.info("[%s] 캠페인 목록 조회…", label)
    camps = api._request("GET", "/ncc/campaigns")
    camps = camps if isinstance(camps, list) else camps.get("items", [])

    span_since = weeks[2][0].isoformat()   # 전전주 시작
    span_until = weeks[0][1].isoformat()   # 이번주 끝
    w0s, w0u = weeks[0][0].isoformat(), weeks[0][1].isoformat()

    campaign_rows = []
    weekly_tot = [{"imp": 0, "clk": 0, "cost": 0} for _ in weeks]

    for c in camps:
        cid = c.get("nccCampaignId", "")
        name = c.get("name", "")
        ctype = c.get("campaignTp", "")

        daily = fetch_campaign_daily(api, cid, span_since, span_until)
        buckets = bucket_by_week(daily, weeks)
        w0 = buckets[0]

        kw_section = None
        if w0["imp"] > 0:
            try:
                kw_list = fetch_campaign_keywords(api, cid)
                if 0 < len(kw_list) <= MAX_KEYWORDS_PER_CAMPAIGN:
                    kw_ids = [k["id"] for k in kw_list if k["id"]]
                    kw_stats = fetch_keyword_stats_batch(api, kw_ids, w0s, w0u)
                    enriched = []
                    for k in kw_list:
                        s = kw_stats.get(k["id"], {"imp": 0, "clk": 0, "cost": 0})
                        enriched.append({**k, **s})
                    kw_section = {"total": len(enriched), "items": enriched}
                elif len(kw_list) > MAX_KEYWORDS_PER_CAMPAIGN:
                    kw_section = {"total": len(kw_list), "skipped": True, "items": []}
            except Exception as e:
                logger.exception("  · [%s] 키워드 수집 실패: %s", name, e)

        campaign_rows.append({
            "name": name, "type": ctype, "user_lock": c.get("userLock", False),
            "weeks": buckets,
            "imp": w0["imp"], "clk": w0["clk"], "cost": w0["cost"],
            "ctr": (w0["clk"] / w0["imp"] * 100) if w0["imp"] else 0,
            "cpc": (w0["cost"] / w0["clk"]) if w0["clk"] else 0,
            "kw_section": kw_section,
        })
        for i in range(len(weeks)):
            for m in ("imp", "clk", "cost"):
                weekly_tot[i][m] += buckets[i][m]
        logger.info("  · %-36s 이번주 imp=%-8d clk=%-5d cost=%d",
                    name[:36], w0["imp"], w0["clk"], w0["cost"])

    # '노출용' 캠페인 3주 합계 — 노출 확보 전용이라 실효율 분석 시 제외
    excl_tot = [{"imp": 0, "clk": 0, "cost": 0} for _ in weeks]
    has_excl = False
    for cr in campaign_rows:
        if EXCLUDE_CAMPAIGN in cr["name"]:
            has_excl = True
            for i in range(len(weeks)):
                for m in ("imp", "clk", "cost"):
                    excl_tot[i][m] += cr["weeks"][i][m]

    return {
        "label": label, "customer_id": customer_id,
        "campaigns": campaign_rows,
        "weekly": weekly_tot,         # 3주 [{imp,clk,cost}]
        "excl": excl_tot,             # '노출용' 캠페인 3주
        "has_excl": has_excl,
    }


# ─────────────────────────────────────────────
# 분석
# ─────────────────────────────────────────────

def collect_off_keywords(reports: list) -> list:
    """조치 권장 키워드 — 평균 CPC OFF_KW_MIN_CPC원 초과 (클릭 1건에 1만원+).

    노출 많고 CPC 싼 키워드(노출용 등)는 노출 전략상 정상 — CPC 기준에서 자연히 빠짐.
    캠페인이 아니라 키워드 단위로 입찰가 인하/OFF 한다.
    """
    out = []
    for r in reports:
        for c in r["campaigns"]:
            ks = c.get("kw_section")
            if not ks or ks.get("skipped"):
                continue
            for k in ks.get("items", []):
                clk = k["clk"]
                cpc = (k["cost"] / clk) if clk else 0
                if cpc >= OFF_KW_MIN_CPC:
                    out.append({
                        "account": r["label"], "campaign": c["name"],
                        "keyword": k["text"], "imp": k["imp"], "clk": clk,
                        "cpc": cpc, "cost": k["cost"],
                    })
    out.sort(key=lambda x: -x["cost"])
    return out


def highcost_summary(reports: list):
    """'고비용'·'입찰기' 계열 캠페인 이번주 집계 (cost, clk, cpc)."""
    cost = clk = 0
    for r in reports:
        for c in r["campaigns"]:
            if "고비용" in c["name"] or "입찰기" in c["name"]:
                cost += c["cost"]
                clk += c["clk"]
    return cost, clk, (cost / clk if clk else 0)


def find_strategy_campaign(reports: list):
    for r in reports:
        for c in r["campaigns"]:
            if "70원전략" in c["name"]:
                return c
    return None


def collect_powercontents(reports: list) -> list:
    """파워컨텐츠(POWER_CONTENTS 유형) 캠페인 — 활동 있는 것만, 3주 버킷 + 반려 키워드 수."""
    out = []
    for r in reports:
        for c in r["campaigns"]:
            if c.get("type") != "POWER_CONTENTS":
                continue
            w = c["weeks"]
            if not any(wk["imp"] > 0 or wk["cost"] > 0 for wk in w):
                continue
            total_kw = rejected = 0
            ks = c.get("kw_section")
            if ks and not ks.get("skipped"):
                for k in ks.get("items", []):
                    total_kw += 1
                    if "DISAPPROV" in str(k.get("status_reason", "")).upper():
                        rejected += 1
            out.append({"account": r["label"], "name": c["name"], "weeks": w,
                        "total_kw": total_kw, "rejected": rejected})
    return out


def merge_burgeori(reports: list) -> list:
    """'버거리' + '신규 버거리' 두 계정 → 단일 '버거리'.

    버거리 계정 이전(694291 → 4265143) 중이라 두 계정에 버거리 광고가 모두 잡힘 →
    주마다 두 계정 캠페인을 합산한다. 단 파워링크_홈페이지·블로그(브랜드/일반 트래픽)는
    가맹모집 성과가 아니므로 제외(BURGEORI_EXCLUDE).
    (주의: .env 라벨 '버거리'=4265143(현재), '신규 버거리'=694291(예전) — 라벨은 사용자 지정값.)
    """
    by_label = {r["label"]: r for r in reports}
    cur = by_label.get("버거리")        # 4265143
    old = by_label.get("신규 버거리")    # 694291
    if not cur or not old:
        return reports

    # 694291 캠페인은 이름 중복 방지로 출처 표시
    old_camps = []
    for c in old["campaigns"]:
        c2 = dict(c)
        c2["name"] = f"{c2['name']} ⟨694291⟩"
        old_camps.append(c2)

    # 두 계정 캠페인 합치되 파워링크(브랜드/일반 트래픽) 캠페인은 제외
    kept = [c for c in (cur["campaigns"] + old_camps)
            if not any(x in c["name"] for x in BURGEORI_EXCLUDE)]

    merged_weekly = [{"imp": 0, "clk": 0, "cost": 0} for _ in range(3)]
    for c in kept:
        for i in range(3):
            for m in ("imp", "clk", "cost"):
                merged_weekly[i][m] += c["weeks"][i][m]

    merged = {
        "label": "버거리",
        "customer_id": f"{cur['customer_id']} + {old['customer_id']}",
        "campaigns": kept,
        "weekly": merged_weekly,
        "excl": [{"imp": 0, "clk": 0, "cost": 0} for _ in range(3)],
        "has_excl": False,
        "merged_note": True,
    }
    out = []
    for r in reports:
        if r["label"] == "버거리":
            out.append(merged)
        elif r["label"] == "신규 버거리":
            continue
        else:
            out.append(r)
    return out


# ─────────────────────────────────────────────
# 렌더링
# ─────────────────────────────────────────────

def fmt_won(n) -> str:
    return f"{int(round(n)):,}원"


def fmt_int(n) -> str:
    return f"{int(round(n)):,}"


def pct_change(cur, base) -> str:
    """증감률 — base 0이면 '신규'/'-'."""
    if base == 0:
        return "신규" if cur > 0 else "-"
    return f"{(cur - base) / base * 100:+.0f}%"


def pp_change(cur, base) -> str:
    """퍼센트포인트 증감 (CTR용)."""
    return f"{cur - base:+.2f}p"


def _ctr(x):
    return (x["clk"] / x["imp"] * 100) if x["imp"] else 0


def _cpc(x):
    return (x["cost"] / x["clk"]) if x["clk"] else 0


def render_kpi_table(L: list, w: list):
    """w = [이번주, 전주, 전전주] 각 {imp,clk,cost}. 3주 KPI 표 출력."""
    w0, w1, w2 = w
    L.append("| 지표 | 전전주 | 전주 | 이번주 | 전주대비 | 전전주대비 |")
    L.append("|---|---:|---:|---:|---:|---:|")
    L.append(f"| 노출 | {fmt_int(w2['imp'])} | {fmt_int(w1['imp'])} | {fmt_int(w0['imp'])} | "
             f"{pct_change(w0['imp'], w1['imp'])} | {pct_change(w0['imp'], w2['imp'])} |")
    L.append(f"| 클릭 | {fmt_int(w2['clk'])} | {fmt_int(w1['clk'])} | {fmt_int(w0['clk'])} | "
             f"{pct_change(w0['clk'], w1['clk'])} | {pct_change(w0['clk'], w2['clk'])} |")
    L.append(f"| 비용 | {fmt_won(w2['cost'])} | {fmt_won(w1['cost'])} | {fmt_won(w0['cost'])} | "
             f"{pct_change(w0['cost'], w1['cost'])} | {pct_change(w0['cost'], w2['cost'])} |")
    L.append(f"| 클릭률 | {_ctr(w2):.2f}% | {_ctr(w1):.2f}% | {_ctr(w0):.2f}% | "
             f"{pp_change(_ctr(w0), _ctr(w1))} | {pp_change(_ctr(w0), _ctr(w2))} |")
    L.append(f"| 클릭당 비용 | {_cpc(w2):,.0f}원 | {_cpc(w1):,.0f}원 | {_cpc(w0):,.0f}원 | "
             f"{pct_change(_cpc(w0), _cpc(w1))} | {pct_change(_cpc(w0), _cpc(w2))} |")


def sub(a: dict, b: dict) -> dict:
    """a - b (지표별)."""
    return {m: a[m] - b[m] for m in ("imp", "clk", "cost")}


def render_markdown(reports: list, failed_accounts: list, weeks: list) -> str:
    today = date.today().isoformat()
    w0s, w0u = weeks[0][0].isoformat(), weeks[0][1].isoformat()
    w1s, w1u = weeks[1][0].isoformat(), weeks[1][1].isoformat()
    w2s, w2u = weeks[2][0].isoformat(), weeks[2][1].isoformat()

    L = []
    L.append(f"# 주간 광고 보고서 — {w0s} ~ {w0u}")
    L.append("")
    scope = " / ".join([r["label"] for r in reports] + failed_accounts)
    L.append(f"> 작성일: {today} · 작성: report_weekly.py  ")
    L.append(f"> 범위: {scope}  ")
    L.append(f"> 비교: 전주({w1s}~{w1u}) · 전전주({w2s}~{w2u})")
    L.append("")
    if failed_accounts:
        L.append(f"> ⚠️ **데이터 누락**: {', '.join(failed_accounts)} — API 키 인증/조회 실패")
        L.append("")

    off_kws = collect_off_keywords(reports)
    strat = find_strategy_campaign(reports)

    # ── 1. 주간 KPI 추이
    L.append("## 1. 주간 KPI 추이 (전전주 → 전주 → 이번주)")
    L.append("")
    sec = 0
    for r in reports:
        if r["has_excl"]:
            # 노출용 포함 + 제외 두 표
            sec += 1
            L.append(f"### 1-{sec}. {r['label']} — 노출용 포함 (전체)")
            L.append("")
            render_kpi_table(L, r["weekly"])
            L.append("")
            sec += 1
            L.append(f"### 1-{sec}. {r['label']} — 노출용 제외 ★ 실질 운영성과")
            L.append("")
            real = [sub(r["weekly"][i], r["excl"][i]) for i in range(3)]
            render_kpi_table(L, real)
            L.append("")
            e0 = r["excl"][0]
            tot0 = r["weekly"][0]
            share = (e0["imp"] / tot0["imp"] * 100) if tot0["imp"] else 0
            L.append(f"> '노출용' 캠페인이 이번주 노출의 **{share:.1f}%**({fmt_int(e0['imp'])})를 "
                     f"차지 — 노출 확보 전용이라 효율 지표를 왜곡. 제외 버전이 실제 운영 성과. "
                     f"노출용 자체는 클릭당 비용 {_cpc(e0):,.0f}원으로 저비용이라 유지.")
            L.append("")
        else:
            sec += 1
            L.append(f"### 1-{sec}. {r['label']}")
            L.append("")
            render_kpi_table(L, r["weekly"])
            L.append("")
            if r.get("merged_note"):
                L.append("> 버거리 = 현재 계정(4265143) + 예전 계정(694291) 두 계정 합산 — "
                         "계정 이전 중이라 양쪽에 버거리 광고가 잡힘. "
                         "**파워링크_홈페이지·블로그(브랜드/일반 트래픽)는 제외** — "
                         "가맹모집 광고 성과만 집계.")
                L.append("")

    # ── 2. 조치 권장 키워드
    L.append("## 2. 조치 권장 키워드 (캠페인 아님 — 키워드 단위)")
    L.append("")
    L.append(f"기준: **평균 클릭당 비용 {OFF_KW_MIN_CPC:,}원 초과** — 클릭 1건에 1만원 넘게 쓰는 "
             "키워드. 캠페인을 끄지 말고 해당 키워드의 입찰가를 내리거나 OFF.")
    L.append("")
    L.append("> 노출 많고 클릭당 비용 싼 키워드(예: 햄버거 77원, 노출용 캠페인)는 "
             "노출 전략상 정상 — 컷 대상 아님.")
    L.append("")
    if off_kws:
        L.append("| 키워드 | 계정 · 캠페인 | 노출 | 클릭 | 클릭당 비용 | 비용 | 권고 |")
        L.append("|---|---|---:|---:|---:|---:|---|")
        for k in off_kws[:25]:
            rec = "🛑 OFF" if k["cpc"] >= OFF_KW_OFF_CPC else "⚠️ 입찰가 인하"
            L.append(f"| {k['keyword']} | {k['account']} · {k['campaign']} | "
                     f"{fmt_int(k['imp'])} | {fmt_int(k['clk'])} | "
                     f"{k['cpc']:,.0f}원 | {fmt_won(k['cost'])} | {rec} |")
        L.append("")
        tot = sum(k["cost"] for k in off_kws)
        L.append(f"→ **{len(off_kws)}개 키워드 · 합계 {fmt_won(tot)}.** "
                 f"클릭당 비용 {OFF_KW_OFF_CPC:,}원 이상은 OFF, 그 외는 입찰가 인하. "
                 "캠페인은 끄지 않고 키워드만 손본다.")
    else:
        L.append(f"_클릭당 비용 {OFF_KW_MIN_CPC:,}원 초과 키워드 없음._")
    L.append("")

    # ── 3. 전략 제언
    L.append("## 3. 전략 제언")
    L.append("")
    hc_cost, hc_clk, hc_cpc = highcost_summary(reports)
    g_cost = sum(r["weekly"][0]["cost"] for r in reports)
    hc_pct = (hc_cost / g_cost * 100) if g_cost else 0
    L.append("### 3-1. 입찰기·고비용 캠페인 — 키워드 입찰가를 손봐야")
    L.append("")
    if hc_cost > 0:
        L.append(f"- 이번 주 '고비용·입찰기' 계열이 **{fmt_won(hc_cost)}** 집행 — "
                 f"클릭 {fmt_int(hc_clk)}건, 평균 클릭당 비용 **{hc_cpc:,.0f}원**, "
                 f"전체 비용의 **{hc_pct:.0f}%**.")
        L.append("- 원인은 캠페인이 아니라 **키워드 개별입찰가**(1~5만원, `reports/diagnose_20260520.md`). "
                 "70원 캡 롱테일 전략과 상충.")
        L.append("- 전환추적이 없어 이 비싼 지출의 성과는 검증 불가.")
        L.append("- **권장**: 캠페인을 끄지 말 것. 2절의 비싼 키워드 입찰가를 단계적으로 인하"
                 "(예: 5만원→1만원→3천원)하며 노출·클릭 변화를 관찰 — 노출 유지되면 더 인하, "
                 "급감하면 직전 단계로 복귀.")
    else:
        L.append("- 이번 주 고비용·입찰기 캠페인 집행 없음.")
    L.append("")

    L.append("### 3-2. 70원전략 현황")
    L.append("")
    L.append("- 약 **100만 개** 키워드 풀 구축 — 입찰가 70원 캡으로 롱테일을 대량 등록(활성 + 예비풀). "
             "검색량 적은 키워드도 수량으로 노출을 만회하는 전략.")
    if strat and strat["imp"] > 0:
        L.append(f"- ✅ 집행 중 — 노출 {fmt_int(strat['imp'])} · 클릭 {fmt_int(strat['clk'])} · "
                 f"비용 {fmt_won(strat['cost'])} · 클릭당 비용 {strat['cpc']:,.0f}원.")
        L.append("- 70원 캡이라 클릭당 비용 부담이 거의 없음 — 관건은 살아남는 키워드 선별. "
                 "**weekly_pruner.py cron 등록**(매주 월 06:00)으로 노출 0 컷 + 예비풀 보충 사이클 가동 필요.")
    else:
        L.append("- 본 보고기간엔 미집행(오픈 예정). 오픈 후 주간 자연선택 사이클로 "
                 "알맹이 8~15만 개로 수렴 예정.")
    L.append("")

    L.append("### 3-3. 파워컨텐츠 — 현황과 살릴 방안")
    L.append("")
    pc = collect_powercontents(reports)
    if pc:
        L.append("| 캠페인 | 노출 (전전주→전주→이번주) | 이번주 클릭 | 이번주 비용 | 키워드(반려) |")
        L.append("|---|---|---:|---:|---|")
        for p in pc:
            w = p["weeks"]
            w0 = w[0]
            kwcell = f"{p['total_kw']}개"
            if p["rejected"]:
                kwcell += f" (반려 {p['rejected']})"
            L.append(f"| {p['name']} | {fmt_int(w[2]['imp'])} → {fmt_int(w[1]['imp'])} → "
                     f"{fmt_int(w0['imp'])} | {fmt_int(w0['clk'])} | {fmt_won(w0['cost'])} | "
                     f"{kwcell} |")
        L.append("")
    else:
        L.append("_파워컨텐츠 캠페인 없음._")
        L.append("")
    worst = max(pc, key=lambda p: p["rejected"], default=None) if pc else None
    if worst and worst["rejected"]:
        pct = worst["rejected"] / worst["total_kw"] * 100 if worst["total_kw"] else 0
        L.append(f"- 🚨 **노출 저조의 직접 원인 = 키워드 검수 반려.** {worst['name']}는 키워드 "
                 f"{worst['total_kw']}개 중 **{worst['rejected']}개({pct:.0f}%)가 반려"
                 f"(KEYWORD_DISAPPROVED)** — 반려 키워드는 노출되지 않음.")
        L.append("- 파워컨텐츠는 **키워드–콘텐츠 적합성**을 검수함. 콘텐츠 소재 주제와 동떨어진 "
                 "키워드(콘텐츠는 호프·치킨인데 닭발창업·맥주프랜차이즈 등)가 반려됨.")
        L.append("- **살릴 방안**: ① 반려 키워드를 콘텐츠 주제에 맞는 것으로 교체, 또는 "
                 "② 반려된 키워드군을 다루는 콘텐츠 소재를 추가 등록 후 재검수 — 살아있는 키워드 "
                 "수를 늘려야 노출이 따라옴. ③ 소재가 검수 대기(PENDING)면 통과 먼저 확인.")
    else:
        L.append("- 파워컨텐츠는 콘텐츠형 검색광고 — 노출이 적으면 등록 키워드 확대 + 입찰가 점검 필요.")
    L.append("- ★ 파워컨텐츠는 신뢰형 지면이라 살릴 가치가 큼 — 반려 키워드 정리가 최우선.")
    L.append("")

    L.append("### 3-4. 다음 주 할 일")
    L.append("")
    if off_kws:
        L.append(f"- [ ] 비싼 키워드 {len(off_kws)}개 입찰가 인하/OFF — 키워드 단위 (2절)")
    L.append("- [ ] 파워컨텐츠 살릴 방안 실행 — 키워드 확대·소재 점검 (3-3)")
    if strat and strat["imp"] > 0:
        L.append("- [ ] weekly_pruner.py cron 등록 (매주 월 06:00 KST)")
    else:
        L.append("- [ ] 70원전략 오픈 후 weekly_pruner.py cron 등록")
    L.append("- [ ] 버거리 노출 회복 점검 (법인 이전 후 입찰가 이슈)")
    L.append("- [ ] 페이스북(버거리) 성과는 Meta Pixel 기준 별도 확인")
    L.append("")

    # ── 4. 계정·캠페인 실적 (이번주)
    L.append("## 4. 계정·캠페인 실적 (이번주)")
    L.append("")
    for r in reports:
        L.append(f"### {r['label']} (Customer {r['customer_id']})")
        L.append("")
        active = [x for x in r["campaigns"] if x["imp"] > 0 or x["cost"] > 0]
        zero = [x for x in r["campaigns"] if x["imp"] == 0 and x["cost"] == 0]
        if active:
            L.append("| 캠페인 | 유형 | 노출 | 클릭 | 클릭률 | 클릭당 비용 | 비용 | 현재 |")
            L.append("|---|---|---:|---:|---:|---:|---:|---|")
            for c in sorted(active, key=lambda x: -x["cost"]):
                state = "OFF" if c.get("user_lock") else "ON"
                L.append(f"| {c['name']} | {c['type']} | {fmt_int(c['imp'])} | "
                         f"{fmt_int(c['clk'])} | {c['ctr']:.2f}% | {c['cpc']:,.0f}원 | "
                         f"{fmt_won(c['cost'])} | {state} |")
            L.append("")
        if zero:
            L.append(f"_무운영(노출 0) 캠페인 {len(zero)}개 — 생략._")
            L.append("")
        if not r["campaigns"]:
            L.append("_캠페인 없음_")
            L.append("")

    # ── 5. 참고
    L.append("## 5. 참고")
    L.append("")
    L.append("- **계정 구성**: 보승에프앤비 산하 — 로얄호프치킨, 그리고 버거리(현재 계정 4265143 + "
             "예전 계정 694291 합산). 694291 계정엔 미쓰족발·육회도·보승회관 등 타 브랜드 "
             "캠페인도 있으나 미운영.")
    L.append("- **버거리 계정이 둘인 이유**: 예전 버거리 계정(694291)이 '보승에프씨' 법인 명의로 "
             "등록돼 있어, 법인 정리를 위해 현재 계정(4265143)으로 이전. 이전 중이라 두 계정에 "
             "버거리 광고가 모두 잡혀 합산. 파워링크_홈페이지·블로그(브랜드 트래픽)는 가맹모집 "
             "성과가 아니므로 제외.")
    L.append("- **70원전략**: 로얄호프치킨에 입찰가 70원 캡 롱테일 키워드 약 100만 개를 구축. "
             "주간 자연선택(노출 0 컷 + 예비풀 보충)으로 알맹이만 남기는 전략 — 상세는 3-2.")
    L.append("- **노출용 캠페인**: 로얄 '노출용'은 노출 확보 전용 캠페인. 노출 비중이 압도적이라 "
             "효율 지표를 왜곡 → KPI를 노출용 포함/제외 두 버전으로 분리, '제외'가 실질 성과. "
             "클릭당 비용이 낮아(70~80원) 비용 부담은 적어 유지.")
    L.append("- **전환추적 미사용**: 로얄·버거리 계정은 네이버 전환추적 미연동 — 전환수 미측정. "
             "키워드·캠페인 평가는 노출·클릭·클릭률·클릭당 비용 기준.")
    L.append("- **페이스북**: 버거리 Meta 광고는 별도 운영(`weekly_meta_*.md`). 검색광고 예산 일부가 "
             "Meta로 이관돼 버거리 검색 일예산은 3만원으로 축소(2026-05-19~).")
    L.append("")

    return "\n".join(L)


def render_html(md_text: str, title: str) -> str:
    try:
        import markdown
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "markdown"])
        import markdown
    body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "Malgun Gothic", sans-serif;
    max-width: 980px; margin: 40px auto; padding: 0 24px; line-height: 1.6; color: #1f2328; }}
  h1 {{ border-bottom: 2px solid #d0d7de; padding-bottom: 8px; }}
  h2 {{ border-bottom: 1px solid #d0d7de; padding-bottom: 4px; margin-top: 32px; }}
  h3 {{ margin-top: 24px; color: #424a53; }}
  table {{ border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 14px; }}
  th, td {{ border: 1px solid #d0d7de; padding: 6px 12px; text-align: left; }}
  th {{ background: #f6f8fa; font-weight: 600; }}
  td:nth-child(n+3) {{ text-align: right; }}
  blockquote {{ border-left: 4px solid #d0d7de; margin: 0; padding: 0 16px; color: #656d76; }}
  code {{ background: #f6f8fa; padding: 2px 6px; border-radius: 4px; font-family: "Consolas", monospace; }}
  ul {{ padding-left: 24px; }}
  li {{ margin: 4px 0; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def last_full_week():
    """직전 월요일~일요일."""
    today = date.today()
    last_sun = today - timedelta(days=today.weekday() + 1)
    last_mon = last_sun - timedelta(days=6)
    return last_mon.isoformat(), last_sun.isoformat()


def main():
    parser = argparse.ArgumentParser(description="3계정 통합 주간 광고 보고서")
    parser.add_argument("--since", default="", help="이번주 시작일 YYYY-MM-DD (기본: 직전 월)")
    parser.add_argument("--until", default="", help="이번주 종료일 YYYY-MM-DD (기본: 직전 일)")
    parser.add_argument("--no-html", action="store_true", help="HTML 생성 안 함")
    args = parser.parse_args()

    since, until = args.since, args.until
    if not since or not until:
        since, until = last_full_week()

    s0, u0 = date.fromisoformat(since), date.fromisoformat(until)
    weeks = [
        (s0, u0),                                                 # 이번주
        (s0 - timedelta(days=7), u0 - timedelta(days=7)),         # 전주
        (s0 - timedelta(days=14), u0 - timedelta(days=14)),       # 전전주
    ]

    load_dotenv(ROOT / ".env")
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)
    logger.info("=== 주간 보고서 생성: 이번주 %s~%s (전주·전전주 비교) ===", since, until)

    accounts = [
        ("로얄호프치킨",
         os.getenv("NAVER_AD_API_KEY"),
         os.getenv("NAVER_AD_SECRET_KEY"),
         os.getenv("NAVER_AD_CUSTOMER_ID")),
        ("버거리",
         os.getenv("BURGEORI_NEW_API_KEY"),
         os.getenv("BURGEORI_NEW_SECRET_KEY"),
         os.getenv("BURGEORI_NEW_CUSTOMER_ID")),
        ("신규 버거리",
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
            reports.append(fetch_account_report(label, k, s, cid, weeks))
        except Exception as e:
            logger.exception("[%s] 보고서 생성 실패: %s", label, e)
            failed.append(label)

    reports = merge_burgeori(reports)
    md = render_markdown(reports, failed, weeks)
    stem = f"weekly_{since.replace('-', '')}_{until.replace('-', '')[4:]}"
    md_path = ROOT / "reports" / f"{stem}.md"
    md_path.write_text(md, encoding="utf-8-sig")
    logger.info("✓ 보고서 저장: %s", md_path)
    print(f"\n✓ MD 완료: {md_path}")

    if not args.no_html:
        html = render_html(md, stem)
        html_path = ROOT / "reports" / f"{stem}.html"
        html_path.write_text(html, encoding="utf-8")
        logger.info("✓ HTML 저장: %s", html_path)
        print(f"✓ HTML 완료: {html_path}")

    print()


if __name__ == "__main__":
    main()
