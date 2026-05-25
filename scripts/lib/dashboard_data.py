# -*- coding: utf-8 -*-
"""
대시보드 데이터 통합 계층.
- Naver 3계정 (로얄호프 / 버거리 신 / 버거리 구=보승회관) 캠페인 일별 stats
- Meta 모든 캠페인 일별 insights
- GA4 (옵션) 일별 + UTM 채널별

캐시: data/dashboard_cache.db (sqlite), TTL 1시간. force_refresh=True로 우회.

대시보드(dashboard.py)는 이 모듈의 fetch_unified만 호출하면 됨.
"""

import os
import sys
import json
import logging
import sqlite3
import hashlib
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.naver_api import NaverAdAPI

logger = logging.getLogger(__name__)

# Vercel 등 read-only 환경에서는 /tmp 사용. 로컬은 data/ 폴더.
if os.path.exists("/tmp") and not os.access(str(ROOT / "data"), os.W_OK):
    CACHE_DB = Path("/tmp/dashboard_cache.db")
else:
    CACHE_DB = ROOT / "data" / "dashboard_cache.db"
CACHE_TTL_SECONDS = 3600  # 1시간


# ─────────────────────────────────────────────
# 캐시 (sqlite)
# ─────────────────────────────────────────────

def _cache_init():
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(CACHE_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)
    con.commit()
    con.close()


def _cache_get(key: str) -> Optional[dict]:
    _cache_init()
    con = sqlite3.connect(CACHE_DB)
    cur = con.execute("SELECT payload, created_at FROM cache WHERE key=?", (key,))
    row = cur.fetchone()
    con.close()
    if not row:
        return None
    payload, created_at = row
    age = int(datetime.now().timestamp()) - int(created_at)
    if age > CACHE_TTL_SECONDS:
        return None
    return json.loads(payload)


def _cache_put(key: str, data: dict):
    _cache_init()
    con = sqlite3.connect(CACHE_DB)
    con.execute(
        "REPLACE INTO cache(key,payload,created_at) VALUES (?,?,?)",
        (key, json.dumps(data, ensure_ascii=False, default=str), int(datetime.now().timestamp())),
    )
    con.commit()
    con.close()


def cache_clear():
    """전체 캐시 초기화 (강제 갱신용)."""
    _cache_init()
    con = sqlite3.connect(CACHE_DB)
    con.execute("DELETE FROM cache")
    con.commit()
    con.close()


def _key(*parts) -> str:
    return hashlib.md5("::".join(str(p) for p in parts).encode()).hexdigest()


# ─────────────────────────────────────────────
# Naver
# ─────────────────────────────────────────────

NAVER_ACCOUNTS = [
    # (env_prefix, label, brand)
    ("NAVER_AD", "로얄호프치킨", "로얄호프"),
    ("BURGEORI_NEW", "버거리(신)", "버거리"),
    ("BURGEORI_OLD", "보승회관(694291)", "기타"),
]


def _naver_api(prefix: str) -> Optional[NaverAdAPI]:
    key = os.getenv(f"{prefix}_API_KEY", "").strip()
    sec = os.getenv(f"{prefix}_SECRET_KEY", "").strip()
    cid = os.getenv(f"{prefix}_CUSTOMER_ID", "").strip()
    if not (key and sec and cid):
        return None
    return NaverAdAPI(key, sec, cid)


def _naver_campaigns(api: NaverAdAPI) -> list[dict]:
    """캠페인 목록."""
    try:
        res = api._request("GET", "/ncc/campaigns")
        return res if isinstance(res, list) else []
    except Exception as e:
        logger.warning("[Naver] 캠페인 목록 실패: %s", e)
        return []


def _naver_campaign_stats(api: NaverAdAPI, campaign_id: str, since: str, until: str) -> list[tuple]:
    """캠페인 일별 stats. [(date, imp, clk, cost)]."""
    params = {
        "id": campaign_id,
        "fields": '["clkCnt","impCnt","salesAmt"]',
        "timeUnit": "day",
        "timeRange": json.dumps({"since": since, "until": until}),
    }
    try:
        res = api._request("GET", "/stats", params=params)
    except Exception as e:
        logger.warning("[Naver] stats 실패 %s: %s", campaign_id, e)
        return []
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


