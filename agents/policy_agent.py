"""
Policy Agent — maps Evidence/Safety/Consistency findings to policy rules, keyed off
categoryId. See SPEC.md §3.5. Deterministic — no model call.
"""

import json
import os
from datetime import datetime, timezone

# inconsistencyScore above this triggers C004. Tuned against real model-call data
# (docs/decisions/0014): 8 real Consistency Agent runs per demo scenario showed a
# clean empirical gap between scenarios that should trigger C004 (only
# "inconsistent", scores 0.505-0.712) and ones that shouldn't (all others,
# 0.089-0.461) -- 0.48 sits in that gap. Configurable (§4). Env var read once at
# import (same pattern as service.py); run_policy_agent also takes a per-call
# override for testing/rerunning without reloading the module.
CONSISTENCY_THRESHOLD = float(os.environ.get("CONSISTENCY_THRESHOLD", "0.48"))

RULES = {
    "W001": {"description": "Weapons prohibited", "severity": "Critical", "autoReject": False},
    "C001": {"description": "Counterfeit goods prohibited", "severity": "High", "autoReject": False},
    "C004": {"description": "Misleading product information", "severity": "Medium", "autoReject": False},
    "D001": {"description": "Illegal drugs prohibited", "severity": "Critical", "autoReject": False},
    "F001": {"description": "Fraud or deceptive listings prohibited", "severity": "High", "autoReject": False},
    "S001": {"description": "Sexual content involving minors prohibited", "severity": "Critical", "autoReject": True},
}

# Confirmed against real calls to nvidia/llama-3.1-nemotron-safety-guard-8b-v3
# (docs/decisions/0012) -- not every category the model can emit is listing-policy
# relevant (e.g. Violence, Hate/Identity Hate, Profanity are chat-safety categories,
# not obviously actionable on a product listing), so only the ones that are get a
# rule. "Copyright/Trademark/Plagiarism" deliberately maps to C001 too (handled
# specially below, not through this dict) rather than getting its own rule -- it's
# the same underlying violation (counterfeit goods) as Evidence Agent's brandMismatch
# signal, just detected from listing text instead of images.
#
# "Criminal Planning/Confessions" and "Illegal Activity" also map to F001
# (docs/decisions/0018) -- revises 0012's original decision to leave them unmapped.
# 0012 reasoned Criminal Planning/Confessions "fires alongside almost every other
# violation, too broad to be its own signal"; real-call testing against Arabic
# job-scam and real-estate-scam listing text found the opposite for fraud specifically
# -- the same scam intent that surfaces as Fraud/Deception in English often surfaces as
# these two categories instead in Arabic, and a broader batch of clean/edgy-but-legal
# Arabic listings (weapons, drugs, vague investment talk, ordinary goods) produced zero
# false positives on either category.
SAFETY_CATEGORY_TO_RULE = {
    "Guns and Illegal Weapons": "W001",
    "Controlled/Regulated Substances": "D001",
    "Fraud/Deception": "F001",
    "Criminal Planning/Confessions": "F001",
    "Illegal Activity": "F001",
    "Sexual (minor)": "S001",
}

# Rule sets are looked up per category (§3.5); "*" is the catch-all applied whenever a
# category has no more specific entry. No category narrows this further yet — add a
# prefix key (e.g. "finance") to scope rules to just that category tree.
RULE_SETS_BY_CATEGORY_PREFIX = {
    "*": ["W001", "C001", "C004", "D001", "F001", "S001"],
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

    if "C001" in applicable:
        # Two independent signals for the same underlying violation (counterfeit
        # goods): Evidence Agent's image-based brandMismatch and Safety Agent's
        # text-based Copyright/Trademark/Plagiarism category. At most one C001 match
        # is ever added (not one per signal) -- run_policy_agent's callers assume
        # policyRules has no duplicate rule ids (§5's DecisionAgent artifact).
        c001_confidences = []
        if evidence.get("brandMismatch"):
            c001_confidences.append(1.0)
        if "Copyright/Trademark/Plagiarism" in safety.get("violations", []):
            c001_confidences.append(safety["confidence"])
        if c001_confidences:
            matches.append(_match("C001", max(c001_confidences)))

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
