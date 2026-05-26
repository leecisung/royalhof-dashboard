# -*- coding: utf-8 -*-
"""
광고 통합 대시보드 — FastAPI 버전 (Vercel 서버리스 호환).

Streamlit Cloud의 cold start/hang 이슈를 회피하고 burgery-pos-deploy와
같은 호스팅 패턴(Vercel + FastAPI) 사용. 데이터 fetch 로직은 기존
scripts/lib/* 그대로 재사용.

로컬 실행:
    python -m uvicorn web.app:app --reload --port 8000

배포: vercel --prod  (api/index.py가 진입점)
"""

import os
import sys
import logging
import secrets as pysecrets

logger = logging.getLogger(__name__)
from pathlib import Path
from datetime import date, timedelta
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fastapi import FastAPI, Request, Form, Response, Cookie, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from lib.dashboard_data import (
    fetch_unified,
    fetch_meta_ad_sets,
    fetch_meta_ads,
    fetch_meta_placements,
    fetch_naver,
)
from web.analytics import (
    classify_budget_decision,
    classify_creative_status,
    classify_keyword_action,
    generate_meta_insights,
    generate_naver_insights,
    diagnose_meta_ad_set,
    analyze_meta_placements,
    generate_meta_strategy,
    VERDICT_KILL,
    VERDICT_FIX,
    VERDICT_KEEP,
    VERDICT_BOOST,
    VERDICT_LEARNING,
)

load_dotenv(ROOT / ".env")

# 스냅샷 파일 (로컬 prefetch_snapshot.py가 생성, api/snapshots/ 로도 복사하면 Vercel 자동 번들)
import json as _json
_SNAPSHOT_CANDIDATES = [
    ROOT / "api" / "snapshots" / "latest.json",   # Vercel 자동 번들 (api/ 안)
    ROOT / "data" / "snapshots" / "latest.json",  # 로컬 dev
]


def _load_snapshot() -> Optional[dict]:
    for p in _SNAPSHOT_CANDIDATES:
        try:
            if p.exists():
                return _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


# 디버그용 (healthz에서 표시)
SNAPSHOT_PATH = _SNAPSHOT_CANDIDATES[0]


def _filter_by_period(rows: list, since: date, until: date) -> list:
    """스냅샷 row 중 [since, until] 기간만."""
    s, u = str(since), str(until)
    return [r for r in rows if s <= r.get("date", "") <= u]


# 템플릿 위치: web/templates/ (로컬) 또는 api/templates/ (Vercel 번들).
_T_LOCAL = Path(__file__).parent / "templates"
_T_VERCEL = ROOT / "api" / "templates"
TEMPLATES = Jinja2Templates(
    directory=str(_T_VERCEL if _T_VERCEL.exists() else _T_LOCAL)
)

app = FastAPI(title="광고 통합 대시보드")

# 세션 토큰 in-memory (Vercel 서버리스에서는 단일 인스턴스 메모리에 한정 — 충분).
# 실제 멀티 인스턴스 환경 필요 시 cookie-signed JWT 권장.
_SESSIONS: set[str] = set()


def _password() -> str:
    return os.getenv("DASHBOARD_PASSWORD", "").strip()


def _check_auth(session: Optional[str] = Cookie(None)) -> bool:
    if not _password():
        return True
    return bool(session and session in _SESSIONS)


def _require_auth(session: Optional[str] = Cookie(None)):
    if not _check_auth(session):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


# ─────────────────────────────────────────────
# 로그인 / 로그아웃
# ─────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: Optional[str] = None):
    if not _password():
        return RedirectResponse("/", status_code=303)
    return TEMPLATES.TemplateResponse(
        request, "login.html", {"error": error}
    )


@app.post("/login")
def login_submit(password: str = Form(...)):
    expected = _password()
    if not expected:
        return RedirectResponse("/", status_code=303)
    if password != expected:
        return RedirectResponse("/login?error=1", status_code=303)
    token = pysecrets.token_urlsafe(32)
    _SESSIONS.add(token)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        "session", token,
        max_age=60 * 60 * 24 * 30,  # 30일
        httponly=True,
        samesite="lax",
        secure=False,  # Vercel은 자체 HTTPS, secure=True여도 OK. 로컬 dev엔 False 필요
    )
    return resp


@app.get("/logout")
def logout(session: Optional[str] = Cookie(None)):
    if session:
        _SESSIONS.discard(session)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp


# ─────────────────────────────────────────────
# 메인 대시보드
# ─────────────────────────────────────────────

