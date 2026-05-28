# -*- coding: utf-8 -*-
"""
버거리 리드 1건 출처 추적 — 특정 날짜의 Meta + GA4 데이터 교차검증.

사용법:
    python scripts/trace_burgerry_lead.py 2026-05-26

출력:
    1. GA4: 그 날 채널/캠페인별 세션 + 전환 이벤트
    2. GA4: 그 날 랜딩 페이지 top
    3. Meta: 그 날 ad-level Lead 이벤트 + 클릭/노출
    4. 종합 진단 (가장 가능성 높은 출처)
"""

import os
import sys
import logging
from datetime import date, datetime
from pathlib import Path

# 인코딩
try:
    sys.stdout.reconfigure(encoding="utf-8-sig")
except Exception:
    pass

# .env
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))
from lib.ga4_api import GA4API, GA4NotConfigured
from lib.meta_api import MetaAdsAPI


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def trace_ga4(target_day: date):
    print(f"\n{'='*70}")
    print(f"GA4 — {target_day}")
    print('='*70)
    try:
        api = GA4API.from_env()
    except GA4NotConfigured as e:
        print(f"GA4 미설정: {e}")
        return

    # 1. 그 날 전체 세션
    daily = api.fetch_daily_summary(target_day, target_day)
    if daily:
        d = daily[0]
        print(f"\n[전체] 세션 {d['sessions']:,} · 사용자 {d['users']:,} · PV {d['pageviews']:,} · 이탈 {d['bounce_rate']:.1%}")
    else:
        print("\n[전체] 데이터 없음 (그 날 트래픽 0 또는 GA4 미반영)")

    # 2. 채널/매체별
    sources = api.fetch_by_source(target_day, target_day)
    print(f"\n[채널·매체별]  ※ 전환 = GA4 이벤트 전환 표시된 것")
    print(f"  {'source':<25} {'medium':<20} {'sessions':>8} {'conv':>6}")
    for r in sources[:20]:
        print(f"  {r['source']:<25.25} {r['medium']:<20.20} {r['sessions']:>8} {r['conversions']:>6.0f}")

    # 3. 캠페인별 (UTM_campaign)
    campaigns = api.fetch_by_campaign(target_day, target_day)
    print(f"\n[UTM 캠페인별]")
    print(f"  {'source':<20} {'medium':<15} {'campaign':<35} {'sess':>5} {'conv':>5}")
    for r in campaigns[:30]:
        c = r['campaign'][:35]
        print(f"  {r['source']:<20.20} {r['medium']:<15.15} {c:<35.35} {r['sessions']:>5} {r['conversions']:>5.0f}")

    # 4. 이벤트별 (Lead 관련 찾기)
    events = api.fetch_conversions_by_event(target_day, target_day)
    print(f"\n[이벤트별] (Lead·form·submit·CompleteRegistration 등)")
    lead_keywords = ("lead", "submit", "form", "contact", "registration", "click", "generate")
    matched = [e for e in events if any(k in e["event_name"].lower() for k in lead_keywords)]
    if not matched:
        matched = events[:15]
    for e in matched[:15]:
        print(f"  {e['event_name']:<40.40} count={e['count']:>5}  users={e['users']:>5}")

    # 4-b. ★ CompleteRegistration / form_submit 이 어느 source/medium/campaign 에서 발생했나
    print(f"\n[★ 전환 이벤트 출처 매칭] CompleteRegistration / form_submit 별로 UTM 추적")
    for event_name in ("CompleteRegistration", "form_submit"):
        try:
            from google.analytics.data_v1beta.types import (
                DateRange, Dimension, Metric, RunReportRequest, Filter, FilterExpression,
            )
            req = RunReportRequest(
                property=f"properties/{api.property_id}",
                dimensions=[
                    Dimension(name="sessionSource"),
                    Dimension(name="sessionMedium"),
                    Dimension(name="sessionCampaignName"),
                    Dimension(name="landingPagePlusQueryString"),
                ],
                metrics=[Metric(name="eventCount"), Metric(name="totalUsers")],
                date_ranges=[DateRange(start_date=str(target_day), end_date=str(target_day))],
                dimension_filter=FilterExpression(
                    filter=Filter(field_name="eventName", string_filter=Filter.StringFilter(value=event_name))
                ),
                limit=50,
            )
            resp = api.client.run_report(req)
            print(f"\n  ▶ '{event_name}' (총 {sum(int(r.metric_values[0].value or 0) for r in resp.rows)}건)")
            print(f"    {'source':<22} {'medium':<14} {'campaign':<32} {'cnt':>4} {'usr':>3}  landing")
            for row in resp.rows:
                dv = [d.value for d in row.dimension_values]
                mv = [m.value for m in row.metric_values]
                print(f"    {dv[0]:<22.22} {dv[1]:<14.14} {dv[2]:<32.32} {int(mv[0] or 0):>4} {int(mv[1] or 0):>3}  {dv[3][:60]}")
        except Exception as e:
            print(f"    {event_name} 필터 조회 실패: {e}")

    # 5. 랜딩 페이지
    pages = api.fetch_landing_pages(target_day, target_day, limit=15)
    print(f"\n[랜딩 페이지]")
    for p in pages[:15]:
        print(f"  sess={p['sessions']:>5}  bounce={p['bounce_rate']:.0%}  {p['page'][:60]}")


