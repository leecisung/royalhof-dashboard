# -*- coding: utf-8 -*-
"""
보고서용 데이터 집계·비교 레이어.

dashboard_data.fetch_unified()의 일별 raw row를 받아서:
- 채널별 합계 (Naver / Meta)
- 브랜드별 합계 (로얄호프 / 버거리 / 보승회관 / 기타)
- 캠페인별 합계 + 정렬
- 비교기간 대비 % 변화

산출물은 dict + markdown 두 형태로 제공.
markdown은 Claude 프롬프트에 그대로 박을 수 있게.
"""

import sys
import logging
from pathlib import Path
from datetime import date, timedelta
from typing import Optional

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.dashboard_data import fetch_unified

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# KPI 계산
# ─────────────────────────────────────────────

def _kpi(rows: list[dict]) -> dict:
    imp = sum(r.get("impressions", 0) for r in rows)
    clk = sum(r.get("clicks", 0) for r in rows)
    cost = sum(r.get("spend", 0) for r in rows)
    conv = sum(r.get("conversions", 0) for r in rows)
    return {
        "impressions": imp,
        "clicks": clk,
        "spend": cost,
        "conversions": conv,
        "ctr": (clk / imp) if imp else 0.0,
        "cpc": (cost / clk) if clk else 0.0,
        "cpa": (cost / conv) if conv else 0.0,
        "cpm": (cost / imp * 1000) if imp else 0.0,
    }


def _kpi_delta(cur: dict, prev: dict) -> dict:
    out = {}
    for k, v in cur.items():
        pv = prev.get(k, 0)
        if pv:
            out[k] = (v - pv) / pv
        else:
            out[k] = None if v == 0 else float("inf")
    return out


# ─────────────────────────────────────────────
# 그룹화
# ─────────────────────────────────────────────

def _group(rows: list[dict], key_fn) -> dict:
    """rows를 key_fn 결과로 묶어서 {key: [rows]}."""
    out = {}
    for r in rows:
        k = key_fn(r)
        out.setdefault(k, []).append(r)
    return out


def group_by_channel(naver_rows, meta_rows) -> dict:
    return {"Naver": naver_rows, "Meta": meta_rows}


def group_by_brand(rows: list[dict]) -> dict:
    return _group(rows, lambda r: r.get("brand", "기타"))


def group_by_campaign(rows: list[dict]) -> dict:
    return _group(
        rows,
        lambda r: (
            r.get("channel"),
            r.get("account"),
            r.get("campaign_name"),
            r.get("campaign_id"),
        ),
    )


# ─────────────────────────────────────────────
# 포맷터
# ─────────────────────────────────────────────

def _fmt_n(v) -> str:
    if v is None or v == 0:
        return "0"
    if isinstance(v, float):
        if abs(v) >= 1:
            return f"{v:,.0f}"
        return f"{v:.2f}"
    return f"{v:,}"


def _fmt_pct(v) -> str:
    if v is None:
        return "-"
    if v == float("inf"):
        return "신규"
    return f"{v*100:+.1f}%"


def _fmt_ctr(v: float) -> str:
    return f"{v*100:.2f}%"


def _fmt_krw(v) -> str:
    return f"{int(v or 0):,}원"


# ─────────────────────────────────────────────
# 마크다운 표 빌더
# ─────────────────────────────────────────────