def _parse_period(preset: str, start: Optional[str], end: Optional[str]) -> tuple[date, date]:
    today = date.today()
    if preset == "today":
        return today, today
    if preset == "7d":
        return today - timedelta(days=6), today
    if preset == "14d":
        return today - timedelta(days=13), today
    if preset == "30d":
        return today - timedelta(days=29), today
    if preset == "mtd":
        return today.replace(day=1), today
    if preset == "lastmo":
        first = today.replace(day=1)
        last_prev = first - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    if preset == "custom" and start and end:
        return date.fromisoformat(start), date.fromisoformat(end)
    return today - timedelta(days=6), today  # default


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    preset: str = "7d",
    start: Optional[str] = None,
    end: Optional[str] = None,
    refresh: int = 0,
    session: Optional[str] = Cookie(None),
):
    if not _check_auth(session):
        return RedirectResponse("/login", status_code=303)

    since, until = _parse_period(preset, start, end)

    # ★ 스냅샷 우선 (빠름). force_refresh 시에만 라이브 fetch.
    snapshot = _load_snapshot()
    if snapshot and not refresh:
        all_naver = snapshot.get("naver", [])
        all_meta = snapshot.get("meta", [])
        ga4_all = snapshot.get("ga4", {}) or {}
        naver_rows = _filter_by_period(all_naver, since, until)
        meta_rows = _filter_by_period(all_meta, since, until)
        # GA4 daily도 기간 필터
        ga4_daily_filtered = [r for r in (ga4_all.get("daily") or []) if since.isoformat() <= r.get("date","") <= until.isoformat()]
        data = {
            "naver": naver_rows, "meta": meta_rows,
            "ga4": {**ga4_all, "daily": ga4_daily_filtered},
            "from_cache": True,
            "fetched_at": snapshot.get("generated_at", ""),
        }
    else:
        data = fetch_unified(since, until, force_refresh=bool(refresh))
        naver_rows = data.get("naver", [])
        meta_rows = data.get("meta", [])

    all_rows = naver_rows + meta_rows

    def _kpi(rows):
        imp = sum(r.get("impressions", 0) for r in rows)
        clk = sum(r.get("clicks", 0) for r in rows)
        cost = sum(r.get("spend", 0) for r in rows)
        conv = sum(r.get("conversions", 0) for r in rows)
        return {
            "impressions": imp,
            "clicks": clk,
            "spend": int(cost),
            "conversions": conv,
            "ctr": (clk / imp * 100) if imp else 0.0,
            "cpc": int(cost / clk) if clk else 0,
            "cpa": int(cost / conv) if conv else 0,
            "cpm": int(cost / imp * 1000) if imp else 0,
        }

    kpi_total = _kpi(all_rows)
    kpi_naver = _kpi(naver_rows)
    kpi_meta = _kpi(meta_rows)

    # 브랜드별
    brand_groups: dict[str, list[dict]] = {}
    for r in all_rows:
        brand_groups.setdefault(r.get("brand", "기타"), []).append(r)
    brands = [
        {"name": b, **_kpi(rs)}
        for b, rs in sorted(brand_groups.items(), key=lambda x: -sum(r.get("spend", 0) for r in x[1]))
    ]

    # 캠페인 Top 10 (지출 기준)
    camp_groups: dict[tuple, list[dict]] = {}
    for r in all_rows:
        key = (r.get("channel"), r.get("account"), r.get("campaign_name"))
        camp_groups.setdefault(key, []).append(r)
    campaigns = []
    for (ch, acct, name), rs in camp_groups.items():
        k = _kpi(rs)
        campaigns.append({"channel": ch, "account": acct, "campaign": name or "(이름없음)", **k})
    campaigns.sort(key=lambda c: -c["spend"])
    top_campaigns = campaigns[:10]

    # 효율 의심 (지출↑ + CTR↓)
    suspicious = [c for c in campaigns if c["spend"] >= 1000]
    suspicious.sort(key=lambda c: -(c["spend"] / max(c["ctr"] / 100, 0.0001)))
    suspicious = suspicious[:10]

    # 일별 시계열
    daily_map: dict[str, list[dict]] = {}
    for r in all_rows:
        daily_map.setdefault(r.get("date", ""), []).append(r)
    daily = [{"date": dt, **_kpi(rs)} for dt, rs in sorted(daily_map.items())]

    ga4 = data.get("ga4", {}) or {}

    return TEMPLATES.TemplateResponse(
        request,
        "dashboard.html",
        {
            "preset": preset,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "days": (until - since).days + 1,
            "kpi_total": kpi_total,
            "kpi_naver": kpi_naver,
            "kpi_meta": kpi_meta,
            "brands": brands,
            "top_campaigns": top_campaigns,
            "suspicious": suspicious,
            "daily": daily,
            "ga4_configured": ga4.get("configured", False),
            "ga4_error": ga4.get("error"),
            "ga4_daily": ga4.get("daily", []),
            "ga4_by_source": (ga4.get("by_source") or [])[:15],
            "ga4_by_campaign": (ga4.get("by_campaign") or [])[:15],
            "ga4_by_event": (ga4.get("by_event") or [])[:15],
            "from_cache": data.get("from_cache", False),
            "fetched_at": data.get("fetched_at", ""),
            "has_password": bool(_password()),
        },
    )


# ─────────────────────────────────────────────
# /meta — Meta 상세 분석 페이지
# ─────────────────────────────────────────────

def _kpi(rows):
    imp = sum(r.get("impressions", 0) for r in rows)
    clk = sum(r.get("clicks", 0) for r in rows)
    cost = sum(r.get("spend", 0) for r in rows)
    conv = sum(r.get("conversions", 0) for r in rows)
    return {
        "impressions": imp, "clicks": clk,
        "spend": int(cost), "conversions": conv,
        "ctr": (clk / imp * 100) if imp else 0.0,
        "cpc": int(cost / clk) if clk else 0,
        "cpa": int(cost / conv) if conv else 0,
        "cpm": int(cost / imp * 1000) if imp else 0,
    }


