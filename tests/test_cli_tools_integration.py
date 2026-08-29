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


def test_find_similar_cases_unknown_listing_raises():
    with pytest.raises(ValueError, match="No such listing"):
        tools.find_similar_cases("LST-DOES-NOT-EXIST")


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


def test_escalate_case_moves_pending_review_to_escalated(seeded_listing, active_moderator):
    result = tools.escalate_case(seeded_listing, "Repeat pattern worth senior review.", active_moderator)
    assert result["status"] == "ESCALATED"


def test_escalate_case_rejects_non_pending_review_listing(seeded_listing, active_moderator):
    tools.approve_listing(seeded_listing, active_moderator, "Looks fine.")

    with pytest.raises(ValueError, match="Can only escalate a PENDING_REVIEW listing"):
        tools.escalate_case(seeded_listing, "too late", active_moderator)


def test_escalated_case_resolves_via_existing_approve_reject_tools(seeded_listing, active_moderator):
    """Resolving an ESCALATED case needs no new code -- approve_listing/reject_listing
    already transition any listing regardless of current status (§8.2)."""
    tools.escalate_case(seeded_listing, "Needs senior review.", active_moderator)

    result = tools.reject_listing(seeded_listing, active_moderator, "Senior review confirms rejection.")
    assert result["status"] == "REJECTED"


def test_escalate_case_unknown_listing_raises(active_moderator):
    with pytest.raises(ValueError, match="No such listing"):
        tools.escalate_case("LST-DOES-NOT-EXIST", "reason", active_moderator)


def test_escalate_case_does_not_touch_seller_violation_count(seeded_listing_with_own_seller, active_moderator):
    listing_id, seller_id = seeded_listing_with_own_seller

    tools.escalate_case(listing_id, "Needs senior review.", active_moderator)

    seller = db.get_seller(seller_id)
    assert seller is None or seller["violation_count"] == 0


def test_request_appeal_moves_rejected_to_appeal_requested(seeded_listing_with_own_seller, active_moderator):
    listing_id, _ = seeded_listing_with_own_seller
    tools.reject_listing(listing_id, active_moderator, "Counterfeit.")

    result = tools.request_appeal(listing_id, "Seller disputes classification.", active_moderator)

    assert result["status"] == "APPEAL_REQUESTED"


def test_request_appeal_unknown_listing_raises(active_moderator):
    with pytest.raises(ValueError, match="No such listing"):
        tools.request_appeal("LST-DOES-NOT-EXIST", "reason", active_moderator)


def test_request_appeal_rejects_non_rejected_listing(seeded_listing, active_moderator):
    tools.approve_listing(seeded_listing, active_moderator, "Looks fine.")

    with pytest.raises(ValueError, match="Can only appeal a REJECTED listing"):
        tools.request_appeal(seeded_listing, "nonsense", active_moderator)


def test_resolve_appeal_denied_upholds_rejection_without_double_counting(
    seeded_listing_with_own_seller, active_moderator
):
    listing_id, seller_id = seeded_listing_with_own_seller
    tools.reject_listing(listing_id, active_moderator, "Counterfeit.")
    assert db.get_seller(seller_id)["violation_count"] == 1

    tools.request_appeal(listing_id, "Seller disputes classification.", active_moderator)
    result = tools.resolve_appeal(listing_id, "REJECT", "Upheld on review.", active_moderator)

    assert result["status"] == "REJECTED"
    assert db.get_seller(seller_id)["violation_count"] == 1  # not double-counted


def test_resolve_appeal_approved_overturns_rejection(seeded_listing, active_moderator):
    tools.reject_listing(seeded_listing, active_moderator, "Counterfeit.")
    tools.request_appeal(seeded_listing, "Seller provided proof of authenticity.", active_moderator)

    result = tools.resolve_appeal(seeded_listing, "APPROVE", "Proof accepted, overturning.", active_moderator)

    assert result["status"] == "APPROVED"


def test_resolve_appeal_unknown_listing_raises(active_moderator):
    with pytest.raises(ValueError, match="No such listing"):
        tools.resolve_appeal("LST-DOES-NOT-EXIST", "APPROVE", "reason", active_moderator)


def test_resolve_appeal_rejects_non_appeal_requested_listing(seeded_listing, active_moderator):
    tools.approve_listing(seeded_listing, active_moderator, "Looks fine.")

    with pytest.raises(ValueError, match="Can only resolve an APPEAL_REQUESTED listing"):
        tools.resolve_appeal(seeded_listing, "APPROVE", "nonsense", active_moderator)


