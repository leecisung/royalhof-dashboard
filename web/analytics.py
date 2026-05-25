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


# ─────────────────────────────────────────────
# Meta 심층 진단 (킬/유지 결정)
# ─────────────────────────────────────────────

VERDICT_KILL = "🛑 KILL"
VERDICT_FIX = "⚠️ FIX"
VERDICT_KEEP = "✅ KEEP"
VERDICT_BOOST = "🚀 BOOST"
VERDICT_LEARNING = "🌱 LEARNING"


def diagnose_meta_ad_set(
    ad_set: dict,
    an_pct: float = 0.0,
    days_since_created: int | None = None,
) -> dict:
    """
    ad set 종합 진단 — 킬/유지/증액 결정 + 근거 + 즉시 조치.

    반환: {
        verdict: 라벨,
        reasons: [str, ...],     # 결정 근거
        actions: [str, ...],     # 즉시 조치
        learning_status: str,    # 학습 상태 텍스트
        learning_progress: float (0~1, 50건 기준)
    }
    """
    spend = float(ad_set.get("spend", 0) or 0)
    conv = int(ad_set.get("conversions", 0) or 0)
    cpa = float(ad_set.get("cpa", 0) or 0)
    freq = float(ad_set.get("frequency", 0) or 0)

    pause_t = cpl_threshold_pause()
    reduce_t = cpl_threshold_reduce()
    boost_t = cpl_threshold_boost()
    learning_days = int(os.getenv("META_LEARNING_PROTECT_DAYS", "14") or 14)
    freq_t = float(os.getenv("META_FREQUENCY_FATIGUE", "3.0") or 3.0)

    reasons: list[str] = []
    actions: list[str] = []

    # 학습 상태
    if conv == 0:
        learning_status = "❌ 학습 불가 (전환 0 / 50)"
        learning_progress = 0.0
    elif conv < 50:
        learning_status = f"🌱 학습 중 ({conv} / 50)"
        learning_progress = conv / 50
    else:
        learning_status = f"✅ 학습 완료 ({conv} 건)"
        learning_progress = 1.0

    # 1) 학습 기간 보호 (생성 14일 이내)
    if days_since_created is not None and days_since_created < learning_days:
        verdict = VERDICT_LEARNING
        reasons.append(f"학습기간 {days_since_created} / {learning_days}일 — 자동 변경 금지")
        # 학습 중에도 명백한 비효율은 표시
        if conv == 0 and spend >= pause_t * 2:
            verdict = VERDICT_FIX
            reasons.append(f"학습 중인데 spend {int(spend):,}원에 전환 0 — 학습 자체가 안 됨")
            actions.append("픽셀 이벤트(Lead) 발화 여부, 도메인 인증, AN 비중, 카피/랜딩 점검")
        else:
            actions.append("학습 끝날 때까지 유지")
    else:
        # 2) KILL 신호
        if conv == 0 and spend >= pause_t:
            verdict = VERDICT_KILL
            reasons.append(f"전환 0 + spend {int(spend):,}원 (≥ {int(pause_t):,})")
            actions.append("Pause 후 원인 진단 (픽셀·AN·카피)")
        elif conv > 0 and cpa >= pause_t:
            verdict = VERDICT_KILL
            reasons.append(f"CPA {int(cpa):,}원 ≥ {int(pause_t):,}원")
            actions.append("Pause 또는 cost cap = 목표 CPA 의 80% 설정")
        # 3) FIX 신호
        elif conv > 0 and cpa >= reduce_t and spend >= 50000:
            verdict = VERDICT_FIX
            reasons.append(f"CPA {int(cpa):,}원 ≥ {int(reduce_t):,}원 (지출 50k+)")
            actions.append("예산 50% 삭감 또는 cost cap 적용")
        elif freq >= freq_t:
            verdict = VERDICT_FIX
            reasons.append(f"frequency {freq:.1f} ≥ {freq_t:.1f} — 소재 피로")
            actions.append("신규 소재 1~2개 교체, 일주일 후 재평가")
        # 4) BOOST 신호
        elif conv >= 5 and 0 < cpa < boost_t:
            verdict = VERDICT_BOOST
            reasons.append(f"CPA {int(cpa):,}원 < {int(boost_t):,}원 · 전환 {conv}건 — 효율 우수")
            actions.append("예산 30% 증액 (학습 깨지지 않게 한번에 50%↑ 금지)")
        # 5) KEEP
        else:
            verdict = VERDICT_KEEP
            if conv == 0:
                reasons.append(f"spend {int(spend):,}원으로 아직 판단 데이터 부족")
                actions.append("지출 5만원까지 추이 관찰")
            else:
                reasons.append(f"CPA {int(cpa):,}원 · frequency {freq:.1f} — 안정")
                actions.append("현 예산 유지, 주간 CPA 추이 관찰")

    # 6) AN 비중 별도 신호 (verdict 와 별개로 추가)
    if an_pct >= 0.40:
        reasons.append(f"⚠ Audience Network spend 비중 {an_pct*100:.0f}% — 일반적으로 트래픽 품질 낮음")
        actions.append("Placement Manual → Audience Network OFF (또는 별도 ad set 분리)")
    elif an_pct >= 0.25:
        reasons.append(f"AN 비중 {an_pct*100:.0f}% — 관찰")

    return {
        "verdict": verdict,
        "reasons": reasons,
        "actions": actions,
        "learning_status": learning_status,
        "learning_progress": learning_progress,
    }


