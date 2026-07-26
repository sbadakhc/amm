"""
Requires a real Postgres instance -- see scripts/dev-db.sh. Skipped automatically if
DATABASE_URL isn't set (conftest.py). These exercise the actual locking/claim SQL
(§2.1) and artifact log (§5), not mocks -- that's the whole point of testing against
a real database rather than reasoning about the SQL in the abstract.
"""

import json
import uuid

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

    results = db.find_similar_by_embedding(listing_a, k=5)
    result_ids = [r["listing_id"] for r in results]

    assert result_ids[0] == listing_b  # identical direction -> distance 0
    assert result_ids[1] == listing_c  # orthogonal -> distance 1
    assert results[0]["distance"] < results[1]["distance"]
    assert listing_a not in result_ids  # excludes itself


def test_upsert_listing_embedding_overwrites(three_embedded_listings):
    listing_a, _, listing_c = three_embedded_listings
    db.upsert_listing_embedding(listing_a, "test-model-v2", _unit_vector(1))  # now matches C's direction

    results = db.find_similar_by_embedding(listing_a, k=5)
    assert results[0]["listing_id"] == listing_c
    assert results[0]["distance"] == 0.0
