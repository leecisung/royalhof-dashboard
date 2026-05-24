# -*- coding: utf-8 -*-
"""
03_register.py — 1회성
reserve_pool.db의 available 키워드를 Naver 광고그룹에 실제 등록

전략:
  1. ad_groups.json의 각 그룹에 대해 현재 등록 수 API 확인
  2. 그룹당 최대 LIMIT_PER_GROUP(1000)개까지 reserve_pool에서 끌어와 등록
  3. 등록 성공 시 reserve_pool → registered 상태로 변경
  4. 진행상황 data/register_progress.json 저장 (중단 후 재개 가능)

실행:
  python scripts/03_register.py --dry-run        # 실제 등록 없이 계획만 출력
  python scripts/03_register.py                   # 전체 그룹 등록
  python scripts/03_register.py --group G1_메인변형  # 특정 그룹만
  python scripts/03_register.py --limit 500       # 그룹당 최대 500개
"""

import os, sys, json, logging, argparse, time
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import load_dotenv
from lib.naver_api import NaverAdAPI
from lib.reserve_pool import init_db, get_available_keywords, mark_registered, get_stats

LOG_FILE = ROOT / "logs" / "api_calls.log"
LOG_FILE.parent.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("03_register")

AD_GROUPS_PATH = ROOT / "data" / "ad_groups.json"
PROGRESS_FILE  = ROOT / "data" / "register_progress.json"
LIMIT_PER_GROUP = 1000  # 네이버 그룹당 키워드 한도


# ── 보호 캠페인 안전장치 ──────────────────────────────────────
def validate_no_protected_campaigns(ad_groups: dict):
    protected_raw = os.getenv("PROTECTED_CAMPAIGN_IDS", "")
    protected_ids = {c.strip() for c in protected_raw.split(",") if c.strip()}
    if not protected_ids:
        return
    for gname, gdata in ad_groups.items():
        cid = gdata.get("campaign_id", "")
        if cid in protected_ids:
            logger.critical(
                "!!! 보호된 캠페인 ID가 ad_groups.json에 있습니다: %s → %s",
                gname, cid
            )
            logger.critical("기존 파워링크 캠페인을 건드릴 수 없습니다. 즉시 중단.")
            sys.exit(1)
    logger.info("[안전] 보호 캠페인 검사 통과 ✓")


# ── 진행 상황 ────────────────────────────────────────────────
def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_progress(progress: dict):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


# ── 현재 그룹 등록 수 API 확인 ──────────────────────────────
def get_current_count(api: NaverAdAPI, adgroup_id: str) -> int:
    try:
        keywords = api.get_keywords_by_group(adgroup_id)
        return len(keywords)
    except Exception as e:
        logger.warning("[API] 그룹 %s 키워드 수 조회 실패: %s (0으로 처리)", adgroup_id, e)
        return 0


