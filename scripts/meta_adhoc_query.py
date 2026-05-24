# -*- coding: utf-8 -*-
"""
meta_adhoc_query.py — 임시 조회용

사용 예:
  python scripts/meta_adhoc_query.py account               # 계정 7일 요약
  python scripts/meta_adhoc_query.py account --days 1      # 어제 1일만
  python scripts/meta_adhoc_query.py ad_sets               # managed ad set 7일 인사이트
  python scripts/meta_adhoc_query.py history --limit 50    # meta_state.db 최근 결정 이력
"""

import sys
import json
import sqlite3
import argparse
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.meta_api import MetaAdsAPI

CONFIG_PATH = ROOT / "data" / "meta_ad_sets.json"
STATE_DB    = ROOT / "data" / "meta_state.db"


def cmd_account(api: MetaAdsAPI, days: int, today: bool = False):
    if today:
        days = 1
    s = api.get_account_summary(days=days, until_today=today)
    label = "오늘" if today else f"{days}일"
    print(f"\n=== 계정 {label} 요약 ===")
    print(f"  노출:     {s['impressions']:>12,}")
    print(f"  클릭:     {s['clicks']:>12,}")
    print(f"  지출:     {int(s['spend']):>12,}원")
    print(f"  CTR:      {s['ctr']:>12.2f}%")
    print(f"  도달:     {s['reach']:>12,}")
    print(f"  frequency:{s['frequency']:>12.2f}")


def cmd_ad_sets(api: MetaAdsAPI, days: int, today: bool = False):
    if not CONFIG_PATH.exists():
        print("meta_ad_sets.json 없음. meta_00_discover.py 먼저 실행.")
        return
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    managed = [m for m in cfg.get("managed", []) if m.get("enabled", True)]
    if not managed:
        print("자동화 대상 ad set이 없습니다.")
        return

    if today:
        days = 1
    label = "오늘" if today else f"{days}일"
    print(f"\n=== managed ad set {label} 인사이트 ===")
    print(f"{'alias':<16} {'노출':>10} {'클릭':>8} {'지출':>10} {'전환':>5} {'CPA':>10} {'freq':>6}")
    print("-" * 80)
    for m in managed:
        ins = api.get_ad_set_insights(m["ad_set_id"], days=days, until_today=today)
        print(
            f"{m.get('alias', '')[:14]:<16} "
            f"{ins['impressions']:>10,} "
            f"{ins['clicks']:>8,} "
            f"{int(ins['spend']):>10,} "
            f"{ins['conversions']:>5} "
            f"{int(ins['cpa']):>10,} "
            f"{ins['frequency']:>6.2f}"
        )


def cmd_history(limit: int):
    if not STATE_DB.exists():
        print("meta_state.db 없음. meta_weekly_pruner.py 한번도 실행 안 됨.")
        return
    con = sqlite3.connect(STATE_DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT created_at, alias, action, cpa, frequency, spend_7d, conversions_7d,
               budget_before, budget_after, reason, dry_run
        FROM ad_set_history
        ORDER BY id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    con.close()

    if not rows:
        print("이력 없음.")
        return

    print(f"\n=== 최근 결정 이력 {len(rows)}건 ===")
    for r in rows:
        dry = " (DRY)" if r["dry_run"] else ""
        budget_change = ""
        if r["budget_before"] != r["budget_after"]:
            budget_change = f" / 예산 {r['budget_before']:,}→{r['budget_after']:,}"
        print(f"[{r['created_at']}{dry}] {r['alias']} → {r['action']}{budget_change}")
        print(f"    spend_7d={int(r['spend_7d'] or 0):,}원, conv={r['conversions_7d']}, "
              f"CPA={int(r['cpa'] or 0):,}원, freq={(r['frequency'] or 0):.2f}")
        print(f"    사유: {r['reason']}")
        print()


def main():
    parser = argparse.ArgumentParser(description="Meta 광고 임시 조회")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_acc = sub.add_parser("account", help="계정 요약")
    p_acc.add_argument("--days", type=int, default=7)
    p_acc.add_argument("--today", action="store_true", help="오늘 날짜까지 포함(실시간)")

    p_as = sub.add_parser("ad_sets", help="managed ad set 인사이트")
    p_as.add_argument("--days", type=int, default=7)
    p_as.add_argument("--today", action="store_true", help="오늘 날짜까지 포함(실시간)")

    p_hist = sub.add_parser("history", help="결정 이력")
    p_hist.add_argument("--limit", type=int, default=30)

    args = parser.parse_args()
    load_dotenv(ROOT / ".env")

    if args.cmd == "history":
        cmd_history(args.limit)
        return

    api = MetaAdsAPI.from_env()
    if args.cmd == "account":
        cmd_account(api, args.days, args.today)
    elif args.cmd == "ad_sets":
        cmd_ad_sets(api, args.days, args.today)


if __name__ == "__main__":
    main()
