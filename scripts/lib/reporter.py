# -*- coding: utf-8 -*-
"""
주간 보고서 생성 및 슬랙 전송
"""

import logging
import json
from datetime import date
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).parents[2] / "reports"


def generate_weekly_report(summary: dict) -> Path:
    """
    주간 보고서 마크다운 파일 생성.

    summary 키:
      - week_start, week_end: str (YYYY-MM-DD)
      - total_keywords: int
      - deleted_cpc: int       (CPC > 200원 삭제)
      - deleted_ctr: int       (CTR < 1% + 노출 10K+ 삭제)
      - deleted_no_imp: int    (14일 노출 0 삭제)
      - bumped_bid: int        (70→100원 인상)
      - replenished: int       (예비 풀에서 보충)
      - reserve_pool_size: int
      - group_summary: [{name, keywords, deleted, added}]
      - top_keywords: [{keyword, impressions, clicks, cpc}]  # 상위 성과
    """
    REPORTS_DIR.mkdir(exist_ok=True)
    today = date.today().strftime("%Y%m%d")
    report_path = REPORTS_DIR / f"weekly_{today}.md"

    total_deleted = (
        summary.get("deleted_cpc", 0)
        + summary.get("deleted_ctr", 0)
        + summary.get("deleted_no_imp", 0)
    )

    lines = [
        f"# 로얄호프치킨 70원 전략 주간 보고서",
        f"",
        f"**기간**: {summary.get('week_start', '')} ~ {summary.get('week_end', '')}",
        f"**생성일**: {date.today().strftime('%Y-%m-%d')}",
        f"",
        f"---",
        f"",
        f"## 요약",
        f"",
        f"| 항목 | 수량 |",
        f"|---|---|",
        f"| 전체 키워드 | {summary.get('total_keywords', 0):,}개 |",
        f"| 삭제 (CPC > 200원) | {summary.get('deleted_cpc', 0):,}개 |",
        f"| 삭제 (CTR < 1% + 노출 10K+) | {summary.get('deleted_ctr', 0):,}개 |",
        f"| 삭제 (14일 노출 0) | {summary.get('deleted_no_imp', 0):,}개 |",
        f"| **총 삭제** | **{total_deleted:,}개** |",
        f"| 입찰가 인상 (70→100원) | {summary.get('bumped_bid', 0):,}개 |",
        f"| 예비 풀 보충 | {summary.get('replenished', 0):,}개 |",
        f"| 예비 풀 잔량 | {summary.get('reserve_pool_size', 0):,}개 |",
        f"",
    ]

    # 예비 풀 잔량 경고
    if summary.get("reserve_pool_size", 0) < 100_000:
        lines += [
            f"⚠️ **예비 풀 잔량이 10만개 미만입니다. keywordstool 재펼침이 필요합니다.**",
            f"",
        ]

    # 그룹별 요약
    group_summary = summary.get("group_summary", [])
    if group_summary:
        lines += [
            f"## 그룹별 현황",
            f"",
            f"| 그룹명 | 키워드 수 | 삭제 | 추가 |",
            f"|---|---|---|---|",
        ]
        for g in group_summary:
            lines.append(
                f"| {g.get('name', '')} | {g.get('keywords', 0):,} | {g.get('deleted', 0):,} | {g.get('added', 0):,} |"
            )
        lines.append("")

    # 상위 성과 키워드
    top_keywords = summary.get("top_keywords", [])
    if top_keywords:
        lines += [
            f"## 상위 성과 키워드 (CPC 70~100원, 안정 노출)",
            f"",
            f"| 키워드 | 노출 | 클릭 | CPC |",
            f"|---|---|---|---|",
        ]
        for kw in top_keywords[:20]:
            lines.append(
                f"| {kw.get('keyword', '')} | {kw.get('impressions', 0):,} | {kw.get('clicks', 0):,} | {kw.get('cpc', 0):,}원 |"
            )
        lines.append("")

    lines += [
        f"---",
        f"",
        f"*자동 생성: weekly_pruner.py*",
    ]

    content = "\n".join(lines)
    report_path.write_text(content, encoding="utf-8-sig")
    logger.info("[REPORT] 보고서 생성: %s", report_path)
    return report_path


