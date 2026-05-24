# -*- coding: utf-8 -*-
"""
10_extensions_all_groups.py — 1회성
G1_메인변형의 9개 확장소재를 나머지 975개 그룹에 일괄 복제 등록

원리:
  1) G1_메인변형 그룹의 모든 확장소재 GET
  2) 깨끗한 템플릿으로 가공 (읽기전용 필드 제거)
  3) 나머지 그룹마다 9개 POST
  4) 그룹 단위로 진행상황 저장 (재개 가능)
  5) 개별 확장소재 실패는 로그만 남기고 계속 진행

실행:
  python scripts/10_extensions_all_groups.py --dry-run
  python scripts/10_extensions_all_groups.py --test       # 1그룹만
  python scripts/10_extensions_all_groups.py              # 전체 (~30분)
  python scripts/10_extensions_all_groups.py --from G2_지역창업_부산
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
logger = logging.getLogger("10_extensions")

AD_GROUPS_PATH = ROOT / "data" / "ad_groups.json"
PROGRESS_FILE  = ROOT / "data" / "extensions_progress.json"
SOURCE_GROUP   = "G1_메인변형"


def load_progress() -> set:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return set(json.load(f).get("done", []))
    return set()


def save_progress(done: set):
    tmp = PROGRESS_FILE.with_suffix(".json.tmp")
    for attempt in range(3):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"done": sorted(done)}, f, ensure_ascii=False)
            os.replace(tmp, PROGRESS_FILE)
            return
        except OSError:
            if attempt < 2:
                time.sleep(0.5)


def fetch_templates(api: NaverAdAPI, source_group_id: str) -> list[dict]:
    """G1 그룹의 확장소재 → POST 가능한 깨끗한 템플릿으로 변환."""
    exts = api._request("GET", "/ncc/ad-extensions", params={"ownerId": source_group_id})
    items = exts if isinstance(exts, list) else exts.get("items", [])

    templates = []
    for ext in items:
        tmpl = {
            "type":         ext.get("type"),
            "adExtension":  ext.get("adExtension"),
            "pcChannelId":  ext.get("pcChannelId"),
            "mobileChannelId": ext.get("mobileChannelId"),
        }
        # null/빈 값 정리
        if tmpl["adExtension"] is None:
            logger.warning("type=%s 의 adExtension이 null → 건너뜀", tmpl["type"])
            continue
        templates.append(tmpl)
    return templates


def create_extension(api: NaverAdAPI, target_group_id: str, tmpl: dict) -> str:
    body = {**tmpl, "ownerId": target_group_id}
    r = api._request("POST", "/ncc/ad-extensions", body=body)
    return r.get("nccAdExtensionId", "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test",    action="store_true")
    parser.add_argument("--from",    dest="from_key", default=None)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    api = NaverAdAPI(
        os.getenv("NAVER_AD_API_KEY"),
        os.getenv("NAVER_AD_SECRET_KEY"),
        os.getenv("NAVER_AD_CUSTOMER_ID"),
    )

    ad_groups = json.load(open(AD_GROUPS_PATH, encoding="utf-8"))
    done = load_progress()

    # G1 → 템플릿
    source_id = ad_groups[SOURCE_GROUP]["group_id"]
    templates = fetch_templates(api, source_id)
    logger.info("템플릿 %d개 로드 (G1_메인변형)", len(templates))
    for t in templates:
        logger.info("  - type=%s", t["type"])

    # 대상 그룹 = G1 제외 전체
    sorted_keys = sorted([k for k in ad_groups.keys() if k != SOURCE_GROUP])
    if args.from_key:
        sorted_keys = [k for k in sorted_keys if k >= args.from_key]
    targets = [k for k in sorted_keys if k not in done]

    total_calls = len(targets) * len(templates)
    est_minutes = total_calls * 0.2 / 60

    logger.info("=== 10_extensions 시작 ===")
    logger.info("대상 그룹: %d개 (이미 처리: %d)", len(targets), len(done))
    logger.info("그룹당 확장소재: %d개", len(templates))
    logger.info("총 API 호출:    ~%d회", total_calls)
    logger.info("예상 소요:      ~%d분", int(est_minutes) + 1)

    if args.dry_run:
        print(f"\n[DRY-RUN]")
        print(f"  G1 템플릿: {len(templates)}개")
        for t in templates:
            print(f"    - {t['type']}")
        print(f"  대상 그룹: {len(targets)}개")
        print(f"  총 호출:  ~{total_calls}회 (~{int(est_minutes)+1}분)")
        return

    created = 0
    failed_exts = []  # (group_key, type, error)

    for i, group_key in enumerate(targets):
        group_id = ad_groups[group_key]["group_id"]
        ext_ok = 0
        ext_fail = 0
        for tmpl in templates:
            try:
                ext_id = create_extension(api, group_id, tmpl)
                if ext_id:
                    created += 1
                    ext_ok += 1
            except Exception as e:
                err = str(e)
                # 중복(2023)은 정상 - 이미 있다는 뜻
                if "2023" in err or "already exists" in err.lower():
                    ext_ok += 1
                else:
                    failed_exts.append((group_key, tmpl["type"], err[-150:]))
                    ext_fail += 1

        done.add(group_key)
        save_progress(done)
        logger.info("[%s] ok=%d fail=%d", group_key, ext_ok, ext_fail)

        if args.test:
            print(f"\n=== TEST 완료 ===")
            print(f"그룹: {group_key}")
            print(f"성공: {ext_ok}/{len(templates)}")
            if ext_fail:
                print(f"실패: {ext_fail}개")
                for gk, t, e in failed_exts:
                    print(f"  - {t}: {e[:80]}")
            return

        if (i + 1) % 50 == 0:
            logger.info("[진행] %d/%d 그룹, 누적 %d개 확장소재 생성",
                        i + 1, len(targets), created)

    print("\n" + "=" * 60)
    print(f"완료: {created}개 확장소재 생성")
    if failed_exts:
        print(f"실패: {len(failed_exts)}건")
        # 타입별 실패 집계
        from collections import Counter
        type_fails = Counter(t for _, t, _ in failed_exts)
        for tp, cnt in type_fails.most_common():
            print(f"  - {tp}: {cnt}건")
    print("=" * 60)


if __name__ == "__main__":
    main()
