# -*- coding: utf-8 -*-
"""광고대행 구조 검증 — 로얄 키로 다른 customer 접근 가능한지 확인."""
import os, sys, time, hashlib, hmac, base64, requests
from pathlib import Path
ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
BASE = "https://api.searchad.naver.com"

ROYAL_KEY = os.getenv("NAVER_AD_API_KEY")
ROYAL_SEC = os.getenv("NAVER_AD_SECRET_KEY")


def sign(secret, ts, method, path):
    raw = hmac.new(secret.encode(), f"{ts}.{method}.{path}".encode(), hashlib.sha256).digest()
    return base64.b64encode(raw).decode()


def call(api_key, secret, customer_id, path="/ncc/campaigns"):
    ts = str(int(time.time() * 1000))
    h = {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Timestamp": ts,
        "X-API-KEY": api_key,
        "X-Customer": str(customer_id),
        "X-Signature": sign(secret, ts, "GET", path),
    }
    r = requests.get(BASE + path, headers=h, timeout=10)
    return r.status_code, r.text[:250].replace("\n", " ")


tests = [
    ("로얄키 + Customer=4328346 (master)", ROYAL_KEY, ROYAL_SEC, "4328346"),
    ("로얄키 + Customer=2436096 (버거리 신)", ROYAL_KEY, ROYAL_SEC, "2436096"),
    ("로얄키 + Customer=1861348 (버거리 구)", ROYAL_KEY, ROYAL_SEC, "1861348"),
]

for label, k, s, cid in tests:
    code, body = call(k, s, cid)
    print(f"[{label}]\n  → {code} {body[:200]}")
    print()
    time.sleep(0.3)

# 광고대행 계정 조회 시도
print("=== /ncc/managed-customer-link 조회 (광고대행 관계 확인) ===")
for path in ["/ncc/managed-customer-link", "/ncc/customers"]:
    code, body = call(ROYAL_KEY, ROYAL_SEC, "4328346", path=path)
    print(f"  {path} → {code} {body[:300]}")
