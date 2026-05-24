# -*- coding: utf-8 -*-
"""
예비 키워드 풀 SQLite 인터페이스
- 등록 대기 키워드 관리
- cooldown 30일 강제 (DELETE된 키워드 재등록 방지)
- 풀 잔량 모니터링
"""

import sqlite3
import logging
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parents[2] / "data" / "reserve_pool.db"
COOLDOWN_DAYS = 30


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """테이블 초기화 (없으면 생성)."""
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS reserve_keywords (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword         TEXT NOT NULL UNIQUE,
                adgroup_key     TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'available',
                -- available / registered / deleted / cooldown
                registered_at   TEXT,
                deleted_at      TEXT,
                cooldown_until  TEXT,
                created_at      TEXT DEFAULT (date('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_status ON reserve_keywords(status);
            CREATE INDEX IF NOT EXISTS idx_cooldown ON reserve_keywords(cooldown_until);
        """)
    logger.info("[DB] reserve_pool 초기화 완료: %s", DB_PATH)


def get_pool_size() -> int:
    """현재 available 키워드 수 반환."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM reserve_keywords WHERE status = 'available'"
        ).fetchone()
    return row[0]


def get_available_keywords(adgroup_key: str, limit: int = 100) -> list[dict]:
    """
    특정 그룹에서 등록 가능한 키워드 조회.
    cooldown 기간 지난 키워드도 포함.
    """
    today = str(date.today())
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT id, keyword, adgroup_key FROM reserve_keywords
            WHERE adgroup_key = ?
              AND (
                status = 'available'
                OR (status = 'cooldown' AND cooldown_until < ?)
              )
            LIMIT ?
            """,
            (adgroup_key, today, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_registered(keyword_ids: list[int]):
    """예비 풀 → registered 상태로 변경."""
    today = str(date.today())
    with _conn() as conn:
        conn.executemany(
            "UPDATE reserve_keywords SET status='registered', registered_at=? WHERE id=?",
            [(today, kid) for kid in keyword_ids],
        )
    logger.info("[DB] %d개 키워드 registered 처리", len(keyword_ids))


def mark_deleted(keywords: list[str]):
    """
    네이버 계정에서 DELETE된 키워드를 cooldown 상태로 변경.
    30일 후 다시 available로 쓸 수 있도록 cooldown_until 설정.
    """
    today = date.today()
    cooldown_until = str(today + timedelta(days=COOLDOWN_DAYS))
    with _conn() as conn:
        conn.executemany(
            """
            UPDATE reserve_keywords
            SET status='cooldown', deleted_at=?, cooldown_until=?
            WHERE keyword=?
            """,
            [(str(today), cooldown_until, kw) for kw in keywords],
        )
    logger.info("[DB] %d개 키워드 cooldown 처리 (해제일: %s)", len(keywords), cooldown_until)


def bulk_insert(keywords: list[dict]):
    """
    예비 풀 대량 삽입. 중복은 무시(IGNORE).
    keywords: [{"keyword": str, "adgroup_key": str}, ...]
    """
    with _conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM reserve_keywords").fetchone()[0]
        conn.executemany(
            """
            INSERT OR IGNORE INTO reserve_keywords (keyword, adgroup_key, status)
            VALUES (:keyword, :adgroup_key, 'available')
            """,
            keywords,
        )
        after = conn.execute("SELECT COUNT(*) FROM reserve_keywords").fetchone()[0]
        count = after - before
    logger.info("[DB] 예비 풀 %d개 신규 삽입", count)
    return count


def get_stats() -> dict:
    """상태별 카운트 요약."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM reserve_keywords GROUP BY status"
        ).fetchall()
    return {r["status"]: r["cnt"] for r in rows}
