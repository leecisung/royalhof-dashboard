# -*- coding: utf-8 -*-
"""
대시보드 페이지용 분석/분류 헬퍼.

- budget_decision: 캠페인의 예산 변경 권고 (Hold/Increase/Decrease/Pause)
- creative_status: 광고 소재의 상태 (Winner/Winner Declining/Learning/Kill)
- insight_rules: 룰베이스 인사이트 생성 (잘한점/개선점/주의)
- CPL 기준 (광고대행사 위탁 후 내부 검증용. 가맹 리드 단가가 핵심)
"""

import os
from datetime import date


# ─────────────────────────────────────────────
# Meta 캠페인 분류 (자체 룰)
# ─────────────────────────────────────────────

def cpl_threshold_pause() -> float:
    return float(os.getenv("META_CPA_PAUSE_THRESHOLD", "50000") or 50000)


def cpl_threshold_reduce() -> float:
    return float(os.getenv("META_CPA_REDUCE_THRESHOLD", "30000") or 30000)


def cpl_threshold_boost() -> float:
    return float(os.getenv("META_CPA_BOOST_THRESHOLD", "15000") or 15000)


def classify_budget_decision(spend: float, conv: int, cpa: float) -> tuple[str, str]:
    """
    캠페인/ad_set 의 budget_decision 자동 분류.
    반환: (라벨, 이유) — 예: ("Increase", "CPA 12,000 < 15k & 전환 5+")
    """
    pause = cpl_threshold_pause()
    reduce = cpl_threshold_reduce()
    boost = cpl_threshold_boost()

    # 전환 없음 + 지출 큼 → Pause
    if conv == 0 and spend >= 50000:
        return ("Pause", f"전환 0건인데 지출 {int(spend):,}원")
    # CPA가 위험 임계 초과
    if conv > 0 and cpa > pause:
        return ("Pause", f"CPA {int(cpa):,}원 > {int(pause):,}원 (위험)")
    if conv > 0 and cpa > reduce and spend >= 50000:
        return ("Decrease", f"CPA {int(cpa):,}원 > {int(reduce):,}원 & 지출 50k+")
    # 효율 좋음 → Boost
    if conv >= 5 and cpa < boost:
        return ("Increase", f"CPA {int(cpa):,}원 < {int(boost):,}원 & 전환 5+")
    # 지출 적음 → 학습 중
    if spend < 30000:
        return ("Hold", "학습 단계 (지출 30k 미만)")
    return ("Hold", "양호 — 추이 관찰")


# ─────────────────────────────────────────────
# Meta 광고 소재 분류
# ─────────────────────────────────────────────

def classify_creative_status(ctr: float, cpa: float, conv: int, spend: float, days_since_created: int | None = None) -> tuple[str, str]:
    """
    광고 단위(소재) 의 creative_status 자동 분류.
    반환: (라벨, 이유)
    days_since_created가 None이면 학습기간 보호 룰 적용 안 함.
    """
    boost = cpl_threshold_boost()
    pause = cpl_threshold_pause()

    # 학습기간 (생성 14일 이내)
    if days_since_created is not None and days_since_created <= 14:
        if spend < 20000:
            return ("Learning", f"학습 중 ({days_since_created}일차, 지출 적음)")

    # 지출 거의 없으면 Learning
    if spend < 5000:
        return ("Learning", "지출 작음 — 학습 단계")

    # 전환 없고 지출 큼 → Kill
    if conv == 0 and spend >= 30000:
        return ("Kill", f"전환 0 + 지출 {int(spend/1000)}k — 학습 실패")

    # CPA 매우 높음 → Kill
    if conv > 0 and cpa > pause * 1.5:
        return ("Kill", f"CPA {int(cpa):,}원 — 위험 수준")

    # 좋은 CTR + 좋은 CPA → Winner
    if ctr >= 3.0 and conv > 0 and cpa <= boost * 1.5:
        return ("Winner", f"CTR {ctr:.1f}% + CPA {int(cpa):,}원")

    # CTR 좋은데 CPA 애매 → Winner Declining
    if ctr >= 2.5 and conv > 0 and cpa <= cpl_threshold_reduce():
        return ("Winner (Declining)", f"CTR {ctr:.1f}% 양호 / CPA {int(cpa):,}원 — 관찰")

    # CTR 낮음 → Kill Learning Fail
    if ctr < 1.0 and spend >= 10000:
        return ("Kill (Learning Fail)", f"CTR {ctr:.2f}% < 1% — 클릭률 부족")

    return ("Learning", "추이 관찰")


# ─────────────────────────────────────────────
# Naver 키워드 권고 (입찰가 인하/OFF)
# ─────────────────────────────────────────────

def classify_keyword_action(cpc: float, impressions: int, clicks: int) -> tuple[str, str] | None:
    """
    Naver 키워드 단위 권고. None이면 조치 불필요.
    """
    if clicks == 0 and impressions < 100:
        return None  # 노출도 거의 없음 — 컷 후보지만 별도 룰
    if cpc >= 30000:
        return ("🛑 OFF", f"클릭당 {int(cpc):,}원 — 30k 이상은 컷")
    if cpc >= 10000:
        return ("⚠️ 입찰가 인하", f"클릭당 {int(cpc):,}원 — 10k 초과")
    return None


