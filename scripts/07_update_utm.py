# -*- coding: utf-8 -*-
"""
07_update_utm.py — 1회성
모든 광고 소재 final URL에 UTM 파라미터 추가
  utm_source=naver
  utm_medium=cpc
  utm_campaign=royalhof_70won
  utm_content={그룹명}    ← 그룹별 성과 추적
  utm_term={keyword}    ← Naver 매크로, 클릭 시 실제 키워드로 치환

display URL은 기존(깨끗한 URL) 유지 → 사용자에게는 https://로얄호프치킨.com 으로 보임

실행:
  python scripts/07_update_utm.py --dry-run            # 샘플 URL 미리보기
  python scripts/07_update_utm.py --test               # 1개 광고만 테스트
  python scripts/07_update_utm.py                      # 전체 (~15분)
  python scripts/07_update_utm.py --from G2_지역창업_부산  # 특정 그룹부터 재개
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
logger = logging.getLogger("07_update_utm")

AD_GROUPS_PATH = ROOT / "data" / "ad_groups.json"
PROGRESS_FILE  = ROOT / "data" / "utm_progress.json"

LANDING_URL   = "https://xn--2o2bq4vzzgxncn7h11b.com"
CAMPAIGN_NAME = "royalhof_70won"


# ── UTM URL 생성 ─────────────────────────────────────────────

def build_utm_url(group_key: str) -> str:
    """
    그룹별 UTM이 붙은 최종 URL.
    utm_term={keyword}는 Naver가 클릭 시 실제 검색어로 치환.
    """
    parts = [
        "utm_source=naver",
        "utm_medium=cpc",
        f"utm_campaign={CAMPAIGN_NAME}",
        f"utm_content={quote(group_key, safe='')}",
        "utm_term={keyword}",
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


# ── 광고 단일 업데이트 ───────────────────────────────────────

def update_ad_url(api: NaverAdAPI, ad_id: str, new_final_url: str) -> dict:
    """기존 광고 GET → final URL만 교체 → PUT."""
    ad = api._request("GET", f"/ncc/ads/{ad_id}")

    if "ad" not in ad:
        raise ValueError(f"광고 구조 이상: {ad_id}")

    ad_inner = ad["ad"]

    if "mobile" in ad_inner:
        ad_inner["mobile"]["final"] = new_final_url
        ad_inner["mobile"].pop("punyCode", None)
    if "pc" in ad_inner:
        ad_inner["pc"]["final"] = new_final_url
        ad_inner["pc"].pop("punyCode", None)

    # PUT은 ad 필드만 보냄
    body = {"ad": ad_inner}

    return api._request(
        "PUT", f"/ncc/ads/{ad_id}",
        params={"fields": '["ad"]'},
        body=body,
    )


# ── 메인 ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="샘플 URL만 보여줌")
    parser.add_argument("--test",    action="store_true", help="첫 그룹 1개 광고만 테스트")
    parser.add_argument("--from",    dest="from_key", default=None, help="이 그룹부터 재개")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    ad_groups = json.load(open(AD_GROUPS_PATH, encoding="utf-8"))
    done = load_progress()

    sorted_keys = sorted(ad_groups.keys())
    if args.from_key:
        sorted_keys = [k for k in sorted_keys if k >= args.from_key]

    total_groups = len(sorted_keys)
    est_ads      = total_groups * 2          # 그룹당 2 소재
    est_calls    = total_groups + est_ads * 2  # list + (GET+PUT) per ad
    est_minutes  = est_calls * 0.2 / 60

    logger.info("=== 07_update_utm 시작 ===")
    logger.info("대상 그룹:    %d개", total_groups)
    logger.info("예상 광고:   ~%d개", est_ads)
    logger.info("예상 API 콜: ~%d회", est_calls)
    logger.info("예상 소요:   ~%d분", int(est_minutes) + 1)

    if args.dry_run:
        sample_key = sorted_keys[0] if sorted_keys else "G1_메인변형"
        sample_url = build_utm_url(sample_key)
        print(f"\n샘플 그룹: {sample_key}")
        print(f"샘플 URL:")
        print(f"  {sample_url}")
        print(f"\n클릭 시 GA4에 들어오는 예시 (utm_term은 검색어로 치환):")
        print(f"  utm_source=naver / utm_medium=cpc / utm_campaign={CAMPAIGN_NAME}")
        print(f"  utm_content={sample_key}")
        print(f"  utm_term=치킨창업비용 (예시)")
        return

    api = NaverAdAPI(
        os.getenv("NAVER_AD_API_KEY"),
        os.getenv("NAVER_AD_SECRET_KEY"),
        os.getenv("NAVER_AD_CUSTOMER_ID"),
    )

    updated   = 0
    skipped   = 0
    failed    = []

    for i, group_key in enumerate(sorted_keys):
        if group_key in done:
            skipped += 1
            continue

        group_id = ad_groups[group_key]["group_id"]
        new_url  = build_utm_url(group_key)

        try:
            ads = api.get_ads_by_group(group_id)
        except Exception as e:
            logger.error("[%s] 광고 목록 조회 실패: %s", group_key, e)
            failed.append(group_key)
            continue

        if not ads:
            logger.warning("[%s] 광고 없음 → 건너뜀", group_key)
            done.add(group_key)
            save_progress(done)
            continue

        group_ok = True
        for ad in ads:
            ad_id = ad.get("nccAdId", "")
            if not ad_id:
                continue
            try:
                update_ad_url(api, ad_id, new_url)
                updated += 1
            except Exception as e:
                logger.error("[%s] 광고 %s 업데이트 실패: %s", group_key, ad_id, e)
                failed.append(f"{group_key}/{ad_id}")
                group_ok = False

            if args.test:
                logger.info("--test: 1개 광고 업데이트 후 종료")
                logger.info("적용된 URL: %s", new_url)
                print("\n=== TEST 완료 ===")
                print(f"그룹:  {group_key}")
                print(f"광고:  {ad_id}")
                print(f"URL:   {new_url}")
                return

        if group_ok:
            done.add(group_key)
            save_progress(done)

        if (i + 1) % 50 == 0:
            logger.info("[진행] %d/%d 그룹 처리, %d개 광고 UTM 적용", i + 1, total_groups, updated)

    print("\n" + "=" * 60)
    print(f"완료: 광고 {updated}개 UTM 업데이트")
    print(f"건너뜀: {skipped}개 (이미 처리됨)")
    if failed:
        print(f"실패: {len(failed)}개 → {failed[:5]}")
    print("=" * 60)


if __name__ == "__main__":
    main()
