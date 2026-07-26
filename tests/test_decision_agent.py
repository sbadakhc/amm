from agents.decision_agent import run_decision_agent


def _artifact(agent: str, payload: dict, produced_at: str = "2026-01-01T00:00:00Z") -> dict:
    return {"listingId": "LST-TEST", "agent": agent, "version": "test", "producedAt": produced_at, "payload": payload}


def _policy(matches: list[dict]) -> dict:
    return _artifact("PolicyAgent", {"matches": matches})


def _run(canonical_doc, matches, inconsistency_score=0.05, **overrides):
    evidence = _artifact("EvidenceAgent", {})
    consistency = _artifact("ConsistencyAgent", {"inconsistencyScore": inconsistency_score})
    safety = _artifact("SafetyAgent", {})
    policy = _policy(matches)
    return run_decision_agent(canonical_doc, evidence, consistency, safety, policy, **overrides)


def test_no_matches_low_inconsistency_approves():
    result = _run({"listingId": "LST-TEST"}, matches=[], inconsistency_score=0.05)
    assert result["payload"]["decision"] == "APPROVE"
    assert result["payload"]["confidence"] == 0.95


def test_no_matches_high_inconsistency_reviews():
    result = _run({"listingId": "LST-TEST"}, matches=[], inconsistency_score=0.3)
    assert result["payload"]["decision"] == "REVIEW"


def test_critical_match_above_threshold_rejects():
    matches = [{"rule": "W001", "severity": "Critical", "autoReject": False, "confidence": 0.99}]
    result = _run({"listingId": "LST-TEST"}, matches=matches)
    assert result["payload"]["decision"] == "REJECT"
    assert result["payload"]["confidence"] == 0.99
    assert result["payload"]["policyRules"] == ["W001"]


def test_critical_match_below_threshold_reviews_not_approves():
    """Critical severity must never auto-approve, even below the reject threshold (§4)."""
    matches = [{"rule": "W001", "severity": "Critical", "autoReject": False, "confidence": 0.80}]
    result = _run({"listingId": "LST-TEST"}, matches=matches)
    assert result["payload"]["decision"] == "REVIEW"


def test_high_severity_match_reviews():
    matches = [{"rule": "C001", "severity": "High", "autoReject": False, "confidence": 1.0}]
    result = _run({"listingId": "LST-TEST"}, matches=matches)
    assert result["payload"]["decision"] == "REVIEW"


def test_autoreject_overrides_confidence():
    """No current rule sets autoReject=True (§3.5) -- this exercises the hard-override
    lever itself, independent of confidence, for whenever one does."""
    matches = [{"rule": "C004", "severity": "Medium", "autoReject": True, "confidence": 0.1}]
    result = _run({"listingId": "LST-TEST"}, matches=matches)
    assert result["payload"]["decision"] == "REJECT"


def test_basedon_references_all_four_upstream_artifacts():
    result = _run({"listingId": "LST-TEST"}, matches=[])
    assert len(result["basedOn"]) == 4
    assert all(ref.endswith("@2026-01-01T00:00:00Z") for ref in result["basedOn"])


def test_seller_history_flips_tentative_approve_to_review():
    canonical_doc = {"listingId": "LST-TEST", "sellerPreviousViolations": 3}
    result = _run(canonical_doc, matches=[], inconsistency_score=0.05)  # base confidence 0.95
    assert result["payload"]["confidence"] == 0.80  # 0.95 - min(0.05*3, 0.20)
    assert result["payload"]["decision"] == "REVIEW"


def test_seller_history_does_not_affect_tentative_review():
    """No adjustment when the case was already heading to REVIEW (§4 step 2)."""
    canonical_doc = {"listingId": "LST-TEST", "sellerPreviousViolations": 3}
    result = _run(canonical_doc, matches=[], inconsistency_score=0.3)  # base confidence 0.70, already REVIEW
    assert result["payload"]["confidence"] == 0.70
    assert result["payload"]["decision"] == "REVIEW"


def test_auto_approve_threshold_override_changes_routing():
    """Same inputs as test_no_matches_high_inconsistency_reviews (confidence 0.70),
    but a lowered auto_approve_threshold flips REVIEW to APPROVE -- proves the
    override parameter actually drives behavior, not just that the default works."""
    result = _run(
        {"listingId": "LST-TEST"}, matches=[], inconsistency_score=0.3, auto_approve_threshold=0.65
    )
    assert result["payload"]["decision"] == "APPROVE"


def test_critical_reject_threshold_override_changes_routing():
    """Same inputs as test_critical_match_below_threshold_reviews_not_approves
    (confidence 0.80), but a lowered critical_reject_threshold flips REVIEW to
    REJECT."""
    matches = [{"rule": "W001", "severity": "Critical", "autoReject": False, "confidence": 0.80}]
    result = _run({"listingId": "LST-TEST"}, matches=matches, critical_reject_threshold=0.75)
    assert result["payload"]["decision"] == "REJECT"


def test_seller_history_adjustment_override_changes_confidence():
    """Same inputs as test_seller_history_flips_tentative_approve_to_review, but a
    smaller per-violation adjustment leaves confidence above the approve threshold
    instead of flipping it to REVIEW."""
    canonical_doc = {"listingId": "LST-TEST", "sellerPreviousViolations": 3}
    result = _run(
        canonical_doc,
        matches=[],
        inconsistency_score=0.05,  # base confidence 0.95
        seller_history_adjustment_per_violation=0.01,
        seller_history_adjustment_cap=0.20,
    )
    assert result["payload"]["confidence"] == 0.92  # 0.95 - min(0.01*3, 0.20)
    assert result["payload"]["decision"] == "APPROVE"
