# -*- coding: utf-8 -*-
"""
00b_setup_region_groups.py — 1회성
서울 25구별 그룹 + 경기 4클러스터 그룹 생성 → ad_groups.json 추가
"""
import os, sys, json, logging, argparse, time
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import load_dotenv
from lib.naver_api import NaverAdAPI

LOG_FILE = ROOT / "logs" / "api_calls.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("00b_setup_region_groups")

BIZ_CHANNEL_ID  = "bsn-a001-00-000000013818883"
CAMPAIGN_ID     = "cmp-a001-01-000000010628546"
AD_GROUPS_PATH  = ROOT / "data" / "ad_groups.json"

# 서울 25구
SEOUL_GU_GROUPS = [
    "G2_지역창업_서울_강남구", "G2_지역창업_서울_강동구", "G2_지역창업_서울_강북구",
    "G2_지역창업_서울_강서구", "G2_지역창업_서울_관악구", "G2_지역창업_서울_광진구",
    "G2_지역창업_서울_구로구", "G2_지역창업_서울_금천구", "G2_지역창업_서울_노원구",
    "G2_지역창업_서울_도봉구", "G2_지역창업_서울_동대문구", "G2_지역창업_서울_동작구",
    "G2_지역창업_서울_마포구", "G2_지역창업_서울_서대문구", "G2_지역창업_서울_서초구",
    "G2_지역창업_서울_성동구", "G2_지역창업_서울_성북구", "G2_지역창업_서울_송파구",
    "G2_지역창업_서울_양천구", "G2_지역창업_서울_영등포구", "G2_지역창업_서울_용산구",
    "G2_지역창업_서울_은평구", "G2_지역창업_서울_종로구", "G2_지역창업_서울_중구",
    "G2_지역창업_서울_중랑구",
]

# 경기 4클러스터
GYEONGGI_CLUSTER_GROUPS = [
    "G2_지역창업_경기_남부",   # 수원, 성남, 용인, 안양, 과천, 군포, 의왕, 오산, 평택, 안성
    "G2_지역창업_경기_서부",   # 부천, 광명, 시흥, 안산, 김포, 화성
    "G2_지역창업_경기_북부",   # 고양, 파주, 의정부, 양주, 동두천, 포천, 연천, 가평
    "G2_지역창업_경기_동부",   # 남양주, 구리, 하남, 광주, 이천, 여주, 양평
]

NEW_GROUPS = SEOUL_GU_GROUPS + GYEONGGI_CLUSTER_GROUPS


def main():
    load_dotenv(ROOT / ".env")
    protected_raw = os.getenv("PROTECTED_CAMPAIGN_IDS", "")
    protected_ids = {c.strip() for c in protected_raw.split(",") if c.strip()}
    if CAMPAIGN_ID in protected_ids:
        logger.critical("캠페인 ID가 보호 목록에 있습니다. 중단.")
        sys.exit(1)

    api = NaverAdAPI(os.getenv("NAVER_AD_API_KEY"), os.getenv("NAVER_AD_SECRET_KEY"), os.getenv("NAVER_AD_CUSTOMER_ID"))

    with open(AD_GROUPS_PATH, encoding="utf-8") as f:
        ad_groups = json.load(f)

    failed = []
    for group_name in NEW_GROUPS:
        if group_name in ad_groups:
            logger.info("  건너뜀 (이미 존재): %s", group_name)
            continue
        try:
            grp = api.create_ad_group(CAMPAIGN_ID, group_name, BIZ_CHANNEL_ID)
            grp_id = grp.get("nccAdgroupId", "")
            if grp_id:
                ad_groups[group_name] = {"group_id": grp_id, "campaign_id": CAMPAIGN_ID}
                logger.info("  ✓ %-45s → %s", group_name, grp_id)
            else:
                logger.error("  ✗ %s 실패: %s", group_name, grp)
                failed.append(group_name)
        except Exception as e:
            logger.error("  ✗ %s 오류: %s", group_name, e)
            failed.append(group_name)
        time.sleep(0.25)

    with open(AD_GROUPS_PATH, "w", encoding="utf-8") as f:
        json.dump(ad_groups, f, ensure_ascii=False, indent=2)

    logger.info("ad_groups.json 저장 완료 (총 %d개 그룹)", len(ad_groups))
    if failed:
        logger.warning("실패한 그룹: %s", failed)
    print(f"\n완료: {len(NEW_GROUPS) - len(failed)}개 생성, {len(failed)}개 실패")


if __name__ == "__main__":
    main()
