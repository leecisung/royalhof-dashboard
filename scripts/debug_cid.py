# -*- coding: utf-8 -*-
"""
debug_cid.py — 특정 customer-id가 .env의 어느 키로 인증되는지 확인.

사용: python scripts/debug_cid.py 694291
비밀키는 .env에서만 읽음 (하드코딩 금지).
"""
import os, sys, time, hashlib, hmac, base64
from pathlib import Path

import requests
from dotenv import load_dotenv

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).parents[1]
load_dotenv(ROOT / ".env")
BASE = "https://api.searchad.naver.com"
PATH = "/ncc/campaigns"


def sign(secret, ts, method, path):
    raw = hmac.new(secret.encode(), f"{ts}.{method}.{path}".encode(), hashlib.sha256).digest()
    return base64.b64encode(raw).decode()


def test(label, key, sec, cid):
    if not key or not sec:
        print(f"[{label}] 키 없음 — 스킵")
        return
    ts = str(int(time.time() * 1000))
    headers = {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Timestamp": ts,
        "X-API-KEY": key,
        "X-Customer": str(cid),
        "X-Signature": sign(sec, ts, "GET", PATH),
    }
    r = requests.get(BASE + PATH, headers=headers, timeout=10)
    snippet = r.text[:160].replace("\n", " ")
    mark = "✅" if r.status_code == 200 else "❌"
    print(f"[{label}] {mark} {r.status_code}  {snippet}")
    time.sleep(0.3)


def main():
    if len(sys.argv) < 2:
        print("사용: python scripts/debug_cid.py <customer_id>")
        sys.exit(1)
    cid = sys.argv[1]
    print(f"=== Customer {cid} 인증 테스트 (.env 키 전체) ===")
    test("로얄호프치킨 키", os.getenv("NAVER_AD_API_KEY"),
         os.getenv("NAVER_AD_SECRET_KEY"), cid)
    test("버거리 키", os.getenv("BURGEORI_NEW_API_KEY"),
         os.getenv("BURGEORI_NEW_SECRET_KEY"), cid)
    test("보승회관 키", os.getenv("BURGEORI_OLD_API_KEY"),
         os.getenv("BURGEORI_OLD_SECRET_KEY"), cid)


if __name__ == "__main__":
    main()
