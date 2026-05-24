# -*- coding: utf-8 -*-
"""
00_setup_campaign.py — 1회성 실행
70원 전략 캠페인 1개 + 광고그룹 22개 생성 → ad_groups.json 업데이트

생성 구조:
  캠페인: 로얄호프치킨_70원전략
  그룹: G1_메인변형, G2_지역창업_서울/경기/... (17개), G3~G6
"""

import os
import sys
import json
import logging
import argparse
import time
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
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("00_setup_campaign")

# ──────────────────────────────────────────────
# 생성할 광고그룹 목록 (순서대로)
# ──────────────────────────────────────────────
AD_GROUPS = [
    "G1_메인변형",
    "G2_지역창업_서울",
    "G2_지역창업_경기",
    "G2_지역창업_부산",
    "G2_지역창업_대구",
    "G2_지역창업_인천",
    "G2_지역창업_광주",
    "G2_지역창업_대전",
    "G2_지역창업_울산",
    "G2_지역창업_세종",
    "G2_지역창업_강원",
    "G2_지역창업_충북",
    "G2_지역창업_충남",
    "G2_지역창업_전북",
    "G2_지역창업_전남",
    "G2_지역창업_경북",
    "G2_지역창업_경남",
    "G2_지역창업_제주",
    "G3_경쟁치킨",
    "G4_경쟁호프",
    "G5_상황조건",
    "G6_메뉴결합",
]

AD_GROUPS_PATH = ROOT / "data" / "ad_groups.json"

# 로얄호프치킨 사이트 Biz Channel ID (royalhofchicken.com)
BIZ_CHANNEL_ID = "bsn-a001-00-000000013818883"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-id", required=True,
                        help="UI에서 생성한 70원전략 캠페인 ID. 예: cmp-a001-01-XXXXXXX")
    args = parser.parse_args()
    campaign_id = args.campaign_id.strip()

    load_dotenv(ROOT / ".env")

    # 보호 캠페인 안전장치
    protected_raw = os.getenv("PROTECTED_CAMPAIGN_IDS", "")
    protected_ids = {cid.strip() for cid in protected_raw.split(",") if cid.strip()}
    if campaign_id in protected_ids:
        logger.critical("입력한 캠페인 ID가 보호 목록에 있습니다! 기존 캠페인을 실수로 입력한 것 같습니다: %s", campaign_id)
        sys.exit(1)

    api = NaverAdAPI(
        os.getenv("NAVER_AD_API_KEY"),
        os.getenv("NAVER_AD_SECRET_KEY"),
        os.getenv("NAVER_AD_CUSTOMER_ID"),
    )

    logger.info("캠페인 ID: %s", campaign_id)

    # ── 2. 광고그룹 생성 ──
    ad_groups_data = {}
    failed = []

    for group_name in AD_GROUPS:
        try:
            group = api.create_ad_group(campaign_id, group_name, BIZ_CHANNEL_ID)
            group_id = group.get("nccAdgroupId", "")
            if group_id:
                ad_groups_data[group_name] = {
                    "group_id": group_id,
                    "campaign_id": campaign_id,
                }
                logger.info("  ✓ %-35s → %s", group_name, group_id)
            else:
                logger.error("  ✗ %s 생성 실패: %s", group_name, group)
                failed.append(group_name)
        except Exception as e:
            logger.error("  ✗ %s 오류: %s", group_name, e)
            failed.append(group_name)
        time.sleep(0.25)  # rate limit 여유

    # ── 3. ad_groups.json 저장 ──
    with open(AD_GROUPS_PATH, "w", encoding="utf-8") as f:
        json.dump(ad_groups_data, f, ensure_ascii=False, indent=2)

    logger.info("ad_groups.json 저장 완료: %s", AD_GROUPS_PATH)

    # ── 4. 결과 요약 ──
    print("\n" + "=" * 60)
    print(f"캠페인: 로얄호프치킨_70원전략  ({campaign_id})")
    print(f"그룹 생성 성공: {len(ad_groups_data)}개 / 전체 {len(AD_GROUPS)}개")
    if failed:
        print(f"실패한 그룹: {failed}")
    print("=" * 60)
    print("\n다음 단계:")
    print("  python scripts/01_generate_seeds.py --dry-run")
    print("  python scripts/01_generate_seeds.py")


if __name__ == "__main__":
    main()
