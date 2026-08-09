# -*- coding: utf-8 -*-
"""
sajik_place_bid.py — 플레이스 '기본(테스트)' 그룹 입찰가 변경.
  python scripts/sajik_place_bid.py 450
"""
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from dotenv import load_dotenv
from lib.naver_api import NaverAdAPI

GID = "grp-a001-06-000000068828400"  # 사직점 플레이스 1M_기본(테스트)
KST = timezone(timedelta(hours=9))


def main():
    load_dotenv(ROOT / ".env")
    bid = int(sys.argv[1]) if len(sys.argv) > 1 else 450
    api = NaverAdAPI(os.getenv("SAJIK_API_KEY"),
                     os.getenv("SAJIK_SECRET_KEY"),
                     os.getenv("SAJIK_CUSTOMER_ID"))
    g = api._request("GET", f"/ncc/adgroups/{GID}")
    old = g.get("bidAmt")
    b = dict(g); b["bidAmt"] = bid
    r = api._request("PUT", f"/ncc/adgroups/{GID}", params={"fields": "bidAmt"}, body=b)
    now = datetime.now(KST).strftime("%m/%d %H:%M")
    msg = f"[{now}] 플레이스 입찰 {old} -> {r.get('bidAmt')}원"
    print(msg)
    try:
        from kakao_send import send_kakao
        send_kakao(f"[버거리 사직] {msg} (저녁 노출용)")
    except Exception as e:
        print("kakao skip:", e)


if __name__ == "__main__":
    main()