def fetch_naver(since: date, until: date) -> list[dict]:
    """네이버 3계정 캠페인 일별 통합. [{date, channel, brand, account, campaign_id, campaign_name, impressions, clicks, spend}]"""
    out = []
    for prefix, label, brand in NAVER_ACCOUNTS:
        api = _naver_api(prefix)
        if not api:
            logger.info("[Naver] %s 미설정, skip", prefix)
            continue
        camps = _naver_campaigns(api)
        for c in camps:
            cid = c.get("nccCampaignId", "")
            cname = c.get("name", "")
            if not cid:
                continue
            rows = _naver_campaign_stats(api, cid, str(since), str(until))
            for d, imp, clk, cost in rows:
                out.append({
                    "date": d,
                    "channel": "Naver",
                    "brand": brand,
                    "account": label,
                    "campaign_id": cid,
                    "campaign_name": cname,
                    "impressions": imp,
                    "clicks": clk,
                    "spend": cost,
                    "conversions": 0,  # 메모리상 전환추적 미사용 결정
                })
    return out


# ─────────────────────────────────────────────
# Meta
# ─────────────────────────────────────────────

def fetch_meta(since: date, until: date) -> list[dict]:
    """Meta 캠페인 일별. [{date, channel='Meta', brand='버거리', campaign_id, campaign_name, impressions, clicks, spend, conversions}]"""
    from lib.meta_api import MetaAdsAPI
    from facebook_business.adobjects.adaccount import AdAccount
    from facebook_business.adobjects.adsinsights import AdsInsights
    try:
        api = MetaAdsAPI.from_env()
    except Exception as e:
        logger.warning("[Meta] API 초기화 실패: %s", e)
        return []
    acct = AdAccount(api.ad_account_id)
    params = {
        "time_range": {"since": str(since), "until": str(until)},
        "time_increment": 1,
        "level": "campaign",
    }
    fields = [
        AdsInsights.Field.date_start,
        AdsInsights.Field.campaign_id,
        AdsInsights.Field.campaign_name,
        AdsInsights.Field.impressions,
        AdsInsights.Field.clicks,
        AdsInsights.Field.spend,
        AdsInsights.Field.actions,
    ]
    try:
        cursor = api._call(acct.get_insights, fields=fields, params=params)
    except Exception as e:
        logger.warning("[Meta] insights 실패: %s", e)
        return []
    out = []
    for r in cursor:
        d = dict(r)
        conv = api._extract_conversions(d.get("actions", []))
        out.append({
            "date": d.get("date_start", ""),
            "channel": "Meta",
            "brand": "버거리",
            "account": "Meta(마케팅팀)",
            "campaign_id": d.get("campaign_id", ""),
            "campaign_name": d.get("campaign_name", ""),
            "impressions": int(d.get("impressions", 0) or 0),
            "clicks": int(d.get("clicks", 0) or 0),
            "spend": float(d.get("spend", 0) or 0),
            "conversions": conv,
        })
    return out


def fetch_meta_ad_sets(since: date, until: date) -> list[dict]:
    """Meta ad set 단위 집계 (기간 합계). [{ad_set_id, ad_set_name, campaign_*, imp/clk/spend/conv/reach/frequency, daily_budget, status}]"""
    from lib.meta_api import MetaAdsAPI
    from facebook_business.adobjects.adaccount import AdAccount
    from facebook_business.adobjects.adsinsights import AdsInsights
    try:
        api = MetaAdsAPI.from_env()
    except Exception as e:
        logger.warning("[Meta] ad_set API init 실패: %s", e)
        return []
    acct = AdAccount(api.ad_account_id)
    params = {
        "time_range": {"since": str(since), "until": str(until)},
        "level": "adset",
        "limit": 500,
    }
    fields = [
        AdsInsights.Field.adset_id, AdsInsights.Field.adset_name,
        AdsInsights.Field.campaign_id, AdsInsights.Field.campaign_name,
        AdsInsights.Field.impressions, AdsInsights.Field.clicks,
        AdsInsights.Field.spend, AdsInsights.Field.ctr,
        AdsInsights.Field.frequency, AdsInsights.Field.reach,
        AdsInsights.Field.actions,
    ]
    try:
        cursor = api._call(acct.get_insights, fields=fields, params=params)
    except Exception as e:
        logger.warning("[Meta] ad_set insights 실패: %s", e)
        return []
    out = []
    for r in cursor:
        d = dict(r)
        conv = api._extract_conversions(d.get("actions", []))
        spend = float(d.get("spend", 0) or 0)
        out.append({
            "ad_set_id": d.get("adset_id", ""),
            "ad_set_name": d.get("adset_name", ""),
            "campaign_id": d.get("campaign_id", ""),
            "campaign_name": d.get("campaign_name", ""),
            "impressions": int(d.get("impressions", 0) or 0),
            "clicks": int(d.get("clicks", 0) or 0),
            "spend": spend,
            "conversions": conv,
            "cpa": (spend / conv) if conv else 0.0,
            "ctr": float(d.get("ctr", 0) or 0),
            "frequency": float(d.get("frequency", 0) or 0),
            "reach": int(d.get("reach", 0) or 0),
        })
    return out


