# -*- coding: utf-8 -*-
"""
weekly_pruner.py — 매주 월요일 06:00 KST 실행

수행 작업:
  1. 전체 광고그룹 키워드 조회
  2. 최근 14일 성과 통계 수집
  3. 룰 기반 컷 실행
     - CPC > 200원 → DELETE
     - CTR < 1% + 노출 10,000+ → DELETE
     - 7일 노출 0 (bid=70원) → 입찰가 100원으로 인상
     - 14일 노출 0 (bid=100원) → DELETE
  4. 예비 풀에서 보충 등록
  5. 예비 풀 잔량 < 10만개 → 경고
  6. 보고서 생성 및 슬랙 전송

[안전장치] PROTECTED_CAMPAIGN_IDS (.env)에 등록된 기존 파워링크 캠페인은
절대 수정/삭제하지 않음. ad_groups.json에 보호 대상 ID가 포함되어 있으면
시작 시 즉시 중단.
"""

import os
import sys
import json
import logging
import argparse
from datetime import date, timedelta
from pathlib import Path

# Windows 콘솔(cp949)에서 em-dash·이모지 출력 깨짐 방지
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

from dotenv import load_dotenv

# ──────────────────────────────────────────────
# 경로 설정
# ──────────────────────────────────────────────
ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.naver_api import NaverAdAPI
from lib.reserve_pool import (
    init_db, get_pool_size, get_available_keywords,
    mark_registered, mark_deleted, get_stats as pool_stats,
)
from lib.reporter import generate_weekly_report, send_to_slack

# ──────────────────────────────────────────────
# 로깅 설정
# ──────────────────────────────────────────────
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
logger = logging.getLogger("weekly_pruner")

# ──────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────
MAX_BID = 200          # 이 초과 시 즉시 삭제
MIN_CTR = 1.0          # % 미만 (노출 10K+ 조건과 함께)
CTR_MIN_IMP = 10_000   # CTR 조건 적용 최소 노출
BID_START = 70         # 초기 입찰가
BID_STEP = 100         # 7일 노출 0 시 인상 값
RESERVE_WARN = 100_000 # 예비 풀 경고 임계값
GROUP_MAX = 1_000      # 그룹당 키워드 한도


