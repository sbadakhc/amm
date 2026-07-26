"""
Postgres access layer. See SPEC.md §2.1 (claiming/locking) and §5 (artifact log).
"""

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector


@contextmanager
def get_conn():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    register_vector(conn)  # adapts Python list[float] <-> pgvector's `vector` type
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


def upsert_listing_embedding(listing_id: str, model: str, embedding: list[float]) -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO listing_embeddings (listing_id, model, embedding)
            VALUES (%s, %s, %s)
            ON CONFLICT (listing_id) DO UPDATE
                SET model = EXCLUDED.model, embedding = EXCLUDED.embedding, produced_at = now()
            """,
            (listing_id, model, embedding),
        )
        conn.commit()


def get_listing_embedding(listing_id: str) -> dict | None:
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM listing_embeddings WHERE listing_id = %s", (listing_id,))
        row = cur.fetchone()
        if row is None:
            return None
        result = dict(row)
        result["embedding"] = result["embedding"].to_list()  # pgvector.HalfVector -> list[float]
        return result


def find_similar_by_embedding(listing_id: str, k: int = 5) -> list[dict]:
    """Nearest neighbors by cosine distance (pgvector's `<=>` operator, §6), excluding
    the listing itself. Returns full listing rows plus a `distance` column (lower =
    more similar, range [0, 2] for cosine distance).

    The target embedding is looked up via a scalar subquery rather than a self-join
    -- confirmed via real EXPLAIN (docs/decisions/0016) that a self-join form (`JOIN
    listing_embeddings le2 ON ...`) never touches the HNSW index at all, regardless
    of data volume, while this form lets the planner treat the ORDER BY as a proper
    ANN search using the index."""
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT l.*, (le.embedding <=> (SELECT embedding FROM listing_embeddings WHERE listing_id = %s)) AS distance
            FROM listing_embeddings le
            JOIN listings l ON l.listing_id = le.listing_id
            WHERE le.listing_id != %s
            ORDER BY distance ASC
            LIMIT %s
            """,
            (listing_id, listing_id, k),
        )
        return [dict(r) for r in cur.fetchall()]


def upsert_seller_if_missing(seller_id: str, initial_violation_count: int = 0) -> None:
    """Creates a `sellers` row the first time a seller is seen (§8.3,
    docs/decisions/0017) -- a no-op if one already exists, so repeated calls (e.g.
    once per listing from the same seller) never reset `violation_count`."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO sellers (seller_id, violation_count)
            VALUES (%s, %s)
            ON CONFLICT (seller_id) DO NOTHING
            """,
            (seller_id, initial_violation_count),
        )
        conn.commit()


def increment_seller_violations(seller_id: str) -> None:
    """Called whenever a listing lands on REJECTED (§8.3) -- both the automated
    Decision Agent path (`pipeline.process_listing`) and a moderator's override
    (`cli.tools.record_decision`)."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE sellers SET violation_count = violation_count + 1, updated_at = now() WHERE seller_id = %s",
            (seller_id,),
        )
        conn.commit()


def get_seller(seller_id: str) -> dict | None:
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM sellers WHERE seller_id = %s", (seller_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_listings_by_seller(seller_id: str) -> list[dict]:
    """Every listing tied to one seller (§8.4's `list_seller_cases`) -- `sellerId` is
    embedded JSONB on `listings`, not a foreign key, since `sellers` is a placeholder
    (§8.1), not the real source of truth for seller identity."""
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT * FROM listings WHERE seller->>'sellerId' = %s ORDER BY created_at",
            (seller_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def update_seller_status(seller_id: str, status: str, reason: str, moderator_id: str) -> None:
    """Backs `cli.tools.suspend_seller`/`reinstate_seller` (§8.4)."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE sellers
            SET status = %s, status_reason = %s, status_changed_by = %s, updated_at = now()
            WHERE seller_id = %s
            """,
            (status, reason, moderator_id, seller_id),
        )
        conn.commit()
