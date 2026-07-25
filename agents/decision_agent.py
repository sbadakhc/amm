"""
Decision Agent — combines PolicyAgent matches into a final decision using the fusion
algorithm in §4. See SPEC.md §3.6. Deterministic — no model call.
"""

import json
from datetime import datetime, timezone

try:
    from agents.policy_agent import RULES
except ImportError:  # running as a script, not a package
    from policy_agent import RULES

CRITICAL_REJECT_THRESHOLD = 0.95
AUTO_APPROVE_THRESHOLD = 0.90
SELLER_HISTORY_ADJUSTMENT_PER_VIOLATION = 0.05
SELLER_HISTORY_ADJUSTMENT_CAP = 0.20


def _route(matches: list[dict], confidence: float) -> str:
    if any(m["autoReject"] for m in matches):
        return "REJECT"
    if any(m["severity"] == "Critical" for m in matches) and confidence >= CRITICAL_REJECT_THRESHOLD:
        return "REJECT"
    if not matches and confidence >= AUTO_APPROVE_THRESHOLD:
        return "APPROVE"
    return "REVIEW"


def _explain(
    decision: str,
    matches: list[dict],
    confidence: float,
    inconsistency_score: float,
    adjustment: float,
) -> str:
    if matches:
        rule_lines = ", ".join(f"{m['rule']} ({RULES[m['rule']]['description']})" for m in matches)
        base = f"Matched policy rule(s): {rule_lines}."
    else:
        base = f"No policy rule matched; residual inconsistency score {inconsistency_score:.2f}."

    if adjustment:
        base += f" Seller history shifted confidence by {adjustment:+.2f}."

    return f"{base} Decision: {decision} (confidence {confidence:.2f})."


def run_decision_agent(
    canonical_doc: dict,
    evidence_artifact: dict,
    consistency_artifact: dict,
    safety_artifact: dict,
    policy_artifact: dict,
    moderator: str | None = None,
) -> dict:
    """
    Fuses EvidenceAgent/ConsistencyAgent/SafetyAgent/PolicyAgent artifacts (§5, full
    artifacts — not just payloads, since basedOn needs their producedAt) into a
    DecisionAgent artifact per §5 using the algorithm in §4.
    """
    matches = policy_artifact["payload"]["matches"]
    inconsistency_score = consistency_artifact["payload"].get("inconsistencyScore", 0.0)

    # Step 1 — aggregate confidence.
    if matches:
        confidence = max(m["confidence"] for m in matches)
    else:
        confidence = 1 - inconsistency_score

    # Step 2 — seller history adjustment, based on where the pre-adjustment confidence
    # would tentatively route (§4 step 2 refers forward to step 3's routing).
    previous_violations = canonical_doc.get("sellerPreviousViolations", 0) or 0
    adjustment = 0.0
    if previous_violations > 0:
        tentative = _route(matches, confidence)
        magnitude = min(
            SELLER_HISTORY_ADJUSTMENT_PER_VIOLATION * previous_violations,
            SELLER_HISTORY_ADJUSTMENT_CAP,
        )
        if tentative == "APPROVE":
            adjustment = -magnitude
        elif tentative == "REJECT":
            adjustment = magnitude
        confidence = min(max(confidence + adjustment, 0.0), 1.0)

    # Step 3 — final routing on the adjusted confidence.
    decision = _route(matches, confidence)
    explanation = _explain(decision, matches, confidence, inconsistency_score, adjustment)

    payload = {
        "decision": decision,
        "confidence": round(confidence, 4),
        "policyRules": [m["rule"] for m in matches],
        "explanation": explanation,
        "moderator": moderator,
    }

    return {
        "listingId": canonical_doc["listingId"],
        "agent": "DecisionAgent",
        "version": "fusion-v1",
        "producedAt": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
        "basedOn": [
            f"EvidenceAgent@{evidence_artifact['producedAt']}",
            f"ConsistencyAgent@{consistency_artifact['producedAt']}",
            f"SafetyAgent@{safety_artifact['producedAt']}",
            f"PolicyAgent@{policy_artifact['producedAt']}",
        ],
    }


if __name__ == "__main__":
    import sys

    canonical_doc, evidence_artifact, consistency_artifact, safety_artifact, policy_artifact = (
        json.loads(a) for a in sys.argv[1:6]
    )
    print(
        json.dumps(
            run_decision_agent(
                canonical_doc, evidence_artifact, consistency_artifact, safety_artifact, policy_artifact
            ),
            indent=2,
        )
    )
