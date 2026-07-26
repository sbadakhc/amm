"""
Policy Agent — maps Evidence/Safety/Consistency findings to policy rules, keyed off
categoryId. See SPEC.md §3.5. Deterministic — no model call.
"""

import json
import os
from datetime import datetime, timezone

# inconsistencyScore above this triggers C004. Configurable (§4), not tuned against
# real traffic yet — picked from observed scores on the demo's synthetic data. Env
# var read once at import (same pattern as service.py); run_policy_agent also takes
# a per-call override for testing/rerunning without reloading the module.
CONSISTENCY_THRESHOLD = float(os.environ.get("CONSISTENCY_THRESHOLD", "0.30"))

RULES = {
    "W001": {"description": "Weapons prohibited", "severity": "Critical", "autoReject": False},
    "C001": {"description": "Counterfeit goods prohibited", "severity": "High", "autoReject": False},
    "C004": {"description": "Misleading product information", "severity": "Medium", "autoReject": False},
    "D001": {"description": "Illegal drugs prohibited", "severity": "Critical", "autoReject": False},
}

SAFETY_CATEGORY_TO_RULE = {
    "Guns and Illegal Weapons": "W001",
    "Controlled/Regulated Substances": "D001",
}

# Rule sets are looked up per category (§3.5); "*" is the catch-all applied whenever a
# category has no more specific entry. No category narrows this further yet — add a
# prefix key (e.g. "finance") to scope rules to just that category tree.
RULE_SETS_BY_CATEGORY_PREFIX = {
    "*": ["W001", "C001", "C004", "D001"],
}


def _rules_for_category(category_id: str) -> set[str]:
    prefix = category_id.split(".")[0] if category_id else ""
    return set(RULE_SETS_BY_CATEGORY_PREFIX.get(prefix, RULE_SETS_BY_CATEGORY_PREFIX["*"]))


def _match(rule_id: str, confidence: float) -> dict:
    rule = RULES[rule_id]
    return {
        "rule": rule_id,
        "severity": rule["severity"],
        "autoReject": rule["autoReject"],
        "confidence": confidence,
    }


def run_policy_agent(
    canonical_doc: dict,
    evidence: dict,
    consistency: dict,
    safety: dict,
    consistency_threshold: float | None = None,
) -> dict:
    """
    Maps EvidenceAgent/ConsistencyAgent/SafetyAgent payloads to policy rule matches and
    returns a PolicyAgent artifact (§5) ready to append to the `artifacts` table.
    `consistency_threshold` defaults to the module constant (env-configurable, see
    above) when not given explicitly.
    """
    consistency_threshold = CONSISTENCY_THRESHOLD if consistency_threshold is None else consistency_threshold
    applicable = _rules_for_category(canonical_doc.get("categoryId", ""))
    matches = []

    matched_safety_rules = set()
    for violation in safety.get("violations", []):
        rule_id = SAFETY_CATEGORY_TO_RULE.get(violation)
        if rule_id and rule_id in applicable and rule_id not in matched_safety_rules:
            matches.append(_match(rule_id, safety["confidence"]))
            matched_safety_rules.add(rule_id)

    if "C001" in applicable and evidence.get("brandMismatch"):
        matches.append(_match("C001", 1.0))

    if "C004" in applicable and consistency.get("inconsistencyScore", 0) > consistency_threshold:
        matches.append(_match("C004", consistency["inconsistencyScore"]))

    return {
        "listingId": canonical_doc["listingId"],
        "agent": "PolicyAgent",
        "version": "rules-v1",
        "producedAt": datetime.now(timezone.utc).isoformat(),
        "payload": {"matches": matches},
    }


if __name__ == "__main__":
    import sys

    canonical_doc, evidence, consistency, safety = (json.loads(a) for a in sys.argv[1:5])
    print(json.dumps(run_policy_agent(canonical_doc, evidence, consistency, safety), indent=2))
