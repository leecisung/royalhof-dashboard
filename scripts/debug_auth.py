# -*- coding: utf-8 -*-
"""버거리 API 키 ↔ customer_id 매핑 검증."""
import os, sys
from pathlib import Path
ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from dotenv import load_dotenv
from lib.naver_api import NaverAdAPI

load_dotenv(ROOT / ".env")

NEW_KEY = os.getenv("BURGEORI_NEW_API_KEY")
NEW_SEC = os.getenv("BURGEORI_NEW_SECRET_KEY")
OLD_KEY = os.getenv("BURGEORI_OLD_API_KEY")
OLD_SEC = os.getenv("BURGEORI_OLD_SECRET_KEY")

NEW_CID = "2436096"
OLD_CID = "1861348"

combos = [
    ("신키 + 신CID(2436096)", NEW_KEY, NEW_SEC, NEW_CID),
    ("신키 + 구CID(1861348)", NEW_KEY, NEW_SEC, OLD_CID),
    ("구키 + 구CID(1861348)", OLD_KEY, OLD_SEC, OLD_CID),
    ("구키 + 신CID(2436096)", OLD_KEY, OLD_SEC, NEW_CID),
]

for label, k, s, c in combos:
    api = NaverAdAPI(k, s, c)
    # 재시도 없이 1회만 호출하도록 직접 요청
    import time, requests
    from lib.naver_api import BASE_URL
    headers = api._headers("GET", "/ncc/campaigns")
    resp = requests.get(BASE_URL + "/ncc/campaigns", headers=headers, timeout=10)
    print(f"[{label}] → {resp.status_code} {resp.text[:120]}")
    time.sleep(0.3)