def analyze_meta_placements(placements: list[dict]) -> dict:
    """
    placement breakdown 집계 + 권고.

    반환: {
        by_platform: [{publisher_platform, spend, conversions, cpa, ctr, share}, ...],
        by_adset: {ad_set_id: {an_pct, total_spend}},
        warnings: [str, ...],
    }
    """
    total_spend = sum(p.get("spend", 0) for p in placements) or 1.0

    plat: dict[str, dict] = {}
    for p in placements:
        key = p.get("publisher_platform", "unknown")
        if key not in plat:
            plat[key] = {
                "publisher_platform": key,
                "impressions": 0, "clicks": 0,
                "spend": 0.0, "conversions": 0,
            }
        plat[key]["impressions"] += p.get("impressions", 0)
        plat[key]["clicks"] += p.get("clicks", 0)
        plat[key]["spend"] += p.get("spend", 0)
        plat[key]["conversions"] += p.get("conversions", 0)

    by_platform = []
    for k, v in plat.items():
        v["cpa"] = int(v["spend"] / v["conversions"]) if v["conversions"] else 0
        v["ctr"] = (v["clicks"] / v["impressions"] * 100) if v["impressions"] else 0.0
        v["share"] = v["spend"] / total_spend
        v["spend"] = int(v["spend"])
        by_platform.append(v)
    by_platform.sort(key=lambda x: -x["spend"])

    by_adset: dict[str, dict] = {}
    for p in placements:
        aid = p.get("ad_set_id", "")
        if not aid:
            continue
        if aid not in by_adset:
            by_adset[aid] = {"total": 0.0, "an": 0.0}
        by_adset[aid]["total"] += p.get("spend", 0)
        if p.get("publisher_platform") == "audience_network":
            by_adset[aid]["an"] += p.get("spend", 0)
    for aid in by_adset:
        t = by_adset[aid]["total"] or 1.0
        by_adset[aid]["an_pct"] = by_adset[aid]["an"] / t

    warnings = []
    an = next((p for p in by_platform if p["publisher_platform"] == "audience_network"), None)
    if an and an["share"] >= 0.30:
        warnings.append(
            f"🚨 Audience Network spend 비중 <strong>{an['share']*100:.0f}%</strong> "
            f"({an['spend']:,}원, CPA {an['cpa']:,}원). AN은 클릭당 비용이 싸지만 가맹 리드 같은 "
            f"고관여 전환에는 거의 기여 못함. Manual placement 로 AN 끄거나 별도 ad set 으로 분리 권장."
        )
    elif an and an["share"] >= 0.15:
        warnings.append(
            f"AN 비중 {an['share']*100:.0f}% — 임계 (30%) 미만이지만 CPA {an['cpa']:,}원 모니터링."
        )

    return {"by_platform": by_platform, "by_adset": by_adset, "warnings": warnings}


