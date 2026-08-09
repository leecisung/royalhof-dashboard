# -*- coding: utf-8 -*-
"""
사직 파워링크 자동입찰기 — 웹 제어판 + Vercel cron 백엔드.

핵심: 지역타겟=부산. 그래서 '추정 순위'(전국 기준)는 부정확 → **실제 달성 모바일순위
(avgRnk, 최근2일)**를 진짜 지표로 사용/표시한다.

저장: 목표설정(pos/cap)은 Upstash KV(REST)에 저장 → 웹 수정 ↔ cron 읽기 공유.
      KV 미설정 시 data/sajik_bid_targets.json 읽기전용 폴백.
Naver: SAJIK_* 환경변수.

register(app, templates, require_auth, require_action_auth) 로 기존 FastAPI 앱에 라우트 등록.
"""
import os
import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import requests
from fastapi import Request, Form, Cookie
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger("bidder")
ROOT = Path(__file__).resolve().parents[1]

# 로컬 dev에서 단독 임포트 시에도 자격증명 로드(프로덕션은 Vercel env 사용 → .env 없어도 무해)
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

PL_CAMP = "cmp-a001-01-000000010750902"           # 사직 파워링크
PLACE_GID = "grp-a001-06-000000068828400"         # 사직 플레이스 기본그룹
CFG_FILE = ROOT / "data" / "sajik_bid_targets.json"
VOL_FILE = ROOT / "data" / "sajik_kw_volume.json"
KV_KEY = "sajik_bid_targets"
BID_MIN = 70