def send_to_slack(report_path: Path, webhook_url: str, summary: dict) -> bool:
    """슬랙 웹훅으로 보고서 요약 전송."""
    total_deleted = (
        summary.get("deleted_cpc", 0)
        + summary.get("deleted_ctr", 0)
        + summary.get("deleted_no_imp", 0)
    )
    text = (
        f"*[로얄호프치킨] 주간 검색광고 보고서*\n"
        f"기간: {summary.get('week_start', '')} ~ {summary.get('week_end', '')}\n"
        f"전체 키워드: {summary.get('total_keywords', 0):,}개\n"
        f"삭제: {total_deleted:,}개 | 입찰 인상: {summary.get('bumped_bid', 0):,}개 | "
        f"보충: {summary.get('replenished', 0):,}개\n"
        f"예비 풀 잔량: {summary.get('reserve_pool_size', 0):,}개\n"
        f"보고서: `{report_path.name}`"
    )
    if summary.get("reserve_pool_size", 0) < 100_000:
        text += "\n⚠️ 예비 풀 10만개 미만 — keywordstool 재펼침 필요"

    payload = {"text": text}
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info("[SLACK] 보고서 전송 완료")
            return True
        logger.error("[SLACK] 전송 실패: %d %s", resp.status_code, resp.text)
        return False
    except Exception as e:
        logger.error("[SLACK] 전송 오류: %s", e)
        return False


# ═════════════════════════════════════════════════════════
# Meta(FB) 광고 주간 보고서
# ═════════════════════════════════════════════════════════

ACTION_KR = {
    "pause":            "🛑 일시정지",
    "reduce":           "📉 예산 50% 삭감",
    "boost":            "📈 예산 30% 증액",
    "flag_fatigue":     "⚠️ 크리에이티브 fatigue",
    "flag_zero_imp":    "⚠️ 7일 노출 0",
    "flag_review":      "🔎 사람 검토 필요",
    "learning_protect": "🛡️ 학습기간 보호",
    "keep":             "✅ 유지",
    "error":            "❌ 오류",
}


