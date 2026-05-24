# -*- coding: utf-8 -*-
"""
통합 광고 대시보드 (Naver + Meta + GA4)

실행:
    streamlit run scripts/dashboard.py

GA4 연동: docs/ga4_setup_guide.md
캐시: data/dashboard_cache.db (1시간 TTL). 강제 갱신 버튼 있음.
"""

import sys
import os
import logging
from pathlib import Path
from datetime import date, datetime, timedelta

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dotenv import load_dotenv

from lib.dashboard_data import fetch_unified, cache_clear

load_dotenv(ROOT / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

st.set_page_config(page_title="광고 통합 대시보드", layout="wide", initial_sidebar_state="expanded")

# ─────────────────────────────────────────────
# Streamlit secrets → os.environ (Cloud 배포 시 .env 대체)
# ─────────────────────────────────────────────

try:
    for _k, _v in dict(st.secrets).items():
        if isinstance(_v, (str, int, float)) and _k not in os.environ:
            os.environ[_k] = str(_v)
except Exception:
    pass

# ─────────────────────────────────────────────
# 비밀번호 게이트 (DASHBOARD_PASSWORD 설정된 경우만 작동)
# ─────────────────────────────────────────────

def _check_password() -> bool:
    expected = os.getenv("DASHBOARD_PASSWORD", "").strip()
    if not expected:
        return True  # 비밀번호 미설정 → 게이트 없음 (로컬 개발)
    if st.session_state.get("auth_ok"):
        return True
    _, mid, _ = st.columns([1, 1, 1])
    with mid:
        st.markdown("### 🔐 광고 대시보드")
        pw = st.text_input("비밀번호", type="password", key="pw_input")
        if st.button("로그인", use_container_width=True):
            if pw == expected:
                st.session_state.auth_ok = True
                st.rerun()
            else:
                st.error("비밀번호가 일치하지 않습니다")
    return False


if not _check_password():
    st.stop()

# ─────────────────────────────────────────────
# Sidebar — 기간/브랜드 컨트롤
# ─────────────────────────────────────────────

st.sidebar.header("📊 컨트롤")

preset = st.sidebar.selectbox(
    "기간",
    ["오늘", "최근 7일", "최근 14일", "최근 30일", "이번 달", "지난 달", "임의 기간"],
    index=1,
)

today = date.today()
if preset == "오늘":
    since, until = today, today
elif preset == "최근 7일":
    since, until = today - timedelta(days=6), today
elif preset == "최근 14일":
    since, until = today - timedelta(days=13), today
elif preset == "최근 30일":
    since, until = today - timedelta(days=29), today
elif preset == "이번 달":
    since, until = today.replace(day=1), today
elif preset == "지난 달":
    first = today.replace(day=1)
    last_prev = first - timedelta(days=1)
    since, until = last_prev.replace(day=1), last_prev
else:  # 임의
    col1, col2 = st.sidebar.columns(2)
    since = col1.date_input("시작", today - timedelta(days=6))
    until = col2.date_input("종료", today)

st.sidebar.caption(f"📅 {since} ~ {until} ({(until - since).days + 1}일)")

brand_filter = st.sidebar.multiselect(
    "브랜드",
    ["로얄호프", "버거리", "기타"],
    default=["로얄호프", "버거리"],
)
channel_filter = st.sidebar.multiselect(
    "채널",
    ["Naver", "Meta"],
    default=["Naver", "Meta"],
)
grain = st.sidebar.radio("집계 단위", ["일별", "주별", "월별"], horizontal=True)

st.sidebar.markdown("---")
if st.sidebar.button("🔄 강제 갱신 (캐시 무시)"):
    cache_clear()
    st.rerun()

# ─────────────────────────────────────────────
# 데이터 fetch
# ─────────────────────────────────────────────

with st.spinner("데이터 로딩 중..."):
    data = fetch_unified(since, until, force_refresh=False)

cache_label = "🟢 캐시" if data.get("from_cache") else "🔵 신규"
st.sidebar.caption(f"{cache_label}  fetched: {data.get('fetched_at', '?')}")

# 통합 DataFrame
rows = []
for row in data.get("naver", []) + data.get("meta", []):
    rows.append(row)
df = pd.DataFrame(rows)

if df.empty:
    st.warning("선택한 기간에 데이터가 없습니다.")
    st.stop()

df["date"] = pd.to_datetime(df["date"])
df["spend"] = df["spend"].astype(float)
df["impressions"] = df["impressions"].astype(int)
df["clicks"] = df["clicks"].astype(int)
df["conversions"] = df["conversions"].astype(int)

# 필터 적용
df_f = df[df["channel"].isin(channel_filter) & df["brand"].isin(brand_filter)].copy()

# ─────────────────────────────────────────────
# 헤더 + KPI 카드
# ─────────────────────────────────────────────

st.title("📊 광고 통합 대시보드")
st.caption(f"기간: **{since} ~ {until}** · 브랜드: {', '.join(brand_filter)} · 채널: {', '.join(channel_filter)}")

def fmt_won(v):
    return f"{int(v):,}원"

def fmt_int(v):
    return f"{int(v):,}"

total_spend = df_f["spend"].sum()
total_imp = df_f["impressions"].sum()
total_clk = df_f["clicks"].sum()
total_conv = df_f["conversions"].sum()
avg_cpc = total_spend / total_clk if total_clk else 0
avg_ctr = total_clk / total_imp * 100 if total_imp else 0
cpa = total_spend / total_conv if total_conv else 0

# GA4 폼완료/리드 (이벤트 기반)
ga4 = data.get("ga4", {})
ga4_lead_events = ["Lead", "CompleteRegistration", "generate_lead", "버거리_가맹문의완료", "Contact"]
ga4_leads = sum(
    e.get("count", 0) for e in ga4.get("by_event", []) if e.get("event_name") in ga4_lead_events
)

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("💰 총 지출", fmt_won(total_spend))
c2.metric("👁 노출", fmt_int(total_imp))
c3.metric("🖱 클릭", fmt_int(total_clk), f"CTR {avg_ctr:.2f}%")
c4.metric("📩 전환(광고)", fmt_int(total_conv), f"CPA {fmt_won(cpa) if cpa else '-'}")
c5.metric("📨 GA4 리드", fmt_int(ga4_leads) if ga4.get("configured") else "—")
c6.metric("💵 평균 CPC", fmt_won(avg_cpc))

if not ga4.get("configured"):
    st.info("ℹ️ GA4 미설정. `docs/ga4_setup_guide.md` 참조 후 .env 채우면 사이트 통합 지표가 활성화됨.")

st.markdown("---")

# ─────────────────────────────────────────────
# 채널 비교
# ─────────────────────────────────────────────

st.subheader("채널 비교")

ch_agg = df_f.groupby("channel", as_index=False).agg(
    spend=("spend", "sum"),
    impressions=("impressions", "sum"),
    clicks=("clicks", "sum"),
    conversions=("conversions", "sum"),
)
ch_agg["CPC"] = ch_agg.apply(lambda r: r["spend"] / r["clicks"] if r["clicks"] else 0, axis=1)
ch_agg["CPA"] = ch_agg.apply(lambda r: r["spend"] / r["conversions"] if r["conversions"] else 0, axis=1)
ch_agg["CTR%"] = ch_agg.apply(lambda r: r["clicks"] / r["impressions"] * 100 if r["impressions"] else 0, axis=1)

col_a, col_b = st.columns([1, 1])
with col_a:
    fig = px.bar(
        ch_agg, x="channel", y="spend", color="channel", text="spend",
        labels={"spend": "지출(원)", "channel": "채널"},
        title="채널별 지출",
    )
    fig.update_traces(texttemplate="%{text:,.0f}원", textposition="outside")
    fig.update_layout(showlegend=False, height=300)
    st.plotly_chart(fig, use_container_width=True)
with col_b:
    fig = px.bar(
        ch_agg, x="channel", y="conversions", color="channel", text="conversions",
        labels={"conversions": "전환", "channel": "채널"},
        title="채널별 전환(광고 어트리뷰션)",
    )
    fig.update_traces(texttemplate="%{text:,}", textposition="outside")
    fig.update_layout(showlegend=False, height=300)
    st.plotly_chart(fig, use_container_width=True)

st.dataframe(
    ch_agg.style.format({"spend": "{:,.0f}원", "CPC": "{:,.0f}원", "CPA": "{:,.0f}원", "CTR%": "{:.2f}%"}),
    use_container_width=True,
)

st.markdown("---")

# ─────────────────────────────────────────────
# 시계열 — 일별/주별/월별
# ─────────────────────────────────────────────

st.subheader(f"시계열 추이 ({grain})")

df_t = df_f.copy()
if grain == "주별":
    df_t["bucket"] = df_t["date"] - pd.to_timedelta(df_t["date"].dt.weekday, unit="d")
elif grain == "월별":
    df_t["bucket"] = df_t["date"].values.astype("datetime64[M]")
else:
    df_t["bucket"] = df_t["date"]

ts = df_t.groupby(["bucket", "channel"], as_index=False).agg(
    spend=("spend", "sum"),
    clicks=("clicks", "sum"),
    conversions=("conversions", "sum"),
)

col_t1, col_t2 = st.columns(2)
with col_t1:
    fig = px.bar(ts, x="bucket", y="spend", color="channel", title="지출 추이", labels={"bucket": "", "spend": "지출(원)"})
    fig.update_layout(height=350, barmode="stack")
    st.plotly_chart(fig, use_container_width=True)
with col_t2:
    fig = px.line(ts, x="bucket", y="clicks", color="channel", markers=True, title="클릭 추이", labels={"bucket": "", "clicks": "클릭"})
    fig.update_layout(height=350)
    st.plotly_chart(fig, use_container_width=True)

if ts["conversions"].sum() > 0:
    fig = px.bar(ts, x="bucket", y="conversions", color="channel", title="전환 추이", labels={"bucket": "", "conversions": "전환"})
    fig.update_layout(height=300, barmode="stack")
    st.plotly_chart(fig, use_container_width=True)

st.markdown("---")

# ─────────────────────────────────────────────
# 캠페인별 세부
# ─────────────────────────────────────────────

st.subheader("캠페인별 세부")

camp_agg = df_f.groupby(["channel", "brand", "account", "campaign_name"], as_index=False).agg(
    spend=("spend", "sum"),
    impressions=("impressions", "sum"),
    clicks=("clicks", "sum"),
    conversions=("conversions", "sum"),
).sort_values("spend", ascending=False)
camp_agg["CPC"] = camp_agg.apply(lambda r: r["spend"] / r["clicks"] if r["clicks"] else 0, axis=1)
camp_agg["CTR%"] = camp_agg.apply(lambda r: r["clicks"] / r["impressions"] * 100 if r["impressions"] else 0, axis=1)
camp_agg["CPA"] = camp_agg.apply(lambda r: r["spend"] / r["conversions"] if r["conversions"] else 0, axis=1)

st.dataframe(
    camp_agg.style.format({
        "spend": "{:,.0f}원",
        "impressions": "{:,}",
        "clicks": "{:,}",
        "conversions": "{:,}",
        "CPC": "{:,.0f}원",
        "CTR%": "{:.2f}%",
        "CPA": "{:,.0f}원",
    }),
    use_container_width=True,
    height=400,
)

# ─────────────────────────────────────────────
# GA4 섹션
# ─────────────────────────────────────────────

if ga4.get("configured"):
    st.markdown("---")
    st.subheader("🌐 GA4 사이트 분석")

    g_col1, g_col2 = st.columns(2)

    with g_col1:
        st.markdown("**채널·매체별 트래픽**")
        src_df = pd.DataFrame(ga4.get("by_source", []))
        if not src_df.empty:
            src_df["source/medium"] = src_df["source"] + " / " + src_df["medium"]
            fig = px.bar(
                src_df.head(10), x="sessions", y="source/medium", orientation="h",
                title="세션 top10 (source/medium)",
            )
            fig.update_layout(height=400, yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(fig, use_container_width=True)

    with g_col2:
        st.markdown("**이벤트별 전환**")
        evt_df = pd.DataFrame(ga4.get("by_event", []))
        if not evt_df.empty:
            evt_df = evt_df.head(15)
            fig = px.bar(evt_df, x="count", y="event_name", orientation="h", title="이벤트 카운트 top15")
            fig.update_layout(height=400, yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("**UTM 캠페인별 세션·전환**")
    camp_ga4 = pd.DataFrame(ga4.get("by_campaign", []))
    if not camp_ga4.empty:
        st.dataframe(
            camp_ga4.style.format({"sessions": "{:,}", "users": "{:,}", "conversions": "{:.1f}"}),
            use_container_width=True,
            height=300,
        )

    st.markdown("**일별 사이트 트래픽**")
    daily_ga4 = pd.DataFrame(ga4.get("daily", []))
    if not daily_ga4.empty:
        daily_ga4["date"] = pd.to_datetime(daily_ga4["date"])
        fig = go.Figure()
        fig.add_trace(go.Bar(x=daily_ga4["date"], y=daily_ga4["sessions"], name="세션"))
        fig.add_trace(go.Scatter(x=daily_ga4["date"], y=daily_ga4["bounce_rate"] * 100, name="이탈률%", yaxis="y2", mode="lines+markers"))
        fig.update_layout(
            title="일별 세션 + 이탈률",
            yaxis=dict(title="세션"),
            yaxis2=dict(title="이탈률 %", overlaying="y", side="right"),
            height=400,
        )
        st.plotly_chart(fig, use_container_width=True)
elif ga4.get("error"):
    st.warning(f"⚠️ GA4 오류: {ga4['error']}")

# ─────────────────────────────────────────────
# 푸터
# ─────────────────────────────────────────────

st.markdown("---")
st.caption(
    f"데이터: Naver Search Ad API + Meta Marketing API"
    + (" + Google Analytics Data API" if ga4.get("configured") else "")
    + f" · 캐시 TTL 1시간 · `dashboard_cache.db`"
)
