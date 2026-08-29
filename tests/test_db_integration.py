"""
Requires a real Postgres instance -- see scripts/dev-db.sh. Skipped automatically if
DATABASE_URL isn't set (conftest.py). These exercise the actual locking/claim SQL
(§2.1) and artifact log (§5), not mocks -- that's the whole point of testing against
a real database rather than reasoning about the SQL in the abstract.
"""

import json
import uuid
from datetime import datetime, timezone

import psycopg2
import pytest

import db

pytestmark = pytest.mark.integration


@pytest.fixture
def seeded_listing():
    listing_id = f"LST-TEST-{uuid.uuid4().hex[:6].upper()}"
    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO listings
                (listing_id, seller, title, description, category, price, quantity,
                 condition, brand, model, sku, images, attributes, shipping, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                listing_id,
                json.dumps({"sellerId": "SUP-TEST", "verified": True, "previousViolations": 0}),
                "Test Listing",
                "A test listing.",
                json.dumps({"id": "electronics.mobile", "name": "Mobile Phones"}),
                json.dumps({"amount": 1.0, "currency": "GBP"}),
                1,
                "new",
                "Apple",
                "Test",
                "SKU-TEST",
                json.dumps([]),
                json.dumps({}),
                json.dumps({}),
                "PENDING_MODERATION",
            ),
        )
        conn.commit()

    yield listing_id

    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM listing_embeddings WHERE listing_id = %s", (listing_id,))
        cur.execute("DELETE FROM artifacts WHERE listing_id = %s", (listing_id,))
        cur.execute("DELETE FROM listings WHERE listing_id = %s", (listing_id,))
        conn.commit()


def test_claim_pending_moves_to_processing(seeded_listing):
    claimed = db.claim_pending(batch_size=50)
    claimed_ids = {row["listing_id"] for row in claimed}
    assert seeded_listing in claimed_ids

    row = db.get_listing_row(seeded_listing)
    assert row["status"] == "PROCESSING"


def test_claim_pending_does_not_reclaim_already_processing(seeded_listing):
    db.claim_pending(batch_size=50)
    second_batch = db.claim_pending(batch_size=50)
    claimed_ids = {row["listing_id"] for row in second_batch}
    assert seeded_listing not in claimed_ids


def test_insert_and_query_artifacts(seeded_listing):
    artifact = {
        "listingId": seeded_listing,
        "agent": "SafetyAgent",
        "version": "test-v1",
        "producedAt": "2026-01-01T00:00:00Z",
        "payload": {"violations": [], "confidence": 0.9, "explanation": "ok"},
    }
    db.insert_artifact(artifact)

    artifacts = db.get_artifacts(seeded_listing)
    assert len(artifacts) == 1
    assert artifacts[0]["agent"] == "SafetyAgent"
    assert artifacts[0]["payload"]["confidence"] == 0.9

    latest = db.latest_artifact(seeded_listing, "SafetyAgent")
    assert latest["id"] == artifacts[0]["id"]


def test_update_listing_status(seeded_listing):
    db.update_listing_status(seeded_listing, "APPROVED")
    row = db.get_listing_row(seeded_listing)
    assert row["status"] == "APPROVED"


@pytest.fixture
def two_listings_distinct_categories():
    """Unique, randomly-suffixed category ids so filter assertions can't be polluted
    by other real/demo listings that might already be sitting in a shared dev DB."""
    suffix = uuid.uuid4().hex[:6]
    category_a = f"test.category.a.{suffix}"
    category_b = f"test.category.b.{suffix}"
    ids = []

    def _insert(category_id):
        listing_id = f"LST-TEST-{uuid.uuid4().hex[:6].upper()}"
        with db.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO listings
                    (listing_id, seller, title, description, category, price, quantity,
                     condition, brand, model, sku, images, attributes, shipping, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    listing_id,
                    json.dumps({"sellerId": "SUP-TEST", "verified": True, "previousViolations": 0}),
                    "Test Listing",
                    "A test listing.",
                    json.dumps({"id": category_id, "name": "Test Category"}),
                    json.dumps({"amount": 1.0, "currency": "GBP"}),
                    1,
                    "new",
                    "Apple",
                    "Test",
                    "SKU-TEST",
                    json.dumps([]),
                    json.dumps({}),
                    json.dumps({}),
                    "PENDING_REVIEW",
                ),
            )
            conn.commit()
        ids.append(listing_id)
        return listing_id

    listing_a1 = _insert(category_a)
    listing_a2 = _insert(category_a)
    listing_b = _insert(category_b)

    yield {
        "category_a": category_a,
        "listing_a1": listing_a1,
        "listing_a2": listing_a2,
        "listing_b": listing_b,
    }

    with db.get_conn() as conn:
        cur = conn.cursor()
        for listing_id in ids:
            cur.execute("DELETE FROM listings WHERE listing_id = %s", (listing_id,))
        conn.commit()