def test_resolve_appeal_rejects_invalid_decision(seeded_listing, active_moderator):
    tools.reject_listing(seeded_listing, active_moderator, "Counterfeit.")
    tools.request_appeal(seeded_listing, "Dispute.", active_moderator)

    with pytest.raises(ValueError, match="decision must be APPROVE or REJECT"):
        tools.resolve_appeal(seeded_listing, "ESCALATE", "nonsense", active_moderator)


def test_list_seller_cases_returns_only_that_sellers_listings(seeded_listing_with_own_seller):
    listing_id, seller_id = seeded_listing_with_own_seller

    cases = tools.list_seller_cases(seller_id)

    assert [c["listing_id"] for c in cases] == [listing_id]


def test_suspend_seller_moves_active_to_suspended(seeded_listing_with_own_seller, active_moderator):
    _, seller_id = seeded_listing_with_own_seller
    db.upsert_seller_if_missing(seller_id)

    result = tools.suspend_seller(seller_id, "Multiple weapon listings.", active_moderator)

    assert result["status"] == "SUSPENDED"
    assert result["status_reason"] == "Multiple weapon listings."
    assert result["status_changed_by"] == active_moderator


def test_suspend_seller_rejects_already_suspended(seeded_listing_with_own_seller, active_moderator):
    _, seller_id = seeded_listing_with_own_seller
    db.upsert_seller_if_missing(seller_id)
    tools.suspend_seller(seller_id, "First suspension.", active_moderator)

    with pytest.raises(ValueError, match="Can only suspend an ACTIVE seller"):
        tools.suspend_seller(seller_id, "Second attempt.", active_moderator)


def test_suspend_seller_rejects_unknown_seller(active_moderator):
    with pytest.raises(ValueError, match="No such seller"):
        tools.suspend_seller("SUP-DOES-NOT-EXIST", "reason", active_moderator)


def test_reinstate_seller_moves_suspended_to_active(seeded_listing_with_own_seller, active_moderator):
    _, seller_id = seeded_listing_with_own_seller
    db.upsert_seller_if_missing(seller_id)
    tools.suspend_seller(seller_id, "Suspended.", active_moderator)

    result = tools.reinstate_seller(seller_id, "Investigation cleared them.", active_moderator)

    assert result["status"] == "ACTIVE"
    assert result["status_reason"] == "Investigation cleared them."


def test_reinstate_seller_rejects_already_active(seeded_listing_with_own_seller, active_moderator):
    _, seller_id = seeded_listing_with_own_seller
    db.upsert_seller_if_missing(seller_id)

    with pytest.raises(ValueError, match="Can only reinstate a SUSPENDED seller"):
        tools.reinstate_seller(seller_id, "reason", active_moderator)


def test_reinstate_seller_rejects_unknown_seller(active_moderator):
    with pytest.raises(ValueError, match="No such seller"):
        tools.reinstate_seller("SUP-DOES-NOT-EXIST", "reason", active_moderator)


def test_rerun_analysis_decision_agent_reads_real_db_rows(seeded_listing):
    """Regression test: `db.latest_artifact` returns raw Postgres rows (snake_case
    `produced_at`, a real `datetime`) -- `run_decision_agent` expects the same
    camelCase-`producedAt`-string shape `run_*_agent()` functions return, to build
    `basedOn` (§5). Calling `rerun_analysis(listingId, agent='DecisionAgent')`
    against real upstream artifacts used to crash with `KeyError: 'producedAt'`."""
    for agent_name, payload in [
        ("EvidenceAgent", {"brandMismatch": False, "brandsDetected": ["Apple"]}),
        ("ConsistencyAgent", {"inconsistencyScore": 0.1, "checks": [], "checksSkipped": []}),
        ("SafetyAgent", {"violations": [], "confidence": 0.98, "explanation": "No safety violations detected."}),
        ("PolicyAgent", {"matches": []}),
    ]:
        db.insert_artifact(
            {"listingId": seeded_listing, "agent": agent_name, "version": "test-v1", "payload": payload}
        )

    decision = tools.rerun_analysis(seeded_listing, agent="DecisionAgent")

    assert decision["agent"] == "DecisionAgent"
    assert len(decision["basedOn"]) == 4
    assert all(ref.count("@") == 1 for ref in decision["basedOn"])
    row = db.get_listing_row(seeded_listing)
    assert row["status"] in ("APPROVED", "REJECTED", "PENDING_REVIEW")
