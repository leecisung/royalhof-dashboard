# -*- coding: utf-8 -*-
"""
Naver Search Ad API 클라이언트
- HMAC-SHA256 인증
- Rate limit: 5 calls/sec
- 실패 시 exponential backoff 3회 재시도
"""

import hashlib
import hmac
import base64
import time
import logging
import json
from datetime import datetime, timedelta
from urllib.parse import urlencode, quote
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.searchad.naver.com"
RATE_LIMIT_INTERVAL = 0.2  # 5 calls/sec → 200ms 간격
BATCH_SIZE = 100


class NaverAdAPI:
    def __init__(self, api_key: str, secret_key: str, customer_id: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.customer_id = customer_id
        self._last_call_time = 0.0

    # ──────────────────────────────────────────────
    # 인증 / 공통
    # ──────────────────────────────────────────────

    def _sign(self, timestamp: str, method: str, path: str) -> str:
        message = f"{timestamp}.{method}.{path}"
        raw = hmac.new(
            self.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(raw).decode("utf-8")

    def _headers(self, method: str, path: str) -> dict:
        timestamp = str(int(time.time() * 1000))
        return {
            "Content-Type": "application/json; charset=UTF-8",
            "X-Timestamp": timestamp,
            "X-API-KEY": self.api_key,
            "X-Customer": str(self.customer_id),
            "X-Signature": self._sign(timestamp, method, path),
        }

    def _rate_limit(self):
        elapsed = time.time() - self._last_call_time
        if elapsed < RATE_LIMIT_INTERVAL:
            time.sleep(RATE_LIMIT_INTERVAL - elapsed)
        self._last_call_time = time.time()

    def _request(self, method: str, path: str, params: dict = None, body=None) -> dict:
        """재시도 포함 공통 요청. 실패 시 exponential backoff (1s, 2s, 4s)."""
        url = BASE_URL + path
        headers = self._headers(method.upper(), path)

        for attempt in range(3):
            self._rate_limit()
            try:
                if method.upper() == "GET":
                    resp = requests.get(url, headers=headers, params=params, timeout=30)
                elif method.upper() == "POST":
                    resp = requests.post(url, headers=headers, params=params, json=body, timeout=30)
                elif method.upper() == "PUT":
                    resp = requests.put(url, headers=headers, params=params, json=body, timeout=30)
                elif method.upper() == "DELETE":
                    resp = requests.delete(url, headers=headers, params=params, timeout=30)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                logger.info("[API] %s %s → %d", method.upper(), path, resp.status_code)

                # 2xx 모두 성공으로 처리 (200, 201, 204 No Content 등)
                if 200 <= resp.status_code < 300:
                    return resp.json() if resp.text else {}
                if resp.status_code == 429:
                    wait = 2 ** attempt
                    logger.warning("[API] Rate limited. %.1fs 대기 후 재시도 (%d/3)", wait, attempt + 1)
                    time.sleep(wait)
                    continue
                logger.error("[API] 오류 %d: %s", resp.status_code, resp.text[:300])
                resp.raise_for_status()

            except requests.RequestException as e:
                wait = 2 ** attempt
                logger.warning("[API] 요청 실패 (%d/3): %s → %.1fs 후 재시도", attempt + 1, e, wait)
                if attempt < 2:
                    time.sleep(wait)
                else:
                    raise

        raise RuntimeError(f"API 호출 3회 모두 실패: {method} {path}")

    # ──────────────────────────────────────────────
    # 키워드 조회
    # ──────────────────────────────────────────────

    def get_keywords_by_group(self, adgroup_id: str) -> list[dict]:
        """광고그룹의 모든 키워드 반환."""
        path = "/ncc/keywords"
        params = {"nccAdgroupId": adgroup_id}
        result = self._request("GET", path, params=params)
        keywords = result if isinstance(result, list) else result.get("items", [])
        logger.info("[API] 그룹 %s 키워드 %d개 조회", adgroup_id, len(keywords))
        return keywords

    # ──────────────────────────────────────────────
    # 성과 통계
    # ──────────────────────────────────────────────

    def get_stats(self, keyword_ids: list[str], days: int = 14) -> dict[str, dict]:
        """
        키워드 ID 리스트의 최근 N일 기간 합산 통계 반환.

        Naver /stats 엔드포인트는 ids=복수 호출 시 timeUnit=day 가 무시되고
        기간 합산 1행/키워드로 응답함. 응답 shape:
            {"data": [{"id": "...", "clkCnt": int, "impCnt": int, "salesAmt": int}, ...]}

        7일/14일/28일 분리가 필요한 경우 두 번 호출.

        반환값:
            {kw_id: {
                "impressions": int,        # 기간(days) 합산
                "clicks": int,
                "cost": int,
                "impressions_14d": int,    # legacy alias = 기간 합산 (weekly_pruner 호환)
                "clicks_14d": int,
                "cost_14d": int,
                "impressions_7d": int,     # 최근 7일
                "clicks_7d": int,
            }}
        """
        if not keyword_ids:
            return {}

        until = datetime.today().date() - timedelta(days=1)
        since_main = until - timedelta(days=days - 1)
        since_7d = until - timedelta(days=6)

        def _fetch_period(s_date, u_date) -> dict[str, dict]:
            out: dict[str, dict] = {}
            for i in range(0, len(keyword_ids), BATCH_SIZE):
                batch = keyword_ids[i : i + BATCH_SIZE]
                params = {
                    "ids": ",".join(batch),
                    "fields": '["clkCnt","impCnt","salesAmt"]',
                    "timeRange": json.dumps({"since": str(s_date), "until": str(u_date)}),
                }
                res = self._request("GET", "/stats", params=params)
                data = res.get("data", []) if isinstance(res, dict) else []
                for row in data:
                    kid = row.get("id")
                    if not kid:
                        continue
                    out[kid] = {
                        "impressions": int(row.get("impCnt", 0) or 0),
                        "clicks": int(row.get("clkCnt", 0) or 0),
                        "cost": int(row.get("salesAmt", 0) or 0),
                    }
            return out

        main = _fetch_period(since_main, until)
        seven = main if days == 7 else _fetch_period(since_7d, until)

        stats: dict[str, dict] = {}
        for kid in set(main) | set(seven):
            m = main.get(kid, {})
            s7 = seven.get(kid, {})
            stats[kid] = {
                "impressions": m.get("impressions", 0),
                "clicks": m.get("clicks", 0),
                "cost": m.get("cost", 0),
                "impressions_14d": m.get("impressions", 0),
                "clicks_14d": m.get("clicks", 0),
                "cost_14d": m.get("cost", 0),
                "impressions_7d": s7.get("impressions", 0),
                "clicks_7d": s7.get("clicks", 0),
            }
        return stats

    # ──────────────────────────────────────────────
    # 키워드 등록
    # ──────────────────────────────────────────────

    def register_keywords(self, adgroup_id: str, campaign_id: str, keywords: list[str], bid: int = 70) -> list[dict]:
        """
        키워드 배치 등록 (최대 100개씩).
        성공한 키워드 객체 리스트 반환.
        """
        registered = []
        for i in range(0, len(keywords), BATCH_SIZE):
            batch = keywords[i : i + BATCH_SIZE]
            body = [
                {
                    "nccAdgroupId": adgroup_id,
                    "nccCampaignId": campaign_id,
                    "keyword": kw,
                    "bidAmt": bid,
                    "useGroupBidAmt": False,
                    "userLock": False,
                }
                for kw in batch
            ]
            # nccAdgroupId를 URL 쿼리 파라미터로도 전달 (Naver API Spring @RequestParam 요건)
            result = self._request("POST", "/ncc/keywords",
                                   params={"nccAdgroupId": adgroup_id}, body=body)
            items = result if isinstance(result, list) else result.get("items", [])
            registered.extend(items)
            logger.info("[API] 키워드 %d개 등록 완료 (배치 %d)", len(items), i // BATCH_SIZE + 1)
        return registered

    # ──────────────────────────────────────────────
    # 입찰가 수정
    # ──────────────────────────────────────────────

    def update_bid(self, keyword_id: str, bid: int) -> dict:
        path = f"/ncc/keywords/{keyword_id}"
        body = {"bidAmt": bid, "useGroupBidAmt": False}
        result = self._request("PUT", path, body=body)
        logger.info("[API] 키워드 %s 입찰가 %d원으로 수정", keyword_id, bid)
        return result

    # ──────────────────────────────────────────────
    # 키워드 삭제
    # ──────────────────────────────────────────────

    def delete_keyword(self, keyword_id: str) -> bool:
        path = f"/ncc/keywords/{keyword_id}"
        try:
            self._request("DELETE", path)
            logger.info("[API] 키워드 %s 삭제 완료", keyword_id)
            return True
        except Exception as e:
            logger.error("[API] 키워드 %s 삭제 실패: %s", keyword_id, e)
            return False

    def lock_keyword(self, keyword_id: str, lock: bool = True) -> dict:
        """키워드 OFF/ON. Naver PUT은 fields 쿼리 파라미터로 수정 대상 필드 명시 필수."""
        path = f"/ncc/keywords/{keyword_id}"
        body = {"nccKeywordId": keyword_id, "userLock": lock}
        result = self._request("PUT", path, params={"fields": "userLock"}, body=body)
        logger.info("[API] 키워드 %s %s 처리", keyword_id, "OFF" if lock else "ON")
        return result

    # ──────────────────────────────────────────────
    # 캠페인 생성
    # ──────────────────────────────────────────────

    def create_campaign(self, name: str) -> dict:
        path = "/ncc/campaigns"
        body = {
            "name": name,
            "campaignTp": "WEB_SITE",
            "useDailyBudget": False,
            "dailyBudget": 0,
            "deliveryMethod": "ACCELERATED",
        }
        result = self._request("POST", path, body=body)
        logger.info("[API] 캠페인 '%s' 생성: %s", name, result.get("nccCampaignId", ""))
        return result

    # ──────────────────────────────────────────────
    # 광고그룹 생성
    # ──────────────────────────────────────────────

    def create_ad_group(self, campaign_id: str, group_name: str, biz_channel_id: str) -> dict:
        path = "/ncc/adgroups"
        body = {
            "nccCampaignId": campaign_id,
            "name": group_name,
            "adgroupType": "WEB_SITE",
            "pcChannelId": biz_channel_id,
            "mobileChannelId": biz_channel_id,
            "bidAmt": 70,
            "useDailyBudget": False,
            "mobileNetworkBidWeight": 100,
            "pcNetworkBidWeight": 100,
        }
        result = self._request("POST", path, body=body)
        logger.info("[API] 광고그룹 '%s' 생성: %s", group_name, result.get("nccAdgroupId", ""))
        return result

    # ──────────────────────────────────────────────
    # 광고 소재 생성
    # ──────────────────────────────────────────────

    def create_ads(self, adgroup_id: str, campaign_id: str, ads: list[dict]) -> list[dict]:
        """
        광고 소재 등록. /ncc/ads는 단건 POST — ads 순회하여 1개씩 등록.
        ads: [{"headline": str, "description": str, "url": str}, ...]
        """
        created = []
        for ad in ads:
            body = {
                "nccAdgroupId": adgroup_id,
                "nccCampaignId": campaign_id,
                "type": "TEXT_45",
                "ad": {
                    "headline": ad["headline"],
                    "description": ad["description"],
                    "mobile": {"final": ad["url"], "display": ad["url"]},
                    "pc":     {"final": ad["url"], "display": ad["url"]},
                },
            }
            result = self._request("POST", "/ncc/ads",
                                   params={"nccAdgroupId": adgroup_id}, body=body)
            if result:
                created.append(result)
        logger.info("[API] 광고 소재 %d개 등록: 그룹 %s", len(created), adgroup_id)
        return created

    def get_ads_by_group(self, adgroup_id: str) -> list[dict]:
        """광고그룹의 소재 목록 조회."""
        result = self._request("GET", "/ncc/ads", params={"nccAdgroupId": adgroup_id})
        items = result if isinstance(result, list) else result.get("items", [])
        return items

    # ──────────────────────────────────────────────
    # 연관 키워드 도구
    # ──────────────────────────────────────────────

    def get_related_keywords(self, seed: str) -> list[str]:
        """keywordstool로 연관 키워드 조회."""
        path = "/keywordstool"
        params = {
            "hintKeywords": quote(seed, safe=""),
            "showDetail": "1",
        }
        result = self._request("GET", path, params=params)
        keywords = []
        for item in result.get("keywordList", []):
            kw = item.get("relKeyword", "")
            if kw:
                keywords.append(kw)
        logger.info("[API] 연관키워드 '%s' → %d개", seed, len(keywords))
        return keywords
