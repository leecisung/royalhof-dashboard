# -*- coding: utf-8 -*-
"""
place_rank.py — 네이버 플레이스(지도) 검색 순위 조회

m.place.naver.com 검색결과(SSR)에서 우리 매장이 몇 번째인지 확인.
부산 사직 좌표를 넣어 실제 근처 검색자와 같은 결과 기준. 상위 ~18개까지 조회됨.

  python scripts/place_rank.py            # 조회 + 이력 저장
  python scripts/place_rank.py --kakao    # 결과 카카오 전송

설정: data/place_rank_keywords.json
이력: data/place_rank_history.csv (utf-8-sig, 날짜별 누적 → 추이 확인용)
"""
import sys
import re
import csv
import json
import argparse
import logging
import urllib.parse
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(ROOT / "logs" / "place_rank.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("place_rank")

CFG = ROOT / "data" / "place_rank_keywords.json"
HISTORY = ROOT / "data" / "place_rank_history.csv"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 Mobile Safari/604.1")


def fetch_list(query: str, x: str, y: str) -> list:
    """지도 검색결과 상위 매장 [(id, name), ...] 반환."""
    url = (f"https://m.place.naver.com/restaurant/list"
           f"?query={urllib.parse.quote(query)}&x={x}&y={y}")
    r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    r.encoding = "utf-8"
    m = re.search(r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\});", r.text, re.S)
    if not m:
        logger.warning("'%s' 결과 파싱 실패 (차단 또는 형식 변경)", query)
        return []
    d = json.loads(m.group(1))
    return [(str(v.get("id")), v.get("name", ""))
            for k, v in d.items() if k.startswith("PlaceListBusinessesItem:")]


def main():
    ap = argparse.ArgumentParser(description="네이버 플레이스 검색 순위 조회")
    ap.add_argument("--kakao", action="store_true", help="결과 카카오 전송")
    args = ap.parse_args()

    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    pid = str(cfg.get("place_id"))
    locations = cfg.get("locations") or [
        {"name": "기본", "x": cfg.get("x"), "y": cfg.get("y")}]
    today = date.today().isoformat()

    rows = []
    lines = [f"[플레이스 순위] {cfg.get('place_name')} ({today})"]
    for kw in cfg.get("keywords", []):
        parts = []
        for loc in locations:
            items = fetch_list(kw, loc.get("x"), loc.get("y"))
            rank = next((i for i, (id_, _) in enumerate(items, 1) if id_ == pid), None)
            shown = f"{rank}위" if rank else (f"{len(items)}위밖" if items else "조회실패")
            top1 = items[0][1] if items else "-"
            parts.append(f"{loc.get('name')} {shown}")
            rows.append([today, kw, loc.get("name"), rank if rank else "", len(items), top1])
        logger.info("'%s' → %s", kw, " / ".join(parts))
        lines.append(f"· {kw}: {' / '.join(parts)}")

    new_file = not HISTORY.exists()
    with open(HISTORY, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "keyword", "location", "rank", "list_size", "top1"])
        w.writerows(rows)
    logger.info("이력 저장: %s", HISTORY.name)

    if args.kakao:
        try:
            from kakao_send import send_kakao
            send_kakao("\n".join(lines))
        except Exception as e:
            logger.info("kakao skip: %s", e)


if __name__ == "__main__":
    main()
