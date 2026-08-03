"""
Fixture responses below are trimmed but structurally faithful copies of real
nvidia/llama-3.1-nemotron-safety-guard-8b-v3 responses captured during development
(see docs/decisions/0001-safety-agent-model-choice.md).
"""

import math

from agents import safety_agent
from tests.conftest import FakeResponse


def _response(content: str, verdict_token: str, verdict_logprob: float) -> dict:
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content, "tool_calls": []},
                "logprobs": {
                    "content": [
                        {"token": "{\"", "logprob": -0.00002},
                        {"token": "User", "logprob": -0.0000002},
                        {"token": " Safety", "logprob": -0.000001},
                        {"token": "\":", "logprob": -0.000004},
                        {"token": " \"", "logprob": 0.0},
                        {"token": verdict_token, "logprob": verdict_logprob},
                    ]
                },
            }
        ]
    }


def test_unsafe_weapon_listing(fake_post, canonical_weapon):
    fake_post(
        safety_agent,
        _response(
            '{"User Safety": "unsafe", "Safety Categories": "Guns and Illegal Weapons"} ',
            "unsafe",
            -0.0001926,
        ),
    )
    artifact = safety_agent.run_safety_agent(canonical_weapon)

    assert artifact["agent"] == "SafetyAgent"
    assert artifact["payload"]["violations"] == ["Guns and Illegal Weapons"]
    assert artifact["payload"]["confidence"] == round(math.exp(-0.0001926), 4)
    assert "Guns and Illegal Weapons" in artifact["payload"]["explanation"]


def test_safe_listing_has_no_violations(fake_post, canonical_clean):
    fake_post(safety_agent, _response('{"User Safety": "safe"} ', "safe", -0.0338))
    artifact = safety_agent.run_safety_agent(canonical_clean)

    assert artifact["payload"]["violations"] == []
    assert artifact["payload"]["explanation"] == "No safety violations detected."


def test_high_confidence_safe_does_not_retry(monkeypatch, canonical_clean):
    """docs/decisions/0019: only a low-confidence safe verdict gets a retry -- a
    confident safe result (the common case) must not cost a second API call."""
    call_count = {"n": 0}

    def fake_post(*a, **kw):
        call_count["n"] += 1
        return FakeResponse(_response('{"User Safety": "safe"} ', "safe", -0.0338))

    monkeypatch.setattr(safety_agent.requests, "post", fake_post)
    safety_agent.run_safety_agent(canonical_clean)

    assert call_count["n"] == 1


def test_low_confidence_safe_retries_and_keeps_higher_confidence_safe(monkeypatch, canonical_clean):
    """First call: safe but low confidence (below SAFE_RETRY_CONFIDENCE_THRESHOLD).
    Retry: safe with higher confidence -- the more decisive safe verdict wins, no
    violation is invented."""
    responses = [
        FakeResponse(_response('{"User Safety": "safe"} ', "safe", math.log(0.02))),
        FakeResponse(_response('{"User Safety": "safe"} ', "safe", math.log(0.9))),
    ]
    call_count = {"n": 0}

    def fake_post(*a, **kw):
        resp = responses[call_count["n"]]
        call_count["n"] += 1
        return resp

    monkeypatch.setattr(safety_agent.requests, "post", fake_post)
    artifact = safety_agent.run_safety_agent(canonical_clean)

    assert call_count["n"] == 2
    assert artifact["payload"]["violations"] == []
    assert artifact["payload"]["confidence"] == 0.9


def test_low_confidence_safe_retry_catches_violation(monkeypatch, canonical_weapon):
    """First call: safe but low confidence, wrongly missing a real violation. Retry:
    unsafe -- the retry's result is used, not silently discarded."""
    responses = [
        FakeResponse(_response('{"User Safety": "safe"} ', "safe", math.log(0.02))),
        FakeResponse(
            _response(
                '{"User Safety": "unsafe", "Safety Categories": "Fraud/Deception"} ',
                "unsafe",
                math.log(0.97),
            )
        ),
    ]
    call_count = {"n": 0}

    def fake_post(*a, **kw):
        resp = responses[call_count["n"]]
        call_count["n"] += 1
        return resp

    monkeypatch.setattr(safety_agent.requests, "post", fake_post)
    artifact = safety_agent.run_safety_agent(canonical_weapon)

    assert call_count["n"] == 2
    assert artifact["payload"]["violations"] == ["Fraud/Deception"]
    assert artifact["payload"]["confidence"] == 0.97
