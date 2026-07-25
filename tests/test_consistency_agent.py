"""
Fixture responses are trimmed, structurally faithful copies of real
mistralai/mistral-nemotron and nvidia/nemotron-nano-12b-v2-vl responses captured
during development.
"""

import math
from pathlib import Path

from agents import consistency_agent
from tests.conftest import FakeResponse

FIXTURE_IMAGE = f"file://{Path(__file__).parent / 'fixtures' / 'tiny.png'}"


def _text_response(verdict: str, logprob: float) -> dict:
    return {
        "choices": [
            {
                "message": {"content": verdict, "reasoning_content": None, "tool_calls": []},
                "logprobs": {"content": [{"token": verdict, "logprob": logprob}]},
            }
        ]
    }


def test_no_contradiction_is_consistent(monkeypatch, canonical_clean):
    monkeypatch.setattr(
        consistency_agent.requests, "post", lambda *a, **kw: FakeResponse(_text_response("false", -0.0101))
    )
    result = consistency_agent.run_consistency_agent(canonical_clean)

    checks = {c["pair"]: c["consistent"] for c in result["payload"]["checks"]}
    assert checks == {"title_vs_description": True}
    assert result["payload"]["inconsistencyScore"] == round(1 - math.exp(-0.0101), 4)


def test_contradiction_is_inconsistent(monkeypatch):
    monkeypatch.setattr(
        consistency_agent.requests, "post", lambda *a, **kw: FakeResponse(_text_response("true", -0.0576))
    )
    doc = {
        "listingId": "LST-TEST",
        "title": "Apple iPhone 16 Pro Max",
        "description": "Brand new Samsung Galaxy S24, factory sealed.",
        "images": [],
        "declaredBrand": "Apple",
        "categoryId": "electronics.mobile",
    }
    result = consistency_agent.run_consistency_agent(doc)

    checks = {c["pair"]: c["consistent"] for c in result["payload"]["checks"]}
    assert checks["title_vs_description"] is False


def test_multi_image_aggregates_as_any_confirms(monkeypatch):
    """Only one of two images needs to confirm a check for it to count consistent."""
    call_count = {"n": 0}

    def fake_post(*a, **kw):
        call_count["n"] += 1
        # First image: no match (false). Second image: match (true).
        verdict = "false" if call_count["n"] % 2 == 1 else "true"
        return FakeResponse(_text_response(verdict, -0.05))

    monkeypatch.setattr(consistency_agent.requests, "post", fake_post)
    monkeypatch.setattr(consistency_agent, "_load_image_data_url", lambda url: url)

    doc = {
        "listingId": "LST-TEST",
        "title": "Apple iPhone 16 Pro Max",
        "description": "Brand new, factory sealed.",
        "images": [FIXTURE_IMAGE, FIXTURE_IMAGE],
        "declaredBrand": "Apple",
        "categoryId": "electronics.mobile",
    }
    result = consistency_agent.run_consistency_agent(doc)

    pairs = {c["pair"] for c in result["payload"]["checks"]}
    assert pairs == {
        "title_vs_description",
        "description_vs_images",
        "images_vs_declaredBrand",
        "category_vs_detectedObjects",
    }