def test_list_listings_by_status_filters_by_category(two_listings_distinct_categories):
    fx = two_listings_distinct_categories
    results = db.list_listings_by_status("PENDING_REVIEW", category_id=fx["category_a"])
    result_ids = {r["listing_id"] for r in results}
    assert result_ids == {fx["listing_a1"], fx["listing_a2"]}
    assert fx["listing_b"] not in result_ids


def test_list_listings_by_status_applies_limit(two_listings_distinct_categories):
    fx = two_listings_distinct_categories
    unlimited = db.list_listings_by_status("PENDING_REVIEW", category_id=fx["category_a"])
    assert len(unlimited) == 2

    limited = db.list_listings_by_status("PENDING_REVIEW", category_id=fx["category_a"], limit=1)
    assert len(limited) == 1


def test_sweep_stale_processing_resets_old_rows(seeded_listing):
    db.claim_pending(batch_size=50)
    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE listings SET updated_at = now() - interval '10 minutes' WHERE listing_id = %s",
            (seeded_listing,),
        )
        conn.commit()

    db.sweep_stale_processing(timeout_minutes=5)
    row = db.get_listing_row(seeded_listing)
    assert row["status"] == "PENDING_REVIEW"


@pytest.fixture
def seeded_moderator():
    moderator_id = f"mod-test-{uuid.uuid4().hex[:6]}"
    db.create_moderator(moderator_id, "Test Moderator", active=True)
    yield moderator_id
    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM moderators WHERE moderator_id = %s", (moderator_id,))
        conn.commit()


def test_create_and_get_moderator(seeded_moderator):
    moderator = db.get_moderator(seeded_moderator)
    assert moderator["name"] == "Test Moderator"
    assert moderator["active"] is True


def test_get_unknown_moderator_returns_none():
    assert db.get_moderator("no-such-moderator") is None


def test_create_moderator_upserts_on_conflict(seeded_moderator):
    db.create_moderator(seeded_moderator, "Renamed", active=False)
    moderator = db.get_moderator(seeded_moderator)
    assert moderator["name"] == "Renamed"
    assert moderator["active"] is False


def _unit_vector(nonzero_index: int, dim: int = 2048) -> list[float]:
    """A deterministic unit vector for testing pgvector cosine distance without a
    real embedding call: identical index -> distance 0, different index -> distance
    1 (orthogonal), no real API dependency needed to test the SQL/storage mechanics
    (live model behavior was verified separately, see docs/decisions/0010)."""
    v = [0.0] * dim
    v[nonzero_index] = 1.0
    return v


@pytest.fixture
def three_embedded_listings():
    """Three listings: A and B share an embedding direction (distance 0 from each
    other), C is orthogonal to both (distance 1)."""
    ids = [f"LST-EMB-{uuid.uuid4().hex[:6].upper()}" for _ in range(3)]
    with db.get_conn() as conn:
        cur = conn.cursor()
        for listing_id in ids:
            cur.execute(
                """
                INSERT INTO listings
                    (listing_id, seller, title, description, category, price, quantity,
                     condition, brand, model, sku, images, attributes, shipping, status)
                VALUES (%s, '{}', 'test', 'test', '{"id":"x"}', '{}', 1, 'new', 'x', 'x', 'x', '[]', '{}', '{}', 'PENDING_MODERATION')
                """,
                (listing_id,),
            )
        conn.commit()

    db.upsert_listing_embedding(ids[0], "test-model", _unit_vector(0))
    db.upsert_listing_embedding(ids[1], "test-model", _unit_vector(0))
    db.upsert_listing_embedding(ids[2], "test-model", _unit_vector(1))

    yield ids

    with db.get_conn() as conn:
        cur = conn.cursor()
        for listing_id in ids:
            cur.execute("DELETE FROM listing_embeddings WHERE listing_id = %s", (listing_id,))
            cur.execute("DELETE FROM listings WHERE listing_id = %s", (listing_id,))
        conn.commit()