def _vol_map() -> dict:
    try:
        return json.loads(VOL_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

# ── Naver client ──────────────────────────────
def _api():
    from lib.naver_api import NaverAdAPI
    return NaverAdAPI(os.getenv("SAJIK_API_KEY", "").strip(),
                      os.getenv("SAJIK_SECRET_KEY", "").strip(),
                      os.getenv("SAJIK_CUSTOMER_ID", "").strip())

# ── 저장소 (Upstash KV REST + 파일 폴백) ──────────
def _kv_env():
    url = os.getenv("KV_REST_API_URL", "").strip().rstrip("/")
    tok = os.getenv("KV_REST_API_TOKEN", "").strip()
    return (url, tok) if url and tok else (None, None)


def load_targets() -> dict:
    url, tok = _kv_env()
    if url:
        try:
            r = requests.get(f"{url}/get/{KV_KEY}", headers={"Authorization": f"Bearer {tok}"}, timeout=8)
            val = r.json().get("result")
            if val:
                return json.loads(val)
        except Exception as e:
            logger.warning("KV load 실패, 파일 폴백: %s", e)
    return json.loads(CFG_FILE.read_text(encoding="utf-8"))


def save_targets(cfg: dict) -> bool:
    url, tok = _kv_env()
    if not url:
        return False  # KV 없으면 저장 불가(읽기전용)
    requests.post(f"{url}/set/{KV_KEY}", headers={"Authorization": f"Bearer {tok}"},
                  data=json.dumps(cfg, ensure_ascii=False).encode("utf-8"), timeout=8).raise_for_status()
    return True


def storage_writable() -> bool:
    return _kv_env()[0] is not None

# ── 입찰 수렴 로직 ────────────────────────────
def decide(bid, imp, rnk, pos, cap, step):
    if bid > cap:
        return cap, f"캡{cap}로내림"
    lo, hi = pos - 0.3, pos + 0.4
    if imp == 0:
        return (min(cap, bid + step), "+미노출") if bid < cap else (bid, "상한_미노출")
    if rnk > hi:
        return (min(cap, bid + step), f"+{pos}위까지") if bid < cap else (bid, f"상한_{pos}위못감")
    if rnk < lo and bid > BID_MIN:
        return max(BID_MIN, bid - step), "-절약"
    return bid, f"{pos}위유지"

# ── 관리키워드 수집 + 실측순위 ──────────────────
def _managed_rows(api, targets):
    """관리대상 키워드의 현입찰·실모바일순위·노출·소진을 모아 반환."""
    groups = api._request("GET", "/ncc/adgroups", params={"nccCampaignId": PL_CAMP})
    groups = groups if isinstance(groups, list) else groups.get("items", [])
    loc = {}   # name -> (kid, gid, bid, inspect)
    for g in groups:
        if g.get("status") == "PAUSED":
            continue
        kws = api._request("GET", "/ncc/keywords", params={"nccAdgroupId": g["nccAdgroupId"]})
        kws = kws if isinstance(kws, list) else kws.get("items", [])
        for k in kws:
            nm = k.get("keyword")
            if nm in targets and nm not in loc:
                loc[nm] = (k.get("nccKeywordId"), g["nccAdgroupId"], int(k.get("bidAmt") or 70),
                           k.get("inspectStatus"))
    ids = [v[0] for v in loc.values()]
    rnk2 = _stats(api, ids, days=2, mobile=True)      # 실측 모바일 순위(부산타겟 반영)
    today = _stats(api, ids, days=1, mobile=False)    # 오늘 소진
    vol = _vol_map()
    rows = []
    for nm, t in targets.items():
        kid, gid, bid, insp = loc.get(nm, (None, None, None, None))
        s2 = rnk2.get(kid, {}); st = today.get(kid, {})
        rows.append({
            "keyword": nm, "kid": kid, "gid": gid, "bid": bid, "inspect": insp,
            "vol": vol.get(nm, 0),
            "pos": t.get("pos"), "cap": t.get("cap"),
            "rnk": round(s2.get("rnk", 0), 1), "imp2": s2.get("imp", 0),
            "imp": st.get("imp", 0), "clk": st.get("clk", 0), "cost": st.get("cost", 0),
            "registered": kid is not None,
        })
    rows.sort(key=lambda r: -(r["vol"] or 0))
    return rows


def _stats(api, ids, days=2, mobile=False):
    if not ids:
        return {}
    until = date.today(); since = until - timedelta(days=days - 1)
    params = {"ids": ",".join(ids),
              "fields": '["impCnt","clkCnt","salesAmt","avgRnk"]',
              "timeRange": json.dumps({"since": str(since), "until": str(until)})}
    if mobile:
        params["breakdown"] = "pcMobile"
    out = {}
    try:
        res = api._request("GET", "/stats", params=params)
    except Exception:
        return out
    for r in (res.get("data", []) if isinstance(res, dict) else []):
        if mobile:
            # 모바일 분해 행 우선, 없으면 통합 avgRnk(부산타겟이라 실측 달성순위) 폴백
            mob = next((b for b in r.get("breakdowns", []) if str(b.get("name", "")).upper().startswith("M")), None)
            src = mob if mob else r
            out[r.get("id")] = {"imp": int(src.get("impCnt", 0) or 0), "rnk": float(src.get("avgRnk", 0) or 0)}
        else:
            out[r.get("id")] = {"imp": int(r.get("impCnt", 0) or 0), "clk": int(r.get("clkCnt", 0) or 0),
                                "cost": int(r.get("salesAmt", 0) or 0)}
    return out

# ── 봇 1회 실행 ───────────────────────────────
def run_bidder(dry=False, only=None):
    """only=키워드 집합이면 그것만 조정(체크한 것만 실행). None이면 전체(cron)."""
    api = _api()
    cfg = load_targets()
    targets = cfg.get("targets", {})
    step = int(cfg.get("_step", 50))
    rows = _managed_rows(api, targets)
    only = set(only) if only else None
    changes = []
    for r in rows:
        if not r["registered"] or r["inspect"] != "APPROVED":
            continue
        if only is not None and r["keyword"] not in only:
            continue
        newbid, reason = decide(r["bid"], r["imp2"], r["rnk"], int(r["pos"]), int(r["cap"]), step)
        if newbid != r["bid"]:
            changes.append({"keyword": r["keyword"], "old": r["bid"], "new": newbid,
                            "reason": reason, "rnk": r["rnk"], "pos": r["pos"]})
            if not dry:
                try:
                    api.update_bid(r["kid"], newbid, r["gid"])
                except Exception as e:
                    logger.error("적용실패 %s: %s", r["keyword"], e)
    logger.info("입찰기 실행 %s: 조정 %d", "DRY" if dry else "LIVE", len(changes))
    return changes


def _curves(api, keywords):
    """키워드별 위치(1~5위)별 추정입찰 곡선 — 5콜로 전체. {kw:{1:bid,...}}"""
    out = {}
    for pos in (1, 2, 3, 4, 5):
        est = api.estimate_position_bid_batch(keywords, pos, device="MOBILE")
        for kw, bid in est.items():
            out.setdefault(kw, {})[pos] = bid
    return out


def get_status():
    api = _api()
    cfg = load_targets()
    targets = cfg.get("targets", {})
    rows = _managed_rows(api, targets)
    # 위치곡선(즉시 예상순위·시뮬용). 등록된 키워드만.
    regkw = [r["keyword"] for r in rows if r["registered"]]
    curves = _curves(api, regkw) if regkw else {}
    for r in rows:
        r["curve"] = curves.get(r["keyword"], {})
    # 플레이스 입찰
    try:
        pg = api._request("GET", f"/ncc/adgroups/{PLACE_GID}")
        place_bid = pg.get("bidAmt"); place_status = pg.get("status")
    except Exception:
        place_bid = None; place_status = None
    try:
        money = api._request("GET", "/billing/bizmoney").get("bizmoney", 0)
    except Exception:
        money = None
    return {"rows": rows, "place_bid": place_bid, "place_status": place_status,
            "bizmoney": money, "writable": storage_writable(),
            "step": int(cfg.get("_step", 50))}

# ── 라우트 등록 ───────────────────────────────
def register(app, templates, require_auth, require_action_auth):

    @app.get("/bidder", response_class=HTMLResponse)
    def bidder_page(request: Request, session: Optional[str] = Cookie(None)):
        require_auth(session)
        return templates.TemplateResponse(request, "bidder.html", {})

    @app.get("/api/bidder/status")
    def bidder_status(session: Optional[str] = Cookie(None)):
        require_auth(session)
        try:
            return JSONResponse(get_status())
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/bidder/ranks")
    def bidder_ranks(kids: str = Form(...), session: Optional[str] = Cookie(None)):
        """순위만 빠르게 재조회(그룹 스캔 없이 kid로 1콜) — '순위보기' 버튼용."""
        require_auth(session)
        try:
            ids = [k for k in kids.split(",") if k]
            data = _stats(_api(), ids, days=2, mobile=True)
            return JSONResponse({k: round(v.get("rnk", 0), 1) for k, v in data.items()})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/bidder/target")
    def bidder_set_target(keyword: str = Form(...), pos: int = Form(...), cap: int = Form(...),
                          session: Optional[str] = Cookie(None)):
        require_action_auth(session)
        if not storage_writable():
            return JSONResponse({"ok": False, "error": "KV 저장소 미설정 — 목표 영구저장 불가(읽기전용)"}, status_code=400)
        cfg = load_targets()
        cfg.setdefault("targets", {})[keyword] = {"pos": int(pos), "cap": int(cap)}
        save_targets(cfg)
        return JSONResponse({"ok": True, "keyword": keyword, "pos": pos, "cap": cap})

    @app.post("/api/bidder/keyword-bid")
    def bidder_kw_bid(kid: str = Form(...), gid: str = Form(...), bid: int = Form(...),
                      session: Optional[str] = Cookie(None)):
        require_action_auth(session)
        try:
            _api().update_bid(kid, int(bid), gid)
            return JSONResponse({"ok": True, "kid": kid, "bid": bid})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.post("/api/bidder/place-bid")
    def bidder_place_bid(bid: int = Form(...), session: Optional[str] = Cookie(None)):
        require_action_auth(session)
        try:
            api = _api()
            g = api._request("GET", f"/ncc/adgroups/{PLACE_GID}")
            b = dict(g); b["bidAmt"] = int(bid)
            r = api._request("PUT", f"/ncc/adgroups/{PLACE_GID}", params={"fields": "bidAmt"}, body=b)
            return JSONResponse({"ok": True, "place_bid": r.get("bidAmt")})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.post("/api/bidder/run")
    def bidder_run(dry: int = Form(0), only: str = Form(""), session: Optional[str] = Cookie(None)):
        require_action_auth(session)
        try:
            sel = [k for k in only.split("||") if k] or None
            return JSONResponse({"ok": True, "changes": run_bidder(dry=bool(dry), only=sel)})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.get("/api/cron/bidder")
    def bidder_cron(request: Request):
        # Vercel cron 또는 CRON_SECRET 헤더로 보호
        secret = os.getenv("CRON_SECRET", "").strip()
        auth = request.headers.get("authorization", "")
        if secret and auth != f"Bearer {secret}":
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
        try:
            return JSONResponse({"ok": True, "changes": run_bidder(dry=False)})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    logger.info("bidder 라우트 등록 완료")
