# -*- coding: utf-8 -*-
"""사직점 플레이스 현황 페이지 — NAS 입찰기(8500)에 이식판.

데이터: /royalhof (autoplace 컨테이너와 공유 볼륨) — config/이력은 그쪽이 갱신.
인증: 입찰기 본체의 require_auth 재사용. register(app, require_auth)로 등록.
네이버 API: SAJIK_* 환경변수 (docker-compose environment).
"""
import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from fastapi import Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse

ROOT = Path("/royalhof")
HERE = os.path.dirname(__file__)
KST = timezone(timedelta(hours=9))
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 Mobile Safari/604.1")
ADS_PROBE_KEYWORD = "사직 맛집"
NAVER_BASE = "https://api.searchad.naver.com"


def _cfg(name: str) -> dict:
    try:
        return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _naver_get(path: str, params: dict = None) -> dict:
    ts = str(int(time.time() * 1000))
    key = os.environ.get("SAJIK_SECRET_KEY", "")
    sig = base64.b64encode(hmac.new(key.encode(), f"{ts}.GET.{path}".encode(),
                                    hashlib.sha256).digest()).decode()
    r = requests.get(NAVER_BASE + path, params=params, timeout=15, headers={
        "X-Timestamp": ts, "X-API-KEY": os.environ.get("SAJIK_API_KEY", ""),
        "X-Customer": os.environ.get("SAJIK_CUSTOMER_ID", ""), "X-Signature": sig})
    r.raise_for_status()
    return r.json() if r.text else {}


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
        base = int(target.get("bid_min", 50))
    ev = target.get("event", {})
    if ev and when.strftime("%Y-%m-%d") in event_dates:
        for s, e, mult in _windows(ev.get("boost_windows", {})):
            if s <= when.hour < e:
                base = int(round(base * float(mult)))
                break
    return max(int(target.get("bid_min", 50)), min(int(target.get("bid_max", 100000)), base))


def _fetch_apollo(query: str, x: str, y: str) -> Optional[dict]:
    url = (f"https://m.place.naver.com/restaurant/list"
           f"?query={urllib.parse.quote(query)}&x={x}&y={y}")
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=12)
        r.encoding = "utf-8"
        m = re.search(r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\});", r.text, re.S)
        return json.loads(m.group(1)) if m else None
    except Exception:
        return None


def _organic_list(apollo: dict) -> list:
    return [(str(v.get("id")), v.get("name", ""))
            for k, v in (apollo or {}).items()
            if k.startswith("PlaceListBusinessesItem:")]


def _ad_list(apollo: dict) -> list:
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


def register(app, require_auth):

    @app.get("/place", response_class=HTMLResponse)
    def place_page(request: Request, _=Depends(require_auth)):
        p = os.path.join(HERE, "templates", "place.html")
        return HTMLResponse(open(p, encoding="utf-8").read())

    @app.get("/api/place/status")
    def place_status(request: Request, _=Depends(require_auth)):
        try:
            cfg = _cfg("autoplace_config.json")
            target = next((t for t in cfg.get("targets", []) if t.get("enabled")), {})
            games = set(_cfg("lotte_home_games.json").get("dates", []))
            now = datetime.now(KST)
            today_str = now.strftime("%Y-%m-%d")
            schedule = [{"h": h, "bid": _bid_at(target, now.replace(hour=h, minute=0), games),
                         "on": True} for h in range(24)]
            live = {}
            try:
                gid = target.get("adgroup_id")
                g = _naver_get(f"/ncc/adgroups/{gid}")
                live = {"bid": g.get("bidAmt"), "status": g.get("status"),
                        "userLock": bool(g.get("userLock")),
                        "dailyBudget": g.get("dailyBudget")}
                yday = (now - timedelta(days=1)).date()
                res = _naver_get("/stats", {
                    "ids": gid, "fields": '["impCnt","clkCnt","salesAmt"]',
                    "timeRange": json.dumps({"since": str(yday), "until": str(yday)})})
                for r in (res.get("data", []) if isinstance(res, dict) else []):
                    clk = int(r.get("clkCnt", 0) or 0)
                    cost = int(r.get("salesAmt", 0) or 0)
                    live["yday"] = {"imp": int(r.get("impCnt", 0) or 0), "clk": clk,
                                    "cost": cost, "cpc": cost // clk if clk else 0}
            except Exception as e:
                live = {"error": str(e)}
            scale = None
            try:
                st = _cfg("autoplace_state.json")
                scale = st.get(target.get("adgroup_id"), {}).get("bid_scale")
            except Exception:
                pass
            return JSONResponse({
                "name": target.get("name"), "now_hour": now.hour,
                "today": today_str, "day": DAY_KEYS[now.weekday()],
                "off_day": DAY_KEYS[now.weekday()] in
                           [str(d).lower() for d in target.get("off_days", [])],
                "on_hours": "상시 ON",
                "home_game_today": today_str in games,
                "upcoming_games": sorted(d for d in games if d >= today_str)[:10],
                "game_dates": sorted(games),
                "off_days": [str(d).lower() for d in target.get("off_days", [])],
                "schedule": schedule, "live": live, "bid_scale": scale,
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/place/tagplan")
    def place_tagplan(request: Request, _=Depends(require_auth)):
        plan = _cfg("sajik_tag_plan.json")
        if not plan:
            return JSONResponse({"error": "sajik_tag_plan.json 없음"}, status_code=404)
        try:
            pid = str(_cfg("place_rank_keywords.json").get("place_id"))
            r = requests.get(f"https://m.place.naver.com/restaurant/{pid}/home",
                             headers={"User-Agent": UA}, timeout=12)
            r.encoding = "utf-8"
            m = re.search(r'"keywordList":\s*(\[[^\]]*\])', r.text)
            if m:
                plan["current_keywords"] = json.loads(m.group(1))
        except Exception:
            pass
        return JSONResponse(plan)

    @app.get("/api/place/rankhistory")
    def place_rankhistory(request: Request, _=Depends(require_auth)):
        import csv as _csv
        p = ROOT / "data" / "place_rank_history.csv"
        if not p.exists():
            return JSONResponse({"dates": [], "series": {}})
        hist, dates = {}, []
        try:
            with open(p, encoding="utf-8-sig") as f:
                for row in _csv.DictReader(f):
                    d, kw = row.get("date"), row.get("keyword")
                    rank = row.get("rank") or None
                    if not d or not kw:
                        continue
                    if d not in dates:
                        dates.append(d)
                    hist.setdefault(kw, {})[d] = int(rank) if rank else None
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return JSONResponse({"dates": sorted(set(dates))[-14:], "series": hist})

    @app.get("/api/place/ranks")
    def place_ranks(request: Request, _=Depends(require_auth)):
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
                                 "location": loc.get("name"), "rows": rows,
                                 "ads_keyword": ADS_PROBE_KEYWORD, "ads": ads})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
