# -*- coding: utf-8 -*-
"""
meta_00_discover.py — 1회성 / 필요 시 재실행

Meta 광고 계정의 캠페인 + ad set + ad를 스캔해
data/meta_ad_sets.json의 `discovered` 영역에 저장하고,
검토용 요약(캠페인 예산 / 광고 개수 / 픽셀 이벤트)을 콘솔에 출력한다.

자격증명 검증 + 자동화 대상 식별용. 실제 변경은 하지 않음.

사용:
  python scripts/meta_00_discover.py                # 발견 + 검토 출력
  python scripts/meta_00_discover.py --enable ADSET_ID --alias LLA_1pct --budget 40000
                                                    # discover + 자동화 대상으로 등록
  python scripts/meta_00_discover.py --disable ADSET_ID
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime
from pathlib import Path

# Windows 콘솔(cp949)에서 em-dash·이모지 출력 시 깨지는 것 방지
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.meta_api import MetaAdsAPI

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
logger = logging.getLogger("meta_discover")

CONFIG_PATH = ROOT / "data" / "meta_ad_sets.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8-sig") as f:
            return json.load(f)
    return {"_comment": "scripts/meta_00_discover.py가 갱신합니다.",
            "managed": [], "discovered": [], "last_discovered_at": None}


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[CONFIG] %s 저장", CONFIG_PATH)


def parse_promoted_object(po) -> str:
    """promoted_object dict → 'LEAD@925376...' 형태 요약."""
    if not po:
        return "(없음)"
    if isinstance(po, str):
        try:
            po = json.loads(po)
        except (ValueError, TypeError):
            return str(po)
    event = po.get("custom_event_type", "") or po.get("object_store_url", "") or "?"
    pixel = po.get("pixel_id", "")
    return f"{event}@{pixel}" if pixel else str(event)


def print_table(discovered: list[dict], managed_ids: set):
    print()
    print("=" * 116)
    print(f"{'ad_set_id':<20} {'이름':<24} {'상태':<9} {'광고수':>5} {'픽셀이벤트':<24} {'관리':>5}")
    print("=" * 116)
    for a in discovered:
        is_managed = "O" if a["ad_set_id"] in managed_ids else ""
        name = (a.get("name") or "")
        name_disp = name[:22] + ("…" if len(name) > 22 else "")
        print(
            f"{a['ad_set_id']:<20} "
            f"{name_disp:<24} "
            f"{(a.get('effective_status') or ''):<9} "
            f"{a.get('ad_count', 0):>5} "
            f"{parse_promoted_object(a.get('promoted_object')):<24} "
            f"{is_managed:>5}"
        )
    print("=" * 116)


def main():
    parser = argparse.ArgumentParser(description="Meta 광고 자산 발견 + 자동화 대상 등록")
    parser.add_argument("--enable", help="자동화 대상에 추가할 ad_set_id")
    parser.add_argument("--alias",  default="", help="--enable과 함께. 별칭")
    parser.add_argument("--budget", type=int, default=0, help="--enable과 함께. 목표 일 예산(원)")
    parser.add_argument("--disable", help="자동화 대상에서 제거할 ad_set_id")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    api = MetaAdsAPI.from_env()

    # 1. 자격증명 검증
    logger.info("[STEP 1] 계정 검증 - 어제 1일 통계 조회")
    summary = api.get_account_summary(days=1)
    logger.info("[STEP 1] OK - 어제 노출 %s / 클릭 %s / 지출 %s원",
                f"{summary['impressions']:,}", f"{summary['clicks']:,}", f"{int(summary['spend']):,}")

    # 2. 캠페인 발견
    logger.info("[STEP 2] 캠페인 발견")
    campaigns = api.discover_campaigns()
    cmp_by_id = {c["id"]: c for c in campaigns}

    # 3. ad set 발견 + 각 ad set의 광고 개수
    logger.info("[STEP 3] ad set 발견")
    raw_ad_sets = api.discover_ad_sets()

    discovered = []
    for a in raw_ad_sets:
        cid = a.get("campaign_id", "")
        budget_raw = a.get("daily_budget", 0)
        try:
            budget_krw = int(budget_raw) if budget_raw else 0
        except (TypeError, ValueError):
            budget_krw = 0

        ad_set_id = a.get("id", "")
        try:
            ads = api.discover_ads(ad_set_id)
        except Exception as e:
            logger.warning("  ad 조회 실패 %s: %s", ad_set_id, e)
            ads = []

        discovered.append({
            "ad_set_id":         ad_set_id,
            "name":              a.get("name", ""),
            "campaign_id":       cid,
            "campaign_name":     cmp_by_id.get(cid, {}).get("name", ""),
            "status":            a.get("status", ""),
            "effective_status":  a.get("effective_status", ""),
            "daily_budget_krw":  budget_krw,
            "optimization_goal": a.get("optimization_goal", ""),
            "billing_event":     a.get("billing_event", ""),
            "promoted_object":   a.get("promoted_object"),
            "ad_count":          len(ads),
            "ads":               [{"id": x.get("id"), "name": x.get("name"),
                                    "status": x.get("effective_status")} for x in ads],
            "created_time":      str(a.get("created_time", "")),
            "start_time":        str(a.get("start_time", "")),
        })

    # 4. 설정 갱신
    cfg = load_config()
    cfg["discovered"] = discovered
    cfg["last_discovered_at"] = datetime.now().isoformat(timespec="seconds")
    managed = cfg.get("managed", [])
    managed_ids = {m["ad_set_id"] for m in managed}

    # 5. enable / disable
    if args.enable:
        if args.enable not in {d["ad_set_id"] for d in discovered}:
            logger.error("--enable ad_set_id가 계정에 없음: %s", args.enable)
            sys.exit(1)
        if args.enable in managed_ids:
            logger.warning("이미 managed에 있음: %s", args.enable)
        else:
            managed.append({"ad_set_id": args.enable, "alias": args.alias or args.enable,
                            "enabled": True, "target_daily_budget_krw": args.budget or 0})
            managed_ids.add(args.enable)
            logger.info("[ENABLE] %s (alias=%s) 자동화 대상 추가", args.enable, args.alias or "-")
        cfg["managed"] = managed

    if args.disable:
        before = len(managed)
        managed = [m for m in managed if m["ad_set_id"] != args.disable]
        cfg["managed"] = managed
        managed_ids = {m["ad_set_id"] for m in managed}
        if len(managed) < before:
            logger.info("[DISABLE] %s 제거", args.disable)
        else:
            logger.warning("disable 대상이 managed에 없음: %s", args.disable)

    save_config(cfg)

    # 6. 검토용 출력
    print()
    print("#" * 60)
    print("# 캠페인")
    print("#" * 60)
    for c in campaigns:
        budget = c.get("daily_budget") or c.get("lifetime_budget") or 0
        try:
            budget = int(budget)
        except (TypeError, ValueError):
            budget = 0
        budget_kind = "캠페인 일예산(CBO)" if c.get("daily_budget") else (
            "캠페인 총예산" if c.get("lifetime_budget") else "광고세트별 예산(ABO)")
        print(f"  - {c.get('name','')}")
        print(f"      ID: {c.get('id','')}")
        print(f"      목표(objective): {c.get('objective','')}")
        print(f"      상태: {c.get('effective_status','')}")
        print(f"      예산: {budget:,}원  [{budget_kind}]")

    print_table(discovered, managed_ids)

    print()
    print(f"발견: 캠페인 {len(campaigns)}개 / ad set {len(discovered)}개 / 자동화 대상 {len(managed)}개")
    print()
    if not managed:
        print("[다음] 자동화 대상이 비어있습니다. 위 ad_set_id로 등록하세요:")
        print("  python scripts/meta_00_discover.py --enable <ad_set_id> --alias LLA_1pct")
        print("  python scripts/meta_00_discover.py --enable <ad_set_id> --alias LLA_3pct")


if __name__ == "__main__":
    main()
