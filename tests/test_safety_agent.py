"""
Fixture responses below are trimmed but structurally faithful copies of real
nvidia/llama-3.1-nemotron-safety-guard-8b-v3 responses captured during development
(see docs/decisions/0001-safety-agent-model-choice.md).
"""

import math

from agents import safety_agent


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
