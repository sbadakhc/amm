"""
Requires a real Postgres instance -- see scripts/dev-db.sh. Skipped automatically if
DATABASE_URL isn't set (conftest.py). Exercises pipeline.py's orchestration directly
(fan-out/fan-in, decision->status routing, the never-silent-approve error path,
poll_and_process's claim+process loop) rather than mocking it away, which every other
test file does. Agent runners and embed_text are mocked -- this is about pipeline.py's
own wiring, not the agents' internal logic (covered by their own test files)."""

import json
import uuid

import pytest

import db
import pipeline

pytestmark = pytest.mark.integration


def _agent_artifact(listing_id: str, agent: str, payload: dict) -> dict:
    return {
        "listingId": listing_id,
        "agent": agent,
        "version": "test",
        "producedAt": "2026-01-01T00:00:00Z",
        "payload": payload,
    }


@pytest.fixture
def mocked_agents(monkeypatch):
    """Mocks every agent runner + embed_text pipeline.py calls, and returns a dict of
    call trackers so tests can assert on ordering/count without touching real models."""
    calls = []

    def _make(agent_name, payload):
        def _runner(canonical_doc):
            calls.append(agent_name)
            return _agent_artifact(canonical_doc["listingId"], agent_name, payload)

        return _runner

    monkeypatch.setattr(pipeline, "run_evidence_agent", _make("EvidenceAgent", {"brandMismatch": False}))
    monkeypatch.setattr(pipeline, "run_consistency_agent", _make("ConsistencyAgent", {"inconsistencyScore": 0.05}))
    monkeypatch.setattr(pipeline, "run_safety_agent", _make("SafetyAgent", {"violations": []}))

    def _policy(canonical_doc, evidence, consistency, safety):
        calls.append("PolicyAgent")
        return _agent_artifact(canonical_doc["listingId"], "PolicyAgent", {"matches": []})

    monkeypatch.setattr(pipeline, "run_policy_agent", _policy)

    def _decision(canonical_doc, evidence, consistency, safety, policy):
        calls.append("DecisionAgent")
        decision = pipeline.__dict__.get("_next_decision", "APPROVE")
        return {
            **_agent_artifact(canonical_doc["listingId"], "DecisionAgent", {"decision": decision, "confidence": 0.95}),
            "basedOn": [],
        }

    monkeypatch.setattr(pipeline, "run_decision_agent", _decision)

    def _embed(text):
        calls.append("embed_text")
        return [0.0] * 2048

    monkeypatch.setattr(pipeline, "embed_text", _embed)

    return calls


def _set_next_decision(monkeypatch, decision: str):
    monkeypatch.setitem(pipeline.__dict__, "_next_decision", decision)


@pytest.fixture
def seeded_pending_listing():
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
                "PENDING_MODERATION",
            ),
        )
        conn.commit()

    yield listing_id, seller_id

    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM listing_embeddings WHERE listing_id = %s", (listing_id,))
        cur.execute("DELETE FROM artifacts WHERE listing_id = %s", (listing_id,))
        cur.execute("DELETE FROM listings WHERE listing_id = %s", (listing_id,))
        cur.execute("DELETE FROM sellers WHERE seller_id = %s", (seller_id,))
        conn.commit()


def test_run_fusion_inserts_artifacts_evidence_consistency_safety_before_policy_before_decision(
    mocked_agents, seeded_pending_listing
):
    listing_id, _ = seeded_pending_listing
    canonical_doc = {
        "listingId": listing_id,
        "title": "Test Listing",
        "description": "A test listing.",
        "declaredBrand": "Apple",
        "categoryId": "electronics.mobile",
        "sellerPreviousViolations": 0,
    }

    decision = pipeline.run_fusion(canonical_doc)

    assert decision["payload"]["decision"] == "APPROVE"
    assert mocked_agents.index("PolicyAgent") > mocked_agents.index("EvidenceAgent")
    assert mocked_agents.index("PolicyAgent") > mocked_agents.index("ConsistencyAgent")
    assert mocked_agents.index("PolicyAgent") > mocked_agents.index("SafetyAgent")
    assert mocked_agents.index("DecisionAgent") > mocked_agents.index("PolicyAgent")

    artifacts = {a["agent"] for a in db.get_artifacts(listing_id)}
    assert artifacts == {"EvidenceAgent", "ConsistencyAgent", "SafetyAgent", "PolicyAgent", "DecisionAgent"}
    assert db.get_listing_embedding(listing_id) is not None