def table_kpi(label: str, kpi: dict, prev_kpi: Optional[dict] = None) -> str:
    """단일 KPI 표 + (옵션) 변화%."""
    delta = _kpi_delta(kpi, prev_kpi) if prev_kpi else {}
    rows = [
        ("노출", _fmt_n(kpi["impressions"]), _fmt_pct(delta.get("impressions"))),
        ("클릭", _fmt_n(kpi["clicks"]), _fmt_pct(delta.get("clicks"))),
        ("지출", _fmt_krw(kpi["spend"]), _fmt_pct(delta.get("spend"))),
        ("전환", _fmt_n(kpi["conversions"]), _fmt_pct(delta.get("conversions"))),
        ("CTR", _fmt_ctr(kpi["ctr"]), _fmt_pct(delta.get("ctr"))),
        ("CPC", _fmt_krw(kpi["cpc"]), _fmt_pct(delta.get("cpc"))),
        ("CPA", _fmt_krw(kpi["cpa"]) if kpi["conversions"] else "-", _fmt_pct(delta.get("cpa"))),
        ("CPM", _fmt_krw(kpi["cpm"]), _fmt_pct(delta.get("cpm"))),
    ]
    header = "| 지표 | 값 | 전기간 대비 |\n|---|---:|---:|" if prev_kpi else "| 지표 | 값 |\n|---|---:|"
    body = "\n".join(
        f"| {n} | {v} | {d} |" if prev_kpi else f"| {n} | {v} |"
        for n, v, d in rows
    )
    return f"**{label}**\n\n{header}\n{body}\n"


def table_by_channel(naver_rows, meta_rows, prev_naver=None, prev_meta=None) -> str:
    cur_n = _kpi(naver_rows)
    cur_m = _kpi(meta_rows)
    prev_n = _kpi(prev_naver) if prev_naver is not None else None
    prev_m = _kpi(prev_meta) if prev_meta is not None else None
    blocks = []
    blocks.append("**채널별 합계**\n")
    if prev_n and prev_m:
        blocks.append("| 채널 | 노출 | 클릭 | 지출 | 전환 | CTR | CPC | CPA | Δ지출 | Δ클릭 |")
        blocks.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for label, cur, prev in [("Naver", cur_n, prev_n), ("Meta", cur_m, prev_m)]:
            d = _kpi_delta(cur, prev)
            blocks.append(
                f"| {label} | {_fmt_n(cur['impressions'])} | {_fmt_n(cur['clicks'])} | "
                f"{_fmt_krw(cur['spend'])} | {_fmt_n(cur['conversions'])} | "
                f"{_fmt_ctr(cur['ctr'])} | {_fmt_krw(cur['cpc'])} | "
                f"{_fmt_krw(cur['cpa']) if cur['conversions'] else '-'} | "
                f"{_fmt_pct(d['spend'])} | {_fmt_pct(d['clicks'])} |"
            )
    else:
        blocks.append("| 채널 | 노출 | 클릭 | 지출 | 전환 | CTR | CPC | CPA |")
        blocks.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for label, cur in [("Naver", cur_n), ("Meta", cur_m)]:
            blocks.append(
                f"| {label} | {_fmt_n(cur['impressions'])} | {_fmt_n(cur['clicks'])} | "
                f"{_fmt_krw(cur['spend'])} | {_fmt_n(cur['conversions'])} | "
                f"{_fmt_ctr(cur['ctr'])} | {_fmt_krw(cur['cpc'])} | "
                f"{_fmt_krw(cur['cpa']) if cur['conversions'] else '-'} |"
            )
    return "\n".join(blocks) + "\n"


def table_by_brand(all_rows: list[dict], prev_rows: Optional[list[dict]] = None) -> str:
    cur_g = group_by_brand(all_rows)
    prev_g = group_by_brand(prev_rows) if prev_rows is not None else {}
    blocks = ["**브랜드별 합계**\n"]
    has_prev = prev_rows is not None
    if has_prev:
        blocks.append("| 브랜드 | 노출 | 클릭 | 지출 | 전환 | CTR | CPC | Δ지출 |")
        blocks.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    else:
        blocks.append("| 브랜드 | 노출 | 클릭 | 지출 | 전환 | CTR | CPC |")
        blocks.append("|---|---:|---:|---:|---:|---:|---:|")
    for brand in sorted(cur_g, key=lambda b: -_kpi(cur_g[b])["spend"]):
        cur = _kpi(cur_g[brand])
        if has_prev:
            prev = _kpi(prev_g.get(brand, []))
            d = _kpi_delta(cur, prev)
            blocks.append(
                f"| {brand} | {_fmt_n(cur['impressions'])} | {_fmt_n(cur['clicks'])} | "
                f"{_fmt_krw(cur['spend'])} | {_fmt_n(cur['conversions'])} | "
                f"{_fmt_ctr(cur['ctr'])} | {_fmt_krw(cur['cpc'])} | {_fmt_pct(d['spend'])} |"
            )
        else:
            blocks.append(
                f"| {brand} | {_fmt_n(cur['impressions'])} | {_fmt_n(cur['clicks'])} | "
                f"{_fmt_krw(cur['spend'])} | {_fmt_n(cur['conversions'])} | "
                f"{_fmt_ctr(cur['ctr'])} | {_fmt_krw(cur['cpc'])} |"
            )
    return "\n".join(blocks) + "\n"