# ── 메인 ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="실제 API 호출 없이 계획만 출력")
    parser.add_argument("--group", type=str, default=None, help="특정 그룹만 처리 (예: G1_메인변형)")
    parser.add_argument("--limit", type=int, default=LIMIT_PER_GROUP, help=f"그룹당 최대 등록 수 (기본 {LIMIT_PER_GROUP})")
    parser.add_argument("--skip-count-check", action="store_true",
                        help="API로 현재 등록 수 확인 생략 (DB 기준만 사용, 빠름)")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")

    with open(AD_GROUPS_PATH, encoding="utf-8") as f:
        ad_groups = json.load(f)

    validate_no_protected_campaigns(ad_groups)
    init_db()

    if args.dry_run:
        logger.info("=== [DRY-RUN] 03_register 계획 확인 ===")
    else:
        logger.info("=== 03_register 시작 ===")

    # 처리 대상 그룹 결정
    if args.group:
        if args.group not in ad_groups:
            logger.error("그룹 '%s'이 ad_groups.json에 없습니다.", args.group)
            sys.exit(1)
        target_groups = {args.group: ad_groups[args.group]}
    else:
        target_groups = ad_groups

    progress = load_progress()

    if not args.dry_run:
        api = NaverAdAPI(
            os.getenv("NAVER_AD_API_KEY"),
            os.getenv("NAVER_AD_SECRET_KEY"),
            os.getenv("NAVER_AD_CUSTOMER_ID"),
        )

    total_registered  = 0
    total_skipped     = 0
    total_no_keywords = 0
    group_results     = []

    for group_name, group_data in target_groups.items():
        adgroup_id  = group_data["group_id"]
        campaign_id = group_data["campaign_id"]

        # 현재 등록 수 파악
        if args.skip_count_check or args.dry_run:
            # DB에서 registered 상태로 이 그룹에 등록된 수 추정
            current_count = progress.get(group_name, {}).get("registered", 0)
        else:
            current_count = get_current_count(api, adgroup_id)
            logger.info("[%s] API 기준 현재 등록 수: %d개", group_name, current_count)

        slots_available = args.limit - current_count
        if slots_available <= 0:
            logger.info("[%s] 이미 %d개 등록됨 → 건너뜀", group_name, current_count)
            total_skipped += 1
            group_results.append({"group": group_name, "status": "full", "registered": 0})
            continue

        # reserve_pool에서 키워드 조회
        pool_kws = get_available_keywords(group_name, limit=slots_available)

        if not pool_kws:
            logger.info("[%s] 예비 풀 키워드 없음 → 건너뜀", group_name)
            total_no_keywords += 1
            group_results.append({"group": group_name, "status": "no_pool", "registered": 0})
            continue

        kw_texts = [r["keyword"] for r in pool_kws]
        kw_ids   = [r["id"] for r in pool_kws]

        if args.dry_run:
            logger.info("[DRY-RUN] %s: 슬롯 %d개, 풀 %d개 → %d개 등록 예정",
                        group_name, slots_available, len(pool_kws), min(slots_available, len(pool_kws)))
            group_results.append({
                "group": group_name,
                "status": "would_register",
                "planned": min(slots_available, len(kw_texts)),
            })
            continue

        # 실제 등록
        try:
            registered_items = api.register_keywords(adgroup_id, campaign_id, kw_texts, bid=70)
            count = len(registered_items)
            if count > 0:
                mark_registered(kw_ids[:count])

            prog_entry = progress.get(group_name, {"registered": current_count})
            prog_entry["registered"] = current_count + count
            progress[group_name] = prog_entry

            total_registered += count
            logger.info("[%s] ✓ %d개 등록 완료 (총 %d개)", group_name, count, current_count + count)
            group_results.append({"group": group_name, "status": "ok", "registered": count})

        except Exception as e:
            logger.error("[%s] 등록 오류: %s", group_name, e)
            group_results.append({"group": group_name, "status": "error", "error": str(e)})

        save_progress(progress)
        time.sleep(0.3)  # rate limit 여유

    # ── 최종 요약 ──
    print("\n" + "=" * 65)
    if args.dry_run:
        print("[DRY-RUN] 등록 계획 요약")
    else:
        print("03_register 완료 요약")
    print("=" * 65)
    print(f"{'그룹명':<40} {'상태':<12} {'등록수':>6}")
    print("-" * 65)
    for r in group_results:
        status = r.get("status", "")
        count  = r.get("registered", r.get("planned", 0))
        print(f"{r['group']:<40} {status:<12} {count:>6}")
    print("=" * 65)

    if args.dry_run:
        total_planned = sum(r.get("planned", 0) for r in group_results)
        print(f"\n등록 예정: {total_planned:,}개 / {len(target_groups)}개 그룹")
    else:
        print(f"\n이번 실행 신규 등록: {total_registered:,}개")
        print(f"스킵 (이미 만석): {total_skipped}개 그룹")
        print(f"스킵 (풀 없음):   {total_no_keywords}개 그룹")
        pool_stats = get_stats()
        print(f"\n예비 풀 현황: {pool_stats}")

    if not args.dry_run and total_registered == 0 and not args.dry_run:
        logger.warning("등록된 키워드가 없습니다. reserve_pool 상태와 그룹 키를 확인하세요.")


if __name__ == "__main__":
    main()
