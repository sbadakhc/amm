"""
Fixture responses below are trimmed but structurally faithful copies of real
nvidia/llama-3.1-nemotron-safety-guard-8b-v3 responses captured during development
(see docs/decisions/0001-safety-agent-model-choice.md).
"""

import math

import requests

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


def _prize_check_response(verdict: str, logprob: float) -> dict:
    """Shape of the targeted true/false prize-scam check (docs/decisions/0020) --
    plain-text completion with a logprobs token list, same as Consistency Agent's
    text checks, not the safety-guard classifier's structured JSON content."""
    return {"choices": [{"message": {"content": verdict}, "logprobs": {"content": [{"token": verdict, "logprob": logprob}]}}]}


def _not_a_prize_scam() -> "FakeResponse":
    return FakeResponse(_prize_check_response("false", math.log(0.95)))


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


def test_safe_listing_has_no_violations(monkeypatch, canonical_clean):
    responses = [FakeResponse(_response('{"User Safety": "safe"} ', "safe", -0.0338)), _not_a_prize_scam()]
    call_count = {"n": 0}

    def fake_post(*a, **kw):
        resp = responses[call_count["n"]]
        call_count["n"] += 1
        return resp

    monkeypatch.setattr(safety_agent.requests, "post", fake_post)
    artifact = safety_agent.run_safety_agent(canonical_clean)

    assert artifact["payload"]["violations"] == []
    assert artifact["payload"]["explanation"] == "No safety violations detected."


def test_high_confidence_safe_does_not_retry_primary_classifier(monkeypatch, canonical_clean):
    """docs/decisions/0019: only a low-confidence safe verdict gets a retry of the
    *primary* classifier -- a confident safe result must not cost a second call to
    that model. It still gets exactly one prize-scam check call (docs/decisions/0020),
    hence 2 total, not 1."""
    responses = [FakeResponse(_response('{"User Safety": "safe"} ', "safe", -0.0338)), _not_a_prize_scam()]
    call_count = {"n": 0}

    def fake_post(*a, **kw):
        resp = responses[call_count["n"]]
        call_count["n"] += 1
        return resp

    monkeypatch.setattr(safety_agent.requests, "post", fake_post)
    safety_agent.run_safety_agent(canonical_clean)

    assert call_count["n"] == 2


def test_unsafe_listing_skips_prize_scam_check(monkeypatch, canonical_weapon):
    """No reason to spend the extra call when the primary classifier already flagged
    the listing unsafe for an unrelated reason."""
    call_count = {"n": 0}

    def fake_post(*a, **kw):
        call_count["n"] += 1
        return FakeResponse(
            _response('{"User Safety": "unsafe", "Safety Categories": "Guns and Illegal Weapons"} ', "unsafe", -0.0001926)
        )

    monkeypatch.setattr(safety_agent.requests, "post", fake_post)
    safety_agent.run_safety_agent(canonical_weapon)

    assert call_count["n"] == 1


def test_low_confidence_safe_retries_and_keeps_higher_confidence_safe(monkeypatch, canonical_clean):
    """First call: safe but low confidence (below SAFE_RETRY_CONFIDENCE_THRESHOLD).
    Retry: safe with higher confidence -- the more decisive safe verdict wins, no
    violation is invented. Then the prize-scam check runs (still safe overall), for 3
    calls total."""
    responses = [
        FakeResponse(_response('{"User Safety": "safe"} ', "safe", math.log(0.02))),
        FakeResponse(_response('{"User Safety": "safe"} ', "safe", math.log(0.9))),
        _not_a_prize_scam(),
    ]
    call_count = {"n": 0}

    def fake_post(*a, **kw):
        resp = responses[call_count["n"]]
        call_count["n"] += 1
        return resp

    monkeypatch.setattr(safety_agent.requests, "post", fake_post)
    artifact = safety_agent.run_safety_agent(canonical_clean)

    assert call_count["n"] == 3
    assert artifact["payload"]["violations"] == []
    assert artifact["payload"]["confidence"] == 0.9