def generate_meta_weekly_report(summary: dict) -> Path:
    """
    Meta 광고 주간 보고서 마크다운 생성.

    summary 키:
      - week_start, week_end (str)
      - dry_run (bool)
      - total_ad_sets, paused, reduced, boosted, flagged_fatigue,
        flagged_zero_imp, learning_protect, errors (int)
      - total_spend_7d (float), total_conversions_7d (int)
      - results: list of per-ad-set dict
        {alias, ad_set_id, name, action, reason, age_days,
         current_budget, new_budget, executed,
         impressions, clicks, spend, conversions, cpa, ctr, frequency, reach}
      - thresholds: dict (cpa_pause, cpa_reduce, cpa_boost, freq_fatigue, ...)
    """
    REPORTS_DIR.mkdir(exist_ok=True)
    today = date.today().strftime("%Y%m%d")
    report_path = REPORTS_DIR / f"weekly_meta_{today}.md"

    total_spend = int(summary.get("total_spend_7d", 0))
    total_conv  = summary.get("total_conversions_7d", 0)
    avg_cpa = (total_spend / total_conv) if total_conv > 0 else 0

    th = summary.get("thresholds", {})

    lines = [
        f"# 버거리 Meta 광고 주간 보고서",
        f"",
        f"**기간**: {summary.get('week_start', '')} ~ {summary.get('week_end', '')}  (7일)",
        f"**생성일**: {date.today().strftime('%Y-%m-%d')}",
    ]
    if summary.get("dry_run"):
        lines += [f"", f"> 🧪 **DRY RUN 모드** — 실제 변경 없이 시뮬레이션한 결과입니다."]

    lines += [
        f"",
        f"---",
        f"",
        f"## 요약",
        f"",
        f"| 항목 | 값 |",
        f"|---|---|",
        f"| 자동화 대상 ad set | {summary.get('total_ad_sets', 0):,}개 |",
        f"| 7일 총 지출 | {total_spend:,}원 |",
        f"| 7일 총 전환(pixel event) | {total_conv:,}건 |",
        f"| 7일 평균 CPA | {int(avg_cpa):,}원 |",
        f"",
        f"### 조치 결과",
        f"",
        f"| 조치 | 수량 |",
        f"|---|---|",
        f"| 🛑 일시정지 | {summary.get('paused', 0)} |",
        f"| 📉 예산 50% 삭감 | {summary.get('reduced', 0)} |",
        f"| 📈 예산 30% 증액 | {summary.get('boosted', 0)} |",
        f"| ⚠️ 크리에이티브 fatigue | {summary.get('flagged_fatigue', 0)} |",
        f"| ⚠️ 7일 노출 0 | {summary.get('flagged_zero_imp', 0)} |",
        f"| 🔎 사람 검토 필요 | {summary.get('flagged_review', 0)} |",
        f"| 🛡️ 학습기간 보호 | {summary.get('learning_protect', 0)} |",
        f"| ❌ 오류 | {summary.get('errors', 0)} |",
        f"",
    ]

    # 임계값
    lines += [
        f"## 적용 임계값 (.env)",
        f"",
        f"| 키 | 값 |",
        f"|---|---|",
        f"| CPA pause | {th.get('cpa_pause', 0):,}원 |",
        f"| CPA reduce | {th.get('cpa_reduce', 0):,}원 |",
        f"| CPA boost | {th.get('cpa_boost', 0):,}원 |",
        f"| frequency fatigue | {th.get('freq_fatigue', 0)} |",
        f"| 예산 상한/ad set | {th.get('budget_cap', 0):,}원 |",
        f"| 학습기간 보호 | {th.get('learning_protect_days', 0)}일 |",
        f"",
    ]

    # ad set별 상세
    results = summary.get("results", [])
    if results:
        lines += [
            f"## ad set별 상세",
            f"",
            f"| 별칭 | 상태 | 노출 | 클릭 | 지출 | 전환 | CPA | freq | 예산변경 | 사유 |",
            f"|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in results:
            action_kr = ACTION_KR.get(r["action"], r["action"])
            budget_change = ""
            if r["current_budget"] != r["new_budget"]:
                budget_change = f"{r['current_budget']:,} → **{r['new_budget']:,}**"
            else:
                budget_change = f"{r['current_budget']:,}"
            lines.append(
                f"| {r.get('alias', '')} "
                f"| {action_kr} "
                f"| {r.get('impressions', 0):,} "
                f"| {r.get('clicks', 0):,} "
                f"| {int(r.get('spend', 0)):,}원 "
                f"| {r.get('conversions', 0)} "
                f"| {int(r.get('cpa', 0)):,}원 "
                f"| {r.get('frequency', 0):.2f} "
                f"| {budget_change} "
                f"| {r.get('reason', '')} |"
            )
        lines.append("")

    # 경고 영역
    warnings = [r for r in results if r["action"] in ("flag_fatigue", "flag_zero_imp", "flag_review", "error")]
    if warnings:
        lines += [f"## ⚠️ 사람이 확인할 것", f""]
        for w in warnings:
            lines.append(f"- **{w.get('alias', '')}** ({ACTION_KR.get(w['action'], w['action'])}) — {w.get('reason', '')}")
        lines.append("")

    lines += [
        f"---",
        f"",
        f"*자동 생성: meta_weekly_pruner.py*",
        f"*상세 로그: `logs/api_calls.log`*",
        f"*결정 이력 DB: `data/meta_state.db`*",
    ]

    content = "\n".join(lines)
    report_path.write_text(content, encoding="utf-8-sig")
    logger.info("[REPORT] Meta 주간 보고서 생성: %s", report_path)
    return report_path


def send_meta_to_slack(report_path: Path, webhook_url: str, summary: dict) -> bool:
    """Meta 주간 보고서 슬랙 요약 전송."""
    total_spend = int(summary.get("total_spend_7d", 0))
    total_conv  = summary.get("total_conversions_7d", 0)
    avg_cpa = (total_spend / total_conv) if total_conv > 0 else 0

    prefix = "🧪 [DRY RUN] " if summary.get("dry_run") else ""
    text_lines = [
        f"*{prefix}[버거리] Meta 광고 주간 보고서*",
        f"기간: {summary.get('week_start', '')} ~ {summary.get('week_end', '')}",
        f"7일 지출 {total_spend:,}원 | 전환 {total_conv}건 | 평균 CPA {int(avg_cpa):,}원",
        (
            f"조치: 정지 {summary.get('paused', 0)} | "
            f"삭감 {summary.get('reduced', 0)} | "
            f"증액 {summary.get('boosted', 0)} | "
            f"fatigue {summary.get('flagged_fatigue', 0)} | "
            f"노출0 {summary.get('flagged_zero_imp', 0)} | "
            f"검토 {summary.get('flagged_review', 0)} | "
            f"보호 {summary.get('learning_protect', 0)}"
        ),
        f"보고서: `{report_path.name}`",
    ]
    if summary.get("errors", 0):
        text_lines.append(f"❌ 오류 {summary['errors']}건 — 로그 확인 필요")

    payload = {"text": "\n".join(text_lines)}
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info("[SLACK] Meta 보고서 전송 완료")
            return True
        logger.error("[SLACK] Meta 전송 실패: %d %s", resp.status_code, resp.text)
        return False
    except Exception as e:
        logger.error("[SLACK] Meta 전송 오류: %s", e)
        return False
