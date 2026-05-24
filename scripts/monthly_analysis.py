# -*- coding: utf-8 -*-
"""
월별 광고 분석 보고서.

기본: 지난달 전체 vs 그 직전달 전체.
Naver 3계정 + Meta + GA4(있을 때).

사용:
    python scripts/monthly_analysis.py                  # 지난달
    python scripts/monthly_analysis.py --month 2026-04

출력:
    reports/monthly_YYYYMM.md
"""

import sys
import calendar
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
    p.add_argument("--month", help="대상월 YYYY-MM (기본: 지난달)")
    p.add_argument("--no-ai", action="store_true")
    return p.parse_args()


def _month_range(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end = date(year, month, last_day)
    return start, end


def _prev_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def main():
    args = _parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    load_dotenv()

    if args.month:
        year, month = map(int, args.month.split("-"))
    else:
        # 지난달
        today = date.today()
        year, month = _prev_month(today.year, today.month)

    start, end = _month_range(year, month)
    pyear, pmonth = _prev_month(year, month)
    pstart, pend = _month_range(pyear, pmonth)

    logger.info("[Monthly] %s ~ %s vs 직전월 %s ~ %s", start, end, pstart, pend)

    cur_data = fetch_for_period(start, end)
    prev_data = fetch_for_period(pstart, pend)

    current_summary = build_summary_markdown(cur_data, prev_data=prev_data,
                                             include_daily=False, include_top_n=15)
    previous_summary = build_summary_markdown(prev_data, include_daily=False, include_top_n=5)

    ai_md = ""
    if not args.no_ai:
        try:
            rep = ClaudeReporter.from_env()
            ai_md = rep.generate_insights(
                period_label=f"{year}-{month:02d} ({start} ~ {end}) vs 직전월 {pyear}-{pmonth:02d}",
                current_summary=current_summary,
                previous_summary=previous_summary,
                operational_notes=operational_notes(),
                max_tokens=3000,
            )
        except Exception as e:
            logger.warning("[Monthly] Claude 호출 실패: %s", e)
            ai_md = f"> ⚠️ AI 인사이트 모듈 초기화 실패: {e}"

    md = []
    md.append(f"# 월별 광고 분석 — {year}-{month:02d}\n")
    md.append(f"> 생성일: {date.today()} · 비교: {pyear}-{pmonth:02d}\n")
    md.append("## 1. 종합 KPI · 채널 · 브랜드 · 캠페인\n")
    md.append(current_summary)
    md.append("## 2. 🤖 AI 인사이트\n")
    md.append(ai_md or "> AI 생략 (`--no-ai`)")
    md.append("\n---")
    md.append(f"*자동 생성: monthly_analysis.py — {date.today()}*")

    out = ROOT / "reports" / f"monthly_{year}{month:02d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8-sig")
    logger.info("[Monthly] 저장 완료: %s", out)
    print(f"✅ {out}")


if __name__ == "__main__":
    main()
