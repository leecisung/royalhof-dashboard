# -*- coding: utf-8 -*-
"""
GA4 데이터 클라이언트 — BigQuery export 방식.

GA4 Data API 대신 GA4 → BigQuery 일간 export(events_YYYYMMDD)를 직접 쿼리.
이유: GA4 admin이 Service Account 이메일 추가를 거부 ("이메일이 Google 계정과 일치하지 않습니다")
하는 케이스가 있어 우회. BQ는 IAM 권한만 있으면 됨.

요구 환경:
- .env: GA4_PROPERTY_ID, GOOGLE_APPLICATION_CREDENTIALS (SA JSON 경로)
- SA에 roles/bigquery.dataViewer + roles/bigquery.jobUser
- GA4 admin → 제품 링크 → BigQuery 링크 설정 완료 (매일 export)
- 첫 데이터까지 최대 24h 대기

데이터셋: analytics_<property_id>  (자동 생성)
테이블:   events_YYYYMMDD          (자동 생성, 매일)

대시보드 호환을 위해 기존 메서드 시그니처/반환값 유지.
"""

import os
import json
import logging
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)


class GA4NotConfigured(RuntimeError):
    """GA4 환경변수 미설정. 대시보드는 이 예외 잡아서 GA4 섹션만 disable."""


