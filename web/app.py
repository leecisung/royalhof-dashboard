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
import secrets as pysecrets
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
    fetch_naver,
)
from web.analytics import (
    classify_budget_decision,
    classify_creative_status,
    classify_keyword_action,
    generate_meta_insights,
    generate_naver_insights,
)

load_dotenv(ROOT / ".env")

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
    data = fetch_unified(since, until, force_refresh=bool(refresh))

    # 집계
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

    # 현재 + 직전기간 fetch
    ad_sets = fetch_meta_ad_sets(since, until)
    ads = fetch_meta_ads(since, until)
    prev_ad_sets = fetch_meta_ad_sets(prev_since, prev_until)

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

    # ad set 정리
    for a in ad_sets:
        a["spend_int"] = int(a["spend"])
        a["cpa_int"] = int(a["cpa"])
        decision, reason = classify_budget_decision(a["spend"], a["conversions"], a["cpa"])
        a["budget_decision"] = decision
        a["budget_reason"] = reason
    ad_sets.sort(key=lambda x: -x["spend"])

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

    cur = fetch_naver(since, until)
    prev = fetch_naver(prev_since, prev_until)
    prev_prev = fetch_naver(prev_prev_since, prev_prev_until)

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

    # 비싼 캠페인을 키워드 권고로 활용 (현재 API에서는 캠페인 단위 limit, 키워드 권고는 별도 fetch 필요)
    # 우선 캠페인 단위 권고로 표시
    expensive = []
    for c in campaigns:
        action = classify_keyword_action(c["cpc"], c["impressions"], c["clicks"])
        if action:
            label, reason = action
            expensive.append({**c, "action_label": label, "action_reason": reason})

    insights = generate_naver_insights(kpi_cur, kpi_prev, expensive)

    return TEMPLATES.TemplateResponse(
        request,
        "naver.html",
        {
            "preset": preset, "since": since.isoformat(),
            "until": until.isoformat(), "days": days,
            "prev_since": prev_since.isoformat(), "prev_until": prev_until.isoformat(),
            "prev_prev_since": prev_prev_since.isoformat(), "prev_prev_until": prev_prev_until.isoformat(),
            "kpi": kpi_cur, "kpi_prev": kpi_prev, "kpi_prev_prev": kpi_prev_prev,
            "campaigns": campaigns,
            "expensive": expensive,
            "insights": insights,
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
# 헬스체크 (Vercel용)
# ─────────────────────────────────────────────

@app.get("/healthz", response_class=JSONResponse)
def healthz():
    return {"ok": True}
