"""
Workflow orchestration — Intake -> {Evidence, Consistency, Safety} in parallel ->
Policy -> Decision. See SPEC.md §1 and §7. Single-process fan-out/fan-in, no broker.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import db
from agents.consistency_agent import run_consistency_agent
from agents.decision_agent import run_decision_agent
from agents.evidence_agent import run_evidence_agent
from agents.policy_agent import run_policy_agent
from agents.safety_agent import run_safety_agent
from embeddings import MODEL as EMBEDDING_MODEL
from embeddings import embed_text
from intake import to_canonical_document

DECISION_TO_STATUS = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "REVIEW": "PENDING_REVIEW"}


def _record_failure(listing_id: str, stage: str, error: Exception) -> None:
    """Any agent erroring or timing out goes to PENDING_REVIEW, never silent-approve
    (§2, §4) — recorded as a Pipeline artifact so `explain_case` shows why."""
    db.insert_artifact(
        {
            "listingId": listing_id,
            "agent": "Pipeline",
            "version": "error-handler",
            "producedAt": datetime.now(timezone.utc).isoformat(),
            "payload": {"failed": True, "stage": stage, "error": str(error)},
        }
    )
    db.update_listing_status(listing_id, "PENDING_REVIEW")


def run_fusion(canonical_doc: dict) -> dict:
    """Runs Evidence/Consistency/Safety in parallel (plus the find_similar_cases
    embedding, §6/§10 — doesn't feed the decision, just needs the same canonical
    doc, so it fans out alongside the agents rather than as a separate pass), then
    Policy, then Decision. Returns the final DecisionAgent artifact. Does not touch
    listing status beyond writing the artifacts/embedding themselves — callers decide
    what to do with the resulting status transition."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        evidence_future = pool.submit(run_evidence_agent, canonical_doc)
        consistency_future = pool.submit(run_consistency_agent, canonical_doc)
        safety_future = pool.submit(run_safety_agent, canonical_doc)
        embedding_future = pool.submit(embed_text, f"{canonical_doc['title']}. {canonical_doc['description']}")
        evidence = evidence_future.result()
        consistency = consistency_future.result()
        safety = safety_future.result()
        embedding = embedding_future.result()

    db.insert_artifact(evidence)
    db.insert_artifact(consistency)
    db.insert_artifact(safety)
    db.upsert_listing_embedding(canonical_doc["listingId"], EMBEDDING_MODEL, embedding)

    policy = run_policy_agent(canonical_doc, evidence["payload"], consistency["payload"], safety["payload"])
    db.insert_artifact(policy)

    decision = run_decision_agent(canonical_doc, evidence, consistency, safety, policy)
    db.insert_artifact(decision)

    return decision


def process_listing(row: dict) -> None:
    """End-to-end processing of one claimed (PROCESSING) listing row (§7)."""
    listing_id = row["listing_id"]
    try:
        canonical_doc = to_canonical_document(row)
        decision = run_fusion(canonical_doc)
        db.update_listing_status(listing_id, DECISION_TO_STATUS[decision["payload"]["decision"]])
    except Exception as e:  # noqa: BLE001 - any agent failure routes to review, never silent-approve
        _record_failure(listing_id, "pipeline", e)


def poll_and_process(batch_size: int = 10) -> int:
    """One poll cycle: claims pending listings and runs each through the pipeline.
    Returns the number processed."""
    rows = db.claim_pending(batch_size=batch_size)
    for row in rows:
        process_listing(row)
    return len(rows)