def trace_meta(target_day: date):
    print(f"\n{'='*70}")
    print(f"Meta — {target_day} (Lead·CompleteRegistration·LandingPageView)")
    print('='*70)
    api = MetaAdsAPI.from_env()

    # 5/26 ad set 단위로 가져와서, 각 ad set의 그 날 클릭/노출/Lead 확인
    # ad set ID는 meta_funnel.json의 funnel 2개 + meta_ad_sets.json의 LLA1/3
    import json
    funnel = json.load(open("data/meta_funnel.json", encoding="utf-8-sig"))
    targets = [
        (funnel["traffic"]["adset_id"], funnel["traffic"]["adset_name"]),
        (funnel["leads"]["adset_id"], funnel["leads"]["adset_name"]),
    ]
    try:
        ad_sets = json.load(open("data/meta_ad_sets.json", encoding="utf-8-sig"))
        for slot in ad_sets.get("managed", {}).values():
            if isinstance(slot, dict) and slot.get("adset_id"):
                aid = slot["adset_id"]
                aname = slot.get("alias") or slot.get("name") or aid
                if aid not in [t[0] for t in targets]:
                    targets.append((aid, aname))
    except Exception:
        pass

    # ad set의 5/26 단일 일자 인사이트
    from facebook_business.adobjects.adset import AdSet
    from facebook_business.adobjects.adsinsights import AdsInsights

    for adset_id, name in targets:
        params = {
            "time_range": {"since": str(target_day), "until": str(target_day)},
            "level": "adset",
        }
        fields = [
            AdsInsights.Field.impressions,
            AdsInsights.Field.clicks,
            AdsInsights.Field.spend,
            AdsInsights.Field.ctr,
            AdsInsights.Field.actions,
        ]
        try:
            cursor = api._call(AdSet(adset_id).get_insights, fields=fields, params=params)
            rows = [dict(r) for r in cursor]
        except Exception as e:
            print(f"\n  {name}({adset_id}) — 조회 실패: {e}")
            continue

        if not rows:
            print(f"\n  {name} ({adset_id}) — 그 날 데이터 0 (정지 or 무노출)")
            continue
        r = rows[0]
        actions = r.get("actions", []) or []
        # 모든 action_type 보여주기 (Lead 가능 후보 전부)
        relevant = []
        for a in actions:
            atype = (a.get("action_type") or "").lower()
            if any(k in atype for k in ("lead", "complete_registration", "submit", "landing_page_view", "view_content")):
                relevant.append((atype, a.get("value", "0")))

        print(f"\n  [{name}] ({adset_id})")
        print(f"    impressions={int(r.get('impressions',0) or 0):,}  clicks={int(r.get('clicks',0) or 0):,}  "
              f"spend={float(r.get('spend',0) or 0):,.0f}원  ctr={float(r.get('ctr',0) or 0):.2f}%")
        if relevant:
            print(f"    actions:")
            for atype, val in relevant:
                print(f"      {atype:<45} = {val}")
        else:
            print(f"    Lead/등록/페이지뷰 action 없음")


def main():
    if len(sys.argv) < 2:
        print("사용법: python scripts/trace_burgerry_lead.py YYYY-MM-DD")
        sys.exit(1)
    target_day = parse_date(sys.argv[1])

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

    trace_ga4(target_day)
    trace_meta(target_day)

    print(f"\n{'='*70}")
    print("진단 가이드")
    print('='*70)
    print(f"""
1) Meta ad set의 'lead'/'complete_registration' action 값 합이 1 이면
   → 그 ad set의 광고에서 유입 (URL의 UTM과 함께 ad_id 매칭)

2) Meta 전환 0 인데 GA4 paid_social 세션이 있으면
   → iOS 도메인 미인증으로 Meta는 못 잡았지만 광고에서 유입한 것 (가능성 大)

3) GA4의 source/medium = naver / search 면
   → 네이버 검색광고 (UTM 박힌 키워드 그룹)

4) GA4의 source/medium = (direct) / (none) 또는 organic
   → 광고와 무관 (자연 유입, 지인 추천, 브랜드 검색 등)

5) GA4도 Meta도 잠잠한데 폼만 들어왔으면
   → 광고와 무관한 채널 (전화, 카톡, 오프라인 등)
""")


if __name__ == "__main__":
    main()
