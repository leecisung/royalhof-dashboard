# -*- coding: utf-8 -*-
"""
Meta(Facebook) Marketing API 클라이언트
- facebook-business SDK 래핑 (System User access token 사용)
- Rate limit: tier 1 ~ 600 calls/hour. 200ms 간격으로 안전 마진
- 실패 시 exponential backoff 3회 재시도
- 자동화 대상 ad set의 campaign_id가 META_PROTECTED_CAMPAIGN_IDS에 있으면 예외

naver_api.py와 같은 패턴을 따름:
  - logger, retry, rate_limit, from_env() 팩토리
  - 모든 write 메서드는 protect 검사 통과해야 함
"""

import os
import time
import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

RATE_LIMIT_INTERVAL = 0.2  # 안전 마진 (5 calls/sec). Meta tier1은 더 관대하지만 보수적.

# Meta는 minor-unit 없는 통화(KRW 등)는 그대로 정수. KRW=40000 → 40,000원.
# 만약 USD라면 4000 = $40.00 (cents)
KRW_CURRENCY_OFFSET = 1


class MetaProtectedError(RuntimeError):
    """보호된 캠페인을 수정/일시정지하려 할 때."""


class MetaAdsAPI:
    """facebook-business SDK 래퍼."""

    def __init__(
        self,
        access_token: str,
        ad_account_id: str,
        app_id: str,
        app_secret: str,
        page_id: str = "",
        pixel_id: str = "",
        pixel_event: str = "Lead",
        protected_campaign_ids: Optional[set] = None,
    ):
        # SDK는 사용 시점에 import (미설치 시 에러를 사용 직전에만 띄움)
        from facebook_business.api import FacebookAdsApi

        self.access_token = access_token
        self.ad_account_id = ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"
        self.app_id = app_id
        self.app_secret = app_secret
        self.page_id = page_id
        self.pixel_id = pixel_id
        self.pixel_event = pixel_event
        self.protected_campaign_ids = set(protected_campaign_ids or [])
        self._last_call_time = 0.0

        FacebookAdsApi.init(self.app_id, self.app_secret, self.access_token, api_version="v19.0")
        logger.info("[META] API 초기화: account=%s, pixel_event=%s, protected=%d개",
                    self.ad_account_id, self.pixel_event, len(self.protected_campaign_ids))

    # ──────────────────────────────────────────────
    # 팩토리
    # ──────────────────────────────────────────────

    @classmethod
    def from_env(cls):
        """환경변수에서 자동 로드. .env가 이미 load_dotenv 처리되어 있어야 함."""
        required = ["META_APP_ID", "META_APP_SECRET", "META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID"]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise RuntimeError(f"필수 환경변수 없음: {missing}. docs/meta_setup_guide.md 참조")

        protected_raw = os.getenv("META_PROTECTED_CAMPAIGN_IDS", "")
        protected = {cid.strip() for cid in protected_raw.split(",") if cid.strip()}

        return cls(
            access_token=os.getenv("META_ACCESS_TOKEN"),
            ad_account_id=os.getenv("META_AD_ACCOUNT_ID"),
            app_id=os.getenv("META_APP_ID"),
            app_secret=os.getenv("META_APP_SECRET"),
            page_id=os.getenv("META_PAGE_ID", ""),
            pixel_id=os.getenv("META_PIXEL_ID", ""),
            pixel_event=os.getenv("META_PIXEL_EVENT", "Lead"),
            protected_campaign_ids=protected,
        )

    # ──────────────────────────────────────────────
    # 공통 (rate limit + retry)
    # ──────────────────────────────────────────────

    def _rate_limit(self):
        elapsed = time.time() - self._last_call_time
        if elapsed < RATE_LIMIT_INTERVAL:
            time.sleep(RATE_LIMIT_INTERVAL - elapsed)
        self._last_call_time = time.time()

    def _call(self, fn, *args, **kwargs):
        """재시도 포함 SDK 호출. 1s, 2s, 4s 백오프."""
        from facebook_business.exceptions import FacebookRequestError

        for attempt in range(3):
            self._rate_limit()
            try:
                result = fn(*args, **kwargs)
                logger.debug("[META] %s ok", fn.__qualname__ if hasattr(fn, "__qualname__") else fn.__name__)
                return result
            except FacebookRequestError as e:
                code = e.api_error_code()
                subcode = e.api_error_subcode()
                msg = e.api_error_message()
                # rate limit / throttle
                if code in (4, 17, 32, 613) or subcode in (1487742,):
                    wait = 2 ** attempt
                    logger.warning("[META] throttled (code=%s) %.1fs 대기 (%d/3)", code, wait, attempt + 1)
                    time.sleep(wait)
                    continue
                # 일시적 서버 오류
                if code in (1, 2):
                    wait = 2 ** attempt
                    logger.warning("[META] 일시오류 (code=%s) %.1fs 대기 (%d/3)", code, wait, attempt + 1)
                    time.sleep(wait)
                    continue
                logger.error("[META] FB API 오류 code=%s sub=%s msg=%s", code, subcode, msg[:300])
                raise
            except Exception as e:
                wait = 2 ** attempt
                logger.warning("[META] 호출 실패 (%d/3): %s → %.1fs 후 재시도", attempt + 1, e, wait)
                if attempt < 2:
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError("[META] API 호출 3회 모두 실패")

    # ──────────────────────────────────────────────
    # 안전장치
    # ──────────────────────────────────────────────

    def _assert_not_protected(self, campaign_id: str):
        if campaign_id and campaign_id in self.protected_campaign_ids:
            raise MetaProtectedError(
                f"보호된 캠페인({campaign_id})에 대한 변경 시도를 차단했습니다. "
                f".env의 META_PROTECTED_CAMPAIGN_IDS 확인."
            )

    # ──────────────────────────────────────────────
    # 발견 (Read)
    # ──────────────────────────────────────────────

    def discover_campaigns(self) -> list[dict]:
        """광고 계정의 모든 캠페인."""
        from facebook_business.adobjects.adaccount import AdAccount
        from facebook_business.adobjects.campaign import Campaign

        account = AdAccount(self.ad_account_id)
        fields = [
            Campaign.Field.id,
            Campaign.Field.name,
            Campaign.Field.objective,
            Campaign.Field.status,
            Campaign.Field.effective_status,
            Campaign.Field.daily_budget,
            Campaign.Field.lifetime_budget,
            Campaign.Field.created_time,
        ]
        cursor = self._call(account.get_campaigns, fields=fields, params={"limit": 200})
        items = [dict(c) for c in cursor]
        logger.info("[META] 캠페인 %d개 발견", len(items))
        return items

    def discover_ad_sets(self, campaign_id: Optional[str] = None) -> list[dict]:
        """캠페인 한 개 또는 전체의 ad set 목록.
        campaign_id=None이면 계정의 모든 ad set 반환.
        """
        from facebook_business.adobjects.adaccount import AdAccount
        from facebook_business.adobjects.campaign import Campaign
        from facebook_business.adobjects.adset import AdSet

        fields = [
            AdSet.Field.id,
            AdSet.Field.name,
            AdSet.Field.campaign_id,
            AdSet.Field.status,
            AdSet.Field.effective_status,
            AdSet.Field.daily_budget,
            AdSet.Field.optimization_goal,
            AdSet.Field.billing_event,
            AdSet.Field.promoted_object,
            AdSet.Field.targeting,
            AdSet.Field.created_time,
            AdSet.Field.start_time,
        ]

        if campaign_id:
            container = Campaign(campaign_id)
            cursor = self._call(container.get_ad_sets, fields=fields, params={"limit": 200})
        else:
            container = AdAccount(self.ad_account_id)
            cursor = self._call(container.get_ad_sets, fields=fields, params={"limit": 200})

        items = []
        for a in cursor:
            d = dict(a)
            # promoted_object / targeting 등 중첩 SDK 객체를 plain dict로 정규화
            for key in ("promoted_object", "targeting"):
                val = d.get(key)
                if val is not None and not isinstance(val, (dict, str, list)):
                    try:
                        d[key] = dict(val)
                    except (TypeError, ValueError):
                        d[key] = {}
            items.append(d)
        logger.info("[META] ad set %d개 발견 (campaign=%s)", len(items), campaign_id or "ALL")
        return items

    def discover_ads(self, ad_set_id: str) -> list[dict]:
        """ad set 안의 광고(크리에이티브 묶음)."""
        from facebook_business.adobjects.adset import AdSet
        from facebook_business.adobjects.ad import Ad

        fields = [
            Ad.Field.id,
            Ad.Field.name,
            Ad.Field.adset_id,
            Ad.Field.campaign_id,
            Ad.Field.creative,
            Ad.Field.status,
            Ad.Field.effective_status,
        ]
        ad_set = AdSet(ad_set_id)
        cursor = self._call(ad_set.get_ads, fields=fields, params={"limit": 100})
        items = [dict(a) for a in cursor]
        logger.info("[META] ad %d개 발견 (ad_set=%s)", len(items), ad_set_id)
        return items

    # ──────────────────────────────────────────────
    # 인사이트
    # ──────────────────────────────────────────────

    def get_account_summary(self, days: int = 7, until_today: bool = False) -> dict:
        """계정 전체 N일 요약. until_today=True면 오늘 날짜까지 포함(실시간 확인용)."""
        from facebook_business.adobjects.adaccount import AdAccount
        from facebook_business.adobjects.adsinsights import AdsInsights

        account = AdAccount(self.ad_account_id)
        until = date.today() if until_today else date.today() - timedelta(days=1)
        since = until - timedelta(days=days - 1)
        params = {
            "time_range": {"since": str(since), "until": str(until)},
            "level": "account",
        }
        fields = [
            AdsInsights.Field.impressions,
            AdsInsights.Field.clicks,
            AdsInsights.Field.spend,
            AdsInsights.Field.ctr,
            AdsInsights.Field.reach,
            AdsInsights.Field.frequency,
        ]
        cursor = self._call(account.get_insights, fields=fields, params=params)
        rows = [dict(r) for r in cursor]
        if not rows:
            return {"impressions": 0, "clicks": 0, "spend": 0.0, "ctr": 0.0, "reach": 0, "frequency": 0.0}
        r = rows[0]
        return {
            "impressions": int(r.get("impressions", 0) or 0),
            "clicks":      int(r.get("clicks", 0) or 0),
            "spend":       float(r.get("spend", 0) or 0),
            "ctr":         float(r.get("ctr", 0) or 0),
            "reach":       int(r.get("reach", 0) or 0),
            "frequency":   float(r.get("frequency", 0) or 0),
        }

    def get_ad_set_insights(self, ad_set_id: str, days: int = 7, until_today: bool = False) -> dict:
        """
        ad set의 N일 인사이트.
        반환: {impressions, clicks, spend, conversions, cpa, ctr, frequency, reach}
        conversions는 META_PIXEL_EVENT 기준.
        until_today=True면 오늘 날짜까지 포함(실시간 확인용).
        """
        from facebook_business.adobjects.adset import AdSet
        from facebook_business.adobjects.adsinsights import AdsInsights

        until = date.today() if until_today else date.today() - timedelta(days=1)
        since = until - timedelta(days=days - 1)
        params = {
            "time_range": {"since": str(since), "until": str(until)},
            "level": "adset",
        }
        fields = [
            AdsInsights.Field.impressions,
            AdsInsights.Field.clicks,
            AdsInsights.Field.spend,
            AdsInsights.Field.ctr,
            AdsInsights.Field.reach,
            AdsInsights.Field.frequency,
            AdsInsights.Field.actions,
        ]
        ad_set = AdSet(ad_set_id)
        cursor = self._call(ad_set.get_insights, fields=fields, params=params)
        rows = [dict(r) for r in cursor]
        if not rows:
            return {
                "impressions": 0, "clicks": 0, "spend": 0.0,
                "conversions": 0, "cpa": 0.0, "ctr": 0.0,
                "frequency": 0.0, "reach": 0,
            }
        r = rows[0]
        spend = float(r.get("spend", 0) or 0)
        conversions = self._extract_conversions(r.get("actions", []))
        cpa = (spend / conversions) if conversions > 0 else 0.0
        return {
            "impressions": int(r.get("impressions", 0) or 0),
            "clicks":      int(r.get("clicks", 0) or 0),
            "spend":       spend,
            "conversions": conversions,
            "cpa":         cpa,
            "ctr":         float(r.get("ctr", 0) or 0),
            "frequency":   float(r.get("frequency", 0) or 0),
            "reach":       int(r.get("reach", 0) or 0),
        }

    def _extract_conversions(self, actions: list) -> int:
        """actions 배열에서 pixel_event에 해당하는 전환 수 추출."""
        if not actions:
            return 0
        target_lower = self.pixel_event.lower()
        candidates = {
            target_lower,
            f"offsite_conversion.fb_pixel_{target_lower}",
            f"onsite_conversion.{target_lower}",
        }
        for action in actions:
            atype = (action.get("action_type") or "").lower()
            if atype in candidates:
                try:
                    return int(float(action.get("value", 0)))
                except (TypeError, ValueError):
                    return 0
        return 0

    # ──────────────────────────────────────────────
    # 변경 (Write — 모두 보호 검사 통과 필요)
    # ──────────────────────────────────────────────

    def _get_ad_set_campaign_id(self, ad_set_id: str) -> str:
        from facebook_business.adobjects.adset import AdSet
        ad_set = AdSet(ad_set_id)
        data = self._call(ad_set.api_get, fields=[AdSet.Field.campaign_id])
        return data.get("campaign_id", "") if data else ""

    def pause_ad_set(self, ad_set_id: str) -> bool:
        from facebook_business.adobjects.adset import AdSet
        cid = self._get_ad_set_campaign_id(ad_set_id)
        self._assert_not_protected(cid)
        ad_set = AdSet(ad_set_id)
        self._call(ad_set.api_update, params={"status": "PAUSED"})
        logger.info("[META] ad_set %s 일시정지", ad_set_id)
        return True

    def unpause_ad_set(self, ad_set_id: str) -> bool:
        from facebook_business.adobjects.adset import AdSet
        cid = self._get_ad_set_campaign_id(ad_set_id)
        self._assert_not_protected(cid)
        ad_set = AdSet(ad_set_id)
        self._call(ad_set.api_update, params={"status": "ACTIVE"})
        logger.info("[META] ad_set %s 활성화", ad_set_id)
        return True

    def update_ad_set_budget(self, ad_set_id: str, new_daily_budget_krw: int) -> bool:
        """일 예산 변경. KRW는 minor-unit이 없어 그대로 정수."""
        from facebook_business.adobjects.adset import AdSet
        cid = self._get_ad_set_campaign_id(ad_set_id)
        self._assert_not_protected(cid)
        budget_value = int(new_daily_budget_krw) * KRW_CURRENCY_OFFSET
        ad_set = AdSet(ad_set_id)
        self._call(ad_set.api_update, params={"daily_budget": budget_value})
        logger.info("[META] ad_set %s 일예산 %d원으로 변경", ad_set_id, new_daily_budget_krw)
        return True

    def get_ad_set_budget(self, ad_set_id: str) -> int:
        """현재 일 예산 원 단위 반환."""
        from facebook_business.adobjects.adset import AdSet
        ad_set = AdSet(ad_set_id)
        data = self._call(ad_set.api_get, fields=[AdSet.Field.daily_budget])
        raw = data.get("daily_budget", 0) if data else 0
        return int(raw) // KRW_CURRENCY_OFFSET if raw else 0