def test_get_listing_embedding(three_embedded_listings):
    listing_a = three_embedded_listings[0]
    embedding = db.get_listing_embedding(listing_a)
    assert embedding["model"] == "test-model"
    assert len(embedding["embedding"]) == 2048


def test_find_similar_by_embedding_ranks_by_cosine_distance(three_embedded_listings):
    listing_a, listing_b, listing_c = three_embedded_listings

    # k large enough to tolerate other embedded listings already in a shared dev DB
    # (e.g. from a manual pipeline run) -- filtered down to this fixture's own ids
    # before asserting order, rather than assuming an empty table.
    results = db.find_similar_by_embedding(listing_a, k=20)
    result_ids = [r["listing_id"] for r in results if r["listing_id"] in (listing_b, listing_c)]

    assert result_ids[0] == listing_b  # identical direction -> distance 0
    assert result_ids[1] == listing_c  # orthogonal -> distance 1
    assert listing_a not in result_ids  # excludes itself


def test_upsert_listing_embedding_overwrites(three_embedded_listings):
    listing_a, _, listing_c = three_embedded_listings
    db.upsert_listing_embedding(listing_a, "test-model-v2", _unit_vector(1))  # now matches C's direction

    results = db.find_similar_by_embedding(listing_a, k=5)
    assert results[0]["listing_id"] == listing_c
    assert results[0]["distance"] == 0.0


@pytest.fixture
def seller_id():
    """§8.3, docs/decisions/0017 -- placeholder sellers table."""
    sid = f"SUP-TEST-{uuid.uuid4().hex[:6].upper()}"
    yield sid
    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM sellers WHERE seller_id = %s", (sid,))
        conn.commit()


def test_upsert_seller_if_missing_creates_row(seller_id):
    db.upsert_seller_if_missing(seller_id, initial_violation_count=2)

    seller = db.get_seller(seller_id)
    assert seller["status"] == "ACTIVE"
    assert seller["violation_count"] == 2


def test_upsert_seller_if_missing_does_not_reset_existing_row(seller_id):
    db.upsert_seller_if_missing(seller_id, initial_violation_count=0)
    db.increment_seller_violations(seller_id)
    db.upsert_seller_if_missing(seller_id, initial_violation_count=0)  # e.g. a second listing from the same seller

    assert db.get_seller(seller_id)["violation_count"] == 1


def test_increment_seller_violations_accumulates(seller_id):
    db.upsert_seller_if_missing(seller_id)
    db.increment_seller_violations(seller_id)
    db.increment_seller_violations(seller_id)

    assert db.get_seller(seller_id)["violation_count"] == 2


def test_get_seller_unknown_returns_none():
    assert db.get_seller("SUP-DOES-NOT-EXIST") is None