def load_env():
    load_dotenv(ROOT / ".env")
    required = ["NAVER_AD_API_KEY", "NAVER_AD_SECRET_KEY", "NAVER_AD_CUSTOMER_ID"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        logger.error("필수 환경변수 없음: %s", missing)
        sys.exit(1)

    # 기존 파워링크 캠페인 보호 목록 (쉼표 구분)
    protected_raw = os.getenv("PROTECTED_CAMPAIGN_IDS", "")
    protected_ids = {cid.strip() for cid in protected_raw.split(",") if cid.strip()}

    return {
        "api_key":        os.getenv("NAVER_AD_API_KEY"),
        "secret_key":     os.getenv("NAVER_AD_SECRET_KEY"),
        "customer_id":    os.getenv("NAVER_AD_CUSTOMER_ID"),
        "slack_url":      os.getenv("SLACK_WEBHOOK_URL", ""),
        "protected_ids":  protected_ids,
    }


def validate_no_protected_campaigns(ad_groups: dict, protected_ids: set):
    """
    ad_groups.json에 기존 파워링크 캠페인 ID가 포함되어 있으면 즉시 중단.
    실수로 기존 캠페인을 건드리는 것을 원천 차단.
    """
    if not protected_ids:
        logger.warning(
            "[안전장치] PROTECTED_CAMPAIGN_IDS 미설정. "
            ".env에 기존 파워링크 캠페인 ID를 등록하는 것을 강력 권장."
        )
        return

    violations = []
    for group_key, info in ad_groups.items():
        cid = info.get("campaign_id", "")
        if cid in protected_ids:
            violations.append(f"{group_key} → campaign_id={cid}")

    if violations:
        logger.critical(
            "!!! 보호된 파워링크 캠페인 ID가 ad_groups.json에 포함되어 있습니다 !!!\n"
            "실행을 중단합니다. 아래 항목을 확인하세요:\n%s",
            "\n".join(violations),
        )
        sys.exit(1)

    logger.info("[안전장치] 보호 캠페인 검증 통과 (보호 대상 %d개)", len(protected_ids))


def load_ad_groups() -> dict:
    path = ROOT / "data" / "ad_groups.json"
    if not path.exists():
        logger.error("ad_groups.json 없음: %s", path)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ──────────────────────────────────────────────
# 컷 룰 적용
# ──────────────────────────────────────────────

def apply_cut_rules(
    keyword: dict,
    stat: dict,
) -> str:
    """
    키워드 하나에 룰 적용.
    반환값: 'delete' | 'bump_bid' | 'keep' | 'lock'
    """
    bid = keyword.get("bidAmt", BID_START)
    imp_14d = stat.get("impressions_14d", 0)
    imp_7d  = stat.get("impressions_7d", 0)
    clk_14d = stat.get("clicks_14d", 0)
    cost_14d = stat.get("cost_14d", 0)

    # CPC 계산 (클릭이 있을 때만)
    cpc = cost_14d // clk_14d if clk_14d > 0 else 0
    # CTR 계산 (노출이 있을 때만)
    ctr = (clk_14d / imp_14d * 100) if imp_14d > 0 else 0.0

    # 룰 1: CPC > 200원 → 즉시 삭제
    if cpc > MAX_BID:
        logger.debug("CPC 초과 삭제: %s (CPC=%d원)", keyword.get("keyword"), cpc)
        return "delete"

    # 룰 2: CTR < 1% + 노출 10,000+ → 삭제
    if imp_14d >= CTR_MIN_IMP and ctr < MIN_CTR:
        logger.debug("CTR 미달 삭제: %s (CTR=%.2f%%, 노출=%d)", keyword.get("keyword"), ctr, imp_14d)
        return "delete"

    # 룰 3: 14일 노출 0 + bid=100원 → 삭제 (이전 주에 이미 인상됨)
    if imp_14d == 0 and bid >= BID_STEP:
        logger.debug("14일 노출 0 삭제: %s (bid=%d원)", keyword.get("keyword"), bid)
        return "delete"

    # 룰 4: 7일 노출 0 + bid=70원 → 입찰가 인상
    if imp_7d == 0 and bid == BID_START:
        logger.debug("7일 노출 0 bid 인상: %s", keyword.get("keyword"))
        return "bump_bid"

    return "keep"


# ──────────────────────────────────────────────
# 그룹 처리
# ──────────────────────────────────────────────

def process_group(
    api: NaverAdAPI,
    group_key: str,
    group_info: dict,
    dry_run: bool = False,
) -> dict:
    """
    그룹 하나에 대해 컷/인상/보충 수행.
    반환: {deleted, bumped, replenished, keywords_after}
    """
    group_id   = group_info["group_id"]
    campaign_id = group_info["campaign_id"]

    logger.info("=== 그룹 처리 시작: %s (%s) ===", group_key, group_id)

    # 1. 키워드 목록 조회
    keywords = api.get_keywords_by_group(group_id)
    if not keywords:
        logger.info("  키워드 없음 — 건너뜀")
        return {"deleted": 0, "bumped": 0, "replenished": 0, "keywords_after": 0}

    kw_ids = [kw["nccKeywordId"] for kw in keywords if kw.get("nccKeywordId")]

    # 2. 최근 14일 통계
    stats = api.get_stats(kw_ids, days=14)

    # 3. 룰 적용
    to_delete = []
    to_bump = []
    deleted_kw_texts = []

    for kw in keywords:
        kw_id = kw.get("nccKeywordId")
        kw_text = kw.get("keyword", "")
        if not kw_id:
            continue
        stat = stats.get(kw_id, {})
        action = apply_cut_rules(kw, stat)

        if action == "delete":
            to_delete.append(kw_id)
            deleted_kw_texts.append(kw_text)
        elif action == "bump_bid":
            to_bump.append(kw_id)

    logger.info("  삭제 예정: %d개 / 입찰 인상 예정: %d개", len(to_delete), len(to_bump))

    if not dry_run:
        # 4. 삭제 실행
        for kw_id in to_delete:
            api.delete_keyword(kw_id)
        if deleted_kw_texts:
            mark_deleted(deleted_kw_texts)

        # 5. 입찰 인상
        for kw_id in to_bump:
            api.update_bid(kw_id, BID_STEP)

    # 6. 보충 — 삭제한 수만큼 예비 풀에서 가져옴
    replenished = 0
    if not dry_run and to_delete:
        available = get_available_keywords(group_key, limit=len(to_delete))
        if available:
            new_kws = [r["keyword"] for r in available]
            registered = api.register_keywords(group_id, campaign_id, new_kws, bid=BID_START)
            if registered:
                mark_registered([r["id"] for r in available[: len(registered)]])
                replenished = len(registered)
                logger.info("  보충 완료: %d개", replenished)
        else:
            logger.warning("  예비 풀 '%s' 가용 키워드 없음", group_key)

    keywords_after = len(keywords) - len(to_delete) + replenished

    return {
        "deleted": len(to_delete),
        "bumped": len(to_bump),
        "replenished": replenished,
        "keywords_after": keywords_after,
        "deleted_kw_texts": deleted_kw_texts if dry_run else [],
    }


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="로얄호프치킨 주간 키워드 정리")
    parser.add_argument("--dry-run", action="store_true", help="실제 API 호출 없이 시뮬레이션")
    args = parser.parse_args()

    if args.dry_run:
        logger.info("=== DRY RUN 모드 — 실제 변경 없음 ===")

    env = load_env()
    api = NaverAdAPI(env["api_key"], env["secret_key"], env["customer_id"])

    init_db()
    ad_groups = load_ad_groups()

    # ★ 기존 파워링크 캠페인 보호 검증 — 절대 건너뛰지 말 것
    validate_no_protected_campaigns(ad_groups, env["protected_ids"])

    week_end = date.today() - timedelta(days=1)
    week_start = week_end - timedelta(days=13)  # 14일 기준

    summary = {
        "week_start": str(week_start),
        "week_end":   str(week_end),
        "deleted_cpc": 0,
        "deleted_ctr": 0,
        "deleted_no_imp": 0,
        "bumped_bid": 0,
        "replenished": 0,
        "total_keywords": 0,
        "reserve_pool_size": 0,
        "group_summary": [],
        "top_keywords": [],
    }

    # 그룹별 처리
    for group_key, group_info in ad_groups.items():
        try:
            result = process_group(api, group_key, group_info, dry_run=args.dry_run)
            summary["bumped_bid"]    += result["bumped"]
            summary["replenished"]   += result["replenished"]
            summary["total_keywords"] += result["keywords_after"]
            # 삭제 유형별 분류는 process_group에서 세분화하지 않으므로 deleted_no_imp에 합산
            summary["deleted_no_imp"] += result["deleted"]
            summary["group_summary"].append({
                "name":     group_key,
                "keywords": result["keywords_after"],
                "deleted":  result["deleted"],
                "added":    result["replenished"],
            })
        except Exception as e:
            logger.error("그룹 %s 처리 중 오류: %s", group_key, e)

    # 예비 풀 잔량
    pool_size = get_pool_size()
    summary["reserve_pool_size"] = pool_size
    if pool_size < RESERVE_WARN:
        logger.warning("⚠️ 예비 풀 잔량 %d개 — 10만개 미만! keywordstool 재펼침 필요", pool_size)

    # 보고서 생성
    report_path = generate_weekly_report(summary)
    logger.info("보고서 저장: %s", report_path)

    # 슬랙 전송
    if env.get("slack_url"):
        send_to_slack(report_path, env["slack_url"], summary)

    # 풀 상태 로그
    ps = pool_stats()
    logger.info("예비 풀 상태: %s", ps)
    logger.info("=== weekly_pruner 완료 ===")


if __name__ == "__main__":
    main()