@app.get("/meta", response_class=HTMLResponse)
def meta_detail(
    request: Request,
    preset: str = "7d",
    start: Optional[str] = None,
    end: Optional[str] = None,
    session: Optional[str] = Cookie(None),
):
    if not _check_auth(session):
        return RedirectResponse("/login", status_code=303)

    since, until = _parse_period(preset, start, end)
    days = (until - since).days + 1
    prev_until = since - timedelta(days=1)
    prev_since = prev_until - timedelta(days=days - 1)

    # 현재 + 직전기간 fetch — Meta는 라이브 fetch가 빨라서 그대로 실행. 실패 시 빈 리스트.
    try:
        ad_sets = fetch_meta_ad_sets(since, until)
        ads = fetch_meta_ads(since, until)
        prev_ad_sets = fetch_meta_ad_sets(prev_since, prev_until)
    except Exception as e:
        logger.warning("[/meta] live fetch 실패: %s", e)
        ad_sets, ads, prev_ad_sets = [], [], []

    # placement breakdown 별도 fetch (실패해도 페이지는 동작)
    try:
        placements = fetch_meta_placements(since, until)
    except Exception as e:
        logger.warning("[/meta] placement fetch 실패: %s", e)
        placements = []
    placement_analysis = analyze_meta_placements(placements)

    # 캠페인 단위로 합산 (ad_sets 기준)
    camp_map = {}
    for a in ad_sets:
        cid = a["campaign_id"]
        if cid not in camp_map:
            camp_map[cid] = {
                "campaign_id": cid,
                "campaign_name": a["campaign_name"],
                "impressions": 0, "clicks": 0, "spend": 0.0,
                "conversions": 0, "reach": 0,
            }
        camp = camp_map[cid]
        camp["impressions"] += a["impressions"]
        camp["clicks"] += a["clicks"]
        camp["spend"] += a["spend"]
        camp["conversions"] += a["conversions"]
        camp["reach"] += a["reach"]

    campaigns = []
    for c in camp_map.values():
        c["ctr"] = (c["clicks"] / c["impressions"] * 100) if c["impressions"] else 0.0
        c["cpc"] = int(c["spend"] / c["clicks"]) if c["clicks"] else 0
        c["cpa"] = int(c["spend"] / c["conversions"]) if c["conversions"] else 0
        c["spend"] = int(c["spend"])
        decision, reason = classify_budget_decision(c["spend"], c["conversions"], c["cpa"])
        c["budget_decision"] = decision
        c["budget_reason"] = reason
        campaigns.append(c)
    campaigns.sort(key=lambda x: -x["spend"])

    # ad set 정리 + 심층 진단
    by_adset_placement = placement_analysis.get("by_adset", {})
    for a in ad_sets:
        a["spend_int"] = int(a["spend"])
        a["cpa_int"] = int(a["cpa"])
        decision, reason = classify_budget_decision(a["spend"], a["conversions"], a["cpa"])
        a["budget_decision"] = decision
        a["budget_reason"] = reason
        an_pct = by_adset_placement.get(a["ad_set_id"], {}).get("an_pct", 0.0)
        a["an_pct"] = an_pct
        a["diagnosis"] = diagnose_meta_ad_set(a, an_pct=an_pct)
    ad_sets.sort(key=lambda x: -x["spend"])

    # verdict 분포 카운트
    verdict_counts = {
        VERDICT_KILL: 0, VERDICT_FIX: 0, VERDICT_KEEP: 0,
        VERDICT_BOOST: 0, VERDICT_LEARNING: 0,
    }
    for a in ad_sets:
        v = a["diagnosis"]["verdict"]
        verdict_counts[v] = verdict_counts.get(v, 0) + 1

    # ads (소재) — Winner/Learning/Kill 분류
    for a in ads:
        a["spend_int"] = int(a["spend"])
        a["cpa_int"] = int(a["cpa"])
        status, reason = classify_creative_status(
            ctr=a["ctr"], cpa=a["cpa"], conv=a["conversions"],
            spend=a["spend"],
        )
        a["creative_status"] = status
        a["creative_reason"] = reason
    ads.sort(key=lambda x: -x["spend"])

    # KPI 현재 + 직전
    cur_kpi = _kpi(ad_sets)
    prev_kpi = _kpi(prev_ad_sets) if prev_ad_sets else None

    # avg frequency
    cur_kpi["frequency"] = (
        sum(a["frequency"] * a["impressions"] for a in ad_sets) /
        sum(a["impressions"] for a in ad_sets)
    ) if sum(a["impressions"] for a in ad_sets) else 0

    insights = generate_meta_insights(cur_kpi, prev_kpi, ad_sets, ads)
    strategy_notes = generate_meta_strategy(ad_sets, placement_analysis, cur_kpi, prev_kpi)

    return TEMPLATES.TemplateResponse(
        request,
        "meta.html",
        {
            "preset": preset, "since": since.isoformat(),
            "until": until.isoformat(), "days": days,
            "kpi": cur_kpi, "prev_kpi": prev_kpi,
            "campaigns": campaigns,
            "ad_sets": ad_sets[:30],
            "ads": ads[:50],
            "insights": insights,
            "strategy_notes": strategy_notes,
            "placement_analysis": placement_analysis,
            "verdict_counts": verdict_counts,
            "has_password": bool(_password()),
        },
    )


