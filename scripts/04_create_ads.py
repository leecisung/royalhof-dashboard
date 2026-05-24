# -*- coding: utf-8 -*-
"""
04_create_ads.py — 1회성
51개 광고그룹에 소재 2개씩 등록

소재:
  1. 동네호프전성시대 로얄호프치킨
  2. 순수익 증명가능 로얄호프치킨

실행:
  python scripts/04_create_ads.py --dry-run   # 계획 확인
  python scripts/04_create_ads.py             # 전체 등록
  python scripts/04_create_ads.py --group G1_메인변형  # 특정 그룹만
"""

import os, sys, json, logging, argparse, time
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
logger = logging.getLogger("04_create_ads")

AD_GROUPS_PATH = ROOT / "data" / "ad_groups.json"
PROGRESS_FILE  = ROOT / "data" / "ads_progress.json"

LANDING_URL = "https://xn--2o2bq4vzzgxncn7h11b.com"

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


def validate_no_protected_campaigns(ad_groups: dict):
    protected_raw = os.getenv("PROTECTED_CAMPAIGN_IDS", "")
    protected_ids = {c.strip() for c in protected_raw.split(",") if c.strip()}
    if not protected_ids:
        return
    for gname, gdata in ad_groups.items():
        cid = gdata.get("campaign_id", "")
        if cid in protected_ids:
            logger.critical("보호된 캠페인 ID 감지: %s → %s. 즉시 중단.", gname, cid)
            sys.exit(1)
    logger.info("[안전] 보호 캠페인 검사 통과 ✓")


def load_progress() -> set:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return set(json.load(f).get("done", []))
    return set()

def save_progress(done: set):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"done": list(done)}, f, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--group", type=str, default=None)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")

    with open(AD_GROUPS_PATH, encoding="utf-8") as f:
        ad_groups = json.load(f)

    validate_no_protected_campaigns(ad_groups)

    if args.group:
        if args.group not in ad_groups:
            logger.error("그룹 '%s' 없음", args.group)
            sys.exit(1)
        target_groups = {args.group: ad_groups[args.group]}
    else:
        target_groups = ad_groups

    if args.dry_run:
        logger.info("=== [DRY-RUN] 소재 등록 계획 ===")
        for g in target_groups:
            logger.info("  %s → 소재 2개 등록 예정", g)
        print(f"\n총 {len(target_groups)}개 그룹 × 2개 소재 = {len(target_groups)*2}개 등록 예정")
        print(f"소재1: {ADS[0]['headline']}")
        print(f"소재2: {ADS[1]['headline']}")
        print(f"URL:   {LANDING_URL}")
        return

    api = NaverAdAPI(
        os.getenv("NAVER_AD_API_KEY"),
        os.getenv("NAVER_AD_SECRET_KEY"),
        os.getenv("NAVER_AD_CUSTOMER_ID"),
    )

    done      = load_progress()
    succeeded = 0
    failed    = []

    logger.info("=== 04_create_ads 시작 (총 %d개 그룹) ===", len(target_groups))

    for group_name, group_data in target_groups.items():
        if group_name in done:
            logger.info("[%s] 이미 처리됨 → 건너뜀", group_name)
            continue

        adgroup_id  = group_data["group_id"]
        campaign_id = group_data["campaign_id"]

        try:
            items = api.create_ads(adgroup_id, campaign_id, ADS)
            if items:
                logger.info("[%s] ✓ 소재 %d개 등록", group_name, len(items))
                done.add(group_name)
                succeeded += 1
            else:
                logger.warning("[%s] 소재 등록 응답 비어있음 (이미 있을 수 있음)", group_name)
                done.add(group_name)
                succeeded += 1
        except Exception as e:
            logger.error("[%s] 오류: %s", group_name, e)
            failed.append(group_name)

        save_progress(done)
        time.sleep(0.3)

    print("\n" + "=" * 50)
    print(f"완료: {succeeded}개 그룹 소재 등록")
    if failed:
        print(f"실패: {failed}")
    print("=" * 50)


if __name__ == "__main__":
    main()
