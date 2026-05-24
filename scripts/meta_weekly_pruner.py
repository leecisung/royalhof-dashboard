# -*- coding: utf-8 -*-
"""
meta_weekly_pruner.py — 매주 월요일 06:00 KST 실행 (네이버 weekly_pruner와 동일 시간 권장)

수행 작업:
  1. meta_ad_sets.json의 managed 영역에서 enabled=true인 ad set만 대상
  2. 각 ad set의 7일 인사이트 조회 (impressions, clicks, spend, conversions, cpa, frequency)
  3. 룰 적용:
     - 학습기간 보호 (생성 N일 안): pause 룰 면제
     - CPA > META_CPA_PAUSE_THRESHOLD                → pause
     - CPA > META_CPA_REDUCE_THRESHOLD + spend > 5만 → 예산 50% 삭감
     - CPA < META_CPA_BOOST_THRESHOLD + conv >= 5   → 예산 30% 증액 (상한 META_BUDGET_CAP_PER_ADSET_KRW)
     - frequency > META_FREQUENCY_FATIGUE           → flag_fatigue (자동 액션 X, 보고서에 경고)
     - impressions == 0                              → flag_zero_imp (자동 액션 X)
     - otherwise                                    → keep
  4. meta_state.db에 결정 이력 기록
  5. 보고서 생성 (reports/weekly_meta_YYYYMMDD.md) + 슬랙 전송

[안전장치] META_PROTECTED_CAMPAIGN_IDS(.env)에 등록된 캠페인 소속 ad set은
MetaAdsAPI 내부에서 자동으로 변경 차단. 추가 검증 없음.
"""

import os
import sys
import json
import sqlite3
import logging
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

# Windows 콘솔(cp949)에서 em-dash·이모지 출력 깨짐 방지
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.meta_api import MetaAdsAPI, MetaProtectedError
from lib.reporter import generate_meta_weekly_report, send_meta_to_slack

# ──────────────────────────────────────────────
# 경로 / 로깅
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
logger = logging.getLogger("meta_weekly_pruner")

CONFIG_PATH = ROOT / "data" / "meta_ad_sets.json"
STATE_DB    = ROOT / "data" / "meta_state.db"


# ──────────────────────────────────────────────
# 상태 DB
# ──────────────────────────────────────────────