# ─────────────────────────────────────────────
# /naver — Naver 상세 분석 페이지
# ─────────────────────────────────────────────

@app.get("/naver", response_class=HTMLResponse)
def naver_detail(
    request: Request,
    preset: str = "7d",
    start: Optional[str] = None,
    end: Optional[str] = None,
    session: Optional[str] = Cookie(None),
):
    if not _check_auth(session):
        return RedirectResponse("/login", status_code=303)

    since, until = _parse_period(preset, start, end)
    days = (until - since).days + 1
    prev_until = since - timedelta(days=1)
    prev_since = prev_until - timedelta(days=days - 1)
    prev_prev_until = prev_since - timedelta(days=1)
    prev_prev_since = prev_prev_until - timedelta(days=days - 1)

    # ★ 스냅샷 우선. 없으면 라이브 fetch 시도. 로컬 prefetch_snapshot.py로 매일 갱신.
    snapshot = _load_snapshot()
    snapshot_age = None
    if snapshot:
        all_naver = snapshot.get("naver", [])
        cur = _filter_by_period(all_naver, since, until)
        prev = _filter_by_period(all_naver, prev_since, prev_until)
        prev_prev = _filter_by_period(all_naver, prev_prev_since, prev_prev_until)
        snapshot_age = snapshot.get("generated_at")
    else:
        try:
            cur = fetch_naver(since, until)
        except Exception as e:
            logger.warning("[/naver] live fetch 실패: %s", e)
            cur = []
        prev = []
        prev_prev = []

    # 계정별 분해 + KPI
    def _account_kpi(rows, account_filter=None, exclude_campaigns=None):
        sel = rows
        if account_filter:
            sel = [r for r in sel if r.get("account") == account_filter]
        if exclude_campaigns:
            sel = [r for r in sel if not any(x in (r.get("campaign_name") or "") for x in exclude_campaigns)]
        return _kpi(sel)

    # 계정별 정보 (라벨 + KPI 3주) — 사용자 요청 3계정
    account_labels = [
        "로얄호프치킨 가맹광고 (파워링크)",
        "버거리 (보승에프앤비)",
        "구 파워링크 (미사용)",
    ]
    accounts_summary = []
    for label in account_labels:
        block = {
            "label": label,
            "kpi_cur": _account_kpi(cur, label),
            "kpi_prev": _account_kpi(prev, label),
            "kpi_prev_prev": _account_kpi(prev_prev, label),
        }
        # 로얄호프는 노출용 제외 버전도 (실질 성과)
        if "로얄호프" in label:
            block["kpi_cur_noexposure"] = _account_kpi(cur, label, exclude_campaigns=["노출용"])
            block["kpi_prev_noexposure"] = _account_kpi(prev, label, exclude_campaigns=["노출용"])
            block["kpi_prev_prev_noexposure"] = _account_kpi(prev_prev, label, exclude_campaigns=["노출용"])
        accounts_summary.append(block)

    # 캠페인 단위 집계 (현재)
    camp_map = {}
    for r in cur:
        cid = r["campaign_id"]
        if cid not in camp_map:
            camp_map[cid] = {
                "campaign_id": cid, "campaign_name": r["campaign_name"],
                "account": r["account"], "brand": r["brand"],
                "impressions": 0, "clicks": 0, "spend": 0,
            }
        camp_map[cid]["impressions"] += r["impressions"]
        camp_map[cid]["clicks"] += r["clicks"]
        camp_map[cid]["spend"] += r["spend"]
    campaigns = []
    for c in camp_map.values():
        c["ctr"] = (c["clicks"] / c["impressions"] * 100) if c["impressions"] else 0.0
        c["cpc"] = int(c["spend"] / c["clicks"]) if c["clicks"] else 0
        campaigns.append(c)
    campaigns.sort(key=lambda x: -x["spend"])

    # 3주 추이
    kpi_cur = _kpi(cur)
    kpi_prev = _kpi(prev)
    kpi_prev_prev = _kpi(prev_prev)

    # 키워드 단위 권고 (스냅샷에서)
    expensive_keywords = []
    if snapshot:
        all_kws = snapshot.get("naver_keywords", []) or []
        for kw in all_kws:
            cpc = kw.get("cpc", 0)
            clicks = kw.get("clicks", 0)
            imp = kw.get("impressions", 0)
            action = classify_keyword_action(cpc, imp, clicks)
            if action:
                ac_label, ac_reason = action
                expensive_keywords.append({**kw, "action_label": ac_label, "action_reason": ac_reason})
        # 비용 큰 순
        expensive_keywords.sort(key=lambda k: -k.get("cost", 0))

    # (대체용) 캠페인 단위에서 cpc 큰 것도 같이 보여줌
    expensive_campaigns = []
    for c in campaigns:
        action = classify_keyword_action(c["cpc"], c["impressions"], c["clicks"])
        if action:
            ac_label, ac_reason = action
            expensive_campaigns.append({**c, "action_label": ac_label, "action_reason": ac_reason})

    insights = generate_naver_insights(kpi_cur, kpi_prev, expensive_keywords or expensive_campaigns)

    # 전략 제언 (룰베이스 자동 텍스트)
    expensive_total_cost = sum(k["cost"] for k in expensive_keywords)
    expensive_kw_count = len(expensive_keywords)
    off_targets = sum(1 for k in expensive_keywords if k["cpc"] >= 30000)
    reduce_targets = sum(1 for k in expensive_keywords if 10000 <= k["cpc"] < 30000)
    strategy_notes = []
    if expensive_kw_count:
        strategy_notes.append(
            f"이번 기간 CPC 10k 초과 키워드 <strong>{expensive_kw_count}개</strong>가 "
            f"<strong>{int(expensive_total_cost):,}원</strong> 집행. "
            f"CPC 30k+ {off_targets}개 → OFF, 10~30k {reduce_targets}개 → 입찰가 단계적 인하 권장."
        )
    royal_70won_active = any("70원전략" in r.get("campaign_name", "") for r in cur)
    if royal_70won_active:
        strategy_notes.append("로얄호프치킨 70원전략 캠페인 집행 중 — 키워드 풀 자연선택 진행 (별도 weekly_pruner.py 관리).")
    if any("입찰기" in c.get("campaign_name", "") or "고비용" in c.get("campaign_name", "") for c in campaigns):
        strategy_notes.append(
            "입찰기·고비용 계열 캠페인 존재 — 캠페인을 끄지 말고, 비싼 키워드 입찰가를 단계적 인하 "
            "(예: 5만원→1만원→3천원) 후 노출·클릭 변화 관찰. 노출 유지되면 더 인하, 급감하면 직전 단계로 복귀."
        )
    if expensive_kw_count == 0:
        strategy_notes.append("CPC 10k 이상 키워드 없음 — 효율 양호. 추이만 관찰.")


    return TEMPLATES.TemplateResponse(
        request,
        "naver.html",
        {
            "preset": preset, "since": since.isoformat(),
            "until": until.isoformat(), "days": days,
            "prev_since": prev_since.isoformat(), "prev_until": prev_until.isoformat(),
            "prev_prev_since": prev_prev_since.isoformat(), "prev_prev_until": prev_prev_until.isoformat(),
            "kpi": kpi_cur, "kpi_prev": kpi_prev, "kpi_prev_prev": kpi_prev_prev,
            "accounts_summary": accounts_summary,
            "campaigns": campaigns,
            "expensive": expensive_campaigns,
            "expensive_keywords": expensive_keywords[:50],
            "expensive_kw_count": expensive_kw_count,
            "expensive_total_cost": int(expensive_total_cost),
            "strategy_notes": strategy_notes,
            "insights": insights,
            "snapshot_age": snapshot_age,
            "has_password": bool(_password()),
        },
    )