def table_top_campaigns(rows: list[dict], by: str = "spend", limit: int = 10,
                        prev_rows: Optional[list[dict]] = None) -> str:
    """캠페인별 상위 N."""
    cur_g = group_by_campaign(rows)
    prev_g = group_by_campaign(prev_rows) if prev_rows is not None else {}
    items = []
    for key, group_rows in cur_g.items():
        ch, acct, cname, cid = key
        cur = _kpi(group_rows)
        prev = _kpi(prev_g.get(key, [])) if prev_rows is not None else None
        items.append((ch, acct, cname, cur, prev))
    items.sort(key=lambda x: -x[3].get(by, 0))
    items = items[:limit]
    blocks = [f"**캠페인 Top {limit} (정렬: {by})**\n"]
    if prev_rows is not None:
        blocks.append("| 채널 | 계정 | 캠페인 | 노출 | 클릭 | 지출 | CTR | CPC | Δ지출 |")
        blocks.append("|---|---|---|---:|---:|---:|---:|---:|---:|")
        for ch, acct, cname, cur, prev in items:
            d = _kpi_delta(cur, prev or {})
            blocks.append(
                f"| {ch} | {acct} | {cname or '(이름없음)'} | {_fmt_n(cur['impressions'])} | "
                f"{_fmt_n(cur['clicks'])} | {_fmt_krw(cur['spend'])} | {_fmt_ctr(cur['ctr'])} | "
                f"{_fmt_krw(cur['cpc'])} | {_fmt_pct(d['spend'])} |"
            )
    else:
        blocks.append("| 채널 | 계정 | 캠페인 | 노출 | 클릭 | 지출 | CTR | CPC |")
        blocks.append("|---|---|---|---:|---:|---:|---:|---:|")
        for ch, acct, cname, cur, _ in items:
            blocks.append(
                f"| {ch} | {acct} | {cname or '(이름없음)'} | {_fmt_n(cur['impressions'])} | "
                f"{_fmt_n(cur['clicks'])} | {_fmt_krw(cur['spend'])} | {_fmt_ctr(cur['ctr'])} | "
                f"{_fmt_krw(cur['cpc'])} |"
            )
    return "\n".join(blocks) + "\n"


def table_bottom_campaigns_efficiency(rows: list[dict], limit: int = 10) -> str:
    """효율 하위(CTR 낮고 지출 큰 캠페인). 의심 캠페인 후보."""
    cur_g = group_by_campaign(rows)
    items = []
    for key, group_rows in cur_g.items():
        ch, acct, cname, cid = key
        cur = _kpi(group_rows)
        if cur["spend"] < 1000:  # 지출 거의 없으면 무의미
            continue
        items.append((ch, acct, cname, cur))
    # 점수: 지출 / CTR (CTR 낮을수록 클수록 의심)
    items.sort(key=lambda x: -(x[3]["spend"] / max(x[3]["ctr"], 0.0001)))
    items = items[:limit]
    blocks = [f"**효율 의심 캠페인 (지출↑ + CTR↓ {limit}개)**\n"]
    blocks.append("| 채널 | 계정 | 캠페인 | 지출 | CTR | CPC |")
    blocks.append("|---|---|---|---:|---:|---:|")
    for ch, acct, cname, cur in items:
        blocks.append(
            f"| {ch} | {acct} | {cname or '(이름없음)'} | {_fmt_krw(cur['spend'])} | "
            f"{_fmt_ctr(cur['ctr'])} | {_fmt_krw(cur['cpc'])} |"
        )
    return "\n".join(blocks) + "\n"


