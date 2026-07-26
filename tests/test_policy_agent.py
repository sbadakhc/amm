from agents.policy_agent import run_policy_agent


def _canonical(category_id="electronics.mobile"):
    return {"listingId": "LST-TEST", "categoryId": category_id}


def test_weapon_violation_maps_to_w001(canonical_weapon):
    safety = {"violations": ["Guns and Illegal Weapons"], "confidence": 0.999}
    evidence = {"brandMismatch": False}
    consistency = {"inconsistencyScore": 0.05}

    result = run_policy_agent(canonical_weapon, evidence, consistency, safety)
    matches = result["payload"]["matches"]

    assert len(matches) == 1
    assert matches[0] == {"rule": "W001", "severity": "Critical", "autoReject": False, "confidence": 0.999}


def test_brand_mismatch_maps_to_c001():
    evidence = {"brandMismatch": True}
    consistency = {"inconsistencyScore": 0.05}
    safety = {"violations": [], "confidence": 0.0}

    result = run_policy_agent(_canonical(), evidence, consistency, safety)
    matches = result["payload"]["matches"]

    assert matches == [{"rule": "C001", "severity": "High", "autoReject": False, "confidence": 1.0}]


def test_high_inconsistency_maps_to_c004():
    evidence = {"brandMismatch": False}
    consistency = {"inconsistencyScore": 0.55}
    safety = {"violations": [], "confidence": 0.0}

    result = run_policy_agent(_canonical(), evidence, consistency, safety)
    matches = result["payload"]["matches"]

    assert matches == [{"rule": "C004", "severity": "Medium", "autoReject": False, "confidence": 0.55}]


def test_clean_listing_has_no_matches():
    evidence = {"brandMismatch": False}
    consistency = {"inconsistencyScore": 0.1}
    safety = {"violations": [], "confidence": 0.0}

    result = run_policy_agent(_canonical(), evidence, consistency, safety)

    assert result["payload"]["matches"] == []


def test_multiple_rules_can_match_simultaneously():
    evidence = {"brandMismatch": True}
    consistency = {"inconsistencyScore": 0.55}
    safety = {"violations": ["Controlled/Regulated Substances"], "confidence": 0.9}

    result = run_policy_agent(_canonical(), evidence, consistency, safety)
    rules = {m["rule"] for m in result["payload"]["matches"]}

    assert rules == {"D001", "C001", "C004"}


def test_fraud_violation_maps_to_f001():
    safety = {"violations": ["Fraud/Deception"], "confidence": 0.95}
    evidence = {"brandMismatch": False}
    consistency = {"inconsistencyScore": 0.05}

    result = run_policy_agent(_canonical(), evidence, consistency, safety)
    matches = result["payload"]["matches"]

    assert matches == [{"rule": "F001", "severity": "High", "autoReject": False, "confidence": 0.95}]


def test_sexual_minor_violation_maps_to_s001_autoreject():
    safety = {"violations": ["Sexual (minor)"], "confidence": 0.99}
    evidence = {"brandMismatch": False}
    consistency = {"inconsistencyScore": 0.05}

    result = run_policy_agent(_canonical(), evidence, consistency, safety)
    matches = result["payload"]["matches"]

    assert matches == [{"rule": "S001", "severity": "Critical", "autoReject": True, "confidence": 0.99}]


def test_copyright_safety_category_maps_to_c001():
    """Text-based counterfeit signal (Safety Agent), distinct from the image-based
    brandMismatch (Evidence Agent) -- both land on the same C001 rule."""
    safety = {"violations": ["Copyright/Trademark/Plagiarism"], "confidence": 0.8}
    evidence = {"brandMismatch": False}
    consistency = {"inconsistencyScore": 0.05}

    result = run_policy_agent(_canonical(), evidence, consistency, safety)
    matches = result["payload"]["matches"]

    assert matches == [{"rule": "C001", "severity": "High", "autoReject": False, "confidence": 0.8}]


def test_brand_mismatch_and_copyright_category_dedupe_to_one_c001_match():
    """Both C001 signals firing at once must not produce two matches for the same
    rule (policyRules would end up with a duplicate 'C001' otherwise) -- takes the
    higher of the two confidences."""
    safety = {"violations": ["Copyright/Trademark/Plagiarism"], "confidence": 0.6}
    evidence = {"brandMismatch": True}
    consistency = {"inconsistencyScore": 0.05}

    result = run_policy_agent(_canonical(), evidence, consistency, safety)
    matches = result["payload"]["matches"]

    assert matches == [{"rule": "C001", "severity": "High", "autoReject": False, "confidence": 1.0}]


def test_consistency_threshold_override_changes_routing():
    """Same inconsistencyScore as test_clean_listing_has_no_matches (0.1, below the
    default 0.30 threshold), but a lowered override makes it match C004 -- proves the
    override parameter actually drives behavior, not just that the default works."""
    evidence = {"brandMismatch": False}
    consistency = {"inconsistencyScore": 0.1}
    safety = {"violations": [], "confidence": 0.0}

    result = run_policy_agent(_canonical(), evidence, consistency, safety, consistency_threshold=0.05)

    assert result["payload"]["matches"] == [
        {"rule": "C004", "severity": "Medium", "autoReject": False, "confidence": 0.1}
    ]
