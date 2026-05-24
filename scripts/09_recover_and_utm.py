# -*- coding: utf-8 -*-
"""
09_recover_and_utm.py — 1회성
1) 빈 그룹(광고 0개)에 UTM 포함 광고 2개 복구 생성
2) 옵션으로 정상 그룹(소재 2개 있음)도 DELETE→재생성하여 UTM 적용

안전장치:
  - 그룹별 atomic 처리: DELETE → 즉시 CREATE → 검증 → 다음
  - CREATE 1번이라도 실패 시 즉시 중단 (확산 방지)
  - 진행상태 파일로 재개 가능
  - --dry-run으로 계획 미리 확인

핵심 fix:
  - DELETE 204는 성공 (naver_api.py 패치 완료)
  - CREATE 시 'alternativeKeyword' 필드 필수 ({keyword} 매크로 사용 시)

실행:
  python scripts/09_recover_and_utm.py --dry-run            # 계획만
  python scripts/09_recover_and_utm.py --empty-only         # 빈 그룹만 복구 ★ 우선
  python scripts/09_recover_and_utm.py --empty-only --test  # 빈 그룹 중 1개만
  python scripts/09_recover_and_utm.py                      # 전체 (빈 그룹 복구 + 정상 그룹 UTM 적용)
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
logger = logging.getLogger("09_recover")

AD_GROUPS_PATH = ROOT / "data" / "ad_groups.json"
PROGRESS_FILE  = ROOT / "data" / "recover_progress.json"

CAMPAIGN_ID    = "cmp-a001-01-000000010628546"
LANDING_URL    = "https://xn--2o2bq4vzzgxncn7h11b.com"
DISPLAY_URL    = "https://xn--2o2bq4vzzgxncn7h11b.com"  # 사용자에게 보이는 깨끗한 URL
CAMPAIGN_NAME  = "royalhof_70won"
ALT_KEYWORD    = "치킨창업"  # {keyword} 매크로 fallback

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


def build_utm_url(group_key: str) -> str:
    parts = [
        "utm_source=naver",
        "utm_medium=cpc",
        f"utm_campaign={CAMPAIGN_NAME}",
        f"utm_content={quote(group_key, safe='')}",
        "utm_term={keyword}",
    ]
    return f"{LANDING_URL}/?{'&'.join(parts)}"


def load_progress() -> set:
    if PROGRESS_FILE.exists():
        return set(json.load(open(PROGRESS_FILE, encoding="utf-8")).get("done", []))
    return set()


def save_progress(done: set):
    """atomic write: 임시 파일에 쓰고 rename. Windows Errno 22 회피."""
    tmp = PROGRESS_FILE.with_suffix(".json.tmp")
    for attempt in range(3):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"done": sorted(done)}, f, ensure_ascii=False)
            # Windows에서 기존 파일이 있으면 rename 실패 → replace 사용
            os.replace(tmp, PROGRESS_FILE)
            return
        except OSError as e:
            if attempt < 2:
                time.sleep(0.5)
            else:
                logger.warning("[저장] progress 저장 실패 (계속 진행): %s", e)


def create_ad_with_utm(api: NaverAdAPI, group_id: str, group_key: str, text: dict) -> dict:
    """단일 광고 생성. alternativeKeyword 포함, UTM URL 사용."""
    url = build_utm_url(group_key)
    body = {
        "nccAdgroupId":  group_id,
        "nccCampaignId": CAMPAIGN_ID,
        "type": "TEXT_45",
        "ad": {
            "headline":           text["headline"],
            "description":        text["description"],
            "alternativeKeyword": ALT_KEYWORD,
            "mobile": {"final": url, "display": DISPLAY_URL},
            "pc":     {"final": url, "display": DISPLAY_URL},
        },
    }
    return api._request("POST", "/ncc/ads",
                        params={"nccAdgroupId": group_id}, body=body)


def process_group(api: NaverAdAPI, group_key: str, group_id: str, delete_first: bool) -> tuple[int, int]:
    """
    그룹 단위 atomic 처리.
    delete_first=True면 기존 광고 삭제 후 재생성.
    반환: (deleted, created)
    """
    deleted = 0
    if delete_first:
        existing = api.get_ads_by_group(group_id)
        for ad in existing:
            ad_id = ad.get("nccAdId", "")
            if ad_id:
                api._request("DELETE", f"/ncc/ads/{ad_id}")
                deleted += 1

    # 현재 광고 헤드라인 목록 (중복 회피용)
    existing_now = api.get_ads_by_group(group_id)
    existing_headlines = {
        (a.get("ad", {}).get("headline", "") or "").strip() for a in existing_now
    }

    # 2개 CREATE (이미 있는 헤드라인은 건너뜀)
    created = 0
    for text in AD_TEXTS:
        if text["headline"] in existing_headlines:
            logger.info("[%s] '%s' 이미 존재 → 건너뜀", group_key, text["headline"])
            continue
        try:
            result = create_ad_with_utm(api, group_id, group_key, text)
            if result.get("nccAdId"):
                created += 1
        except Exception as e:
            # 3822 (동일 내용 존재)는 무시
            if "3822" in str(e) or "already exists" in str(e).lower():
                logger.warning("[%s] 동일 광고 이미 존재 (3822) → 무시", group_key)
            else:
                raise

    # 검증: 정말 2개가 있는지
    final = api.get_ads_by_group(group_id)
    if len(final) < 2:
        raise RuntimeError(f"검증 실패: 그룹에 {len(final)}개만 존재 (예상 2개)")

    return deleted, created


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--empty-only", action="store_true",
                        help="빈 그룹만 복구 (정상 그룹 건드리지 않음) ★ 안전")
    parser.add_argument("--test",       action="store_true",
                        help="첫 1그룹만 처리")
    parser.add_argument("--from",       dest="from_key", default=None)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    api = NaverAdAPI(
        os.getenv("NAVER_AD_API_KEY"),
        os.getenv("NAVER_AD_SECRET_KEY"),
        os.getenv("NAVER_AD_CUSTOMER_ID"),
    )

    ad_groups = json.load(open(AD_GROUPS_PATH, encoding="utf-8"))
    done = load_progress()

    # ── 1단계: 전 그룹 광고 개수 조회 ──────────────────────
    logger.info("=== 그룹별 광고 개수 조회 (전수 검사) ===")
    empty_groups   = []
    partial_groups = []
    ok_groups      = []

    sorted_keys = sorted(ad_groups.keys())
    if args.from_key:
        sorted_keys = [k for k in sorted_keys if k >= args.from_key]

    for i, key in enumerate(sorted_keys):
        try:
            ads = api.get_ads_by_group(ad_groups[key]["group_id"])
            n = len(ads)
            if n == 0:
                empty_groups.append(key)
            elif n < 2:
                partial_groups.append(key)
            else:
                ok_groups.append(key)
        except Exception as e:
            logger.warning("[%s] 조회 실패: %s", key, e)
        if (i + 1) % 100 == 0:
            logger.info("  [%d/%d] empty=%d partial=%d ok=%d",
                        i + 1, len(sorted_keys), len(empty_groups), len(partial_groups), len(ok_groups))

    logger.info("=== 조사 결과 ===")
    logger.info("정상(2+개): %d", len(ok_groups))
    logger.info("부족(1개):  %d", len(partial_groups))
    logger.info("없음(0개):  %d", len(empty_groups))

    # 처리 대상 결정
    if args.empty_only:
        targets = empty_groups + partial_groups
        mode = "복구 전용 (빈/부족 그룹만)"
    else:
        targets = empty_groups + partial_groups + ok_groups
        mode = "전체 (빈 그룹 복구 + 정상 그룹 UTM 적용)"

    targets = [k for k in targets if k not in done]

    logger.info("처리 대상: %d개 (%s)", len(targets), mode)
    logger.info("이미 처리: %d개", len(done))

    if args.dry_run:
        print(f"\n[DRY-RUN]")
        print(f"  복구 대상 (빈 그룹):     {len(empty_groups)}개")
        print(f"  복구 대상 (부족 그룹):    {len(partial_groups)}개")
        print(f"  UTM 재적용 대상 (정상):  {len(ok_groups)}개")
        print(f"  총 처리 대상:            {len(targets)}개")
        if empty_groups:
            print(f"\n빈 그룹 예시:")
            for g in empty_groups[:10]:
                print(f"  - {g}")
        return

    # ── 2단계: 처리 ─────────────────────────────────────
    deleted_total = 0
    created_total = 0

    for i, group_key in enumerate(targets):
        group_id = ad_groups[group_key]["group_id"]
        is_empty = group_key in empty_groups or group_key in partial_groups
        delete_first = not is_empty  # 빈 그룹은 DELETE 안 함

        try:
            d, c = process_group(api, group_key, group_id, delete_first)
            deleted_total += d
            created_total += c
            done.add(group_key)
            save_progress(done)
            logger.info("[%s] ✓ del=%d create=%d", group_key, d, c)
        except Exception as e:
            logger.error("[%s] 처리 실패: %s → 즉시 중단 (확산 방지)", group_key, e)
            print(f"\n!! 중단: [{group_key}] {e}")
            print(f"진행: {i}/{len(targets)} 그룹 처리")
            print(f"누적: 삭제 {deleted_total} / 생성 {created_total}")
            return

        if args.test:
            logger.info("--test: 1그룹 후 종료")
            print(f"\n=== TEST 완료 ===")
            print(f"그룹: {group_key}")
            print(f"  삭제 {d} / 생성 {c}")
            print(f"URL:  {build_utm_url(group_key)}")
            return

        if (i + 1) % 50 == 0:
            logger.info("[진행] %d/%d (del=%d, create=%d)", i + 1, len(targets), deleted_total, created_total)

    print("\n" + "=" * 60)
    print(f"완료: 삭제 {deleted_total}개 / 생성 {created_total}개")
    print("=" * 60)


if __name__ == "__main__":
    main()
