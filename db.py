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


def get_stats(since: str | None = None) -> dict:
    """Aggregates pipeline accuracy/performance signals from the artifact log (§5) --
    built for `cli.tools.get_stats()`/`scripts/pipeline_stats.py` (docs/decisions/0027)
    to answer "how well is the automated pipeline actually doing" without a separate
    metrics store: everything here is derived from the same append-only artifacts
    table every agent already writes to.

    `since` (ISO timestamp, optional) filters to listings created at/after that time;
    None means all-time. Every count below is scoped to that same listing set.

    Distinguishing an automated decision from a moderator one relies on two fields
    `DecisionAgent` artifacts already carry, not a new column: `version = 'fusion-v1'`
    marks the automated pipeline's own decision (agents/decision_agent.py always sets
    this, `moderator=None`); any artifact with a non-null `payload->>'moderator'` came
    from a human action (cli/tools.py's record_decision, used by every one of
    approve_listing/reject_listing/escalate_case/request_appeal/resolve_appeal)."""
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Listing volume by current status, scoped to the same `since` window as
        # everything else below.
        cur.execute(
            """
            SELECT status, count(*) AS n
            FROM listings
            WHERE %(since)s::timestamptz IS NULL OR created_at >= %(since)s::timestamptz
            GROUP BY status
            """,
            {"since": since},
        )
        listings_by_status = {r["status"]: r["n"] for r in cur.fetchall()}

        # One row per listing: its first (earliest) automated decision -- the pipeline
        # never re-emits a second fusion-v1 artifact for the same listing in normal
        # operation, but DISTINCT ON guards the stat against rerun_analysis edge cases
        # rather than assuming it.
        cur.execute(
            """
            SELECT DISTINCT ON (a.listing_id)
                a.listing_id, a.payload->>'decision' AS decision, (a.payload->>'confidence')::float AS confidence
            FROM artifacts a
            JOIN listings l ON l.listing_id = a.listing_id
            WHERE a.agent = 'DecisionAgent' AND a.version = 'fusion-v1'
              AND (%(since)s::timestamptz IS NULL OR l.created_at >= %(since)s::timestamptz)
            ORDER BY a.listing_id, a.produced_at ASC
            """,
            {"since": since},
        )
        automated = {r["listing_id"]: r for r in cur.fetchall()}

        # One row per listing: the latest moderator-issued APPROVE/REJECT verdict --
        # ESCALATE/REQUEST_APPEAL are excluded, they're intermediate steps, not a
        # verdict to compare against the automated one.
        cur.execute(
            """
            SELECT DISTINCT ON (a.listing_id)
                a.listing_id, a.payload->>'decision' AS decision, a.payload->>'moderator' AS moderator
            FROM artifacts a
            JOIN listings l ON l.listing_id = a.listing_id
            WHERE a.agent = 'DecisionAgent' AND a.payload->>'moderator' IS NOT NULL
              AND a.payload->>'decision' IN ('APPROVE', 'REJECT')
              AND (%(since)s::timestamptz IS NULL OR l.created_at >= %(since)s::timestamptz)
            ORDER BY a.listing_id, a.produced_at DESC
            """,
            {"since": since},
        )
        human_verdicts = {r["listing_id"]: r for r in cur.fetchall()}

        # Safety/Consistency Agent signal quality -- avg confidence and avg
        # inconsistency score, one row averaged per listing (not per artifact) so a
        # listing that got a low-confidence-triggered retry doesn't get double-weighted.
        cur.execute(
            """
            SELECT avg((sub.confidence)::float) AS avg_confidence
            FROM (
                SELECT DISTINCT ON (a.listing_id) (a.payload->>'confidence')::float AS confidence
                FROM artifacts a
                JOIN listings l ON l.listing_id = a.listing_id
                WHERE a.agent = 'SafetyAgent'
                  AND (%(since)s::timestamptz IS NULL OR l.created_at >= %(since)s::timestamptz)
                ORDER BY a.listing_id, a.produced_at DESC
            ) sub
            """,
            {"since": since},
        )
        avg_safety_confidence = cur.fetchone()["avg_confidence"]

        cur.execute(
            """
            SELECT avg((sub.score)::float) AS avg_score
            FROM (
                SELECT DISTINCT ON (a.listing_id) (a.payload->>'inconsistencyScore')::float AS score
                FROM artifacts a
                JOIN listings l ON l.listing_id = a.listing_id
                WHERE a.agent = 'ConsistencyAgent'
                  AND (%(since)s::timestamptz IS NULL OR l.created_at >= %(since)s::timestamptz)
                ORDER BY a.listing_id, a.produced_at DESC
            ) sub
            """,
            {"since": since},
        )
        avg_inconsistency_score = cur.fetchone()["avg_score"]

        # End-to-end automated pipeline latency: earliest of the three parallel
        # agents (Evidence/Consistency/Safety, run_fusion, §7) to the automated
        # DecisionAgent artifact, per listing, averaged.
        cur.execute(
            """
            SELECT avg(EXTRACT(EPOCH FROM (d.produced_at - fan_out.started_at))) AS avg_seconds
            FROM (
                SELECT listing_id, min(produced_at) AS started_at
                FROM artifacts
                WHERE agent IN ('EvidenceAgent', 'ConsistencyAgent', 'SafetyAgent')
                GROUP BY listing_id
            ) fan_out
            JOIN (
                SELECT DISTINCT ON (listing_id) listing_id, produced_at
                FROM artifacts
                WHERE agent = 'DecisionAgent' AND version = 'fusion-v1'
                ORDER BY listing_id, produced_at ASC
            ) d ON d.listing_id = fan_out.listing_id
            JOIN listings l ON l.listing_id = fan_out.listing_id
            WHERE %(since)s::timestamptz IS NULL OR l.created_at >= %(since)s::timestamptz
            """,
            {"since": since},
        )
        avg_pipeline_latency_seconds = cur.fetchone()["avg_seconds"]

        # Pipeline failures (agents/pipeline.py's _record_failure) -- what broke and
        # how often, grouped by the literal error message (stable per failure type,
        # e.g. every "410 Gone" on the same dead model produces the same string).
        cur.execute(
            """
            SELECT a.payload->>'error' AS error, count(*) AS n
            FROM artifacts a
            JOIN listings l ON l.listing_id = a.listing_id
            WHERE a.agent = 'Pipeline'
              AND (%(since)s::timestamptz IS NULL OR l.created_at >= %(since)s::timestamptz)
            GROUP BY a.payload->>'error'
            ORDER BY n DESC
            """,
            {"since": since},
        )
        failures_by_error = [dict(r) for r in cur.fetchall()]

        # Which policy rules actually fire, and how often -- unnests each
        # PolicyAgent artifact's matches array.
        cur.execute(
            """
            SELECT m->>'rule' AS rule, count(*) AS n
            FROM artifacts a
            JOIN listings l ON l.listing_id = a.listing_id
            CROSS JOIN LATERAL jsonb_array_elements(a.payload->'matches') AS m
            WHERE a.agent = 'PolicyAgent'
              AND (%(since)s::timestamptz IS NULL OR l.created_at >= %(since)s::timestamptz)
            GROUP BY m->>'rule'
            ORDER BY n DESC
            """,
            {"since": since},
        )
        policy_rule_hits = {r["rule"]: r["n"] for r in cur.fetchall()}

    automated_decision_counts: dict[str, int] = {}
    automated_confidences = []
    for r in automated.values():
        automated_decision_counts[r["decision"]] = automated_decision_counts.get(r["decision"], 0) + 1
        if r["confidence"] is not None:
            automated_confidences.append(r["confidence"])

    review_outcomes: dict[str, int] = {}
    overridden = 0
    reviewed = 0
    for listing_id, human in human_verdicts.items():
        auto = automated.get(listing_id)
        if auto is None:
            continue
        if auto["decision"] == "REVIEW":
            review_outcomes[human["decision"]] = review_outcomes.get(human["decision"], 0) + 1
            continue
        # Only an automated APPROVE/REJECT is a verdict a human can agree or disagree
        # with -- REVIEW is handled above and excluded from this denominator entirely.
        reviewed += 1
        if human["decision"] != auto["decision"]:
            overridden += 1

    return {
        "since": since,
        "listingsByStatus": listings_by_status,
        "automatedDecisionCounts": automated_decision_counts,
        "automatedAvgConfidence": (sum(automated_confidences) / len(automated_confidences)) if automated_confidences else None,
        "humanReviewedCount": reviewed,
        "humanReviewOutcomes": review_outcomes,
        "overriddenCount": overridden,
        "overrideRate": (overridden / reviewed) if reviewed else None,
        "avgSafetyConfidence": avg_safety_confidence,
        "avgInconsistencyScore": avg_inconsistency_score,
        "avgPipelineLatencySeconds": avg_pipeline_latency_seconds,
        "failuresByError": failures_by_error,
        "policyRuleHits": policy_rule_hits,
    }


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
