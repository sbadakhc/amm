"""
Moderator CLI tools — see SPEC.md §6. Conversational, tool-driven, no direct DB access:
a moderator (or Claude Code on their behalf) calls these functions; nothing here talks
to Postgres except through them.
"""

import os
from datetime import datetime, timezone

import db
from agents.consistency_agent import run_consistency_agent
from agents.decision_agent import run_decision_agent
from agents.evidence_agent import run_evidence_agent
from agents.policy_agent import RULES, run_policy_agent
from agents.safety_agent import run_safety_agent
from intake import to_canonical_document
from pipeline import DECISION_TO_STATUS, run_fusion

AGENT_RUNNERS = {
    "EvidenceAgent": run_evidence_agent,
    "ConsistencyAgent": run_consistency_agent,
    "SafetyAgent": run_safety_agent,
}


def list_pending(limit: int | None = None, category: str | None = None) -> list[dict]:
    """`list_pending()` (§6) — the moderator's review queue, i.e. listings currently
    `PENDING_REVIEW`."""
    return db.list_listings_by_status("PENDING_REVIEW", limit=limit, category_id=category)


def get_listing(listing_id: str) -> dict:
    """`get_listing(listingId)` (§6) — full document + agent outputs."""
    row = db.get_listing_row(listing_id)
    if row is None:
        raise ValueError(f"No such listing: {listing_id}")
    return {"listing": row, "artifacts": db.get_artifacts(listing_id)}


def explain_case(listing_id: str) -> list[dict]:
    """`explain_case(listingId)` (§6) — all artifacts for the listing, per-agent, in
    order; reads straight off the artifact log (§5), no separate reasoning trace."""
    return db.get_artifacts(listing_id)


def show_images(listing_id: str) -> list[str]:
    """`show_images(listingId)` (§6)."""
    row = db.get_listing_row(listing_id)
    if row is None:
        raise ValueError(f"No such listing: {listing_id}")
    return [img["url"] for img in row["images"]]


def search_policy(query: str) -> list[dict]:
    """`search_policy(query)` (§6) — matches against the rule registry (§3.5)."""
    q = query.strip().lower()
    return [
        {"rule": rule_id, **rule}
        for rule_id, rule in RULES.items()
        if q in rule_id.lower() or q in rule["description"].lower()
    ]


def find_similar_cases(listing_id: str, k: int = 5) -> list[dict]:
    """`find_similar_cases(listingId, k)` (§6). Real semantic similarity via text
    embeddings (title+description, `embeddings.py`) stored and queried in Postgres
    with pgvector's cosine distance operator -- replaces the category+rule-overlap
    heuristic (docs/decisions/0005, superseded by 0010). The embedding is computed
    once per listing during `pipeline.run_fusion`; a listing that hasn't been through
    the pipeline yet has no embedding to compare against."""
    row = db.get_listing_row(listing_id)
    if row is None:
        raise ValueError(f"No such listing: {listing_id}")
    if db.get_listing_embedding(listing_id) is None:
        raise ValueError(f"No embedding computed yet for listing {listing_id} — run the pipeline on it first")
    return db.find_similar_by_embedding(listing_id, k=k)


def _resolve_moderator(moderator_id: str | None) -> str:
    """Authorization, not authentication (§6, docs/decisions/0009): checks the given
    (or MODERATOR_ID env var-defaulted, like git's user.name) moderator_id is a known,
    active entry in the moderators table. No passwords, no tokens."""
    moderator_id = moderator_id or os.environ.get("MODERATOR_ID")
    if not moderator_id:
        raise ValueError("moderator_id not provided and MODERATOR_ID env var not set")
    moderator = db.get_moderator(moderator_id)
    if moderator is None:
        raise ValueError(f"Unknown moderator: {moderator_id!r}")
    if not moderator["active"]:
        raise ValueError(f"Moderator {moderator_id!r} is not active")
    return moderator_id


def whoami(moderator_id: str | None = None) -> dict:
    """Not in SPEC.md's original §6 tool table -- added alongside the moderator
    registry so a moderator can confirm their own identity/active status before
    acting on a case."""
    moderator_id = _resolve_moderator(moderator_id)
    return db.get_moderator(moderator_id)


