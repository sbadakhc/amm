"""
Requires a real Postgres instance -- see scripts/dev-db.sh. Skipped automatically if
DATABASE_URL isn't set (conftest.py). Exercises the moderator authorization added in
docs/decisions/0009 against a real moderators table, not mocks.
"""

import json
import uuid

import pytest

import db
from cli import tools

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
                "PENDING_REVIEW",
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


@pytest.fixture
def active_moderator():
    moderator_id = f"mod-active-{uuid.uuid4().hex[:6]}"
    db.create_moderator(moderator_id, "Active Moderator", active=True)
    yield moderator_id
    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM moderators WHERE moderator_id = %s", (moderator_id,))
        conn.commit()


@pytest.fixture
def inactive_moderator():
    moderator_id = f"mod-inactive-{uuid.uuid4().hex[:6]}"
    db.create_moderator(moderator_id, "Inactive Moderator", active=False)
    yield moderator_id
    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM moderators WHERE moderator_id = %s", (moderator_id,))
        conn.commit()


def test_approve_listing_with_active_moderator_succeeds(seeded_listing, active_moderator):
    row = tools.approve_listing(seeded_listing, active_moderator, "Looks fine.")
    assert row["status"] == "APPROVED"


def test_approve_listing_with_unknown_moderator_raises(seeded_listing):
    with pytest.raises(ValueError, match="Unknown moderator"):
        tools.approve_listing(seeded_listing, "no-such-moderator", "note")


def test_approve_listing_with_inactive_moderator_raises(seeded_listing, inactive_moderator):
    with pytest.raises(ValueError, match="not active"):
        tools.approve_listing(seeded_listing, inactive_moderator, "note")


def test_reject_listing_requires_reason(seeded_listing, active_moderator):
    with pytest.raises(ValueError, match="reason is required"):
        tools.reject_listing(seeded_listing, active_moderator)


def test_moderator_id_falls_back_to_env_var(monkeypatch, seeded_listing, active_moderator):
    monkeypatch.setenv("MODERATOR_ID", active_moderator)
    row = tools.approve_listing(seeded_listing, note="Approved via env default.")
    assert row["status"] == "APPROVED"


def test_missing_moderator_id_and_env_var_raises(monkeypatch, seeded_listing):
    monkeypatch.delenv("MODERATOR_ID", raising=False)
    with pytest.raises(ValueError, match="MODERATOR_ID"):
        tools.approve_listing(seeded_listing, note="no moderator given")


def test_whoami_returns_own_registry_entry(active_moderator):
    result = tools.whoami(active_moderator)
    assert result["moderator_id"] == active_moderator
    assert result["active"] is True


def test_find_similar_cases_without_embedding_raises(seeded_listing):
    with pytest.raises(ValueError, match="No embedding computed yet"):
        tools.find_similar_cases(seeded_listing)


def test_find_similar_cases_ranks_by_embedding_distance(seeded_listing):
    other_id = f"LST-TEST-{uuid.uuid4().hex[:6].upper()}"
    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO listings
                (listing_id, seller, title, description, category, price, quantity,
                 condition, brand, model, sku, images, attributes, shipping, status)
            VALUES (%s, '{}', 'test', 'test', '{"id":"x"}', '{}', 1, 'new', 'x', 'x', 'x', '[]', '{}', '{}', 'PENDING_REVIEW')
            """,
            (other_id,),
        )
        conn.commit()

    vec = [0.0] * 2048
    vec[0] = 1.0
    db.upsert_listing_embedding(seeded_listing, "test-model", vec)
    db.upsert_listing_embedding(other_id, "test-model", vec)

    try:
        results = tools.find_similar_cases(seeded_listing)
        assert results[0]["listing_id"] == other_id
        assert results[0]["distance"] == 0.0
    finally:
        with db.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM listing_embeddings WHERE listing_id = %s", (other_id,))
            cur.execute("DELETE FROM listings WHERE listing_id = %s", (other_id,))
            conn.commit()


@pytest.fixture
def seeded_listing_with_own_seller():
    """Same shape as `seeded_listing`, but with a unique sellerId rather than the
    shared 'SUP-TEST' -- needed for §8.3 tests below since record_decision(REJECT)
    now creates/increments a real `sellers` row keyed by sellerId, and sharing one
    across tests would make violation_count assertions order-dependent."""
    listing_id = f"LST-TEST-{uuid.uuid4().hex[:6].upper()}"
    seller_id = f"SUP-TEST-{uuid.uuid4().hex[:6].upper()}"
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
                json.dumps({"sellerId": seller_id, "verified": True, "previousViolations": 0}),
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

    yield listing_id, seller_id

    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM artifacts WHERE listing_id = %s", (listing_id,))
        cur.execute("DELETE FROM listings WHERE listing_id = %s", (listing_id,))
        cur.execute("DELETE FROM sellers WHERE seller_id = %s", (seller_id,))
        conn.commit()


def test_reject_listing_increments_seller_violation_count(seeded_listing_with_own_seller, active_moderator):
    listing_id, seller_id = seeded_listing_with_own_seller

    tools.reject_listing(listing_id, active_moderator, "Counterfeit.")

    assert db.get_seller(seller_id)["violation_count"] == 1


def test_approve_listing_does_not_touch_seller_violation_count(seeded_listing_with_own_seller, active_moderator):
    listing_id, seller_id = seeded_listing_with_own_seller

    tools.approve_listing(listing_id, active_moderator, "Looks fine.")

    seller = db.get_seller(seller_id)
    assert seller is None or seller["violation_count"] == 0
