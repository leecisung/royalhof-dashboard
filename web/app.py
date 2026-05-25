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

from lib.dashboard_data import fetch_unified

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
# 헬스체크 (Vercel용)
# ─────────────────────────────────────────────

@app.get("/healthz", response_class=JSONResponse)
def healthz():
    return {"ok": True}
