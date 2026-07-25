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
