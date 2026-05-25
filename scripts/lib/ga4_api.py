# -*- coding: utf-8 -*-
"""
GA4 Data API 클라이언트.

인증: 본인 OAuth 사용자 토큰 (.ga4-user-token.json).
이유: GA4 admin이 Service Account 이메일 추가를 거부하는 알려진 이슈 우회.
본인(GA4 관리자)이 직접 만든 OAuth 클라이언트로 한 번만 인증하면 끝.

발급 흐름:
    1. GCP Console → OAuth 클라이언트 ID (데스크톱 앱) 생성 → JSON 다운로드
    2. 파일을 .ga4-oauth-client.json 으로 저장 (gitignored)
    3. python scripts/ga4_oauth_setup.py 1회 실행
    4. 브라우저에서 본인 GA4 관리자 계정 로그인 + 권한 승인
    5. .ga4-user-token.json 자동 생성 → 이후 자동 사용

대시보드 호환을 위해 기존 메서드 시그니처/반환값 유지.
"""

import os
import json
import logging
from pathlib import Path
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)


class GA4NotConfigured(RuntimeError):
    """GA4 환경변수/토큰 미설정. 대시보드는 이 예외 잡아서 GA4 섹션만 disable."""


def _load_credentials(token_path: Path, client_path: Optional[Path] = None):
    """OAuth 사용자 토큰 → Credentials 객체. 만료 시 refresh 자동."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError as e:
        raise RuntimeError(f"google-auth 미설치: pip install google-auth ({e})")

    if not token_path.exists():
        raise GA4NotConfigured(
            f"OAuth 토큰 없음 ({token_path}). "
            f"python scripts/ga4_oauth_setup.py 로 1회 발급 필요."
        )
    data = json.loads(token_path.read_text(encoding="utf-8"))
    creds = Credentials.from_authorized_user_info(data, data.get("scopes"))
    # 만료됐으면 새 access token 갱신
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
        logger.info("[GA4] access token 자동 갱신 완료")
    return creds


class GA4API:
    def __init__(self, property_id: str, token_path: Path):
        self.property_id = str(property_id)
        try:
            from google.analytics.data_v1beta import BetaAnalyticsDataClient
        except ImportError as e:
            raise RuntimeError(
                f"google-analytics-data 미설치: pip install google-analytics-data ({e})"
            )
        creds = _load_credentials(token_path)
        self.client = BetaAnalyticsDataClient(credentials=creds)
        logger.info("[GA4] 초기화 property=%s (OAuth user token)", self.property_id)

    @classmethod
    def from_env(cls) -> "GA4API":
        pid = os.getenv("GA4_PROPERTY_ID", "").strip()
        if not pid:
            raise GA4NotConfigured("GA4_PROPERTY_ID 미설정")
        token_path_str = os.getenv("GA4_OAUTH_TOKEN_PATH", "").strip()
        if token_path_str:
            token_path = Path(token_path_str)
        else:
            # 기본 경로: 프로젝트 루트
            token_path = Path(__file__).parents[2] / ".ga4-user-token.json"
        return cls(pid, token_path)

    # ─────────────────────────────────────────────
    # 핵심 fetch 메서드
    # ─────────────────────────────────────────────

    def fetch_daily_summary(self, since: date, until: date) -> list[dict]:
        """일별 세션·사용자·페이지뷰·이탈률·평균체류."""
        rows = self._run(
            dimensions=["date"],
            metrics=["sessions", "totalUsers", "screenPageViews", "bounceRate", "averageSessionDuration"],
            since=since, until=until,
        )
        out = []
        for r in rows:
            dv = r["dim"][0]
            out.append({
                "date": f"{dv[0:4]}-{dv[4:6]}-{dv[6:8]}",
                "sessions": int(r["metric"][0] or 0),
                "users": int(r["metric"][1] or 0),
                "pageviews": int(r["metric"][2] or 0),
                "bounce_rate": float(r["metric"][3] or 0),
                "avg_session_duration": float(r["metric"][4] or 0),
            })
        return sorted(out, key=lambda x: x["date"])

    def fetch_by_source(self, since: date, until: date) -> list[dict]:
        """채널/매체별 트래픽."""
        rows = self._run(
            dimensions=["sessionSource", "sessionMedium"],
            metrics=["sessions", "totalUsers", "conversions"],
            since=since, until=until,
        )
        out = []
        for r in rows:
            out.append({
                "source": r["dim"][0],
                "medium": r["dim"][1],
                "sessions": int(r["metric"][0] or 0),
                "users": int(r["metric"][1] or 0),
                "conversions": float(r["metric"][2] or 0),
            })
        return sorted(out, key=lambda x: -x["sessions"])

    def fetch_by_campaign(self, since: date, until: date) -> list[dict]:
        """UTM campaign 단위."""
        rows = self._run(
            dimensions=["sessionSource", "sessionMedium", "sessionCampaignName"],
            metrics=["sessions", "totalUsers", "conversions"],
            since=since, until=until,
        )
        out = []
        for r in rows:
            out.append({
                "source": r["dim"][0],
                "medium": r["dim"][1],
                "campaign": r["dim"][2],
                "sessions": int(r["metric"][0] or 0),
                "users": int(r["metric"][1] or 0),
                "conversions": float(r["metric"][2] or 0),
            })
        return sorted(out, key=lambda x: -x["sessions"])

    def fetch_conversions_by_event(self, since: date, until: date) -> list[dict]:
        """이벤트별 카운트."""
        rows = self._run(
            dimensions=["eventName"],
            metrics=["eventCount", "totalUsers"],
            since=since, until=until,
        )
        out = []
        for r in rows:
            out.append({
                "event_name": r["dim"][0],
                "count": int(r["metric"][0] or 0),
                "users": int(r["metric"][1] or 0),
            })
        return sorted(out, key=lambda x: -x["count"])

    def fetch_landing_pages(self, since: date, until: date, limit: int = 20) -> list[dict]:
        """랜딩 페이지 top N."""
        rows = self._run(
            dimensions=["landingPagePlusQueryString"],
            metrics=["sessions", "bounceRate", "averageSessionDuration"],
            since=since, until=until,
            limit=limit,
        )
        out = []
        for r in rows:
            out.append({
                "page": r["dim"][0],
                "sessions": int(r["metric"][0] or 0),
                "bounce_rate": float(r["metric"][1] or 0),
                "avg_duration": float(r["metric"][2] or 0),
            })
        return sorted(out, key=lambda x: -x["sessions"])

    # ─────────────────────────────────────────────
    # 내부
    # ─────────────────────────────────────────────

    def _run(self, dimensions, metrics, since: date, until: date, limit: int = 1000) -> list[dict]:
        from google.analytics.data_v1beta.types import (
            DateRange, Dimension, Metric, RunReportRequest,
        )
        req = RunReportRequest(
            property=f"properties/{self.property_id}",
            dimensions=[Dimension(name=d) for d in dimensions],
            metrics=[Metric(name=m) for m in metrics],
            date_ranges=[DateRange(start_date=str(since), end_date=str(until))],
            limit=limit,
        )
        resp = self.client.run_report(req)
        out = []
        for row in resp.rows:
            out.append({
                "dim": [d.value for d in row.dimension_values],
                "metric": [m.value for m in row.metric_values],
            })
        logger.info("[GA4] run_report %s × %s : %d rows", dimensions, metrics, len(out))
        return out


if __name__ == "__main__":
    import sys
    from datetime import timedelta
    from dotenv import load_dotenv
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    load_dotenv()
    try:
        api = GA4API.from_env()
        today = date.today()
        since = today - timedelta(days=7)
        rows = api.fetch_daily_summary(since, today)
        if not rows:
            print(f"⚠️ 데이터 없음. 기간 안에 트래픽이 없거나 새로 만든 property일 수 있음.")
        else:
            print(f"✅ GA4 연결 성공 (property {api.property_id})")
            print(f"   기간 {since} ~ {today}, {len(rows)}일")
            total = sum(r["sessions"] for r in rows)
            print(f"   총 세션: {total:,}")
            for r in rows[-3:]:
                print(f"   {r['date']}  sessions={r['sessions']:,}  bounce={r['bounce_rate']:.1%}")
    except GA4NotConfigured as e:
        print(f"⚠️ GA4 미설정: {e}")
    except Exception as e:
        print(f"❌ GA4 오류: {e}")
        import traceback
        traceback.print_exc()