def generate_meta_strategy(
    ad_sets_with_diag: list[dict],
    placement_analysis: dict,
    cur_kpi: dict,
    prev_kpi: dict | None,
) -> list[str]:
    """
    페이지 상단 종합 전략 제언 (HTML 허용).
    """
    notes: list[str] = []

    kill = [a for a in ad_sets_with_diag if a.get("diagnosis", {}).get("verdict") == VERDICT_KILL]
    fix = [a for a in ad_sets_with_diag if a.get("diagnosis", {}).get("verdict") == VERDICT_FIX]
    boost = [a for a in ad_sets_with_diag if a.get("diagnosis", {}).get("verdict") == VERDICT_BOOST]
    learning = [a for a in ad_sets_with_diag if a.get("diagnosis", {}).get("verdict") == VERDICT_LEARNING]

    if kill:
        kill_spend = sum(int(a.get("spend", 0)) for a in kill)
        notes.append(
            f"🛑 <strong>즉시 KILL 후보 {len(kill)}개</strong> "
            f"(누적 spend {kill_spend:,}원). 전환 0 또는 CPA 위험치 초과 — 학습 회복 불가, 빨리 끊고 예산 재배분."
        )
    if fix:
        notes.append(
            f"⚠️ <strong>FIX 필요 {len(fix)}개</strong> — 예산 삭감·소재 교체·cost cap 등 즉시 조치. 끄지 말고 고치는 게 우선."
        )
    if boost:
        notes.append(
            f"🚀 <strong>BOOST 가능 {len(boost)}개</strong> — CPA 목표 하회 + 전환 5+. "
            f"30% 정도 점진 증액. 한번에 50%↑ 올리면 학습 깨짐."
        )
    if learning:
        notes.append(
            f"🌱 학습기간 보호 {len(learning)}개 — 14일 안 됐거나 데이터 부족. 자동 변경 금지, 14일 후 재평가."
        )

    # 학습 불가 비율
    no_conv = [a for a in ad_sets_with_diag if int(a.get("conversions", 0) or 0) == 0]
    if no_conv and len(no_conv) >= len(ad_sets_with_diag) * 0.5:
        notes.append(
            "🧪 <strong>ad set 의 절반 이상이 전환 0</strong> — 채널 단위 문제 의심: "
            "픽셀 Lead 이벤트 발화 검증, 도메인 인증, 랜딩 폼 점검 우선."
        )

    # placement 경고 흡수
    for w in placement_analysis.get("warnings", []):
        notes.append(w)

    # CPA 추이
    if prev_kpi and cur_kpi.get("cpa") and prev_kpi.get("cpa"):
        delta = (cur_kpi["cpa"] - prev_kpi["cpa"]) / prev_kpi["cpa"]
        if delta <= -0.15:
            notes.append(
                f"📉 평균 CPA {int(prev_kpi['cpa']):,}→{int(cur_kpi['cpa']):,}원 "
                f"({delta*100:+.0f}%) — 정리 효과. 줄인 ad set 의 예산을 BOOST 후보로 이전."
            )
        elif delta >= 0.20:
            notes.append(
                f"📈 평균 CPA {int(prev_kpi['cpa']):,}→{int(cur_kpi['cpa']):,}원 "
                f"({delta*100:+.0f}%) — 악화. 위 KILL/FIX 즉시 실행."
            )

    if not notes:
        notes.append("✅ 특이사항 없음 — 모든 ad set 안정. 주간 추이 모니터링.")

    return notes
