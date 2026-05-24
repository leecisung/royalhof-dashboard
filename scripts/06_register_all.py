# -*- coding: utf-8 -*-
"""
06_register_all.py — 1회성
예비 풀 전체 키워드를 Naver에 한방 등록 (100만개)
필요한 만큼 새 광고그룹 자동 생성 + 소재 등록까지

실행:
  python scripts/06_register_all.py --dry-run   # 계획 확인
  python scripts/06_register_all.py             # 전체 등록 (~40분)
  python scripts/06_register_all.py --from G2_지역창업_강원  # 특정 키부터 재개
"""

import os, sys, json, logging, argparse, time, math, sqlite3
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import load_dotenv
from lib.naver_api import NaverAdAPI

LOG_FILE = ROOT / "logs" / "api_calls.log"
LOG_FILE.parent.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("06_register_all")

AD_GROUPS_PATH = ROOT / "data" / "ad_groups.json"
PROGRESS_FILE  = ROOT / "data" / "register_all_progress.json"
DB_PATH        = ROOT / "data" / "reserve_pool.db"

CAMPAIGN_ID    = "cmp-a001-01-000000010628546"
BIZ_CHANNEL_ID = "bsn-a001-00-000000013818883"
LANDING_URL    = "https://xn--2o2bq4vzzgxncn7h11b.com"
GROUP_LIMIT    = 1000

ADS = [
    {
        "headline":    "동네호프전성시대 로얄호프치킨",
        "description": "유행은 가고 결국 동네 호프의 전성시대가 돌아옵니다. 다시, 동네 호프의 시대",
        "url": LANDING_URL,
    },
    {
        "headline":    "순수익 증명가능 로얄호프치킨",
        "description": "코리안스타일호프 월 수익률 34.7%, 매출 9,400만원, 순수익 3,262만원",
        "url": LANDING_URL,
    },
]


# ── DB 헬퍼 ──────────────────────────────────────────────────

def db_get_available_stats() -> dict:
    """adgroup_key별 available 카운트"""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT adgroup_key, COUNT(*) FROM reserve_keywords "
            "WHERE status='available' GROUP BY adgroup_key"
        ).fetchall()
    return {key: cnt for key, cnt in rows if cnt > 0}


def db_fetch_available(adgroup_key: str, limit: int) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, keyword FROM reserve_keywords "
            "WHERE adgroup_key=? AND status='available' LIMIT ?",
            (adgroup_key, limit),
        ).fetchall()
    return [{"id": r[0], "keyword": r[1]} for r in rows]


def db_update_adgroup_key(ids: list[int], new_key: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany(
            "UPDATE reserve_keywords SET adgroup_key=? WHERE id=?",
            [(new_key, kid) for kid in ids],
        )


def db_mark_registered(ids: list[int]):
    today = str(date.today())
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany(
            "UPDATE reserve_keywords SET status='registered', registered_at=? WHERE id=?",
            [(today, kid) for kid in ids],
        )


# ── 진행 상태 ────────────────────────────────────────────────

def load_progress() -> set:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return set(json.load(f).get("done", []))
    return set()


def save_progress(done: set):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"done": sorted(done)}, f, ensure_ascii=False)


# ── overflow 그룹 키 계산 ─────────────────────────────────────

def next_overflow_key(base_key: str, ad_groups: dict) -> str:
    """
    base_key 에 대한 다음 overflow 그룹 키 반환.
    기존 그룹: base_key (n=1), base_key_2 (n=2), ...
    """
    n = 1
    while True:
        key = base_key if n == 1 else f"{base_key}_{n}"
        if key not in ad_groups:
            return key
        n += 1


