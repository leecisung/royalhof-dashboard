# -*- coding: utf-8 -*-
"""
sajik_tag_plan.py — 사직점 태그 조합안 + 점령 키워드 분석

영상(양승일) 태그 구조: [지역명 묶음] + [공통 키워드] + [메뉴/목적] 을 네이버가
자유 조합 → 완성되는 모든 키워드에 노출. 이 스크립트는:
  1. 지역×공통×메뉴 조합 생성
  2. keywordstool로 월간 검색량 조회
  3. m.place 오가닉 순위 크롤 (상위 볼륨 키워드만)
  4. 분류: 먹고있음(≤10위) / 점령대상(11위~ or 미노출) → 태그 우선순위
저장: data/sajik_tag_plan.json, out/sajik_tag_plan.md

  python scripts/sajik_tag_plan.py            # 전체 (검색량 + 순위, 수 분 소요)
  python scripts/sajik_tag_plan.py --no-rank  # 검색량만
"""
import os
import sys
import re
import json
import time
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
from dotenv import load_dotenv
from lib.naver_api import NaverAdAPI

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(ROOT / "logs" / "sajik_tag_plan.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("tag_plan")

PLACE_ID = "2061765623"
X, Y = "129.0615", "35.1972"   # 사직야구장 인근 (서울 사직동 오염 방지)
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 Mobile Safari/604.1")

# ── 태그 구성요소 (영상 구조: 지역 몰아넣기 + 공통 + 메뉴/목적) ──
REGIONS = ["사직", "사직동", "부산사직", "사직야구장", "종합운동장", "종합운동장역",
           "동래", "동래역", "미남역", "거제동", "아시아드"]
COMMON = ["맛집", "점심", "저녁", "밥집", "혼밥", "데이트", "회식", "포장", "근처맛집"]
MENU = ["햄버거", "수제버거", "버거", "치즈버거", "브런치", "양식"]
EXTRA = ["사직야구장 근처 맛집", "롯데 직관 맛집", "부산 수제버거", "부산 햄버거 맛집",
         "동래구맛집", "사직 가족외식", "사직 분위기좋은맛집"]

# 최종 태그안 (2026-08-09 확정, /place 복붙 섹션에 표시. 대표키워드는 니치 완점 중 → 유지)
RECOMMEND = {
    "note": "대표키워드 5개는 현재 니치 완점(포장1위·햄버거2위) 중이므로 유지. 확장은 광고 태그로 — 태그가 광고 노출의 8할 결정.",
    "keep_keywords": ["사직야구장햄버거포장", "사직야구장먹거리", "사직동햄버거", "사직동수제버거", "사직구장먹거리"],
    "tags_region": ["사직", "사직동", "부산사직", "사직야구장", "사직구장", "종합운동장",
                    "종합운동장역", "동래", "동래역", "미남역", "거제동", "아시아드"],
    "tags_common": ["맛집", "점심", "저녁", "밥집", "혼밥", "브런치", "포장", "근처",
                    "주변", "데이트", "회식", "모임"],
    "tags_menu": ["수제버거", "햄버거", "버거", "치즈버거", "감자튀김", "양식", "먹거리",
                  "직관", "야구"],
}


def norm(s): return s.replace(" ", "").upper()


def to_int(v):
    if isinstance(v, str):
        v = v.replace("<", "").replace(",", "").strip()
        try:
            return int(v)
        except ValueError:
            return 0
    return int(v) if v else 0


def gen_candidates() -> list:
    out = []
    for r in REGIONS:
        for c in COMMON:
            out.append(f"{r} {c}")
        for m in MENU:
            out.append(f"{r} {m}")
    out.extend(EXTRA)
    return list(dict.fromkeys(out))


def fetch_volumes(api, keywords: list) -> dict:
    """keywordstool 5개씩 배치. 반환 {norm_kw: (pc, mo, comp)}"""
    lookup = {}
    for i in range(0, len(keywords), 5):
        chunk = keywords[i:i + 5]
        hint = ",".join(k.replace(" ", "") for k in chunk)
        try:
            res = api._request("GET", "/keywordstool",
                               params={"hintKeywords": hint, "showDetail": "1"})
        except Exception as e:
            logger.warning("keywordstool 실패 %s: %s", chunk, e)
            continue
        for it in res.get("keywordList", []):
            kw = it.get("relKeyword", "")
            if kw:
                lookup[norm(kw)] = (to_int(it.get("monthlyPcQcCnt")),
                                    to_int(it.get("monthlyMobileQcCnt")),
                                    it.get("compIdx", "-"))
        time.sleep(0.3)
    return lookup


def fetch_rank(query: str) -> tuple:
    """(순위 or None, 조회건수). 실패 시 (None, -1)."""
    url = (f"https://m.place.naver.com/restaurant/list"
           f"?query={urllib.parse.quote(query)}&x={X}&y={Y}")
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=12)
        r.encoding = "utf-8"
        m = re.search(r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\});", r.text, re.S)
        if not m:
            return None, -1
        d = json.loads(m.group(1))
        ids = [str(v.get("id")) for k, v in d.items()
               if k.startswith("PlaceListBusinessesItem:")]
        rank = ids.index(PLACE_ID) + 1 if PLACE_ID in ids else None
        return rank, len(ids)
    except Exception as e:
        logger.warning("rank 조회 실패 '%s': %s", query, e)
        return None, -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-rank", action="store_true", help="순위 크롤 생략")
    ap.add_argument("--rank-top", type=int, default=60, help="순위 조회할 상위 N개")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    api = NaverAdAPI(os.getenv("SAJIK_API_KEY"), os.getenv("SAJIK_SECRET_KEY"),
                     os.getenv("SAJIK_CUSTOMER_ID"))

    cands = gen_candidates()
    logger.info("후보 %d개 검색량 조회 시작", len(cands))
    vol = fetch_volumes(api, cands)

    rows = []
    for kw in cands:
        pc, mo, comp = vol.get(norm(kw), (0, 0, "-"))
        rows.append({"kw": kw, "pc": pc, "mo": mo, "total": pc + mo, "comp": comp})
    rows.sort(key=lambda r: -r["total"])

    # 검색량 있는 상위 N개만 순위 크롤
    targets = [r for r in rows if r["total"] >= 20][:args.rank_top]
    if not args.no_rank:
        logger.info("상위 %d개 오가닉 순위 조회", len(targets))
        for r in targets:
            rank, size = fetch_rank(r["kw"])
            r["rank"], r["list_size"] = rank, size
            logger.info("  %s (%d) → %s", r["kw"], r["total"],
                        f"{rank}위" if rank else f"{size}위밖" if size > 0 else "실패")
            time.sleep(1.0)

    for r in rows:
        rank = r.get("rank")
        if r["total"] < 20:
            r["status"] = "저볼륨"
        elif rank and rank <= 10:
            r["status"] = "먹고있음"
        elif rank:
            r["status"] = "노출중(하위)"
        elif r.get("list_size", -1) >= 0:
            r["status"] = "점령대상"
        else:
            r["status"] = "미조사"

    plan = {
        "generated": date.today().isoformat(),
        "place_id": PLACE_ID,
        "tags": {"region": REGIONS, "common": COMMON, "menu": MENU},
        "recommendation": RECOMMEND,
        "rows": [r for r in rows if r["total"] >= 20],
    }
    out_json = ROOT / "data" / "sajik_tag_plan.json"
    out_json.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")

    md = [f"# 사직점 태그/키워드 점령 플랜 ({plan['generated']})", ""]
    md.append("| 키워드 | 월검색(PC+MO) | 현재순위 | 상태 |")
    md.append("|---|---|---|---|")
    for r in rows:
        if r["total"] < 20:
            continue
        rank = r.get("rank")
        shown = f"{rank}위" if rank else (
            f"{r['list_size']}위밖" if r.get("list_size", -1) > 0 else "-")
        md.append(f"| {r['kw']} | {r['total']:,} | {shown} | {r['status']} |")
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "sajik_tag_plan.md").write_text("\n".join(md), encoding="utf-8")
    logger.info("저장: %s, out/sajik_tag_plan.md (%d행)", out_json.name, len(plan["rows"]))


if __name__ == "__main__":
    main()
