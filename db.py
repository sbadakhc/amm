"""
Postgres access layer. See SPEC.md §2.1 (claiming/locking) and §5 (artifact log).
"""

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras


@contextmanager
def get_conn():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        yield conn
    finally:
        conn.close()


def claim_pending(batch_size: int = 10) -> list[dict]:
    """Atomically claims up to `batch_size` PENDING_MODERATION listings (§2.1)."""
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            UPDATE listings
            SET status = 'PROCESSING', updated_at = now()
            WHERE listing_id IN (
                SELECT listing_id FROM listings
                WHERE status = 'PENDING_MODERATION'
                ORDER BY created_at
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *
            """,
            (batch_size,),
        )
        rows = cur.fetchall()
        conn.commit()
        return [dict(r) for r in rows]


def sweep_stale_processing(timeout_minutes: int = 5) -> int:
    """Resets listings stuck in PROCESSING past the lease timeout (§2.1). Returns the
    number of rows reset."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE listings
            SET status = 'PENDING_REVIEW', updated_at = now()
            WHERE status = 'PROCESSING' AND updated_at < now() - interval '%s minutes'
            """,
            (timeout_minutes,),
        )
        count = cur.rowcount
        conn.commit()
        return count


def get_listing_row(listing_id: str) -> dict | None:
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM listings WHERE listing_id = %s", (listing_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_listings_by_status(status: str, limit: int | None = None, category_id: str | None = None) -> list[dict]:
    query = "SELECT * FROM listings WHERE status = %s"
    params: list = [status]
    if category_id:
        query += " AND category->>'id' = %s"
        params.append(category_id)
    query += " ORDER BY created_at"
    if limit:
        query += " LIMIT %s"
        params.append(limit)
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


def update_listing_status(listing_id: str, status: str) -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE listings SET status = %s, updated_at = now() WHERE listing_id = %s",
            (status, listing_id),
        )
        conn.commit()


def insert_artifact(artifact: dict) -> dict:
    """Appends one immutable artifact row (§5). `artifact` is the same shape
    run_*_agent() functions return: {listingId, agent, version, producedAt, payload,
    basedOn?}."""
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            INSERT INTO artifacts (listing_id, agent, version, produced_at, payload, based_on)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                artifact["listingId"],
                artifact["agent"],
                artifact["version"],
                artifact.get("producedAt", datetime.now(timezone.utc).isoformat()),
                json.dumps(artifact["payload"]),
                json.dumps(artifact.get("basedOn")) if artifact.get("basedOn") is not None else None,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return dict(row)


def get_artifacts(listing_id: str, agent: str | None = None) -> list[dict]:
    query = "SELECT * FROM artifacts WHERE listing_id = %s"
    params: list = [listing_id]
    if agent:
        query += " AND agent = %s"
        params.append(agent)
    query += " ORDER BY produced_at"
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


def latest_artifact(listing_id: str, agent: str) -> dict | None:
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT * FROM artifacts
            WHERE listing_id = %s AND agent = %s
            ORDER BY produced_at DESC
            LIMIT 1
            """,
            (listing_id, agent),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_moderator(moderator_id: str) -> dict | None:
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM moderators WHERE moderator_id = %s", (moderator_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def create_moderator(moderator_id: str, name: str, active: bool = True) -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO moderators (moderator_id, name, active)
            VALUES (%s, %s, %s)
            ON CONFLICT (moderator_id) DO UPDATE SET name = EXCLUDED.name, active = EXCLUDED.active
            """,
            (moderator_id, name, active),
        )
        conn.commit()
