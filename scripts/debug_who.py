# -*- coding: utf-8 -*-
"""API 키가 어느 customer에 소속됐는지 확인."""
import os, sys, time, hashlib, hmac, base64, requests
from pathlib import Path
ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

BASE = "https://api.searchad.naver.com"


def sign(secret: str, ts: str, method: str, path: str) -> str:
    msg = f"{ts}.{method}.{path}"
    raw = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    return base64.b64encode(raw).decode()


def try_endpoint(label: str, api_key: str, sec: str, cid_options: list[str], path: str = "/ncc/campaigns"):
    print(f"\n=== {label} ===")
    print(f"API_KEY: {api_key[:30]}...")
    for cid in cid_options + [None]:
        ts = str(int(time.time() * 1000))
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "X-Timestamp": ts,
            "X-API-KEY": api_key,
            "X-Signature": sign(sec, ts, "GET", path),
        }
        if cid:
            headers["X-Customer"] = str(cid)
        resp = requests.get(BASE + path, headers=headers, timeout=10)
        snippet = resp.text[:200].replace("\n", " ")
        print(f"  X-Customer={cid or '(none)':<10} → {resp.status_code} {snippet[:150]}")
        time.sleep(0.3)


# 신버 신키 (방금 받은 키)
try_endpoint(
    "신버 신키",
    "010000000018c36ab7308bd4b8b0d0f76e4214aa6b85c1e1039ae52834a5612b00681e46d3",
    "AQAAAACbl1K5VyxbrljReOOcPvsVgXMc/CxWcBgNKufiOket3Q==",
    ["2436096", "1861348", "4328346"],
)

# 구버 구키
try_endpoint(
    "구버 구키",
    os.getenv("BURGEORI_OLD_API_KEY"),
    os.getenv("BURGEORI_OLD_SECRET_KEY"),
    ["1861348", "2436096", "4328346"],
)

# 로얄 키 (대조군 — 잘 작동하는 거)
try_endpoint(
    "로얄 (대조군)",
    os.getenv("NAVER_AD_API_KEY"),
    os.getenv("NAVER_AD_SECRET_KEY"),
    ["4328346"],
)