# ─────────────────────────────────────────────
# 룰베이스 인사이트 (페이지 상단 카드)
# ─────────────────────────────────────────────

def generate_meta_insights(
    current_kpi: dict,
    previous_kpi: dict | None,
    ad_sets: list[dict],
    ads: list[dict],
) -> dict:
    """{잘한점:[..], 개선점:[..], 주의:[..]} 형태."""
    wins, fixes, warnings = [], [], []

    # KPI 변화
    if previous_kpi:
        cur_cpl = current_kpi.get("cpa", 0) or 0
        prev_cpl = previous_kpi.get("cpa", 0) or 0
        if cur_cpl and prev_cpl:
            delta = (cur_cpl - prev_cpl) / prev_cpl
            if delta <= -0.15:
                wins.append(f"CPL이 직전기간 대비 {abs(delta)*100:.0f}% 개선 ({int(prev_cpl):,}→{int(cur_cpl):,}원)")
            elif delta >= 0.20:
                fixes.append(f"CPL이 직전기간 대비 {delta*100:.0f}% 악화 ({int(prev_cpl):,}→{int(cur_cpl):,}원) — 원인 캠페인 점검")
        cur_ctr = current_kpi.get("ctr", 0) or 0
        prev_ctr = previous_kpi.get("ctr", 0) or 0
        if prev_ctr:
            ctr_delta = (cur_ctr - prev_ctr) / prev_ctr
            if ctr_delta <= -0.20:
                fixes.append(f"CTR {prev_ctr:.2f}%→{cur_ctr:.2f}% ({ctr_delta*100:.0f}%) — 소재 피로도 의심")

    # Pause 후보
    pause_targets = [
        a for a in ad_sets
        if a["conversions"] == 0 and a["spend"] >= 50000
    ]
    if pause_targets:
        for t in pause_targets[:3]:
            warnings.append(
                f"⏸ '{t['ad_set_name']}' — 전환 0 + 지출 {int(t['spend']):,}원 → Pause 권장"
            )

    # 좋은 소재
    boost = cpl_threshold_boost()
    winners = [a for a in ads if a["conversions"] >= 5 and a["cpa"] and a["cpa"] < boost]
    if winners:
        top = sorted(winners, key=lambda x: -x["conversions"])[0]
        wins.append(f"🏆 '{top['ad_name']}' 소재 — CPL {int(top['cpa']):,}원, 전환 {top['conversions']}건")

    # frequency 피로
    fatigue_threshold = float(os.getenv("META_FREQUENCY_FATIGUE", "3.0") or 3.0)
    fatigued = [a for a in ad_sets if a["frequency"] >= fatigue_threshold]
    if fatigued:
        for t in fatigued[:2]:
            warnings.append(
                f"📛 '{t['ad_set_name']}' frequency {t['frequency']:.1f} — 소재 교체 필요"
            )

    if not wins:
        wins.append("특이사항 없음 (변화 작음)")
    if not fixes and not warnings:
        fixes.append("개선 우선순위 후보 없음 — 안정적")

    return {"wins": wins, "fixes": fixes, "warnings": warnings}


def generate_naver_insights(
    current_kpi: dict,
    previous_kpi: dict | None,
    expensive_keywords: list[dict],
) -> dict:
    """Naver 페이지용 인사이트."""
    wins, fixes, warnings = [], [], []

    if previous_kpi:
        cur_cpc = current_kpi.get("cpc", 0) or 0
        prev_cpc = previous_kpi.get("cpc", 0) or 0
        if cur_cpc and prev_cpc:
            delta = (cur_cpc - prev_cpc) / prev_cpc
            if delta <= -0.10:
                wins.append(f"평균 CPC {int(prev_cpc):,}→{int(cur_cpc):,}원 ({delta*100:.0f}%) — 키워드 정리 효과")
            elif delta >= 0.15:
                fixes.append(f"평균 CPC {int(prev_cpc):,}→{int(cur_cpc):,}원 ({delta*100:+.0f}%) — 고비용 키워드 점검")

    # 비싼 키워드 합산
    over_30k = [k for k in expensive_keywords if k["cpc"] >= 30000]
    over_10k = [k for k in expensive_keywords if 10000 <= k["cpc"] < 30000]

    # 지출 키는 항목 출처에 따라 spend(캠페인) 또는 cost(키워드 단위) 둘 다 가능
    def _spend(k: dict) -> int:
        return int(k.get("spend", k.get("cost", 0)) or 0)

    if over_30k:
        spend_sum = sum(_spend(k) for k in over_30k)
        fixes.append(f"🛑 CPC 30k+ 키워드 {len(over_30k)}개 (지출 {spend_sum:,}원) — 즉시 OFF 권장")
    if over_10k:
        spend_sum = sum(_spend(k) for k in over_10k)
        fixes.append(f"⚠️ CPC 10~30k 키워드 {len(over_10k)}개 (지출 {spend_sum:,}원) — 입찰가 단계적 인하")

    if not over_30k and not over_10k:
        wins.append("CPC 10k 이상 키워드 없음 — 효율 양호")

    return {"wins": wins, "fixes": fixes, "warnings": warnings}