# ─────────────────────────────────────────────
# 액션 라우트 (POST — 실제 광고 시스템 변경)
# ─────────────────────────────────────────────

def _require_action_auth(session: Optional[str]):
    """액션은 항상 비밀번호 게이트 통과 필수."""
    if not _password():
        raise HTTPException(403, "DASHBOARD_PASSWORD 미설정 시 액션 비활성")
    if not session or session not in _SESSIONS:
        raise HTTPException(401, "로그인 필요")


@app.post("/actions/meta/pause-adset")
def action_meta_pause_adset(
    ad_set_id: str = Form(...),
    session: Optional[str] = Cookie(None),
):
    _require_action_auth(session)
    from lib.meta_api import MetaAdsAPI, MetaProtectedError
    try:
        api = MetaAdsAPI.from_env()
        api.pause_ad_set(ad_set_id)
        return JSONResponse({"ok": True, "ad_set_id": ad_set_id, "status": "paused"})
    except MetaProtectedError as e:
        return JSONResponse({"ok": False, "error": f"보호된 캠페인: {e}"}, status_code=403)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/actions/meta/update-budget")
def action_meta_update_budget(
    ad_set_id: str = Form(...),
    new_budget_krw: int = Form(...),
    session: Optional[str] = Cookie(None),
):
    _require_action_auth(session)
    from lib.meta_api import MetaAdsAPI, MetaProtectedError
    try:
        api = MetaAdsAPI.from_env()
        api.update_ad_set_budget(ad_set_id, new_budget_krw)
        return JSONResponse({"ok": True, "ad_set_id": ad_set_id, "new_budget": new_budget_krw})
    except MetaProtectedError as e:
        return JSONResponse({"ok": False, "error": f"보호된 캠페인: {e}"}, status_code=403)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/actions/naver/update-bid")
def action_naver_update_bid(
    keyword_id: str = Form(...),
    bid: int = Form(...),
    account: str = Form("NAVER_AD"),  # NAVER_AD / BURGEORI_NEW / BURGEORI_OLD
    session: Optional[str] = Cookie(None),
):
    _require_action_auth(session)
    from lib.naver_api import NaverAdAPI
    try:
        key = os.getenv(f"{account}_API_KEY", "").strip()
        sec = os.getenv(f"{account}_SECRET_KEY", "").strip()
        cid = os.getenv(f"{account}_CUSTOMER_ID", "").strip()
        if not (key and sec and cid):
            return JSONResponse({"ok": False, "error": f"{account} 인증정보 없음"}, status_code=400)
        api = NaverAdAPI(key, sec, cid)
        api.update_bid(keyword_id, bid)
        return JSONResponse({"ok": True, "keyword_id": keyword_id, "new_bid": bid})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/actions/naver/delete-keyword")
