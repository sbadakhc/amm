"""
Fixture response is a trimmed, structurally faithful copy of a real
nvidia/nemotron-nano-12b-v2-vl response captured during development (see
docs/decisions/0001-safety-agent-model-choice.md for the pattern of testing against
real API responses before trusting a model's behavior).
"""

from pathlib import Path

from agents import evidence_agent

FIXTURE_IMAGE = f"file://{Path(__file__).parent / 'fixtures' / 'tiny.png'}"


def _vlm_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_brand_detected_matches_declared_brand(fake_post):
    fake_post(
        evidence_agent,
        _vlm_response('{"objects": [], "ocr": ["APPLE", "iPhone 16 Pro Max", "256GB"], '
                      '"brands": ["APPLE"], "certificateNumbers": [], "serialNumbers": [], '
                      '"expiryDate": null, "countryOfOrigin": null}'),
    )
    canonical_doc = {
        "listingId": "LST-TEST",
        "images": [FIXTURE_IMAGE],
        "declaredBrand": "Apple",
    }
    artifact = evidence_agent.run_evidence_agent(canonical_doc)

    assert artifact["payload"]["brandMismatch"] is False
    assert artifact["payload"]["brandsDetected"] == ["APPLE"]


def test_no_brand_detected_is_a_mismatch(fake_post):
    fake_post(
        evidence_agent,
        _vlm_response('{"objects": [], "ocr": ["SMARTPHONE", "PRO 16"], "brands": [], '
                      '"certificateNumbers": [], "serialNumbers": [], "expiryDate": null, '
                      '"countryOfOrigin": null}'),
    )
    canonical_doc = {
        "listingId": "LST-TEST",
        "images": [FIXTURE_IMAGE],
        "declaredBrand": "Apple",
    }
    artifact = evidence_agent.run_evidence_agent(canonical_doc)

    assert artifact["payload"]["brandMismatch"] is True


def test_extra_data_after_json_is_tolerated(fake_post):
    """Regression test: the model occasionally emits a stray duplicate closing
    markdown fence after the real one (confirmed via real calls while testing real
    product photography, docs/decisions/0013) -- json.loads on the naive fence-strip
    result raised 'Extra data'. _parse_json_object must parse the first JSON object
    and ignore trailing garbage instead of assuming the whole string is clean JSON."""
    fake_post(
        evidence_agent,
        _vlm_response('```json\n{"objects": [], "ocr": ["SONY"], "brands": ["SONY"], '
                      '"certificateNumbers": [], "serialNumbers": [], "expiryDate": null, '
                      '"countryOfOrigin": null}\n```\n```\n'),
    )
    canonical_doc = {
        "listingId": "LST-TEST",
        "images": [FIXTURE_IMAGE],
        "declaredBrand": "Sony",
    }
    artifact = evidence_agent.run_evidence_agent(canonical_doc)

    assert artifact["payload"]["brandMismatch"] is False
    assert artifact["payload"]["brandsDetected"] == ["SONY"]


def test_generic_declared_brand_is_never_a_mismatch(fake_post):
    """A declared brand of 'Generic'/'Unbranded'/etc. isn't a brand claim -- nothing
    to corroborate, so no mismatch, regardless of what's detected (or not) on the
    image. Regression test for the false-positive C001 match on the weapon scenario
    found while building the Policy Agent."""
    fake_post(
        evidence_agent,
        _vlm_response('{"objects": [], "ocr": ["AK-47"], "brands": [], '
                      '"certificateNumbers": [], "serialNumbers": [], "expiryDate": null, '
                      '"countryOfOrigin": null}'),
    )
    canonical_doc = {
        "listingId": "LST-TEST",
        "images": [FIXTURE_IMAGE],
        "declaredBrand": "Generic",
    }
    artifact = evidence_agent.run_evidence_agent(canonical_doc)

    assert artifact["payload"]["brandMismatch"] is False
