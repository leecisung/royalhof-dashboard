# -*- coding: utf-8 -*-
"""
사직 플레이스 현황 — 오토플레이스 운영상태 + 지도 오가닉 순위 + 경쟁 플레이스광고.

- GET /place            : 현황 페이지
- GET /api/place/status : 광고그룹 라이브 상태 + 오늘 스케줄(autoplace_config) + 홈경기
- GET /api/place/ranks  : m.place.naver.com 오가닉 순위 라이브 조회 + 광고 집행 업체

스케줄 계산은 scripts/autoplace.py와 동일 로직(요일별 구간 + 홈경기 boost).
config: data/autoplace_config.json, data/place_rank_keywords.json, data/lotte_home_games.json

register(app, templates, require_auth) 로 기존 FastAPI 앱에 라우트 등록.
"""
import os
import re
import json
import logging
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests
from fastapi import Request, Cookie
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger("place")
ROOT = Path(__file__).resolve().parents[1]

KST = timezone(timedelta(hours=9))
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 Mobile Safari/604.1")
ADS_PROBE_KEYWORD = "사직 맛집"   # 경쟁 플레이스광고 추출용 대표 키워드


def _cfg(name: str) -> dict:
    try:
        return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("config %s 로드 실패: %s", name, e)
        return {}


def _api():
    from lib.naver_api import NaverAdAPI
    return NaverAdAPI(os.getenv("SAJIK_API_KEY", "").strip(),
                      os.getenv("SAJIK_SECRET_KEY", "").strip(),
                      os.getenv("SAJIK_CUSTOMER_ID", "").strip())


# ── 스케줄 계산 (autoplace.py와 동일 규칙) ─────────────

def _windows(sched: dict) -> list:
    out = []
    for rng, val in (sched or {}).items():
        if str(rng).startswith("_"):
            continue
        try:
            s, e = rng.split("-")
            out.append((int(s), int(e), val))
        except (ValueError, AttributeError):
            pass
    return sorted(out)


def _bid_at(target: dict, when: datetime, event_dates: set) -> int:
    sched = target.get("schedule", {})
    day_sched = sched.get(DAY_KEYS[when.weekday()]) or sched.get("default") or {}
    base = None
    for s, e, val in _windows(day_sched):
        if s <= when.hour < e:
            base = int(val)
            break
    if base is None:
        base = int(target.get("bid_min", 70))
    ev = target.get("event", {})
    if ev and when.strftime("%Y-%m-%d") in event_dates:
        for s, e, mult in _windows(ev.get("boost_windows", {})):
            if s <= when.hour < e:
                base = int(round(base * float(mult)))
                break
    return max(int(target.get("bid_min", 70)), min(int(target.get("bid_max", 100000)), base))


def _on_at(target: dict, when: datetime) -> bool:
    if DAY_KEYS[when.weekday()] in [str(d).lower() for d in target.get("off_days", [])]:
        return False
    on_hours = target.get("on_hours")
    if not on_hours:
        return True
    try:
        s, e = (int(x) for x in on_hours.split("-"))
        return s <= when.hour < e
    except ValueError:
        return True


# ── 지도 오가닉 순위 ───────────────────────────────

def _fetch_apollo(query: str, x: str, y: str) -> Optional[dict]:
    url = (f"https://m.place.naver.com/restaurant/list"
           f"?query={urllib.parse.quote(query)}&x={x}&y={y}")
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=12)
        r.encoding = "utf-8"
        m = re.search(r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\});", r.text, re.S)
        return json.loads(m.group(1)) if m else None
    except Exception as e:
        logger.warning("m.place '%s' 조회 실패: %s", query, e)
        return None


def _organic_list(apollo: dict) -> list:
    return [(str(v.get("id")), v.get("name", ""))
            for k, v in (apollo or {}).items()
            if k.startswith("PlaceListBusinessesItem:")]


def _ad_list(apollo: dict) -> list:
    """ROOT_QUERY adBusinesses(...) → [(id, name)] (플레이스광고 집행 업체)."""
    out, seen = [], set()
    rq = (apollo or {}).get("ROOT_QUERY", {})
    for k, v in rq.items():
        if not k.startswith("adBusinesses"):
            continue
        items = v.get("items") if isinstance(v, dict) else v
        for it in (items if isinstance(items, list) else []):
            if isinstance(it, dict) and "__ref" in it:
                ref = (apollo or {}).get(it["__ref"], {})
                pid, name = str(ref.get("id")), ref.get("name", "")
                if pid and pid not in seen:
                    seen.add(pid)
                    out.append((pid, name))
    return out


# ── 라우트 ────────────────────────────────────────

