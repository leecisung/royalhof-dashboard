# -*- coding: utf-8 -*-
"""
주별 광고 분석 보고서.

기본: 지난 월~일 (월요일 실행 기준) vs 그 직전주.
Naver 3계정 + Meta + GA4(있을 때).

사용:
    python scripts/weekly_analysis.py                            # 지난주
    python scripts/weekly_analysis.py --start 2026-05-12 --end 2026-05-18

출력:
    reports/weekly_YYYYMMDD_YYYYMMDD.md
"""

import sys
import argparse
import logging
from pathlib import Path
from datetime import date, timedelta

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import load_dotenv

from lib.analysis_data import (
    fetch_for_period,
    build_summary_markdown,
    operational_notes,
)
from lib.claude_api import ClaudeReporter

logger = logging.getLogger(__name__)


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--start", help="시작일 YYYY-MM-DD")
    p.add_argument("--end", help="종료일 YYYY-MM-DD")
    p.add_argument("--no-ai", action="store_true")
    return p.parse_args()


def _default_week_range():
    """오늘 기준 지난 월요일~일요일."""
    today = date.today()
    # 오늘이 화요일이면 weekday=1. 지난 일요일은 today - (weekday+1).
    last_sun = today - timedelta(days=today.weekday() + 1)
    last_mon = last_sun - timedelta(days=6)
    return last_mon, last_sun


def main():
    args = _parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    load_dotenv()

    if args.start and args.end:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    else:
        start, end = _default_week_range()
    days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)

    logger.info("[Weekly] %s ~ %s (%d일) vs 직전주 %s ~ %s",
                start, end, days, prev_start, prev_end)

    cur_data = fetch_for_period(start, end)
    prev_data = fetch_for_period(prev_start, prev_end)

    current_summary = build_summary_markdown(cur_data, prev_data=prev_data,
                                             include_daily=True, include_top_n=10)
    previous_summary = build_summary_markdown(prev_data, include_daily=False, include_top_n=5)

    ai_md = ""
    if not args.no_ai:
        try:
            rep = ClaudeReporter.from_env()
            ai_md = rep.generate_insights(
                period_label=f"{start} ~ {end} ({days}일, vs 직전주 {prev_start} ~ {prev_end})",
                current_summary=current_summary,
                previous_summary=previous_summary,
                operational_notes=operational_notes(),
                max_tokens=2500,
            )
        except Exception as e:
            logger.warning("[Weekly] Claude 호출 실패: %s", e)
            ai_md = f"> ⚠️ AI 인사이트 모듈 초기화 실패: {e}"

    md = []
    md.append(f"# 주별 광고 분석 — {start} ~ {end}\n")
    md.append(f"> 생성일: {date.today()} · 비교: 직전주 {prev_start} ~ {prev_end}\n")
    md.append("## 1. 종합 KPI · 채널 · 브랜드 · 캠페인\n")
    md.append(current_summary)
    md.append("## 2. 🤖 AI 인사이트\n")
    md.append(ai_md or "> AI 생략 (`--no-ai`)")
    md.append("\n---")
    md.append(f"*자동 생성: weekly_analysis.py — {date.today()}*")

    out = ROOT / "reports" / f"weekly_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8-sig")
    logger.info("[Weekly] 저장 완료: %s", out)
    print(f"✅ {out}")


if __name__ == "__main__":
    main()
