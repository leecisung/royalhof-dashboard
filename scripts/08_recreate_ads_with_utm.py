# -*- coding: utf-8 -*-
"""
08_recreate_ads_with_utm.py — 1회성
모든 광고 DELETE → UTM 포함 URL로 재생성

전략:
  1) 그룹별 기존 광고 GET
  2) 각 광고 DELETE
  3) UTM 포함 URL로 2개 신규 CREATE
  4) 그룹별 done 진행 저장 (재개 가능)

소재 누락 방지 핵심:
  - 그룹별로 CREATE까지 성공해야 done 처리
  - CREATE 실패 시 즉시 로그 + 실패 목록에 추가
  - 종료 시 최종 검증으로 그룹당 소재 개수 확인

실행:
  python scripts/08_recreate_ads_with_utm.py --dry-run
  python scripts/08_recreate_ads_with_utm.py --test    # 1그룹만
  python scripts/08_recreate_ads_with_utm.py           # 전체 (~16분)
  python scripts/08_recreate_ads_with_utm.py --from G2_지역창업_부산
"""

import os, sys, json, logging, argparse, time
from pathlib import Path
from urllib.parse import quote

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
logger = logging.getLogger("08_recreate_ads_with_utm")

AD_GROUPS_PATH = ROOT / "data" / "ad_groups.json"
PROGRESS_FILE  = ROOT / "data" / "recreate_ads_progress.json"

CAMPAIGN_ID   = "cmp-a001-01-000000010628546"
LANDING_URL   = "https://xn--2o2bq4vzzgxncn7h11b.com"
CAMPAIGN_NAME = "royalhof_70won"

# 광고 텍스트 (UTM은 URL에 별도 결합)
AD_TEXTS = [
    {
        "headline":    "동네호프전성시대 로얄호프치킨",
        "description": "유행은 가고 결국 동네 호프의 전성시대가 돌아옵니다. 다시, 동네 호프의 시대",
    },
    {
        "headline":    "순수익 증명가능 로얄호프치킨",
        "description": "코리안스타일호프 월 수익률 34.7%, 매출 9,400만원, 순수익 3,262만원",
    },
]


# ── UTM URL 생성 ─────────────────────────────────────────────

def build_utm_url(group_key: str) -> str:
    parts = [
        "utm_source=naver",
        "utm_medium=cpc",
        f"utm_campaign={CAMPAIGN_NAME}",
        f"utm_content={quote(group_key, safe='')}",
        "utm_term={keyword}",  # Naver 매크로
    ]
    return f"{LANDING_URL}/?{'&'.join(parts)}"


# ── 진행 상태 ────────────────────────────────────────────────

def load_progress() -> set:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return set(json.load(f).get("done", []))
    return set()


def save_progress(done: set):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"done": sorted(done)}, f, ensure_ascii=False)


# ── 광고 DELETE ──────────────────────────────────────────────

def delete_ad(api: NaverAdAPI, ad_id: str) -> bool:
    try:
        api._request("DELETE", f"/ncc/ads/{ad_id}")
        return True
    except Exception as e:
        logger.error("[DEL] %s 실패: %s", ad_id, e)
        return False


# ── 메인 ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test",    action="store_true", help="첫 그룹만 처리")
    parser.add_argument("--from",    dest="from_key", default=None)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    ad_groups = json.load(open(AD_GROUPS_PATH, encoding="utf-8"))
    done = load_progress()

    sorted_keys = sorted(ad_groups.keys())
    if args.from_key:
        sorted_keys = [k for k in sorted_keys if k >= args.from_key]

    total = len(sorted_keys)
    est_minutes = total * 5 * 0.2 / 60  # 그룹당 ~5 API call

    logger.info("=== 08_recreate_ads_with_utm 시작 ===")
    logger.info("대상 그룹: %d개 (이미 처리: %d)", total, len(done))
    logger.info("예상 소요: ~%d분", int(est_minutes) + 1)

    if args.dry_run:
        sample = sorted_keys[0] if sorted_keys else "G1_메인변형"
        print(f"\n샘플 그룹: {sample}")
        print(f"샘플 URL:  {build_utm_url(sample)}")
        print(f"소재 1:    {AD_TEXTS[0]['headline']}")
        print(f"소재 2:    {AD_TEXTS[1]['headline']}")
        return

    api = NaverAdAPI(
        os.getenv("NAVER_AD_API_KEY"),
        os.getenv("NAVER_AD_SECRET_KEY"),
        os.getenv("NAVER_AD_CUSTOMER_ID"),
    )

    deleted_total = 0
    created_total = 0
    failed_groups = []

    for i, group_key in enumerate(sorted_keys):
        if group_key in done:
            continue

        group_id = ad_groups[group_key]["group_id"]
        new_url  = build_utm_url(group_key)

        # 1) 기존 광고 조회
        try:
            existing = api.get_ads_by_group(group_id)
        except Exception as e:
            logger.error("[%s] 광고 조회 실패: %s", group_key, e)
            failed_groups.append(group_key)
            continue

        # 2) 기존 광고 전부 DELETE
        delete_ok = True
        for ad in existing:
            ad_id = ad.get("nccAdId", "")
            if ad_id and delete_ad(api, ad_id):
                deleted_total += 1
            else:
                delete_ok = False

        if not delete_ok:
            logger.error("[%s] 일부 DELETE 실패 → CREATE 건너뜀", group_key)
            failed_groups.append(group_key)
            continue

        # 3) UTM URL로 2개 신규 CREATE
        ads_to_create = [{**a, "url": new_url} for a in AD_TEXTS]
        try:
            created = api.create_ads(group_id, CAMPAIGN_ID, ads_to_create)
            if len(created) < 2:
                logger.error("[%s] 소재 %d/2개만 생성됨!", group_key, len(created))
                failed_groups.append(group_key)
                continue
            created_total += len(created)
            logger.info("[%s] ✓ 삭제 %d → 생성 %d", group_key, len(existing), len(created))
        except Exception as e:
            logger.error("[%s] CREATE 실패: %s", group_key, e)
            failed_groups.append(group_key)
            continue

        done.add(group_key)
        save_progress(done)

        if args.test:
            logger.info("--test: 첫 그룹 후 종료")
            print(f"\n=== TEST 완료 ===")
            print(f"그룹: {group_key}")
            print(f"URL:  {new_url}")
            print(f"삭제: {len(existing)} / 생성: {len(created)}")
            return

        if (i + 1) % 50 == 0:
            logger.info("[진행] %d/%d 그룹 처리 (삭제 %d / 생성 %d)",
                        i + 1, total, deleted_total, created_total)

    # ── 최종 검증: 모든 그룹에 소재 2개씩 있는지 확인 ──
    logger.info("=== 최종 검증 시작 ===")
    missing = []
    for group_key in sorted_keys:
        if group_key in failed_groups:
            continue
        try:
            ads = api.get_ads_by_group(ad_groups[group_key]["group_id"])
            if len(ads) < 2:
                missing.append((group_key, len(ads)))
        except Exception as e:
            logger.warning("[검증] %s 조회 실패: %s", group_key, e)

    print("\n" + "=" * 60)
    print(f"완료: 삭제 {deleted_total}개 / 생성 {created_total}개")
    if failed_groups:
        print(f"실패: {len(failed_groups)}개 그룹 → {failed_groups[:5]}")
    if missing:
        print(f"⚠ 소재 부족: {len(missing)}개 → {missing[:5]}")
    else:
        print(f"✓ 모든 그룹에 소재 2개씩 정상 등록 확인")
    print("=" * 60)


if __name__ == "__main__":
    main()
