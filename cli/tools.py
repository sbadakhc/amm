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


def get_stats(since: str | None = None) -> dict:
    """`get_stats(since?)` (§6, docs/decisions/0027) — automated-pipeline accuracy and
    performance signals: decision distribution, moderator override rate, confidence,
    latency, failure rate, policy rule hit counts. Thin passthrough to `db.get_stats`
    -- see there for what each field means and how it's derived from the artifact
    log. `since` is an optional ISO timestamp; omitted means all-time."""
    return db.get_stats(since=since)


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


def record_decision(
    listing_id: str,
    decision: str,
    reason: str,
    moderator_id: str | None = None,
    version: str = "moderator-override",
    count_violation: bool = True,
) -> dict:
    """`record_decision(listingId, decision, reason)` (§6) — appends a new DecisionAgent
    artifact (moderator override, §5) rather than editing the automated one, and moves
    the listing to the matching status. A REJECT decision also increments the
    seller's placeholder violation count (§8.3, docs/decisions/0017), same as the
    automated path in `pipeline.process_listing` -- unless `count_violation=False`,
    used by `resolve_appeal` when denying an appeal: the violation was already
    counted when the listing was first rejected, so upholding it on appeal isn't a
    *new* violation. `version` lets callers mark what produced this artifact --
    `resolve_appeal` uses `"moderator-appeal-resolution"` instead of the default, so
    an appeal outcome is distinguishable from a plain override in the artifact log
    without a separate terminal-state vocabulary (§8.2)."""
    if decision not in DECISION_TO_STATUS:
        raise ValueError(f"decision must be one of {list(DECISION_TO_STATUS)}, got {decision!r}")

    moderator_id = _resolve_moderator(moderator_id)
    previous = db.latest_artifact(listing_id, "DecisionAgent")
    artifact = {
        "listingId": listing_id,
        "agent": "DecisionAgent",
        "version": version,
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
    if decision == "REJECT" and count_violation:
        row = db.get_listing_row(listing_id)
        seller_id = row["seller"]["sellerId"]
        db.upsert_seller_if_missing(seller_id, row["seller"].get("previousViolations", 0))
        db.increment_seller_violations(seller_id)
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


def escalate_case(listing_id: str, reason: str, moderator_id: str | None = None) -> dict:
    """`escalate_case(listingId, reason, moderatorId?)` (§8.4) -- moves a
    PENDING_REVIEW case to ESCALATED for senior-reviewer attention. Only valid from
    PENDING_REVIEW (§8.2's state machine extension); escalating an already-terminal
    or already-escalated listing is rejected rather than silently re-escalated.

    Resolving an escalated case reuses approve_listing/reject_listing unchanged --
    they don't check current status before transitioning. There is no senior-reviewer
    role distinction in the moderators table yet (§8.4) -- any active moderator can
    resolve an ESCALATED case, same as a PENDING_REVIEW one. Noted as a known
    simplification, not solved here."""
    row = db.get_listing_row(listing_id)
    if row is None:
        raise ValueError(f"No such listing: {listing_id}")
    if row["status"] != "PENDING_REVIEW":
        raise ValueError(f"Can only escalate a PENDING_REVIEW listing, got status {row['status']!r}")
    record_decision(listing_id, "ESCALATE", reason, moderator_id)
    return db.get_listing_row(listing_id)


def request_appeal(listing_id: str, reason: str, moderator_id: str | None = None) -> dict:
    """`request_appeal(listingId, reason, moderatorId?)` (§8.4) -- moves a REJECTED
    listing to APPEAL_REQUESTED. Moderator-invoked only: this system has no
    seller-facing surface at all (SPEC.md is explicit -- conversational CLI, no web
    UI), so there's no `initiatedBy: seller | moderator` distinction to model here.
    This tool relays an appeal that reached a human through some other channel (the
    marketplace app, support, etc.), not something a seller triggers directly.

    Only valid from REJECTED, not APPROVED -- there's no real use case for
    contesting an approval, appeals exist to contest adverse decisions."""
    row = db.get_listing_row(listing_id)
    if row is None:
        raise ValueError(f"No such listing: {listing_id}")
    if row["status"] != "REJECTED":
        raise ValueError(f"Can only appeal a REJECTED listing, got status {row['status']!r}")
    record_decision(listing_id, "REQUEST_APPEAL", reason, moderator_id)
    return db.get_listing_row(listing_id)


def resolve_appeal(listing_id: str, decision: str, reason: str, moderator_id: str | None = None) -> dict:
    """`resolve_appeal(listingId, decision, reason, moderatorId?)` (§8.4) -- closes an
    APPEAL_REQUESTED case, `decision` one of APPROVE (overturns the original
    rejection) or REJECT (upholds it). Reuses APPROVED/REJECTED as the listing's
    actual final status rather than separate APPEAL_APPROVED/APPEAL_DENIED states
    (§8.2) -- the artifact's `version` marks it as an appeal resolution instead.

    Denying (REJECT) does not increment the seller's violation count again -- it was
    already counted when the listing was first rejected; upholding it on appeal
    isn't a *new* violation."""
    if decision not in ("APPROVE", "REJECT"):
        raise ValueError(f"decision must be APPROVE or REJECT, got {decision!r}")
    row = db.get_listing_row(listing_id)
    if row is None:
        raise ValueError(f"No such listing: {listing_id}")
    if row["status"] != "APPEAL_REQUESTED":
        raise ValueError(f"Can only resolve an APPEAL_REQUESTED listing, got status {row['status']!r}")
    record_decision(
        listing_id, decision, reason, moderator_id, version="moderator-appeal-resolution", count_violation=False
    )
    return db.get_listing_row(listing_id)


def list_seller_cases(seller_id: str) -> list[dict]:
    """`list_seller_cases(sellerId)` (§8.4) -- every listing tied to one seller."""
    return db.list_listings_by_seller(seller_id)


def suspend_seller(seller_id: str, reason: str, moderator_id: str | None = None) -> dict:
    """`suspend_seller(sellerId, reason, moderatorId?)` (§8.4) -- moves a seller's
    placeholder account (§8.3) from ACTIVE to SUSPENDED. Only valid from ACTIVE.
    Does not cascade to the seller's existing listings (e.g. auto-rejecting pending
    ones) -- a known simplification, not solved here; each listing is still decided
    on its own via the normal tools."""
    moderator_id = _resolve_moderator(moderator_id)
    seller = db.get_seller(seller_id)
    if seller is None:
        raise ValueError(f"No such seller: {seller_id}")
    if seller["status"] != "ACTIVE":
        raise ValueError(f"Can only suspend an ACTIVE seller, got status {seller['status']!r}")
    db.update_seller_status(seller_id, "SUSPENDED", reason, moderator_id)
    return db.get_seller(seller_id)


def reinstate_seller(seller_id: str, reason: str, moderator_id: str | None = None) -> dict:
    """`reinstate_seller(sellerId, reason, moderatorId?)` (§8.4) -- moves a seller's
    placeholder account (§8.3) from SUSPENDED back to ACTIVE. Only valid from
    SUSPENDED -- there is no `terminate_seller` tool, so TERMINATED is a valid schema
    status (§8.1) that nothing currently produces, not reachable from here."""
    moderator_id = _resolve_moderator(moderator_id)
    seller = db.get_seller(seller_id)
    if seller is None:
        raise ValueError(f"No such seller: {seller_id}")
    if seller["status"] != "SUSPENDED":
        raise ValueError(f"Can only reinstate a SUSPENDED seller, got status {seller['status']!r}")
    db.update_seller_status(seller_id, "ACTIVE", reason, moderator_id)
    return db.get_seller(seller_id)


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