def init_state_db():
    con = sqlite3.connect(STATE_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ad_set_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ad_set_id TEXT NOT NULL,
            alias TEXT,
            action TEXT,
            cpa REAL,
            frequency REAL,
            spend_7d REAL,
            conversions_7d INTEGER,
            impressions_7d INTEGER,
            budget_before INTEGER,
            budget_after INTEGER,
            reason TEXT,
            dry_run INTEGER,
            created_at TEXT
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_history_adset ON ad_set_history(ad_set_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_history_created ON ad_set_history(created_at)")
    con.commit()
    con.close()


def record_history(row: dict):
    con = sqlite3.connect(STATE_DB)
    con.execute("""
        INSERT INTO ad_set_history (
            ad_set_id, alias, action, cpa, frequency,
            spend_7d, conversions_7d, impressions_7d,
            budget_before, budget_after, reason, dry_run, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        row["ad_set_id"], row.get("alias", ""), row["action"],
        row.get("cpa", 0.0), row.get("frequency", 0.0),
        row.get("spend_7d", 0.0), row.get("conversions_7d", 0),
        row.get("impressions_7d", 0),
        row.get("budget_before", 0), row.get("budget_after", 0),
        row.get("reason", ""), 1 if row.get("dry_run") else 0,
        datetime.now().isoformat(timespec="seconds"),
    ))
    con.commit()
    con.close()


# ──────────────────────────────────────────────
# 설정 로드
# ──────────────────────────────────────────────

def load_env():
    load_dotenv(ROOT / ".env")
    return {
        "cpa_pause":  int(os.getenv("META_CPA_PAUSE_THRESHOLD", "50000")),
        "cpa_reduce": int(os.getenv("META_CPA_REDUCE_THRESHOLD", "30000")),
        "cpa_boost":  int(os.getenv("META_CPA_BOOST_THRESHOLD", "15000")),
        "freq_fatigue": float(os.getenv("META_FREQUENCY_FATIGUE", "3.0")),
        "budget_cap": int(os.getenv("META_BUDGET_CAP_PER_ADSET_KRW", "50000")),
        "learning_protect_days": int(os.getenv("META_LEARNING_PROTECT_DAYS", "14")),
        "slack_url":  os.getenv("SLACK_WEBHOOK_URL", ""),
    }


def load_managed_ad_sets() -> list[dict]:
    if not CONFIG_PATH.exists():
        logger.error("meta_ad_sets.json 없음. 먼저 meta_00_discover.py 실행.")
        sys.exit(1)
    with open(CONFIG_PATH, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    managed   = [m for m in cfg.get("managed", []) if m.get("enabled", True)]
    discovered = {d["ad_set_id"]: d for d in cfg.get("discovered", [])}

    # managed 항목에 discovered 메타데이터(name, campaign_id, created_time 등) 머지
    enriched = []
    for m in managed:
        d = discovered.get(m["ad_set_id"], {})
        enriched.append({
            **m,
            "name":         d.get("name", ""),
            "campaign_id":  d.get("campaign_id", ""),
            "campaign_name": d.get("campaign_name", ""),
            "created_time": d.get("created_time", ""),
        })
    return enriched


# ──────────────────────────────────────────────
# 룰
# ──────────────────────────────────────────────

def get_ad_set_age_days(created_time_str: str) -> int:
    if not created_time_str:
        return 999
    try:
        d = date.fromisoformat(created_time_str[:10])
        return (date.today() - d).days
    except (ValueError, TypeError):
        return 999


def decide_action(insights: dict, age_days: int, current_budget: int, rules: dict) -> tuple[str, str, int]:
    """
    반환: (action, reason, new_budget_krw)
    action: 'pause'|'reduce'|'boost'|'flag_fatigue'|'flag_zero_imp'|'flag_review'|'learning_protect'|'keep'
    new_budget_krw: 변경 안 하면 current_budget 그대로

    CBO(캠페인 예산) 캠페인은 광고세트에 예산이 없음(current_budget<=0).
    이 경우 예산 증감(boost/reduce)은 불가 — Meta가 캠페인 레벨에서 자동 분배하므로
    boost는 keep, reduce는 flag_review(사람 검토)로 대체한다. pause/flag는 그대로.
    """
    cpa = insights["cpa"]
    spend = insights["spend"]
    conv = insights["conversions"]
    freq = insights["frequency"]
    imp  = insights["impressions"]

    in_learning = age_days < rules["learning_protect_days"]
    is_cbo = (current_budget <= 0)

    # 학습기간이지만 노출 0은 보고 가치 있음
    if imp == 0:
        return ("flag_zero_imp",
                f"7일 노출 0 (생성 {age_days}일째)",
                current_budget)

    # 학습 보호: pause/reduce 면제 (boost는 학습 빨라지니 허용, flag는 정보 차원)
    if in_learning:
        if conv >= 5 and cpa > 0 and cpa < rules["cpa_boost"]:
            if is_cbo:
                return ("keep",
                        f"학습중 + CPA={int(cpa):,}원 우수 — 예산은 캠페인(CBO) 자동분배",
                        current_budget)
            new_budget = min(int(current_budget * 1.3), rules["budget_cap"])
            if new_budget > current_budget:
                return ("boost",
                        f"학습중 + CPA={int(cpa):,}원 + 전환 {conv}건 → 학습 가속",
                        new_budget)
        if freq > rules["freq_fatigue"]:
            return ("flag_fatigue",
                    f"학습중 + frequency {freq:.2f} > {rules['freq_fatigue']}",
                    current_budget)
        return ("learning_protect",
                f"학습기간 보호 (생성 {age_days}일째, 보호 {rules['learning_protect_days']}일까지)",
                current_budget)

    # 정상 운영 룰
    if conv == 0 or cpa <= 0:
        # 전환 자체가 없으면 CPA는 무한대 — pause 룰 적용
        if spend >= rules["cpa_pause"]:  # spend가 pause 임계값만큼 쌓였는데 전환 0 → 컷
            return ("pause",
                    f"전환 0건 + 7일 spend {int(spend):,}원 ≥ {rules['cpa_pause']:,}원",
                    current_budget)
        # spend 미달이면 그냥 keep (데이터 부족)
        return ("keep", f"전환 0건, spend {int(spend):,}원 (관망)", current_budget)

    if cpa > rules["cpa_pause"]:
        return ("pause",
                f"CPA={int(cpa):,}원 > {rules['cpa_pause']:,}원",
                current_budget)

    if cpa > rules["cpa_reduce"] and spend > 50_000:
        if is_cbo:
            return ("flag_review",
                    f"CPA={int(cpa):,}원 높음 (>{rules['cpa_reduce']:,}원) — CBO라 예산 자동조정 불가, "
                    f"캠페인 예산·타겟·크리에이티브 검토 필요",
                    current_budget)
        new_budget = max(int(current_budget * 0.5), 5_000)  # 5천원 최소
        return ("reduce",
                f"CPA={int(cpa):,}원 > {rules['cpa_reduce']:,}원, spend {int(spend):,}원 → 예산 50% 삭감",
                new_budget)

    if cpa < rules["cpa_boost"] and conv >= 5:
        if is_cbo:
            return ("keep",
                    f"CPA={int(cpa):,}원 우수 — 예산은 캠페인(CBO) 자동분배 중",
                    current_budget)
        new_budget = min(int(current_budget * 1.3), rules["budget_cap"])
        if new_budget > current_budget:
            return ("boost",
                    f"CPA={int(cpa):,}원 < {rules['cpa_boost']:,}원, 전환 {conv}건 → 예산 30% 증액",
                    new_budget)

    if freq > rules["freq_fatigue"]:
        return ("flag_fatigue",
                f"frequency {freq:.2f} > {rules['freq_fatigue']} → 크리에이티브 교체 권장",
                current_budget)

    return ("keep", f"CPA={int(cpa):,}원, 전환 {conv}건, freq={freq:.2f} (정상)", current_budget)


# ──────────────────────────────────────────────
# ad set 1개 처리
# ──────────────────────────────────────────────

def process_ad_set(api: MetaAdsAPI, ad_set: dict, rules: dict, dry_run: bool) -> dict:
    alias = ad_set.get("alias", ad_set["ad_set_id"])
    ad_set_id = ad_set["ad_set_id"]

    logger.info("=== ad set 처리: %s (%s) ===", alias, ad_set_id)

    insights = api.get_ad_set_insights(ad_set_id, days=7)
    logger.info("  인사이트: 노출=%s, 클릭=%s, spend=%s원, 전환=%s, CPA=%s원, freq=%.2f",
                f"{insights['impressions']:,}", f"{insights['clicks']:,}",
                f"{int(insights['spend']):,}", insights["conversions"],
                f"{int(insights['cpa']):,}", insights["frequency"])

    current_budget = api.get_ad_set_budget(ad_set_id)
    age_days = get_ad_set_age_days(ad_set.get("created_time", ""))

    action, reason, new_budget = decide_action(insights, age_days, current_budget, rules)
    logger.info("  결정: %s — %s (예산 %s→%s원)",
                action, reason, f"{current_budget:,}", f"{new_budget:,}")

    # 실행
    executed = False
    if not dry_run:
        try:
            if action == "pause":
                api.pause_ad_set(ad_set_id); executed = True
            elif action in ("reduce", "boost") and new_budget != current_budget:
                api.update_ad_set_budget(ad_set_id, new_budget); executed = True
            # flag_*, keep, learning_protect는 변경 없음
        except MetaProtectedError as e:
            logger.error("  보호된 캠페인 — 변경 차단: %s", e)
            action = f"blocked_{action}"
            reason = f"PROTECTED: {reason}"

    # 기록
    record_history({
        "ad_set_id":     ad_set_id,
        "alias":         alias,
        "action":        action,
        "cpa":           insights["cpa"],
        "frequency":     insights["frequency"],
        "spend_7d":      insights["spend"],
        "conversions_7d": insights["conversions"],
        "impressions_7d": insights["impressions"],
        "budget_before": current_budget,
        "budget_after":  new_budget if executed else current_budget,
        "reason":        reason,
        "dry_run":       dry_run,
    })

    return {
        "alias":         alias,
        "ad_set_id":     ad_set_id,
        "name":          ad_set.get("name", ""),
        "action":        action,
        "reason":        reason,
        "age_days":      age_days,
        "current_budget": current_budget,
        "new_budget":    new_budget if executed else current_budget,
        "executed":      executed,
        **insights,
    }


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Meta 광고 주간 자동 정리")
    parser.add_argument("--dry-run", action="store_true", help="실제 변경 없이 시뮬레이션")
    args = parser.parse_args()

    if args.dry_run:
        logger.info("=== DRY RUN 모드 — 실제 변경 없음 ===")

    env = load_env()
    api = MetaAdsAPI.from_env()
    init_state_db()

    managed = load_managed_ad_sets()
    if not managed:
        logger.error("자동화 대상 ad set이 없습니다. meta_00_discover.py --enable 로 등록.")
        sys.exit(1)
    logger.info("자동화 대상 ad set %d개", len(managed))

    week_end = date.today() - timedelta(days=1)
    week_start = week_end - timedelta(days=6)

    results = []
    for ad_set in managed:
        try:
            r = process_ad_set(api, ad_set, env, args.dry_run)
            results.append(r)
        except Exception as e:
            logger.error("ad set %s 처리 중 오류: %s", ad_set.get("alias", ad_set["ad_set_id"]), e)
            results.append({
                "alias": ad_set.get("alias", ""),
                "ad_set_id": ad_set["ad_set_id"],
                "action": "error", "reason": str(e)[:200],
                "impressions": 0, "clicks": 0, "spend": 0.0,
                "conversions": 0, "cpa": 0.0, "frequency": 0.0,
                "current_budget": 0, "new_budget": 0, "executed": False,
                "age_days": 0, "name": ad_set.get("name", ""),
            })

    summary = {
        "week_start": str(week_start),
        "week_end":   str(week_end),
        "dry_run":    args.dry_run,
        "total_ad_sets":    len(results),
        "paused":           sum(1 for r in results if r["action"] == "pause" and r["executed"]),
        "reduced":          sum(1 for r in results if r["action"] == "reduce" and r["executed"]),
        "boosted":          sum(1 for r in results if r["action"] == "boost" and r["executed"]),
        "flagged_fatigue":  sum(1 for r in results if r["action"] == "flag_fatigue"),
        "flagged_zero_imp": sum(1 for r in results if r["action"] == "flag_zero_imp"),
        "flagged_review":   sum(1 for r in results if r["action"] == "flag_review"),
        "learning_protect": sum(1 for r in results if r["action"] == "learning_protect"),
        "errors":           sum(1 for r in results if r["action"] == "error"),
        "total_spend_7d":   sum(r.get("spend", 0) for r in results),
        "total_conversions_7d": sum(r.get("conversions", 0) for r in results),
        "results":          results,
        "thresholds":       env,
    }

    report_path = generate_meta_weekly_report(summary)
    logger.info("보고서 저장: %s", report_path)

    if env.get("slack_url"):
        send_meta_to_slack(report_path, env["slack_url"], summary)

    logger.info("=== meta_weekly_pruner 완료 ===")


if __name__ == "__main__":
    main()
