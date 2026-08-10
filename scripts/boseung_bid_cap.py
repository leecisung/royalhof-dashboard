# -*- coding: utf-8 -*-
"""
boseung_bid_cap.py — 보승회관 파워컨텐츠 입찰가 일괄 인하 (캡 적용)

파워컨텐츠 키워드 입찰 수정은 공식 SearchAd API가 3705로 거부 →
ads.naver.com 웹 API(NID 쿠키, NAS 입찰기에 저장된 것 재사용)로 처리.
인하만 한다 (cap보다 낮은 입찰은 건드리지 않음).

  python scripts/boseung_bid_cap.py                # dry-run (기본)
  python scripts/boseung_bid_cap.py --apply        # 실제 적용
  python scripts/boseung_bid_cap.py --cap 500 --apply

.env: BURGEORI_OLD_* (조회), BIDDER_URL, BIDDER_PASSWORD (쿠키 출처=NAS 입찰기)
"""
import os
import sys
import json
import time
import argparse
import logging
from pathlib import Path

import requests

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from dotenv import load_dotenv
from lib.naver_api import NaverAdAPI

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(ROOT / "logs" / "boseung_bid_cap.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("bid_cap")

GROUPS = ["grp-a001-03-000000070458830", "grp-a001-03-000000070990160"]
CUSTOMER = "694291"


def get_cookies() -> tuple:
    """NAS 입찰기에 저장된 네이버 로그인 쿠키 재사용."""
    base = os.getenv("BIDDER_URL", "").rstrip("/")
    pw = os.getenv("BIDDER_PASSWORD", "")
    s = requests.Session()
    r = s.post(f"{base}/api/login", json={"password": pw}, timeout=10)
    r.raise_for_status()
    st = s.get(f"{base}/api/status", timeout=15).json().get("settings", {})
    aut, ses = st.get("nid_aut"), st.get("nid_ses")
    if not (aut and ses):
        raise RuntimeError("입찰기에 쿠키 미설정")
    return aut, ses


def web_session(aut: str, ses: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "accept": "application/json, text/plain, */*",
        "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"),
        "x-ad-customer-id": CUSTOMER, "x-accept-language": "ko",
        "content-type": "application/json", "origin": "https://ads.naver.com",
        "referer": f"https://ads.naver.com/manage/ad-accounts/{CUSTOMER}/sa/adgroups"})
    s.cookies.set("NID_AUT", aut, domain=".naver.com")
    s.cookies.set("NID_SES", ses, domain=".naver.com")
    return s


def main():
    ap = argparse.ArgumentParser(description="보승회관 파워컨텐츠 입찰 일괄 인하")
    ap.add_argument("--cap", type=int, default=800, help="입찰 상한 (기본 800원)")
    ap.add_argument("--apply", action="store_true", help="실제 적용 (기본은 dry-run)")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")

    api = NaverAdAPI(os.getenv("BURGEORI_OLD_API_KEY"),
                     os.getenv("BURGEORI_OLD_SECRET_KEY"), CUSTOMER)
    targets = []
    for gid in GROUPS:
        for k in api.get_keywords_by_group(gid):
            bid = int(k.get("bidAmt") or 0)
            if bid > args.cap:
                targets.append({"nccKeywordId": k["nccKeywordId"], "nccAdgroupId": gid,
                                "nccCampaignId": k.get("nccCampaignId"),
                                "keyword": k.get("keyword"), "old": bid})
    logger.info("=== 캡 %d원 초과 %d개 (%s) ===", args.cap, len(targets),
                "APPLY" if args.apply else "DRY-RUN")
    for t in targets:
        logger.info("  %s %s → %d", t["keyword"], f"{t['old']:,}", args.cap)
    if not args.apply or not targets:
        return

    aut, ses = get_cookies()
    s = web_session(aut, ses)
    url = "https://ads.naver.com/apis/sa/api/ncc/keywords?fields=bidAmt"
    ok = 0
    for i in range(0, len(targets), 20):
        batch = targets[i:i + 20]
        body = [{"nccKeywordId": t["nccKeywordId"], "nccAdgroupId": t["nccAdgroupId"],
                 "nccCampaignId": t["nccCampaignId"], "bidAmt": args.cap,
                 "useGroupBidAmt": False} for t in batch]
        r = s.put(url, json=body, timeout=25)
        if r.status_code == 200:
            ok += len(batch)
            logger.info("배치 %d~%d 적용 완료", i + 1, i + len(batch))
        else:
            logger.error("배치 실패 %d: %s", r.status_code, r.text[:200])
        time.sleep(1.0)
    logger.info("=== 완료: %d/%d 인하 ===", ok, len(targets))
    try:
        from kakao_send import send_kakao
        send_kakao(f"[보승회관] 파워컨텐츠 입찰 일괄인하: {ok}개 → {args.cap}원 캡")
    except Exception as e:
        logger.info("kakao skip: %s", e)


if __name__ == "__main__":
    main()