def test_prize_advance_fee_scam_detected_when_primary_classifier_missed_it(monkeypatch, canonical_clean):
    """docs/decisions/0020: the primary classifier has a confirmed systematic blind
    spot for lottery/prize-advance-fee scams -- this is the case the targeted
    second-opinion check exists to catch."""
    responses = [
        FakeResponse(_response('{"User Safety": "safe"} ', "safe", math.log(0.87))),
        FakeResponse(_prize_check_response("true", math.log(0.94))),
    ]
    call_count = {"n": 0}

    def fake_post(*a, **kw):
        resp = responses[call_count["n"]]
        call_count["n"] += 1
        return resp

    monkeypatch.setattr(safety_agent.requests, "post", fake_post)
    artifact = safety_agent.run_safety_agent(canonical_clean)

    assert call_count["n"] == 2
    assert artifact["payload"]["violations"] == ["Prize/Advance-Fee Scam"]
    assert artifact["payload"]["confidence"] == 0.94
    assert "Prize/Advance-Fee Scam" in artifact["payload"]["explanation"]


def test_low_confidence_unsafe_retries_and_corrects_spurious_flag(monkeypatch, canonical_clean):
    """docs/decisions/0024: mirror of the low-confidence-safe retry (0019), found via
    0023's eBay false-positive eval -- a real call flagged a plain clothing listing
    unsafe at confidence 0.0086 (spurious 'Criminal Planning/Confessions'/'PII/Privacy'
    categories). First call: unsafe but low confidence. Retry: safe with higher
    confidence -- the more decisive verdict wins, correcting the spurious flag."""
    responses = [
        FakeResponse(
            _response(
                '{"User Safety": "unsafe", "Safety Categories": "Criminal Planning/Confessions, PII/Privacy"} ',
                "unsafe",
                math.log(0.0086),
            )
        ),
        FakeResponse(_response('{"User Safety": "safe"} ', "safe", math.log(0.99))),
        _not_a_prize_scam(),
    ]
    call_count = {"n": 0}

    def fake_post(*a, **kw):
        resp = responses[call_count["n"]]
        call_count["n"] += 1
        return resp

    monkeypatch.setattr(safety_agent.requests, "post", fake_post)
    artifact = safety_agent.run_safety_agent(canonical_clean)

    assert call_count["n"] == 3
    assert artifact["payload"]["violations"] == []
    assert artifact["payload"]["confidence"] == 0.99


def test_low_confidence_unsafe_retry_keeps_violation_if_still_more_confident(monkeypatch, canonical_weapon):
    """First call: unsafe but low confidence. Retry: still unsafe, and more confident
    -- the violation is confirmed, not discarded just because the first call was
    low-confidence."""
    responses = [
        FakeResponse(
            _response(
                '{"User Safety": "unsafe", "Safety Categories": "Guns and Illegal Weapons"} ',
                "unsafe",
                math.log(0.3),
            )
        ),
        FakeResponse(
            _response(
                '{"User Safety": "unsafe", "Safety Categories": "Guns and Illegal Weapons"} ',
                "unsafe",
                math.log(0.95),
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
    assert artifact["payload"]["violations"] == ["Guns and Illegal Weapons"]
    assert artifact["payload"]["confidence"] == 0.95


def test_prize_scam_check_timeout_skips_instead_of_raising(monkeypatch, canonical_clean):
    """A hung/unresponsive backend for the prize-scam second-opinion model must not
    block the whole agent run -- the primary classifier's safe verdict stands, and no
    violation is invented from a check that never completed."""
    responses = [FakeResponse(_response('{"User Safety": "safe"} ', "safe", math.log(0.9)))]
    call_count = {"n": 0}

    def fake_post(*a, **kw):
        if call_count["n"] < len(responses):
            resp = responses[call_count["n"]]
            call_count["n"] += 1
            return resp
        call_count["n"] += 1
        raise requests.exceptions.ReadTimeout("simulated hung backend")

    monkeypatch.setattr(safety_agent.requests, "post", fake_post)
    artifact = safety_agent.run_safety_agent(canonical_clean)

    assert call_count["n"] == 2
    assert artifact["payload"]["violations"] == []
    assert artifact["payload"]["confidence"] == 0.9


def test_prize_scam_check_connection_error_skips_instead_of_raising(monkeypatch, canonical_clean):
    responses = [FakeResponse(_response('{"User Safety": "safe"} ', "safe", math.log(0.9)))]
    call_count = {"n": 0}

    def fake_post(*a, **kw):
        if call_count["n"] < len(responses):
            resp = responses[call_count["n"]]
            call_count["n"] += 1
            return resp
        call_count["n"] += 1
        raise requests.exceptions.ConnectionError("simulated connection failure")

    monkeypatch.setattr(safety_agent.requests, "post", fake_post)
    artifact = safety_agent.run_safety_agent(canonical_clean)

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
