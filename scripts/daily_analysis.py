# -*- coding: utf-8 -*-
"""
일별 광고 분석 보고서 생성.

기준일(어제) vs 직전일(그제) 비교 + 지난주 동요일 보조.
Naver 3계정 + Meta 통합. AI 인사이트 섹션 포함 (Claude API 크레딧 있을 때).

사용:
    python scripts/daily_analysis.py                  # 어제 기준
    python scripts/daily_analysis.py --date 2026-05-23

출력:
    reports/daily_YYYYMMDD.md
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
    _kpi,
)
from lib.claude_api import ClaudeReporter

logger = logging.getLogger(__name__)


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="기준일 YYYY-MM-DD (기본: 어제)")
    p.add_argument("--no-ai", action="store_true", help="Claude AI 인사이트 생략")
    return p.parse_args()


def main():
    args = _parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    load_dotenv()

    target = date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1)
    prev = target - timedelta(days=1)
    last_week = target - timedelta(days=7)

    logger.info("[Daily] 기준일 %s, 비교일 %s (직전일), 지난주 %s", target, prev, last_week)

    # 단일일 기간으로 fetch
    cur_data = fetch_for_period(target, target)
    prev_data = fetch_for_period(prev, prev)
    lw_data = fetch_for_period(last_week, last_week)

    # 현재 vs 직전일 비교를 기준으로 마크다운
    current_summary = build_summary_markdown(cur_data, prev_data=prev_data,
                                             include_daily=False, include_top_n=10)
    # 별도로 지난주 동요일 비교 표
    cur_kpi = _kpi(cur_data["naver"] + cur_data["meta"])
    lw_kpi  = _kpi(lw_data["naver"] + lw_data["meta"])

    # AI 인사이트
    ai_md = ""
    if not args.no_ai:
        try:
            rep = ClaudeReporter.from_env()
            period_label = f"{target} (어제, vs {prev} 그제 비교)"
            ai_md = rep.generate_insights(
                period_label=period_label,
                current_summary=current_summary,
                previous_summary=build_summary_markdown(prev_data, include_daily=False, include_top_n=5),
                operational_notes=operational_notes(),
            )
        except Exception as e:
            logger.warning("[Daily] Claude 호출 실패: %s", e)
            ai_md = f"> ⚠️ AI 인사이트 모듈 초기화 실패: {e}"

    # ─ 마크다운 조립
    md = []
    md.append(f"# 일별 광고 분석 — {target}\n")
    md.append(f"> 생성일: {date.today()} · 직전일({prev}) vs 지난주 동요일({last_week}) 비교\n")
    md.append("## 1. 어제 vs 그제 종합\n")
    md.append(current_summary)
    md.append("## 2. 지난주 동요일 비교 (참고)\n")
    md.append("| 지표 | 어제 | 지난주 동요일 |")
    md.append("|---|---:|---:|")
    md.append(f"| 노출 | {cur_kpi['impressions']:,} | {lw_kpi['impressions']:,} |")
    md.append(f"| 클릭 | {cur_kpi['clicks']:,} | {lw_kpi['clicks']:,} |")
    md.append(f"| 지출 | {int(cur_kpi['spend']):,}원 | {int(lw_kpi['spend']):,}원 |")
    md.append(f"| 전환 | {cur_kpi['conversions']:,} | {lw_kpi['conversions']:,} |")
    md.append(f"| CTR | {cur_kpi['ctr']*100:.2f}% | {lw_kpi['ctr']*100:.2f}% |")
    md.append(f"| CPC | {int(cur_kpi['cpc']):,}원 | {int(lw_kpi['cpc']):,}원 |")
    md.append("")
    md.append("## 3. 🤖 AI 인사이트\n")
    md.append(ai_md or "> AI 생략 (`--no-ai`)")
    md.append("\n---")
    md.append(f"*자동 생성: daily_analysis.py — {date.today()}*")

    out = ROOT / "reports" / f"daily_{target.strftime('%Y%m%d')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8-sig")
    logger.info("[Daily] 저장 완료: %s", out)
    print(f"✅ {out}")


if __name__ == "__main__":
    main()
