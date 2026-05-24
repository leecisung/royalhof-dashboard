# -*- coding: utf-8 -*-
"""
meta_daily_analysis.py — 버거리 Meta 광고 일별 분석

기존 meta_adhoc_query.py는 N일 '합산'만 지원한다. 이 스크립트는
time_increment=1 로 하루 단위로 쪼개서 일별 추세를 본다.

사용:
  python scripts/meta_daily_analysis.py                  # ad set 생성일 ~ 오늘
  python scripts/meta_daily_analysis.py --days 7         # 최근 7일
  python scripts/meta_daily_analysis.py --since 2026-05-20 --until 2026-05-22
  python scripts/meta_daily_analysis.py --no-report      # 보고서 파일 저장 안 함
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import date, datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.meta_api import MetaAdsAPI

CONFIG_PATH = ROOT / "data" / "meta_ad_sets.json"
REPORTS_DIR = ROOT / "reports"


def _daily_rows(api, obj, level, since, until):
    """obj(get_insights 가진 SDK 객체)에서 time_increment=1 일별 행을 뽑는다."""
    from facebook_business.adobjects.adsinsights import AdsInsights

    params = {
        "time_range": {"since": str(since), "until": str(until)},
        "time_increment": 1,
        "level": level,
    }
    fields = [
        AdsInsights.Field.date_start,
        AdsInsights.Field.ad_id,
        AdsInsights.Field.ad_name,
        AdsInsights.Field.impressions,
        AdsInsights.Field.clicks,
        AdsInsights.Field.spend,
        AdsInsights.Field.ctr,
        AdsInsights.Field.cpc,
        AdsInsights.Field.cpm,
        AdsInsights.Field.reach,
        AdsInsights.Field.frequency,
        AdsInsights.Field.actions,
    ]
    cursor = api._call(obj.get_insights, fields=fields, params=params)
    out = []
    for r in cursor:
        d = dict(r)
        spend = float(d.get("spend", 0) or 0)
        conv = api._extract_conversions(d.get("actions", []))
        out.append({
            "date":        d.get("date_start", ""),
            "ad_id":       d.get("ad_id", ""),
            "ad_name":     d.get("ad_name", ""),
            "impressions": int(d.get("impressions", 0) or 0),
            "clicks":      int(d.get("clicks", 0) or 0),
            "spend":       spend,
            "ctr":         float(d.get("ctr", 0) or 0),
            "cpc":         float(d.get("cpc", 0) or 0),
            "cpm":         float(d.get("cpm", 0) or 0),
            "reach":       int(d.get("reach", 0) or 0),
            "frequency":   float(d.get("frequency", 0) or 0),
            "conversions": conv,
            "cpa":         (spend / conv) if conv else 0.0,
        })
    return out


def adset_daily(api, ad_set_id, since, until):
    from facebook_business.adobjects.adset import AdSet
    return _daily_rows(api, AdSet(ad_set_id), "adset", since, until)


def adset_ads_daily(api, ad_set_id, since, until):
    from facebook_business.adobjects.adset import AdSet
    return _daily_rows(api, AdSet(ad_set_id), "ad", since, until)


def fmt_table(rows, header, keys, widths):
    lines = []
    head = "  ".join(f"{h:>{w}}" for h, w in zip(header, widths))
    lines.append(head)
    lines.append("-" * len(head))
    for row in rows:
        cells = []
        for k, w in zip(keys, widths):
            v = row.get(k, "")
            if isinstance(v, float):
                cells.append(f"{v:>{w},.2f}")
            elif isinstance(v, int):
                cells.append(f"{v:>{w},}")
            else:
                cells.append(f"{str(v):>{w}}")
        lines.append("  ".join(cells))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="버거리 Meta 광고 일별 분석")
    parser.add_argument("--since", help="YYYY-MM-DD")
    parser.add_argument("--until", help="YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--days", type=int, help="최근 N일 (since 미지정 시)")
    parser.add_argument("--no-report", action="store_true", help="보고서 파일 저장 안 함")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    managed = [m for m in cfg.get("managed", []) if m.get("enabled", True)]
    discovered = {d["ad_set_id"]: d for d in cfg.get("discovered", [])}

    today = date.today()
    until = datetime.strptime(args.until, "%Y-%m-%d").date() if args.until else today

    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").date()
    elif args.days:
        since = until - timedelta(days=args.days - 1)
    else:
        # ad set 생성일 중 가장 이른 날
        created = []
        for m in managed:
            ct = discovered.get(m["ad_set_id"], {}).get("created_time", "")
            if ct:
                created.append(datetime.strptime(ct[:10], "%Y-%m-%d").date())
        since = min(created) if created else until - timedelta(days=6)

    api = MetaAdsAPI.from_env()

    out = []
    out.append("=" * 78)
    out.append(f"버거리 Meta 광고 일별 분석   기간 {since} ~ {until}   픽셀이벤트={api.pixel_event}")
    out.append("=" * 78)

    combined = {}  # date -> 합산
    report_lines = [
        "# 버거리 Meta 광고 일별 분석",
        "",
        f"**기간**: {since} ~ {until}",
        f"**생성일**: {today}",
        f"**전환 기준**: Pixel `{api.pixel_event}`",
        "",
    ]

    for m in managed:
        alias = m.get("alias", m["ad_set_id"])
        disc = discovered.get(m["ad_set_id"], {})
        rows = adset_daily(api, m["ad_set_id"], since, until)
        rows.sort(key=lambda r: r["date"])

        out.append("")
        out.append(f"■ {alias}  ({disc.get('name','')})  campaign={disc.get('campaign_name','')}")
        out.append(f"  생성: {disc.get('created_time','?')[:10]}  최적화: {disc.get('optimization_goal','?')}")
        if not rows:
            out.append("  (기간 내 데이터 없음 — 노출 0)")
        else:
            keys = ["date", "impressions", "clicks", "spend", "ctr", "cpc", "cpm", "reach", "frequency", "conversions", "cpa"]
            hdr  = ["날짜", "노출", "클릭", "지출", "CTR%", "CPC", "CPM", "도달", "freq", "전환", "CPA"]
            wid  = [10, 8, 6, 9, 6, 7, 7, 8, 6, 5, 9]
            out.append(fmt_table(rows, hdr, keys, wid))

        report_lines.append(f"## {alias} — {disc.get('name','')}")
        report_lines.append("")
        report_lines.append("| 날짜 | 노출 | 클릭 | 지출 | CTR | CPC | CPM | 도달 | freq | 전환 | CPA |")
        report_lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            report_lines.append(
                f"| {r['date']} | {r['impressions']:,} | {r['clicks']:,} | {int(r['spend']):,}원 "
                f"| {r['ctr']:.2f}% | {int(r['cpc']):,}원 | {int(r['cpm']):,}원 | {r['reach']:,} "
                f"| {r['frequency']:.2f} | {r['conversions']} | {int(r['cpa']):,}원 |"
            )
        if not rows:
            report_lines.append("| _기간 내 데이터 없음_ | | | | | | | | | | |")
        report_lines.append("")

        for r in rows:
            c = combined.setdefault(r["date"], {"impressions": 0, "clicks": 0, "spend": 0.0, "conversions": 0})
            c["impressions"] += r["impressions"]
            c["clicks"] += r["clicks"]
            c["spend"] += r["spend"]
            c["conversions"] += r["conversions"]

        # 광고(크리에이티브 A/B)별 일별
        ad_rows = adset_ads_daily(api, m["ad_set_id"], since, until)
        if ad_rows:
            ad_rows.sort(key=lambda r: (r["date"], r["ad_name"]))
            out.append(f"  └ 광고별(A/B):")
            keys = ["date", "ad_name", "impressions", "clicks", "spend", "ctr", "conversions", "cpa"]
            hdr  = ["날짜", "광고", "노출", "클릭", "지출", "CTR%", "전환", "CPA"]
            wid  = [10, 22, 8, 6, 9, 6, 5, 9]
            out.append(fmt_table(ad_rows, hdr, keys, wid))
            report_lines.append(f"### {alias} 광고별(A/B) 일별")
            report_lines.append("")
            report_lines.append("| 날짜 | 광고 | 노출 | 클릭 | 지출 | CTR | 전환 | CPA |")
            report_lines.append("|---|---|---|---|---|---|---|---|")
            for r in ad_rows:
                report_lines.append(
                    f"| {r['date']} | {r['ad_name']} | {r['impressions']:,} | {r['clicks']:,} "
                    f"| {int(r['spend']):,}원 | {r['ctr']:.2f}% | {r['conversions']} | {int(r['cpa']):,}원 |"
                )
            report_lines.append("")

    # 일별 합산
    out.append("")
    out.append("■ 일별 합산 (managed ad set 전체)")
    if combined:
        crows = []
        for d in sorted(combined):
            c = combined[d]
            crows.append({
                "date": d,
                "impressions": c["impressions"],
                "clicks": c["clicks"],
                "spend": c["spend"],
                "conversions": c["conversions"],
                "cpa": (c["spend"] / c["conversions"]) if c["conversions"] else 0.0,
            })
        keys = ["date", "impressions", "clicks", "spend", "conversions", "cpa"]
        hdr  = ["날짜", "노출", "클릭", "지출", "전환", "CPA"]
        wid  = [10, 10, 8, 11, 6, 10]
        out.append(fmt_table(crows, hdr, keys, wid))

        report_lines.append("## 일별 합산 (managed ad set 전체)")
        report_lines.append("")
        report_lines.append("| 날짜 | 노출 | 클릭 | 지출 | 전환 | CPA |")
        report_lines.append("|---|---|---|---|---|---|")
        for r in crows:
            report_lines.append(
                f"| {r['date']} | {r['impressions']:,} | {r['clicks']:,} | {int(r['spend']):,}원 "
                f"| {r['conversions']} | {int(r['cpa']):,}원 |"
            )
        report_lines.append("")
    else:
        out.append("  (전 기간 노출 0 — 광고가 아직 게재되지 않았거나 검수 대기 중)")
        report_lines.append("## 일별 합산")
        report_lines.append("")
        report_lines.append("_전 기간 노출 0._")
        report_lines.append("")

    report_lines.append("---")
    report_lines.append("*자동 생성: meta_daily_analysis.py*")

    print("\n".join(out))

    if not args.no_report:
        REPORTS_DIR.mkdir(exist_ok=True)
        path = REPORTS_DIR / f"meta_daily_{since:%Y%m%d}_{until:%Y%m%d}.md"
        path.write_text("\n".join(report_lines), encoding="utf-8-sig")
        print(f"\n보고서 저장: {path}")


if __name__ == "__main__":
    main()