def fetch_meta_ads(since: date, until: date) -> list[dict]:
    """Meta 광고(개별 소재) 단위 집계 (기간 합계). [{ad_id, ad_name, ad_set_id, campaign_*, imp/clk/spend/conv, ctr, frequency}]"""
    from lib.meta_api import MetaAdsAPI
    from facebook_business.adobjects.adaccount import AdAccount
    from facebook_business.adobjects.adsinsights import AdsInsights
    try:
        api = MetaAdsAPI.from_env()
    except Exception as e:
        logger.warning("[Meta] ad API init 실패: %s", e)
        return []
    acct = AdAccount(api.ad_account_id)
    params = {
        "time_range": {"since": str(since), "until": str(until)},
        "level": "ad",
        "limit": 500,
    }
    fields = [
        AdsInsights.Field.ad_id, AdsInsights.Field.ad_name,
        AdsInsights.Field.adset_id, AdsInsights.Field.adset_name,
        AdsInsights.Field.campaign_id, AdsInsights.Field.campaign_name,
        AdsInsights.Field.impressions, AdsInsights.Field.clicks,
        AdsInsights.Field.spend, AdsInsights.Field.ctr,
        AdsInsights.Field.frequency,
        AdsInsights.Field.actions,
    ]
    try:
        cursor = api._call(acct.get_insights, fields=fields, params=params)
    except Exception as e:
        logger.warning("[Meta] ad insights 실패: %s", e)
        return []
    out = []
    for r in cursor:
        d = dict(r)
        conv = api._extract_conversions(d.get("actions", []))
        spend = float(d.get("spend", 0) or 0)
        out.append({
            "ad_id": d.get("ad_id", ""),
            "ad_name": d.get("ad_name", ""),
            "ad_set_id": d.get("adset_id", ""),
            "ad_set_name": d.get("adset_name", ""),
            "campaign_id": d.get("campaign_id", ""),
            "campaign_name": d.get("campaign_name", ""),
            "impressions": int(d.get("impressions", 0) or 0),
            "clicks": int(d.get("clicks", 0) or 0),
            "spend": spend,
            "conversions": conv,
            "cpa": (spend / conv) if conv else 0.0,
            "ctr": float(d.get("ctr", 0) or 0),
            "frequency": float(d.get("frequency", 0) or 0),
        })
    return out


# ─────────────────────────────────────────────
# GA4 (옵션)
# ─────────────────────────────────────────────

def fetch_ga4(since: date, until: date) -> dict:
    """GA4 데이터. {daily:[...], by_source:[...], by_campaign:[...], by_event:[...], configured: bool, error: str|None}"""
    try:
        from lib.ga4_api import GA4API, GA4NotConfigured
    except Exception as e:
        return {"configured": False, "error": f"GA4 모듈 import 실패: {e}", "daily": [], "by_source": [], "by_campaign": [], "by_event": []}
    try:
        api = GA4API.from_env()
    except GA4NotConfigured as e:
        return {"configured": False, "error": str(e), "daily": [], "by_source": [], "by_campaign": [], "by_event": []}
    except Exception as e:
        return {"configured": False, "error": f"GA4 초기화 실패: {e}", "daily": [], "by_source": [], "by_campaign": [], "by_event": []}
    try:
        return {
            "configured": True,
            "error": None,
            "daily": api.fetch_daily_summary(since, until),
            "by_source": api.fetch_by_source(since, until),
            "by_campaign": api.fetch_by_campaign(since, until),
            "by_event": api.fetch_conversions_by_event(since, until),
        }
    except Exception as e:
        logger.warning("[GA4] fetch 실패: %s", e)
        return {"configured": True, "error": str(e), "daily": [], "by_source": [], "by_campaign": [], "by_event": []}


# ─────────────────────────────────────────────
# 통합 (캐시 적용)
# ─────────────────────────────────────────────

def fetch_unified(since: date, until: date, force_refresh: bool = False) -> dict:
    """
    {
      "since": "...", "until": "...",
      "naver":  [{date, channel='Naver', brand, account, campaign_*, imp/clk/spend/conv}, ...],
      "meta":   [{date, channel='Meta',  brand='버거리', ...}, ...],
      "ga4":    {configured, error, daily, by_source, by_campaign, by_event},
      "fetched_at": "ISO timestamp",
      "from_cache": bool,
    }
    """
    key = _key("unified_v1", str(since), str(until))
    if not force_refresh:
        cached = _cache_get(key)
        if cached:
            cached["from_cache"] = True
            return cached

    logger.info("[Dashboard] unified fetch %s ~ %s", since, until)
    data = {
        "since": str(since),
        "until": str(until),
        "naver": fetch_naver(since, until),
        "meta": fetch_meta(since, until),
        "ga4": fetch_ga4(since, until),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "from_cache": False,
    }
    _cache_put(key, data)
    return data