# ── 메인 ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--from", dest="from_key", default=None,
                        help="이 adgroup_key부터 재개 (알파벳 순 기준)")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")

    ad_groups = json.load(open(AD_GROUPS_PATH, encoding="utf-8"))
    done      = load_progress()

    stats = db_get_available_stats()
    sorted_keys = sorted(stats.keys())

    total_available  = sum(stats.values())
    total_new_groups = sum(math.ceil(v / GROUP_LIMIT) for v in stats.values())
    api_calls_est    = total_new_groups + math.ceil(total_available / 100) + total_new_groups * 2
    time_est_min     = math.ceil(api_calls_est * 0.2 / 60)

    logger.info("=== 06_register_all 시작 ===")
    logger.info("등록 대기 키워드: %d개", total_available)
    logger.info("생성 필요 그룹:  ~%d개", total_new_groups)
    logger.info("예상 API 콜:    ~%d회", api_calls_est)
    logger.info("예상 소요 시간:  ~%d분", time_est_min)

    if args.dry_run:
        print(f"\n등록 대기: {total_available:,}개")
        print(f"신규 그룹: ~{total_new_groups:,}개")
        print(f"예상 시간: ~{time_est_min}분")
        print(f"\nadgroup_key별 현황:")
        for key in sorted_keys:
            cnt = stats[key]
            n = math.ceil(cnt / GROUP_LIMIT)
            print(f"  {key[:55]}: {cnt:6,}개 → +{n}그룹")
        return

    api = NaverAdAPI(
        os.getenv("NAVER_AD_API_KEY"),
        os.getenv("NAVER_AD_SECRET_KEY"),
        os.getenv("NAVER_AD_CUSTOMER_ID"),
    )

    created   = 0
    registered = 0
    failed    = []
    skip_until = args.from_key

    for base_key in sorted_keys:
        if skip_until and base_key < skip_until:
            logger.info("[건너뜀] %s (--from 이전)", base_key)
            continue

        available = stats.get(base_key, 0)
        if available == 0:
            continue

        logger.info("[%s] 시작: available=%d", base_key, available)

        while True:
            batch = db_fetch_available(base_key, GROUP_LIMIT)
            if not batch:
                break

            group_key = next_overflow_key(base_key, ad_groups)
            batch_ids = [r["id"] for r in batch]
            keywords  = [r["keyword"] for r in batch]

            if group_key in done:
                logger.info("[%s] 이미 완료 → 건너뜀", group_key)
                # 여전히 DB에 available로 남아있으면 등록 처리만
                db_mark_registered(batch_ids)
                continue

            # overflow 그룹이면 DB adgroup_key 먼저 업데이트
            if group_key != base_key:
                db_update_adgroup_key(batch_ids, group_key)

            # Naver 그룹 생성
            try:
                result = api.create_ad_group(CAMPAIGN_ID, group_key, BIZ_CHANNEL_ID)
                group_id = result.get("nccAdgroupId", "")
                if not group_id:
                    raise ValueError(f"그룹 ID 없음: {result}")
                ad_groups[group_key] = {"group_id": group_id, "campaign_id": CAMPAIGN_ID}
                with open(AD_GROUPS_PATH, "w", encoding="utf-8") as f:
                    json.dump(ad_groups, f, ensure_ascii=False, indent=2)
                logger.info("[%s] 그룹 생성 ✓ (%s)", group_key, group_id)
                created += 1
            except Exception as e:
                logger.error("[%s] 그룹 생성 실패: %s", group_key, e)
                # DB 롤백 (adgroup_key 원복)
                if group_key != base_key:
                    db_update_adgroup_key(batch_ids, base_key)
                failed.append(group_key)
                time.sleep(2)
                break

            # 키워드 등록
            try:
                api.register_keywords(group_id, CAMPAIGN_ID, keywords)
                db_mark_registered(batch_ids)
                registered += len(batch)
                logger.info("[%s] 키워드 %d개 등록 ✓", group_key, len(batch))
            except Exception as e:
                logger.error("[%s] 키워드 등록 실패: %s", group_key, e)
                failed.append(group_key)
                time.sleep(2)
                break

            # 소재 등록
            try:
                api.create_ads(group_id, CAMPAIGN_ID, ADS)
                logger.info("[%s] 소재 2개 등록 ✓", group_key)
            except Exception as e:
                logger.warning("[%s] 소재 등록 실패 (계속): %s", group_key, e)

            done.add(group_key)
            save_progress(done)

            if created % 50 == 0 and created > 0:
                logger.info("[진행] 그룹 %d개 생성, 키워드 %d개 등록", created, registered)

    print("\n" + "=" * 60)
    print(f"완료: 그룹 {created}개 생성, 키워드 {registered:,}개 등록")
    if failed:
        print(f"실패: {len(failed)}개 그룹 → {failed[:10]}")
    stats_final = {}
    with sqlite3.connect(DB_PATH) as conn:
        for row in conn.execute("SELECT status, COUNT(*) FROM reserve_keywords GROUP BY status"):
            stats_final[row[0]] = row[1]
    print(f"DB 현황: {stats_final}")
    print("=" * 60)


if __name__ == "__main__":
    main()
