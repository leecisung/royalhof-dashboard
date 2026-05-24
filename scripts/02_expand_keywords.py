# -*- coding: utf-8 -*-
"""
02_expand_keywords.py — 1회성
keywordstool API로 시드 키워드를 1백만개로 확장 → reserve_pool.db 삽입

전략:
  1. reserve_pool.db의 available 키워드를 pivot으로 사용
  2. 5개씩 배치로 keywordstool 호출 → 연관 키워드 수집
  3. 관련성 필터 통과한 키워드만 저장 (창업/가맹/치킨/호프 등 포함)
  4. reserve_pool >= TARGET(100만) 도달하면 종료

실행: python scripts/02_expand_keywords.py
     python scripts/02_expand_keywords.py --target 1000000
     python scripts/02_expand_keywords.py --pivot-limit 5000  # pivot 수 제한
"""

import os, sys, json, logging, argparse, time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import load_dotenv
from lib.naver_api import NaverAdAPI
from lib.reserve_pool import init_db, bulk_insert, get_pool_size, get_stats

import sqlite3

LOG_FILE = ROOT / "logs" / "api_calls.log"
LOG_FILE.parent.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("02_expand_keywords")

DB_PATH = ROOT / "data" / "reserve_pool.db"

# ── 관련성 필터 ──────────────────────────────────────────────
RELEVANCE_KEYWORDS = {
    "창업", "가맹", "프랜차이즈", "치킨", "호프", "맥주", "주점",
    "치맥", "닭발", "안주", "소주", "생맥주", "수제맥주", "포차",
    "이자카야", "BBQ", "교촌", "BHC", "노랑통닭", "굽네",
}

def is_relevant(kw: str) -> bool:
    return any(r in kw for r in RELEVANCE_KEYWORDS)

# ── 그룹 분류기 ──────────────────────────────────────────────
def classify_group(kw: str, default_group: str) -> str:
    """반환된 키워드를 그룹에 배정. 기본은 pivot의 그룹 유지."""
    chicken_brands = {"BBQ","교촌","BHC","노랑통닭","굽네","처갓집","네네치킨","푸라닭","호식이","60계","멕시카나","또래오래","페리카나","지코바"}
    beer_brands    = {"가르텐비어","역전할머니맥주","생활맥주","투다리","봉구비어","치어스"}
    situation_kws  = {"소자본","소액","퇴직","은퇴","직장인","부부","40대","50대","1억","2억","5000만"}
    menu_kws       = {"생맥주","수제맥주","크래프트","이자카야","포차","안주","치맥"}

    for b in chicken_brands:
        if b in kw:
            return "G3_경쟁치킨"
    for b in beer_brands:
        if b in kw:
            return "G4_경쟁호프"
    for s in situation_kws:
        if s in kw:
            return "G5_상황조건"
    for m in menu_kws:
        if m in kw:
            return "G6_메뉴결합"
    return default_group

# ── pivot 로드 ──────────────────────────────────────────────
def load_pivots(limit: int, broad_only: bool = False) -> list[dict]:
    """reserve_pool.db에서 pivot 로드.
    broad_only=True 이면 짧은 키워드(≤8자)만 사용 — keywordstool 효율 극대화.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    if broad_only:
        rows = conn.execute(
            "SELECT id, keyword, adgroup_key FROM reserve_keywords "
            "WHERE status='available' AND length(keyword) <= 8 ORDER BY id LIMIT ?",
            (limit,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, keyword, adgroup_key FROM reserve_keywords "
            "WHERE status='available' ORDER BY id LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── 진행 상황 저장/로드 ──────────────────────────────────────
PROGRESS_FILE = ROOT / "data" / "expand_progress.json"

def load_progress() -> set:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return set(json.load(f).get("done_ids", []))
    return set()

def save_progress(done_ids: set):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"done_ids": list(done_ids)}, f)

# ── 메인 ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=1_000_000, help="목표 키워드 수")
    parser.add_argument("--pivot-limit", type=int, default=5_000, help="사용할 pivot 최대 수")
    parser.add_argument("--batch-size", type=int, default=5, help="keywordstool 배치 크기 (최대 5)")
    parser.add_argument("--broad-only", action="store_true", default=True, help="짧은 키워드만 pivot으로 사용")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    api = NaverAdAPI(
        os.getenv("NAVER_AD_API_KEY"),
        os.getenv("NAVER_AD_SECRET_KEY"),
        os.getenv("NAVER_AD_CUSTOMER_ID"),
    )
    init_db()

    current = get_pool_size()
    logger.info("=== 02_expand_keywords 시작 ===")
    logger.info("현재 풀 크기: %d / 목표: %d", current, args.target)

    if current >= args.target:
        logger.info("이미 목표 달성. 종료.")
        return

    pivots = load_pivots(args.pivot_limit, broad_only=args.broad_only)
    logger.info("pivot 로드: %d개", len(pivots))

    done_ids  = load_progress()
    total_new = 0
    batch_num = 0

    for i in range(0, len(pivots), args.batch_size):
        current = get_pool_size()
        if current >= args.target:
            logger.info("목표 달성! 풀 크기: %d", current)
            break

        batch = pivots[i : i + args.batch_size]
        # 이미 처리한 pivot 건너뜀
        batch = [p for p in batch if p["id"] not in done_ids]
        if not batch:
            continue

        batch_num += 1
        pivot_kws = [p["keyword"] for p in batch]
        default_group = batch[0]["adgroup_key"]  # 배치의 첫 pivot 그룹 사용

        try:
            # keywordstool API 호출
            hint = ",".join(quote(kw, safe="") for kw in pivot_kws)
            path = "/keywordstool"
            params = {"hintKeywords": hint, "showDetail": "1"}
            result = api._request("GET", path, params=params)

            new_keywords = []
            for item in result.get("keywordList", []):
                kw = item.get("relKeyword", "").strip()
                if not kw or len(kw) < 3 or len(kw) > 30:
                    continue
                if not is_relevant(kw):
                    continue
                grp = classify_group(kw, default_group)
                new_keywords.append({"keyword": kw, "adgroup_key": grp})

            if new_keywords:
                inserted = bulk_insert(new_keywords)
                total_new += inserted

            for p in batch:
                done_ids.add(p["id"])

            if batch_num % 100 == 0:
                save_progress(done_ids)
                current = get_pool_size()
                logger.info("[진행] 배치 %d | 풀 %d개 | 이번 세션 신규 %d개",
                            batch_num, current, total_new)

        except Exception as e:
            logger.error("[오류] 배치 %d: %s", batch_num, e)
            time.sleep(2)

    save_progress(done_ids)
    final = get_pool_size()
    stats = get_stats()
    logger.info("=== 완료 ===")
    logger.info("최종 풀 크기: %d개 (이번 세션 신규: %d개)", final, total_new)
    logger.info("풀 상태: %s", stats)

    if final < args.target:
        logger.warning("목표 미달 (%d / %d). pivot 재실행 또는 --pivot-limit 늘려서 재실행 권장.", final, args.target)


if __name__ == "__main__":
    main()