def register(app, templates, require_auth):

    @app.get("/place", response_class=HTMLResponse)
    def place_page(request: Request, session: Optional[str] = Cookie(None)):
        require_auth(session)
        return templates.TemplateResponse(request, "place.html", {})

    @app.get("/api/place/status")
    def place_status(session: Optional[str] = Cookie(None)):
        require_auth(session)
        try:
            cfg = _cfg("autoplace_config.json")
            target = next((t for t in cfg.get("targets", []) if t.get("enabled")), {})
            games = set(_cfg(target.get("event", {}).get("dates_file", "lotte_home_games.json")
                             .replace("data/", "")).get("dates", []))
            now = datetime.now(KST)
            today_str = now.strftime("%Y-%m-%d")

            schedule = []
            for h in range(24):
                t = now.replace(hour=h, minute=0)
                schedule.append({"h": h, "bid": _bid_at(target, t, games),
                                 "on": _on_at(target, t)})

            live = {}
            try:
                api = _api()
                gid = target.get("adgroup_id")
                g = api._request("GET", f"/ncc/adgroups/{gid}")
                live = {"bid": g.get("bidAmt"), "status": g.get("status"),
                        "userLock": bool(g.get("userLock")),
                        "dailyBudget": g.get("dailyBudget")}
                yday = (now - timedelta(days=1)).date()
                res = api._request("GET", "/stats", params={
                    "ids": gid, "fields": '["impCnt","clkCnt","salesAmt"]',
                    "timeRange": json.dumps({"since": str(yday), "until": str(yday)}),
                })
                for r in (res.get("data", []) if isinstance(res, dict) else []):
                    clk = int(r.get("clkCnt", 0) or 0)
                    cost = int(r.get("salesAmt", 0) or 0)
                    live["yday"] = {"imp": int(r.get("impCnt", 0) or 0), "clk": clk,
                                    "cost": cost, "cpc": cost // clk if clk else 0}
            except Exception as e:
                live = {"error": str(e)}

            scale = None
            try:
                st = json.loads((ROOT / "data" / "autoplace_state.json")
                                .read_text(encoding="utf-8"))
                scale = st.get(target.get("adgroup_id"), {}).get("bid_scale")
            except Exception:
                pass

            return JSONResponse({
                "name": target.get("name"), "now_hour": now.hour,
                "today": today_str, "day": DAY_KEYS[now.weekday()],
                "off_day": DAY_KEYS[now.weekday()] in
                           [str(d).lower() for d in target.get("off_days", [])],
                "on_hours": target.get("on_hours"),
                "home_game_today": today_str in games,
                "upcoming_games": sorted(d for d in games if d >= today_str)[:10],
                "game_dates": sorted(games),
                "off_days": [str(d).lower() for d in target.get("off_days", [])],
                "schedule": schedule, "live": live, "bid_scale": scale,
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/place/tagplan")
    def place_tagplan(session: Optional[str] = Cookie(None)):
        """사직점 태그/키워드 점령 플랜 (scripts/sajik_tag_plan.py 산출물)."""
        require_auth(session)
        plan = _cfg("sajik_tag_plan.json")
        if not plan:
            return JSONResponse({"error": "sajik_tag_plan.json 없음 — "
                                 "python scripts/sajik_tag_plan.py 실행 필요"},
                                status_code=404)
        return JSONResponse(plan)

    @app.get("/api/place/ranks")
    def place_ranks(session: Optional[str] = Cookie(None)):
        require_auth(session)
        try:
            cfg = _cfg("place_rank_keywords.json")
            pid = str(cfg.get("place_id"))
            locs = cfg.get("locations") or [{"name": "기본", "x": cfg.get("x"), "y": cfg.get("y")}]
            loc = locs[0]
            keywords = cfg.get("keywords", [])

            with ThreadPoolExecutor(max_workers=4) as ex:
                apollos = list(ex.map(
                    lambda kw: _fetch_apollo(kw, loc.get("x"), loc.get("y")), keywords))

            rows, ads = [], []
            for kw, apollo in zip(keywords, apollos):
                items = _organic_list(apollo)
                rank = next((i for i, (id_, _) in enumerate(items, 1) if id_ == pid), None)
                rows.append({"keyword": kw, "rank": rank, "list_size": len(items),
                             "top1": items[0][1] if items else None,
                             "ok": apollo is not None})
                if kw == ADS_PROBE_KEYWORD and apollo:
                    ads = [{"id": i, "name": n, "mine": i == pid}
                           for i, n in _ad_list(apollo)]

            return JSONResponse({"place_name": cfg.get("place_name"),
                                 "location": loc.get("name"),
                                 "rows": rows,
                                 "ads_keyword": ADS_PROBE_KEYWORD, "ads": ads})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    logger.info("place 라우트 등록 완료")