class GA4API:
    def __init__(
        self,
        property_id: str,
        credentials_path: Optional[str] = None,
        project_id: Optional[str] = None,
        dataset: Optional[str] = None,
        location: Optional[str] = None,
    ):
        self.property_id = str(property_id)
        if credentials_path:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path

        try:
            from google.cloud import bigquery
        except ImportError as e:
            raise RuntimeError(
                f"google-cloud-bigquery 설치 필요: pip install google-cloud-bigquery ({e})"
            )

        # project_id가 명시 안 됐으면 SA JSON에서 추출
        if not project_id and credentials_path and os.path.exists(credentials_path):
            with open(credentials_path, encoding="utf-8") as f:
                project_id = json.load(f).get("project_id")

        self.project_id = project_id
        self.dataset = dataset or f"analytics_{self.property_id}"
        self.location = location or "asia-southeast3"
        self.client = bigquery.Client(project=project_id) if project_id else bigquery.Client()
        self.table_ref = f"`{self.client.project}.{self.dataset}.events_*`"
        logger.info(
            "[GA4-BQ] 초기화 project=%s dataset=%s location=%s property=%s",
            self.client.project, self.dataset, self.location, self.property_id,
        )

    @classmethod
    def from_env(cls) -> "GA4API":
        pid = os.getenv("GA4_PROPERTY_ID", "").strip()
        cred = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        project = os.getenv("GA4_BQ_PROJECT", "").strip() or None
        dataset = os.getenv("GA4_BQ_DATASET", "").strip() or None
        location = os.getenv("GA4_BQ_LOCATION", "").strip() or None
        if not pid:
            raise GA4NotConfigured("GA4_PROPERTY_ID 미설정")
        if cred and not os.path.exists(cred):
            raise GA4NotConfigured(f"GOOGLE_APPLICATION_CREDENTIALS 경로 없음: {cred}")
        return cls(pid, cred or None, project, dataset, location)

    # ─────────────────────────────────────────────
    # 핵심 fetch 메서드 (반환 shape는 GA4 Data API 버전과 동일)
    # ─────────────────────────────────────────────

    def fetch_daily_summary(self, since: date, until: date) -> list[dict]:
        """일별 세션·사용자·페이지뷰·이탈률·평균체류."""
        sql = f"""
        WITH events AS (
          SELECT
            event_date,
            user_pseudo_id,
            event_name,
            event_timestamp,
            (SELECT value.int_value FROM UNNEST(event_params)
              WHERE key='ga_session_id') AS session_id,
            (SELECT value.int_value FROM UNNEST(event_params)
              WHERE key='session_engaged') AS engaged
          FROM {self.table_ref}
          WHERE _TABLE_SUFFIX BETWEEN @start AND @end
        ),
        sessions AS (
          SELECT
            event_date,
            user_pseudo_id,
            session_id,
            MAX(engaged) AS engaged,
            MIN(event_timestamp) AS start_ts,
            MAX(event_timestamp) AS end_ts,
            COUNTIF(event_name='page_view') AS pv
          FROM events
          WHERE session_id IS NOT NULL
          GROUP BY event_date, user_pseudo_id, session_id
        )
        SELECT
          event_date AS dt,
          COUNT(*) AS sessions,
          COUNT(DISTINCT user_pseudo_id) AS users,
          SUM(pv) AS pageviews,
          SAFE_DIVIDE(COUNTIF(engaged IS NULL OR engaged = 0), COUNT(*)) AS bounce_rate,
          AVG(SAFE_DIVIDE(end_ts - start_ts, 1000000)) AS avg_session_duration
        FROM sessions
        GROUP BY event_date
        ORDER BY event_date
        """
        rows = self._run(sql, since, until)
        out = []
        for r in rows:
            dv = r["dt"]
            out.append({
                "date": f"{dv[0:4]}-{dv[4:6]}-{dv[6:8]}",
                "sessions": int(r["sessions"] or 0),
                "users": int(r["users"] or 0),
                "pageviews": int(r["pageviews"] or 0),
                "bounce_rate": float(r["bounce_rate"] or 0),
                "avg_session_duration": float(r["avg_session_duration"] or 0),
            })
        return out

    def fetch_by_source(self, since: date, until: date) -> list[dict]:
        """채널/매체별 트래픽."""
        sql = f"""
        WITH session_keys AS (
          SELECT
            user_pseudo_id,
            (SELECT value.int_value FROM UNNEST(event_params)
              WHERE key='ga_session_id') AS session_id,
            event_name,
            collected_traffic_source.manual_source AS src,
            collected_traffic_source.manual_medium AS med
          FROM {self.table_ref}
          WHERE _TABLE_SUFFIX BETWEEN @start AND @end
        ),
        sessions AS (
          SELECT
            user_pseudo_id, session_id,
            ANY_VALUE(IF(event_name='session_start', src, NULL) IGNORE NULLS) AS src,
            ANY_VALUE(IF(event_name='session_start', med, NULL) IGNORE NULLS) AS med,
            COUNTIF(event_name IN ('purchase','generate_lead','sign_up','login')) AS conv
          FROM session_keys
          WHERE session_id IS NOT NULL
          GROUP BY user_pseudo_id, session_id
        )
        SELECT
          COALESCE(src, '(direct)') AS source,
          COALESCE(med, '(none)') AS medium,
          COUNT(*) AS sessions,
          COUNT(DISTINCT user_pseudo_id) AS users,
          SUM(conv) AS conversions
        FROM sessions
        GROUP BY source, medium
        ORDER BY sessions DESC
        """
        rows = self._run(sql, since, until)
        return [{
            "source": r["source"],
            "medium": r["medium"],
            "sessions": int(r["sessions"] or 0),
            "users": int(r["users"] or 0),
            "conversions": float(r["conversions"] or 0),
        } for r in rows]

    def fetch_by_campaign(self, since: date, until: date) -> list[dict]:
        """UTM campaign 단위."""
        sql = f"""
        WITH session_keys AS (
          SELECT
            user_pseudo_id,
            (SELECT value.int_value FROM UNNEST(event_params)
              WHERE key='ga_session_id') AS session_id,
            event_name,
            collected_traffic_source.manual_source AS src,
            collected_traffic_source.manual_medium AS med,
            collected_traffic_source.manual_campaign_name AS camp
          FROM {self.table_ref}
          WHERE _TABLE_SUFFIX BETWEEN @start AND @end
        ),
        sessions AS (
          SELECT
            user_pseudo_id, session_id,
            ANY_VALUE(IF(event_name='session_start', src, NULL) IGNORE NULLS) AS src,
            ANY_VALUE(IF(event_name='session_start', med, NULL) IGNORE NULLS) AS med,
            ANY_VALUE(IF(event_name='session_start', camp, NULL) IGNORE NULLS) AS camp,
            COUNTIF(event_name IN ('purchase','generate_lead','sign_up','login')) AS conv
          FROM session_keys
          WHERE session_id IS NOT NULL
          GROUP BY user_pseudo_id, session_id
        )
        SELECT
          COALESCE(src, '(direct)') AS source,
          COALESCE(med, '(none)') AS medium,
          COALESCE(camp, '(not set)') AS campaign,
          COUNT(*) AS sessions,
          COUNT(DISTINCT user_pseudo_id) AS users,
          SUM(conv) AS conversions
        FROM sessions
        GROUP BY source, medium, campaign
        ORDER BY sessions DESC
        """
        rows = self._run(sql, since, until)
        return [{
            "source": r["source"],
            "medium": r["medium"],
            "campaign": r["campaign"],
            "sessions": int(r["sessions"] or 0),
            "users": int(r["users"] or 0),
            "conversions": float(r["conversions"] or 0),
        } for r in rows]

    def fetch_conversions_by_event(self, since: date, until: date) -> list[dict]:
        """이벤트별 카운트."""
        sql = f"""
        SELECT
          event_name AS event_name,
          COUNT(*) AS cnt,
          COUNT(DISTINCT user_pseudo_id) AS users
        FROM {self.table_ref}
        WHERE _TABLE_SUFFIX BETWEEN @start AND @end
        GROUP BY event_name
        ORDER BY cnt DESC
        """
        rows = self._run(sql, since, until)
        return [{
            "event_name": r["event_name"],
            "count": int(r["cnt"] or 0),
            "users": int(r["users"] or 0),
        } for r in rows]

    def fetch_landing_pages(self, since: date, until: date, limit: int = 20) -> list[dict]:
        """랜딩 페이지 top N."""
        sql = f"""
        WITH session_keys AS (
          SELECT
            user_pseudo_id,
            (SELECT value.int_value FROM UNNEST(event_params)
              WHERE key='ga_session_id') AS session_id,
            event_name,
            event_timestamp,
            (SELECT value.string_value FROM UNNEST(event_params)
              WHERE key='page_location') AS page,
            (SELECT value.int_value FROM UNNEST(event_params)
              WHERE key='session_engaged') AS engaged
          FROM {self.table_ref}
          WHERE _TABLE_SUFFIX BETWEEN @start AND @end
        ),
        sessions AS (
          SELECT
            user_pseudo_id, session_id,
            ARRAY_AGG(page ORDER BY event_timestamp ASC LIMIT 1)[OFFSET(0)] AS landing,
            MAX(engaged) AS engaged,
            MIN(event_timestamp) AS start_ts,
            MAX(event_timestamp) AS end_ts
          FROM session_keys
          WHERE session_id IS NOT NULL
          GROUP BY user_pseudo_id, session_id
        )
        SELECT
          landing AS page,
          COUNT(*) AS sessions,
          SAFE_DIVIDE(COUNTIF(engaged IS NULL OR engaged = 0), COUNT(*)) AS bounce_rate,
          AVG(SAFE_DIVIDE(end_ts - start_ts, 1000000)) AS avg_duration
        FROM sessions
        WHERE landing IS NOT NULL
        GROUP BY landing
        ORDER BY sessions DESC
        LIMIT @limit
        """
        rows = self._run(sql, since, until, limit=limit)
        return [{
            "page": r["page"],
            "sessions": int(r["sessions"] or 0),
            "bounce_rate": float(r["bounce_rate"] or 0),
            "avg_duration": float(r["avg_duration"] or 0),
        } for r in rows]

    # ─────────────────────────────────────────────
    # 내부
    # ─────────────────────────────────────────────

    def _run(self, sql: str, since: date, until: date, limit: Optional[int] = None) -> list[dict]:
        from google.cloud import bigquery
        params = [
            bigquery.ScalarQueryParameter("start", "STRING", since.strftime("%Y%m%d")),
            bigquery.ScalarQueryParameter("end",   "STRING", until.strftime("%Y%m%d")),
        ]
        if limit is not None:
            params.append(bigquery.ScalarQueryParameter("limit", "INT64", int(limit)))
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        try:
            result = self.client.query(sql, job_config=job_config, location=self.location).result()
            rows = [dict(r) for r in result]
            logger.info("[GA4-BQ] %d rows (%s ~ %s)", len(rows), since, until)
            return rows
        except Exception as e:
            msg = str(e)
            # 첫 24h: events_* 테이블 미존재. 대시보드 깨지지 않게 빈 결과 반환.
            if "Not found" in msg or "Table" in msg and "not found" in msg.lower():
                logger.warning("[GA4-BQ] 데이터셋/테이블 없음 (export 대기중?): %s", msg.split('\n')[0])
                return []
            raise


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
            print(f"⚠️ 데이터 없음. GA4→BQ export는 첫 데이터까지 최대 24h 걸립니다.")
            print(f"   project={api.client.project} dataset={api.dataset}")
            print(f"   내일 다시 실행해보세요.")
        else:
            print(f"✅ GA4(BQ) 연결 성공 (property {api.property_id})")
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