def table_daily(rows: list[dict], prev_rows: Optional[list[dict]] = None) -> str:
    """일별 합계 시계열."""
    cur_g = _group(rows, lambda r: r.get("date", ""))
    blocks = ["**일별 합계**\n"]
    blocks.append("| 날짜 | 노출 | 클릭 | 지출 | 전환 | CTR | CPC |")
    blocks.append("|---|---:|---:|---:|---:|---:|---:|")
    for dt in sorted(cur_g):
        k = _kpi(cur_g[dt])
        blocks.append(
            f"| {dt} | {_fmt_n(k['impressions'])} | {_fmt_n(k['clicks'])} | "
            f"{_fmt_krw(k['spend'])} | {_fmt_n(k['conversions'])} | "
            f"{_fmt_ctr(k['ctr'])} | {_fmt_krw(k['cpc'])} |"
        )
    return "\n".join(blocks) + "\n"


# ─────────────────────────────────────────────
# 통합 fetch + summary 빌더 (Claude 프롬프트용)
# ─────────────────────────────────────────────

def fetch_for_period(since: date, until: date) -> dict:
    """fetch_unified의 얇은 래퍼. 캐시는 그쪽이 관리."""
    return fetch_unified(since, until, force_refresh=False)


def build_summary_markdown(
    data: dict,
    prev_data: Optional[dict] = None,
    include_daily: bool = True,
    include_top_n: int = 10,
) -> str:
    """현재 기간 데이터(+ 옵션으로 비교) → 마크다운 요약. Claude 프롬프트용."""
    naver = data.get("naver", [])
    meta = data.get("meta", [])
    all_rows = naver + meta

    prev_naver = prev_data.get("naver", []) if prev_data else None
    prev_meta = prev_data.get("meta", []) if prev_data else None
    prev_all = (prev_naver or []) + (prev_meta or []) if prev_data else None

    parts = []
    parts.append(table_kpi("종합", _kpi(all_rows), _kpi(prev_all) if prev_all is not None else None))
    parts.append(table_by_channel(naver, meta, prev_naver, prev_meta))
    parts.append(table_by_brand(all_rows, prev_all))
    if include_top_n:
        parts.append(table_top_campaigns(all_rows, by="spend", limit=include_top_n, prev_rows=prev_all))
        parts.append(table_bottom_campaigns_efficiency(all_rows, limit=include_top_n))
    if include_daily:
        parts.append(table_daily(all_rows, prev_all))

    # GA4 (configured + 데이터 있을 때만)
    ga4 = data.get("ga4", {})
    if ga4.get("configured") and ga4.get("daily"):
        parts.append("**GA4 일별 사이트 트래픽**\n")
        parts.append("| 날짜 | 세션 | 사용자 | 페이지뷰 | 이탈률 |")
        parts.append("|---|---:|---:|---:|---:|")
        for r in ga4["daily"]:
            parts.append(
                f"| {r['date']} | {_fmt_n(r['sessions'])} | {_fmt_n(r['users'])} | "
                f"{_fmt_n(r['pageviews'])} | {_fmt_ctr(r['bounce_rate'])} |"
            )
        parts.append("")

    return "\n".join(parts)


def operational_notes() -> list[str]:
    """모든 보고서가 공유하는 운영 컨텍스트. Claude 시스템 프롬프트의 변동분."""
    return [
        "네이버 검색광고는 전환추적 미사용 결정 → conversions=0 고정. CTR/CPC만 의미 있음.",
        "Meta(버거리)는 Pixel COMPLETE_REGISTRATION 기반 전환. iOS 도메인 미인증으로 일부 차단됨.",
        "로얄호프치킨 70원전략은 별도 운영 (저비용 다량 키워드). 다른 캠페인과 분리 판단할 것.",
        "버거리는 보승에프앤비 신규 법인 이전 직후 (2026-05월). 키워드/입찰가 재조정 중.",
        "차주(2026-05-31~)부터 외부 광고대행사 위탁 예정. 본 보고서는 내부 검증·이상치 감시용.",
    ]