def record_decision(listing_id: str, decision: str, reason: str, moderator_id: str | None = None) -> dict:
    """`record_decision(listingId, decision, reason)` (§6) — appends a new DecisionAgent
    artifact (moderator override, §5) rather than editing the automated one, and moves
    the listing to the matching terminal status."""
    if decision not in DECISION_TO_STATUS:
        raise ValueError(f"decision must be one of {list(DECISION_TO_STATUS)}, got {decision!r}")

    moderator_id = _resolve_moderator(moderator_id)
    previous = db.latest_artifact(listing_id, "DecisionAgent")
    artifact = {
        "listingId": listing_id,
        "agent": "DecisionAgent",
        "version": "moderator-override",
        "producedAt": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "decision": decision,
            "confidence": 1.0,
            "policyRules": previous["payload"]["policyRules"] if previous else [],
            "explanation": reason,
            "moderator": moderator_id,
        },
        "basedOn": [f"DecisionAgent@{previous['produced_at']}"] if previous else None,
    }
    inserted = db.insert_artifact(artifact)
    db.update_listing_status(listing_id, DECISION_TO_STATUS[decision])
    return inserted


def approve_listing(listing_id: str, moderator_id: str | None = None, note: str | None = None) -> dict:
    """`approve_listing(listingId, moderatorId, note?)` (§6). `moderator_id` defaults to
    the `MODERATOR_ID` env var when not given explicitly."""
    record_decision(listing_id, "APPROVE", note or "Approved by moderator.", moderator_id)
    return db.get_listing_row(listing_id)


def reject_listing(listing_id: str, moderator_id: str | None = None, reason: str | None = None) -> dict:
    """`reject_listing(listingId, moderatorId, reason)` (§6) — matches the documented
    positional order; `reason` is still required at runtime (not truly optional, just
    can't have a Python default positioned after `moderator_id`'s). `moderator_id`
    defaults to the `MODERATOR_ID` env var when not given explicitly."""
    if reason is None:
        raise ValueError("reason is required")
    record_decision(listing_id, "REJECT", reason, moderator_id)
    return db.get_listing_row(listing_id)


def rerun_analysis(listing_id: str, agent: str | None = None) -> dict:
    """`rerun_analysis(listingId, agent?)` (§6). Appends new artifact(s) rather than
    overwriting (§5) — safe to call on a terminal listing, e.g. after a model upgrade.
    `agent=None` reruns the full Evidence/Consistency/Safety/Policy/Decision chain;
    naming one agent reruns just that step (Policy/Decision pull the latest upstream
    artifacts already on file rather than re-running everything upstream of them)."""
    row = db.get_listing_row(listing_id)
    if row is None:
        raise ValueError(f"No such listing: {listing_id}")
    canonical_doc = to_canonical_document(row)

    if agent is None:
        return run_fusion(canonical_doc)

    if agent in AGENT_RUNNERS:
        artifact = AGENT_RUNNERS[agent](canonical_doc)
        return db.insert_artifact(artifact)

    if agent == "PolicyAgent":
        evidence = db.latest_artifact(listing_id, "EvidenceAgent")
        consistency = db.latest_artifact(listing_id, "ConsistencyAgent")
        safety = db.latest_artifact(listing_id, "SafetyAgent")
        artifact = run_policy_agent(canonical_doc, evidence["payload"], consistency["payload"], safety["payload"])
        return db.insert_artifact(artifact)

    if agent == "DecisionAgent":
        evidence = db.latest_artifact(listing_id, "EvidenceAgent")
        consistency = db.latest_artifact(listing_id, "ConsistencyAgent")
        safety = db.latest_artifact(listing_id, "SafetyAgent")
        policy = db.latest_artifact(listing_id, "PolicyAgent")
        decision = run_decision_agent(canonical_doc, evidence, consistency, safety, policy)
        db.insert_artifact(decision)
        db.update_listing_status(listing_id, DECISION_TO_STATUS[decision["payload"]["decision"]])
        return decision

    raise ValueError(f"Unknown agent: {agent}")
