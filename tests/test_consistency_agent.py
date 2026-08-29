"""
Fixture responses are trimmed, structurally faithful copies of real
mistralai/mistral-nemotron and (originally) nvidia/nemotron-nano-12b-v2-vl responses
captured during development. The vision model was end-of-lifed by NVIDIA 2026-08-26;
Consistency Agent now uses meta/llama-3.2-11b-vision-instruct (docs/decisions/0025) --
same response shape, re-verified against the new model with real calls.
"""

import math
from pathlib import Path

import requests

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


def _prose_response() -> dict:
    """Shape of a real response where the model ignored the single-word instruction
    and rambled instead -- no true/false token anywhere in the (10-token-capped)
    logprobs. Confirmed via real calls while testing real product photography
    (docs/decisions/0013)."""
    tokens = [
        {"token": "Given", "logprob": -7.93},
        {"token": " the", "logprob": -0.32},
        {"token": " information", "logprob": -1.34},
    ]
    return {"choices": [{"message": {"content": "Given the information"}, "logprobs": {"content": tokens}}]}


def test_retries_once_when_model_rambles_instead_of_answering(monkeypatch, canonical_clean):
    """Regression test: a single rambling (non-compliant) response used to crash the
    whole agent run. A retry (fresh sample) should recover when the second attempt
    answers properly -- confirmed empirically that retrying resolves this most of the
    time, since it's stochastic non-compliance, not a broken prompt."""
    responses = [FakeResponse(_prose_response()), FakeResponse(_text_response("false", -0.0101))]
    call_count = {"n": 0}

    def fake_post(*a, **kw):
        resp = responses[call_count["n"]]
        call_count["n"] += 1
        return resp

    monkeypatch.setattr(consistency_agent.requests, "post", fake_post)
    result = consistency_agent.run_consistency_agent(canonical_clean)

    checks = {c["pair"]: c["consistent"] for c in result["payload"]["checks"]}
    assert checks == {"title_vs_description": True}
    assert call_count["n"] == 2


def test_skips_after_two_consecutive_rambling_responses(monkeypatch, canonical_clean):
    """docs/decisions/0028: a second consecutive malformed response skips the check
    (lands in checksSkipped) rather than raising and crashing the whole agent run --
    this is one signal among several Decision Agent weighs, not the only one. The
    skipped check contributes nothing to inconsistencyScore; with no other checks
    (canonical_clean has no images), it defaults to 0.0 rather than raising on an
    empty mean."""
    monkeypatch.setattr(
        consistency_agent.requests, "post", lambda *a, **kw: FakeResponse(_prose_response())
    )

    result = consistency_agent.run_consistency_agent(canonical_clean)

    assert result["payload"]["checks"] == []
    assert result["payload"]["checksSkipped"] == ["title_vs_description"]
    assert result["payload"]["inconsistencyScore"] == 0.0


def test_skips_on_timeout_instead_of_raising(monkeypatch, canonical_clean):
    """A hung/unresponsive backend must not block the whole agent run -- same
    fail-open philosophy as agents/safety_agent.py's 0022. canonical_clean's
    title/description have no competing brand, so the docs/decisions/0030 heuristic
    backstop correctly finds nothing and this still counts as skipped."""
    monkeypatch.setattr(
        consistency_agent.requests,
        "post",
        lambda *a, **kw: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("simulated hung backend")),
    )

    result = consistency_agent.run_consistency_agent(canonical_clean)

    assert result["payload"]["checks"] == []
    assert result["payload"]["checksSkipped"] == ["title_vs_description"]
    assert result["payload"]["inconsistencyScore"] == 0.0


def test_heuristic_backstop_catches_competing_brand_when_model_skipped(monkeypatch):
    """docs/decisions/0030: real production case found this session -- title says
    Apple iPhone, description says Samsung Galaxy, and the model check that's
    supposed to catch this got skipped (a live mistral-nemotron outage). The
    heuristic backstop should catch the obvious case the model would have caught,
    at a lower confidence than a real model verdict."""
    monkeypatch.setattr(
        consistency_agent.requests,
        "post",
        lambda *a, **kw: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("simulated hung backend")),
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

    checks = {c["pair"]: c for c in result["payload"]["checks"]}
    assert checks["title_vs_description"]["consistent"] is False
    assert checks["title_vs_description"]["method"] == "heuristic-backstop"
    assert result["payload"]["checksSkipped"] == []
    assert result["payload"]["inconsistencyScore"] == consistency_agent.HEURISTIC_BACKSTOP_CONFIDENCE