@pytest.fixture
def stats_listings():
    """Three listings covering the three cases docs/decisions/0027's stats need to
    distinguish: a REVIEW-routed listing a moderator approves (not an override -- a
    REVIEW isn't a verdict to disagree with), an APPROVE a moderator later reverses
    (a real override), and a REJECT a moderator agrees with (reviewed, not
    overridden). Plus one Pipeline failure and one PolicyAgent match, to cover those
    aggregates too.

    `since` marks the instant right before these listings are inserted, so tests can
    scope `db.get_stats(since=...)` to just this fixture's data -- a shared dev DB
    (e.g. one with real listings from a manual pipeline run) would otherwise pollute
    every unscoped assertion here."""
    since = datetime.now(timezone.utc).isoformat()
    ids = [f"LST-STATS-{uuid.uuid4().hex[:6].upper()}" for _ in range(3)]
    with db.get_conn() as conn:
        cur = conn.cursor()
        for listing_id in ids:
            cur.execute(
                """
                INSERT INTO listings
                    (listing_id, seller, title, description, category, price, quantity,
                     condition, brand, model, sku, images, attributes, shipping, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    listing_id,
                    json.dumps({"sellerId": "SUP-STATS-TEST", "verified": True, "previousViolations": 0}),
                    "Test Listing",
                    "A test listing.",
                    json.dumps({"id": "electronics.mobile", "name": "Mobile Phones"}),
                    json.dumps({"amount": 1.0, "currency": "GBP"}),
                    1,
                    "new",
                    "Apple",
                    "Test",
                    "SKU-TEST",
                    json.dumps([]),
                    json.dumps({}),
                    json.dumps({}),
                    "PENDING_REVIEW",
                ),
            )
        conn.commit()

    review_id, override_id, agree_id = ids

    def _decision(listing_id, decision, confidence, moderator=None, version="fusion-v1", produced_at="2026-01-01T00:00:00Z"):
        db.insert_artifact(
            {
                "listingId": listing_id,
                "agent": "DecisionAgent",
                "version": version,
                "producedAt": produced_at,
                "payload": {"decision": decision, "confidence": confidence, "policyRules": [], "explanation": "", "moderator": moderator},
            }
        )

    # Automated REVIEW -> moderator approves. Not an override: REVIEW isn't a verdict.
    _decision(review_id, "REVIEW", 0.5, produced_at="2026-01-01T00:00:00Z")
    _decision(review_id, "APPROVE", 1.0, moderator="mod-1", version="moderator-override", produced_at="2026-01-01T00:01:00Z")

    # Automated REJECT -> moderator overturns to APPROVE. A real override.
    _decision(override_id, "REJECT", 0.97, produced_at="2026-01-01T00:00:00Z")
    _decision(override_id, "APPROVE", 1.0, moderator="mod-1", version="moderator-override", produced_at="2026-01-01T00:01:00Z")

    # Automated REJECT -> moderator agrees, also REJECT. Reviewed, not overridden.
    _decision(agree_id, "REJECT", 0.99, produced_at="2026-01-01T00:00:00Z")
    _decision(agree_id, "REJECT", 1.0, moderator="mod-1", version="moderator-override", produced_at="2026-01-01T00:01:00Z")

    # One Pipeline failure and one PolicyAgent match, unrelated to the decisions above.
    db.insert_artifact(
        {
            "listingId": review_id,
            "agent": "Pipeline",
            "version": "error-handler",
            "producedAt": "2026-01-01T00:00:00Z",
            "payload": {"failed": True, "stage": "pipeline", "error": "410 Client Error: Gone for url: test"},
        }
    )
    db.insert_artifact(
        {
            "listingId": review_id,
            "agent": "PolicyAgent",
            "version": "test-v1",
            "producedAt": "2026-01-01T00:00:00Z",
            "payload": {"matches": [{"rule": "C004", "severity": "Medium", "confidence": 0.6, "autoReject": False}]},
        }
    )

    yield {"review": review_id, "override": override_id, "agree": agree_id, "ids": ids, "since": since}

    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM artifacts WHERE listing_id = ANY(%s)", (ids,))
        cur.execute("DELETE FROM listings WHERE listing_id = ANY(%s)", (ids,))
        conn.commit()


def test_get_stats_review_outcome_is_not_counted_as_override(stats_listings):
    stats = db.get_stats(since=stats_listings["since"])
    assert stats["overriddenCount"] == 1  # only the REJECT->APPROVE case
    assert stats["humanReviewOutcomes"].get("APPROVE") == 1  # the REVIEW->APPROVE case


def test_get_stats_override_rate_excludes_agreements(stats_listings):
    stats = db.get_stats(since=stats_listings["since"])
    # 2 listings had an automated APPROVE/REJECT with a later moderator verdict
    # (override + agree); 1 of those 2 differed.
    assert stats["humanReviewedCount"] == 2
    assert stats["overriddenCount"] == 1
    assert stats["overrideRate"] == 0.5


def test_get_stats_automated_decision_counts(stats_listings):
    stats = db.get_stats(since=stats_listings["since"])
    assert stats["automatedDecisionCounts"]["REVIEW"] == 1
    assert stats["automatedDecisionCounts"]["REJECT"] == 2


def test_get_stats_policy_rule_hits(stats_listings):
    stats = db.get_stats(since=stats_listings["since"])
    assert stats["policyRuleHits"]["C004"] == 1


def test_get_stats_failures_by_error(stats_listings):
    stats = db.get_stats(since=stats_listings["since"])
    matching = [row for row in stats["failuresByError"] if row["error"] == "410 Client Error: Gone for url: test"]
    assert matching and matching[0]["n"] == 1


def test_get_stats_since_filters_out_older_listings(stats_listings):
    stats = db.get_stats(since="2099-01-01T00:00:00Z")
    assert stats["humanReviewedCount"] == 0
    assert stats["automatedDecisionCounts"] == {}