@pytest.mark.parametrize(
    "decision,expected_status",
    [("APPROVE", "APPROVED"), ("REJECT", "REJECTED"), ("REVIEW", "PENDING_REVIEW")],
)
def test_process_listing_maps_decision_to_status(
    monkeypatch, mocked_agents, seeded_pending_listing, decision, expected_status
):
    listing_id, _ = seeded_pending_listing
    _set_next_decision(monkeypatch, decision)
    row = db.get_listing_row(listing_id)

    pipeline.process_listing(row)

    assert db.get_listing_row(listing_id)["status"] == expected_status


def test_process_listing_reject_increments_seller_violations(monkeypatch, mocked_agents, seeded_pending_listing):
    listing_id, seller_id = seeded_pending_listing
    _set_next_decision(monkeypatch, "REJECT")
    row = db.get_listing_row(listing_id)

    pipeline.process_listing(row)

    assert db.get_seller(seller_id)["violation_count"] == 1


@pytest.mark.parametrize("decision", ["APPROVE", "REVIEW"])
def test_process_listing_non_reject_does_not_increment_seller_violations(
    monkeypatch, mocked_agents, seeded_pending_listing, decision
):
    listing_id, seller_id = seeded_pending_listing
    _set_next_decision(monkeypatch, decision)
    row = db.get_listing_row(listing_id)

    pipeline.process_listing(row)

    assert db.get_seller(seller_id)["violation_count"] == 0


def test_process_listing_upserts_seller_without_resetting_existing_violations(
    monkeypatch, mocked_agents, seeded_pending_listing
):
    listing_id, seller_id = seeded_pending_listing
    db.upsert_seller_if_missing(seller_id, initial_violation_count=2)
    _set_next_decision(monkeypatch, "APPROVE")
    row = db.get_listing_row(listing_id)

    pipeline.process_listing(row)

    assert db.get_seller(seller_id)["violation_count"] == 2


def test_process_listing_agent_error_routes_to_pending_review_never_silent_approve(
    monkeypatch, mocked_agents, seeded_pending_listing
):
    """AGENTS.md/SPEC.md §2, §4: any agent erroring must never result in a silent
    APPROVE -- it goes to PENDING_REVIEW with a Pipeline artifact explaining why."""
    listing_id, _ = seeded_pending_listing

    def _boom(canonical_doc):
        raise RuntimeError("simulated agent failure")

    monkeypatch.setattr(pipeline, "run_evidence_agent", _boom)
    row = db.get_listing_row(listing_id)

    pipeline.process_listing(row)

    assert db.get_listing_row(listing_id)["status"] == "PENDING_REVIEW"
    failure_artifacts = db.get_artifacts(listing_id, agent="Pipeline")
    assert len(failure_artifacts) == 1
    assert failure_artifacts[0]["payload"]["failed"] is True
    assert failure_artifacts[0]["payload"]["stage"] == "pipeline"
    assert "simulated agent failure" in failure_artifacts[0]["payload"]["error"]


def test_poll_and_process_claims_and_processes_pending_listings(monkeypatch, mocked_agents, seeded_pending_listing):
    """Mocks db.claim_pending to return only our seeded row rather than calling the
    real one -- the real claim_pending claims *any* PENDING_MODERATION row in the
    whole table (ORDER BY created_at LIMIT batch_size), and against a shared dev
    Postgres that may already hold real/demo listings, letting this test claim them
    too would reprocess someone else's data with fake mocked agent output."""
    listing_id, _ = seeded_pending_listing
    _set_next_decision(monkeypatch, "APPROVE")
    row = db.get_listing_row(listing_id)
    monkeypatch.setattr(db, "claim_pending", lambda batch_size=10: [row])

    processed = pipeline.poll_and_process(batch_size=10)

    assert processed == 1
    assert db.get_listing_row(listing_id)["status"] == "APPROVED"