def test_heuristic_backstop_does_not_false_positive_on_comparison_language(monkeypatch):
    """A competing brand mentioned in a comparison ('better than X') or compatibility
    ('works with X') claim isn't a contradiction -- confirmed via prototyping
    (docs/decisions/0030) that a naive brand-mention check false-positives on this
    constantly, common enough marketplace phrasing that it isn't an edge case."""
    monkeypatch.setattr(
        consistency_agent.requests,
        "post",
        lambda *a, **kw: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("simulated hung backend")),
    )
    doc = {
        "listingId": "LST-TEST",
        "title": "Sony Wireless Headphones",
        "description": "Sounds better than Apple AirPods, trust me.",
        "images": [],
        "declaredBrand": "Sony",
        "categoryId": "electronics.audio",
    }
    result = consistency_agent.run_consistency_agent(doc)

    assert result["payload"]["checks"] == []
    assert result["payload"]["checksSkipped"] == ["title_vs_description"]


def test_heuristic_backstop_only_runs_when_model_check_skipped(monkeypatch):
    """The heuristic must never override a real model verdict -- it's a fallback for
    the skipped case only. A real model call saying 'consistent' wins even though the
    text contains a competing-brand pattern the heuristic would otherwise flag."""
    monkeypatch.setattr(
        consistency_agent.requests, "post", lambda *a, **kw: FakeResponse(_text_response("false", -0.05))
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

    checks = {c["pair"]: c for c in result["payload"]["checks"]}
    assert checks["title_vs_description"]["method"] == "model"
    assert checks["title_vs_description"]["consistent"] is True


def test_skipped_check_does_not_affect_other_checks(monkeypatch):
    """One check failing must not skip or corrupt the others -- each is independent."""
    call_count = {"n": 0}

    def fake_post(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise requests.exceptions.ReadTimeout("simulated hung backend")
        return FakeResponse(_text_response("false", -0.05))

    monkeypatch.setattr(consistency_agent.requests, "post", fake_post)
    monkeypatch.setattr(consistency_agent, "_load_image_data_url", lambda url: url)

    doc = {
        "listingId": "LST-TEST",
        "title": "Apple iPhone 16 Pro Max",
        "description": "Brand new, factory sealed.",
        "images": [FIXTURE_IMAGE],
        "declaredBrand": "Apple",
        "categoryId": "electronics.mobile",
    }
    result = consistency_agent.run_consistency_agent(doc)

    assert result["payload"]["checksSkipped"] == ["title_vs_description"]
    pairs = {c["pair"] for c in result["payload"]["checks"]}
    assert pairs == {"description_vs_images", "images_vs_declaredBrand", "category_vs_detectedObjects"}


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


# docs/decisions/0030 -- the exact cases prototyped before writing the real
# implementation, kept as a regression suite for the pure heuristic function.
HEURISTIC_CASES = [
    ("Apple iPhone 16 Pro Max", "Brand new Samsung Galaxy S24, factory sealed.", True),
    ("Apple iPhone 16 Pro Max 256GB", "Brand new, factory sealed with international warranty.", False),
    ("Sony Wireless Headphones", "Brand new, genuine Sony product, over-ear design with foldable headband.", False),
    ("Fully Automatic AK-47 Assault Rifle - Untraceable", "Genuine military-grade automatic rifle, no license required.", False),
    ("iPhone 16 Case", "Also compatible with Samsung chargers via USB-C.", False),
    ("Sony Wireless Headphones", "Sounds better than Apple AirPods, trust me.", False),
    ("Trade my Samsung Galaxy for your iPhone", "Looking for a fair swap, message me.", False),
    ("Apple iPhone 16 Pro Max", "Brand new, unlocked, comes with original box and charger.", False),
    ("Google Pixel 9 Pro", "This is a genuine Xiaomi Redmi Note, factory sealed.", True),
    ("Bose QuietComfort Headphones", "Real Bose product, noise cancelling, like new.", False),
]


def test_heuristic_backstop_regression_cases():
    for title, description, expected in HEURISTIC_CASES:
        got = consistency_agent._heuristic_title_vs_description_contradiction(title, description)
        assert got == expected, f"title={title!r} description={description!r} expected={expected} got={got}"