def action_naver_delete_keyword(
    keyword_id: str = Form(...),
    account: str = Form("NAVER_AD"),
    session: Optional[str] = Cookie(None),
):
    _require_action_auth(session)
    from lib.naver_api import NaverAdAPI
    try:
        key = os.getenv(f"{account}_API_KEY", "").strip()
        sec = os.getenv(f"{account}_SECRET_KEY", "").strip()
        cid = os.getenv(f"{account}_CUSTOMER_ID", "").strip()
        if not (key and sec and cid):
            return JSONResponse({"ok": False, "error": f"{account} 인증정보 없음"}, status_code=400)
        api = NaverAdAPI(key, sec, cid)
        api.delete_keyword(keyword_id)
        return JSONResponse({"ok": True, "keyword_id": keyword_id, "status": "deleted"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ─────────────────────────────────────────────
# CSV export — /meta /naver 결과보고서 다운로드
# ─────────────────────────────────────────────

import csv as _csv
import io as _io


def _csv_response(rows: list, filename: str) -> Response:
    """utf-8-sig BOM 입혀 Excel에서 한글 그대로 열리도록."""
    buf = _io.StringIO()
    buf.write("﻿")  # BOM
    w = _csv.writer(buf)
    for row in rows:
        w.writerow(row)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/meta/export.csv")
def meta_export(
    preset: str = "7d",
    start: Optional[str] = None,
    end: Optional[str] = None,
    session: Optional[str] = Cookie(None),
):
    if not _check_auth(session):
        return RedirectResponse("/login", status_code=303)

    since, until = _parse_period(preset, start, end)
    days = (until - since).days + 1
    prev_until = since - timedelta(days=1)
    prev_since = prev_until - timedelta(days=days - 1)

    try:
        ad_sets = fetch_meta_ad_sets(since, until)
        ads = fetch_meta_ads(since, until)
        prev_ad_sets = fetch_meta_ad_sets(prev_since, prev_until)
    except Exception as e:
        logger.warning("[/meta/export] fetch 실패: %s", e)
        ad_sets, ads, prev_ad_sets = [], [], []
    try:
        placements = fetch_meta_placements(since, until)
    except Exception as e:
        logger.warning("[/meta/export] placement 실패: %s", e)
        placements = []
    placement_analysis = analyze_meta_placements(placements)
    by_adset_placement = placement_analysis.get("by_adset", {})

    # 캠페인 합산
    camp_map = {}
    for a in ad_sets:
        cid = a["campaign_id"]
        if cid not in camp_map:
            camp_map[cid] = {"campaign_id": cid, "campaign_name": a["campaign_name"],
                             "impressions": 0, "clicks": 0, "spend": 0.0,
                             "conversions": 0, "reach": 0}
        c = camp_map[cid]
        c["impressions"] += a["impressions"]; c["clicks"] += a["clicks"]
        c["spend"] += a["spend"]; c["conversions"] += a["conversions"]; c["reach"] += a["reach"]
    campaigns = []
    for c in camp_map.values():
        c["ctr"] = (c["clicks"] / c["impressions"] * 100) if c["impressions"] else 0.0
        c["cpc"] = int(c["spend"] / c["clicks"]) if c["clicks"] else 0
        c["cpa"] = int(c["spend"] / c["conversions"]) if c["conversions"] else 0
        c["spend"] = int(c["spend"])
        dec, rea = classify_budget_decision(c["spend"], c["conversions"], c["cpa"])
        c["budget_decision"] = dec; c["budget_reason"] = rea
        campaigns.append(c)
    campaigns.sort(key=lambda x: -x["spend"])

    # Ad set 분류
    for a in ad_sets:
        a["spend_int"] = int(a["spend"]); a["cpa_int"] = int(a["cpa"])
        dec, rea = classify_budget_decision(a["spend"], a["conversions"], a["cpa"])
        a["budget_decision"] = dec; a["budget_reason"] = rea
        an_pct = by_adset_placement.get(a["ad_set_id"], {}).get("an_pct", 0.0)
        a["an_pct"] = an_pct
        a["diagnosis"] = diagnose_meta_ad_set(a, an_pct=an_pct)
    ad_sets.sort(key=lambda x: -x["spend"])

    # Ad 분류
    for a in ads:
        a["spend_int"] = int(a["spend"]); a["cpa_int"] = int(a["cpa"])
        st, rea = classify_creative_status(ctr=a["ctr"], cpa=a["cpa"],
                                           conv=a["conversions"], spend=a["spend"])
        a["creative_status"] = st; a["creative_reason"] = rea
    ads.sort(key=lambda x: -x["spend"])

    cur_kpi = _kpi(ad_sets)
    prev_kpi = _kpi(prev_ad_sets) if prev_ad_sets else None

    rows = []
    rows.append(["Meta 광고 결과보고서"])
    rows.append([f"기간: {since.isoformat()} ~ {until.isoformat()} ({days}일)"])
    rows.append([f"직전기간: {prev_since.isoformat()} ~ {prev_until.isoformat()}"])
    rows.append([])

    # KPI 요약
    rows.append(["[KPI 요약]"])
    rows.append(["지표", "현재", "직전기간"])
    metrics = [("노출", "impressions"), ("클릭", "clicks"), ("지출(원)", "spend"),
               ("전환(리드)", "conversions"), ("CTR(%)", "ctr"), ("CPC(원)", "cpc"),
               ("CPA(원)", "cpa"), ("CPM(원)", "cpm")]
    for label, key in metrics:
        cur_v = cur_kpi.get(key, 0)
        prev_v = prev_kpi.get(key, 0) if prev_kpi else ""
        if key == "ctr":
            cur_v = f"{cur_v:.2f}"; prev_v = f"{prev_v:.2f}" if prev_kpi else ""
        rows.append([label, cur_v, prev_v])
    rows.append([])

    # 캠페인
    rows.append(["[캠페인]"])
    rows.append(["캠페인명", "노출", "클릭", "지출(원)", "전환", "CTR(%)", "CPC(원)",
                 "CPA(원)", "예산 권고", "사유"])
    for c in campaigns:
        rows.append([c["campaign_name"], c["impressions"], c["clicks"], c["spend"],
                     c["conversions"], f"{c['ctr']:.2f}", c["cpc"], c["cpa"],
                     c.get("budget_decision", ""), c.get("budget_reason", "")])
    rows.append([])

    # Ad set + 진단
    rows.append(["[Ad set 진단]"])
    rows.append(["Ad set명", "캠페인", "노출", "클릭", "지출(원)", "전환", "CTR(%)",
                 "CPC(원)", "CPA(원)", "Frequency", "AN%", "Verdict", "Verdict 사유",
                 "예산 권고", "사유"])
    for a in ad_sets:
        diag = a.get("diagnosis", {})
        rows.append([a["name"], a.get("campaign_name", ""), a["impressions"], a["clicks"],
                     a["spend_int"], a["conversions"], f"{a['ctr']:.2f}",
                     int(a["cpc"]), a["cpa_int"], f"{a['frequency']:.2f}",
                     f"{a['an_pct']:.1f}", diag.get("verdict", ""), diag.get("reason", ""),
                     a.get("budget_decision", ""), a.get("budget_reason", "")])
    rows.append([])

    # Ad (소재)
    rows.append(["[광고 소재]"])
    rows.append(["광고명", "Ad set", "노출", "클릭", "지출(원)", "전환", "CTR(%)",
                 "CPC(원)", "CPA(원)", "소재 평가", "사유"])
    for a in ads:
        rows.append([a["name"], a.get("ad_set_name", ""), a["impressions"], a["clicks"],
                     a["spend_int"], a["conversions"], f"{a['ctr']:.2f}",
                     int(a["cpc"]), a["cpa_int"],
                     a.get("creative_status", ""), a.get("creative_reason", "")])
    rows.append([])

    # Placement
    rows.append(["[Placement 분석]"])
    by_placement = placement_analysis.get("by_placement", {})
    if by_placement:
        rows.append(["Placement", "노출", "클릭", "지출(원)", "전환", "CTR(%)", "CPC(원)"])
        for pname, pdata in sorted(by_placement.items(), key=lambda x: -x[1].get("spend", 0)):
            rows.append([pname, pdata.get("impressions", 0), pdata.get("clicks", 0),
                         int(pdata.get("spend", 0)), pdata.get("conversions", 0),
                         f"{pdata.get('ctr', 0):.2f}", int(pdata.get("cpc", 0))])
    rows.append([])

    fname = f"meta_report_{since.isoformat()}_{until.isoformat()}.csv"
    return _csv_response(rows, fname)


@app.get("/naver/export.csv")
def naver_export(
    preset: str = "7d",
    start: Optional[str] = None,
    end: Optional[str] = None,
    session: Optional[str] = Cookie(None),
):
    if not _check_auth(session):
        return RedirectResponse("/login", status_code=303)

    since, until = _parse_period(preset, start, end)
    days = (until - since).days + 1
    prev_until = since - timedelta(days=1)
    prev_since = prev_until - timedelta(days=days - 1)
    prev_prev_until = prev_since - timedelta(days=1)
    prev_prev_since = prev_prev_until - timedelta(days=days - 1)

    snapshot = _load_snapshot()
    if snapshot:
        all_naver = snapshot.get("naver", [])
        cur = _filter_by_period(all_naver, since, until)
        prev = _filter_by_period(all_naver, prev_since, prev_until)
        prev_prev = _filter_by_period(all_naver, prev_prev_since, prev_prev_until)
    else:
        try:
            cur = fetch_naver(since, until)
        except Exception:
            cur = []
        prev = []; prev_prev = []

    def _kpi_filter(rows, account=None, exclude_campaigns=None):
        sel = rows
        if account:
            sel = [r for r in sel if r.get("account") == account]
        if exclude_campaigns:
            sel = [r for r in sel if not any(x in (r.get("campaign_name") or "") for x in exclude_campaigns)]
        return _kpi(sel)

    rows = []
    rows.append(["Naver 광고 결과보고서"])
    rows.append([f"기간: {since.isoformat()} ~ {until.isoformat()} ({days}일)"])
    rows.append([f"전주: {prev_since.isoformat()} ~ {prev_until.isoformat()}"])
    rows.append([f"전전주: {prev_prev_since.isoformat()} ~ {prev_prev_until.isoformat()}"])
    rows.append([])

    # 3주 KPI 추이
    kc, kp, kpp = _kpi(cur), _kpi(prev), _kpi(prev_prev)
    rows.append(["[3주 KPI 추이]"])
    rows.append(["지표", "전전주", "전주", "이번주"])
    for label, key in [("노출", "impressions"), ("클릭", "clicks"), ("지출(원)", "spend"),
                       ("전환", "conversions"), ("CTR(%)", "ctr"), ("CPC(원)", "cpc")]:
        if key == "ctr":
            rows.append([label, f"{kpp[key]:.2f}", f"{kp[key]:.2f}", f"{kc[key]:.2f}"])
        else:
            rows.append([label, kpp[key], kp[key], kc[key]])
    rows.append([])

    # 계정별 (3계정 × 3주)
    rows.append(["[계정별 KPI]"])
    rows.append(["계정", "기간", "노출", "클릭", "지출(원)", "CTR(%)", "CPC(원)"])
    for label in ["로얄호프치킨 가맹광고 (파워링크)", "버거리 (보승에프앤비)", "구 파워링크 (미사용)"]:
        for period_label, src in [("전전주", prev_prev), ("전주", prev), ("이번주", cur)]:
            k = _kpi_filter(src, account=label)
            rows.append([label, period_label, k["impressions"], k["clicks"], k["spend"],
                         f"{k['ctr']:.2f}", k["cpc"]])
        if "로얄호프" in label:
            for period_label, src in [("전전주(노출용제외)", prev_prev),
                                       ("전주(노출용제외)", prev),
                                       ("이번주(노출용제외)", cur)]:
                k = _kpi_filter(src, account=label, exclude_campaigns=["노출용"])
                rows.append([label, period_label, k["impressions"], k["clicks"], k["spend"],
                             f"{k['ctr']:.2f}", k["cpc"]])
    rows.append([])

    # 캠페인 단위
    camp_map = {}
    for r in cur:
        cid = r["campaign_id"]
        if cid not in camp_map:
            camp_map[cid] = {"campaign_name": r["campaign_name"], "account": r["account"],
                             "brand": r["brand"], "impressions": 0, "clicks": 0, "spend": 0}
        camp_map[cid]["impressions"] += r["impressions"]
        camp_map[cid]["clicks"] += r["clicks"]
        camp_map[cid]["spend"] += r["spend"]
    campaigns = []
    for c in camp_map.values():
        c["ctr"] = (c["clicks"] / c["impressions"] * 100) if c["impressions"] else 0.0
        c["cpc"] = int(c["spend"] / c["clicks"]) if c["clicks"] else 0
        campaigns.append(c)
    campaigns.sort(key=lambda x: -x["spend"])

    rows.append(["[캠페인]"])
    rows.append(["계정", "캠페인명", "노출", "클릭", "지출(원)", "CTR(%)", "CPC(원)"])
    for c in campaigns:
        rows.append([c["account"], c["campaign_name"], c["impressions"], c["clicks"],
                     c["spend"], f"{c['ctr']:.2f}", c["cpc"]])
    rows.append([])

    # 고비용 키워드 권고
    expensive_keywords = []
    if snapshot:
        all_kws = snapshot.get("naver_keywords", []) or []
        for kw in all_kws:
            action = classify_keyword_action(kw.get("cpc", 0), kw.get("impressions", 0),
                                              kw.get("clicks", 0))
            if action:
                ac_label, ac_reason = action
                expensive_keywords.append({**kw, "action_label": ac_label,
                                            "action_reason": ac_reason})
        expensive_keywords.sort(key=lambda k: -k.get("cost", 0))

    if expensive_keywords:
        rows.append(["[고비용 키워드 권고]"])
        rows.append(["키워드", "계정", "캠페인", "노출", "클릭", "비용(원)", "CPC(원)",
                     "권고", "사유"])
        for kw in expensive_keywords:
            rows.append([kw.get("keyword", ""), kw.get("account", ""),
                         kw.get("campaign_name", ""), kw.get("impressions", 0),
                         kw.get("clicks", 0), int(kw.get("cost", 0)),
                         int(kw.get("cpc", 0)), kw.get("action_label", ""),
                         kw.get("action_reason", "")])
        rows.append([])

    fname = f"naver_report_{since.isoformat()}_{until.isoformat()}.csv"
    return _csv_response(rows, fname)


# ─────────────────────────────────────────────
# 헬스체크 (Vercel용)
# ─────────────────────────────────────────────

@app.get("/healthz", response_class=JSONResponse)
def healthz():
    snap_exists = SNAPSHOT_PATH.exists()
    snap_size = SNAPSHOT_PATH.stat().st_size if snap_exists else 0
    return {
        "ok": True,
        "snapshot_path": str(SNAPSHOT_PATH),
        "snapshot_exists": snap_exists,
        "snapshot_size": snap_size,
        "cwd": os.getcwd(),
        "root": str(ROOT),
    }
