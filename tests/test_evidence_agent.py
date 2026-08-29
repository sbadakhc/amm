"""
Fixture response is a trimmed, structurally faithful copy of a real chat-completion
response captured during development (see
docs/decisions/0001-safety-agent-model-choice.md for the pattern of testing against
real API responses before trusting a model's behavior). Originally captured from
nvidia/nemotron-nano-12b-v2-vl; that model was end-of-lifed by NVIDIA 2026-08-26 and
Evidence Agent now uses meta/llama-3.2-11b-vision-instruct (docs/decisions/0025) --
the response shape (OpenAI-compatible chat completion) is unchanged, re-verified
against the new model with real calls.
"""

from pathlib import Path

import requests

from agents import evidence_agent
from tests.conftest import FakeResponse

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


def test_timeout_skips_image_and_does_not_manufacture_mismatch(monkeypatch):
    """docs/decisions/0029: a hung/unresponsive backend must not block the whole
    agent run, and a skipped extraction must not read as 'packaging confirmed
    nothing' -- that would manufacture a brandMismatch that was never actually
    checked. Real production incident this session: an Evidence Agent call hit
    exactly this failure mode against the live API."""
    monkeypatch.setattr(
        evidence_agent.requests,
        "post",
        lambda *a, **kw: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("simulated hung backend")),
    )
    canonical_doc = {
        "listingId": "LST-TEST",
        "images": [FIXTURE_IMAGE],
        "declaredBrand": "Apple",
    }
    artifact = evidence_agent.run_evidence_agent(canonical_doc)

    assert artifact["payload"]["brandMismatch"] is False
    assert artifact["payload"]["imagesSkipped"] == [FIXTURE_IMAGE]
    assert artifact["payload"]["brandsDetected"] == []


def test_malformed_twice_skips_image(monkeypatch):
    """Real production incident this session: the vision model occasionally returns
    prose with no '{' at all, which used to crash the whole agent run with
    ValueError('substring not found'). A retry (fresh sample) gets one chance to
    recover; two failures in a row skip the image instead of raising."""
    call_count = {"n": 0}

    def fake_post(*a, **kw):
        call_count["n"] += 1
        return FakeResponse(_vlm_response("I see a phone but cannot format that as JSON."))

    monkeypatch.setattr(evidence_agent.requests, "post", fake_post)
    canonical_doc = {
        "listingId": "LST-TEST",
        "images": [FIXTURE_IMAGE],
        "declaredBrand": "Apple",
    }
    artifact = evidence_agent.run_evidence_agent(canonical_doc)

    assert call_count["n"] == 2  # one retry, then skip
    assert artifact["payload"]["imagesSkipped"] == [FIXTURE_IMAGE]
    assert artifact["payload"]["brandMismatch"] is False


def test_partial_failure_uses_evidence_from_successful_images(monkeypatch):
    """One image fails, one succeeds: the failure doesn't erase real evidence the
    other image provided, and only the failed one lands in imagesSkipped."""
    call_count = {"n": 0}

    def fake_post(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise requests.exceptions.ReadTimeout("simulated hung backend")
        return FakeResponse(
            _vlm_response('{"objects": [], "ocr": ["APPLE"], "brands": ["APPLE"], '
                          '"certificateNumbers": [], "serialNumbers": [], "expiryDate": null, '
                          '"countryOfOrigin": null}')
        )

    monkeypatch.setattr(evidence_agent.requests, "post", fake_post)
    monkeypatch.setattr(evidence_agent, "_load_image_b64", lambda url: ("", "image/png"))
    image_a, image_b = FIXTURE_IMAGE, f"{FIXTURE_IMAGE}#b"
    canonical_doc = {
        "listingId": "LST-TEST",
        "images": [image_a, image_b],
        "declaredBrand": "Apple",
    }
    artifact = evidence_agent.run_evidence_agent(canonical_doc)

    assert artifact["payload"]["imagesSkipped"] == [image_a]
    assert artifact["payload"]["brandsDetected"] == ["APPLE"]
    assert artifact["payload"]["brandMismatch"] is False
