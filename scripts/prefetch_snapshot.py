# -*- coding: utf-8 -*-
"""
Vercel 대시보드용 스냅샷 생성 — 매일 1회 로컬에서 실행.

Naver 3계정 + Meta + GA4의 최근 28일 데이터를 fetch 해서
data/snapshot_{kind}.json 으로 저장. Vercel /naver, /meta 페이지가 이걸 읽음.

실행:
    python scripts/prefetch_snapshot.py

이후 git push 하면 Vercel 자동 배포되며 최신 스냅샷 반영.

권장 자동화: Windows 작업 스케줄러로 매일 06:00 실행 + 자동 git commit/push.
"""

import sys
import os
import json
import logging
from pathlib import Path
from datetime import date, timedelta

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import load_dotenv
from lib.dashboard_data import fetch_naver, fetch_meta, fetch_ga4, NAVER_ACCOUNTS, _naver_api

load_dotenv(ROOT / ".env")

SNAPSHOT_DIR = ROOT / "data" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

# 28일 = 4주. /naver의 3주 추이(이번주/지난주/전전주)와 여유 마진.
DAYS = 28


SKIP_CAMPAIGN_KEYWORDS = ("70원전략", "노출용")  # 키워드 단위 fetch에서 제외 (양이 큼)


def fetch_naver_keywords_expensive(since: date, until: date, min_cost: int = 0) -> list[dict]:
    """
    비-70원전략, 비-노출용 캠페인의 키워드 단위 통계.
    반환: [{account, campaign_id, campaign_name, group_id, group_name, keyword_id, keyword, bid, impressions, clicks, cost, cpc}]
    """
    days = (until - since).days + 1
    out = []
    for prefix, label, brand in NAVER_ACCOUNTS:
        api = _naver_api(prefix)
        if not api:
            continue
        # 캠페인 목록
        try:
            camps = api._request("GET", "/ncc/campaigns")
        except Exception as e:
            logging.warning("[KW] %s 캠페인 조회 실패: %s", label, e)
            continue
        if not isinstance(camps, list):
            continue

        for c in camps:
            cname = c.get("name", "")
            cid = c.get("nccCampaignId", "")
            if not cid or any(s in cname for s in SKIP_CAMPAIGN_KEYWORDS):
                continue
            # 캠페인 → 광고그룹들
            try:
                groups = api._request("GET", "/ncc/adgroups", params={"nccCampaignId": cid})
            except Exception as e:
                logging.warning("[KW] %s 그룹 조회 실패: %s", cname, e)
                continue
            if not isinstance(groups, list):
                continue
            for g in groups:
                gid = g.get("nccAdgroupId", "")
                gname = g.get("name", "")
                if not gid:
                    continue
                # 그룹 → 키워드들
                try:
                    keywords = api.get_keywords_by_group(gid)
                except Exception as e:
                    logging.warning("[KW] %s 키워드 조회 실패: %s", gname, e)
                    continue
                if not keywords:
                    continue
                kw_ids = [k.get("nccKeywordId") for k in keywords if k.get("nccKeywordId")]
                if not kw_ids:
                    continue
                # 키워드 → 기간 통계
                try:
                    stats = api.get_stats(kw_ids, days=days)
                except Exception as e:
                    logging.warning("[KW] %s stats 실패: %s", gname, e)
                    continue
                # 키워드 dict로 매핑
                kw_by_id = {k.get("nccKeywordId"): k for k in keywords}
                for kid, s in stats.items():
                    kw = kw_by_id.get(kid, {})
                    cost = s.get("cost_14d", 0)
                    clicks = s.get("clicks_14d", 0)
                    imp = s.get("impressions_14d", 0)
                    if cost < min_cost and clicks == 0:
                        continue  # 노출만 있고 비용 없는 키워드 컷
                    cpc = (cost / clicks) if clicks else 0
                    out.append({
                        "account": label,
                        "brand": brand,
                        "campaign_id": cid,
                        "campaign_name": cname,
                        "group_id": gid,
                        "group_name": gname,
                        "keyword_id": kid,
                        "keyword": kw.get("keyword", ""),
                        "bid": int(kw.get("bidAmt", 0) or 0),
                        "impressions": imp,
                        "clicks": clicks,
                        "cost": cost,
                        "cpc": int(cpc),
                    })
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    until = date.today()
    since = until - timedelta(days=DAYS - 1)
    logging.info("스냅샷 생성 시작: %s ~ %s (%d일)", since, until, DAYS)

    # 1. Naver 3계정 캠페인 일별
    logging.info("Naver fetch 시작 (시간 걸림 — 3계정 × 캠페인 × 일별 stats)")
    naver_rows = fetch_naver(since, until)
    logging.info("Naver: %d개 row", len(naver_rows))

    # 1-2. Naver 키워드 단위 (비-70원/비-노출용 캠페인만)
    logging.info("Naver 키워드 단위 fetch 시작 (입찰기/고비용/일반 캠페인)")
    naver_keywords = fetch_naver_keywords_expensive(since, until)
    logging.info("Naver 키워드: %d개 row", len(naver_keywords))

    # 2. Meta 일별
    logging.info("Meta fetch")
    meta_rows = fetch_meta(since, until)
    logging.info("Meta: %d개 row", len(meta_rows))

    # 3. GA4 (옵션)
    logging.info("GA4 fetch")
    ga4 = fetch_ga4(since, until)
    logging.info("GA4 configured=%s rows=%d", ga4.get("configured"), len(ga4.get("daily", [])))

    snapshot = {
        "generated_at": date.today().isoformat(),
        "since": since.isoformat(),
        "until": until.isoformat(),
        "days": DAYS,
        "naver": naver_rows,
        "naver_keywords": naver_keywords,
        "meta": meta_rows,
        "ga4": ga4,
    }

    out = SNAPSHOT_DIR / "latest.json"
    out.write_text(json.dumps(snapshot, ensure_ascii=False, default=str), encoding="utf-8")

    # 사이즈 출력
    size_kb = out.stat().st_size / 1024
    logging.info("저장 완료: %s (%.1fKB)", out, size_kb)
    print(f"✅ {out} ({size_kb:.1f}KB)")
    print()
    print("다음 단계:")
    print("  git add data/snapshots/latest.json")
    print('  git commit -m "snapshot: 2026-MM-DD"')
    print("  git push")


if __name__ == "__main__":
    main()
