"""
Safety Agent — content-safety classification. See SPEC.md §3.4.

Calls nvidia/llama-3.1-nemotron-safety-guard-8b-v3, not nemotron-3.5-content-safety:
the latter only returns a binary safe/unsafe verdict with no category, and Policy
Agent (§3.5) needs a category to map to a specific rule (W001 vs D001 etc).
"""

import json
import math
import os
from datetime import datetime, timezone

import requests

NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "nvidia/llama-3.1-nemotron-safety-guard-8b-v3"

# Second-opinion model for the prize/advance-fee scam check (docs/decisions/0020) --
# same model and "ask one targeted true/false question" pattern already used by
# Consistency Agent, not the safety-guard classifier itself.
PRIZE_SCAM_CHECK_MODEL = "mistralai/mistral-nemotron"
PRIZE_SCAM_CATEGORY = "Prize/Advance-Fee Scam"

# A "safe" verdict below this confidence gets one retry (docs/decisions/0019).
# Empirically every genuinely-safe real-call test case observed confidence >= 0.70;
# the one confirmed false-safe result (a fraud listing misclassified safe) had
# confidence 0.023 -- a wide gap, same threshold-tuning pattern as
# CONSISTENCY_THRESHOLD (docs/decisions/0014). Configurable per docs/decisions/0008.
SAFE_RETRY_CONFIDENCE_THRESHOLD = float(os.environ.get("SAFETY_SAFE_RETRY_THRESHOLD", "0.5"))


def _classify(text: str) -> dict:
    """Calls the safety-guard model. Returns {"unsafe", "categories", "confidence"}."""
    resp = requests.post(
        NVIDIA_API_URL,
        headers={
            "Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": text}],
            "logprobs": True,
            "top_logprobs": 1,
        },
        timeout=30,
    )
    resp.raise_for_status()
    choice = resp.json()["choices"][0]
    content = json.loads(choice["message"]["content"])

    unsafe = content["User Safety"] == "unsafe"
    categories = [c.strip() for c in content.get("Safety Categories", "").split(",") if c.strip()]

    # The model's own probability for the safe/unsafe token it emitted, used as confidence.
    token_logprobs = choice["logprobs"]["content"]
    verdict_token = next(t for t in token_logprobs if t["token"].strip().lower() in ("safe", "unsafe"))
    confidence = math.exp(verdict_token["logprob"])

    return {"unsafe": unsafe, "categories": categories, "confidence": confidence}


def _check_prize_advance_fee_scam(text: str) -> tuple[bool, float]:
    """Targeted second-opinion check (docs/decisions/0020): the safety-guard
    classifier has a confirmed systematic blind spot for prize/lottery/advance-fee
    scams ("you won a prize, pay a fee to claim it") -- real-call testing found it
    consistently and confidently classifies this pattern as safe regardless of
    retries, phrasing, or language (docs/decisions/0019's retry only fixes stochastic
    low-confidence misses, not this). A direct, narrowly-scoped question to a general
    text model catches it far more reliably (5/5 vs. 3/5 for a keyword heuristic, on
    the same real-call test corpus) because it reasons about intent rather than
    matching phrasing. Retries once on a malformed (non-true/false) response, same
    failure mode Consistency Agent's `_post_for_verdict` already handles."""
    prompt = (
        "Does this marketplace listing describe the recipient receiving something of "
        "value (a prize, lottery winnings, an inheritance, a free item) that is "
        "contingent on the recipient first sending money, a fee, or payment details? "
        "Answer with exactly one word: true or false.\n"
        f"Listing text: {text}"
    )
    last_error = None
    for _attempt in range(2):
        resp = requests.post(
            NVIDIA_API_URL,
            headers={
                "Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}",
                "Content-Type": "application/json",
            },
            json={
                "model": PRIZE_SCAM_CHECK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 10,
                "logprobs": True,
                "top_logprobs": 1,
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["logprobs"]["content"]
        for tok in content:
            word = tok["token"].strip().strip("▁").lower()
            if word in ("true", "false"):
                return word == "true", math.exp(tok["logprob"])
        last_error = ValueError(f"No true/false token found in {content}")
    raise last_error


def run_safety_agent(canonical_doc: dict) -> dict:
    """
    Runs the Safety Agent over a canonical listing document (§3.1) and returns a
    SafetyAgent artifact (§5) ready to append to the `artifacts` table.

    A low-confidence "safe" verdict gets one retry (docs/decisions/0019) -- confirmed
    via real calls that the model occasionally emits "safe" with near-zero confidence
    on genuinely fraudulent text (a well-formed but wrong answer, unlike Consistency
    Agent's rambling-response failure mode); a fresh sample resolves this more often
    than not. If the retry also comes back safe, the higher-confidence of the two safe
    verdicts wins -- retrying never *invents* a violation, it only gives a low-trust
    safe verdict a second chance to be corrected.

    A listing that's still "safe" after that gets one more check: a targeted
    prize/advance-fee scam question (docs/decisions/0020), since this is a confirmed
    systematic (not stochastic) blind spot the retry above doesn't fix. Only runs when
    the primary classifier already said safe -- no reason to spend the extra call on a
    listing already flagged unsafe by something else.
    """
    text = f"{canonical_doc['title']}\n{canonical_doc['description']}"
    result = _classify(text)
    if not result["unsafe"] and result["confidence"] < SAFE_RETRY_CONFIDENCE_THRESHOLD:
        retry = _classify(text)
        if retry["unsafe"] or retry["confidence"] > result["confidence"]:
            result = retry

    violations = list(result["categories"]) if result["unsafe"] else []
    confidence = result["confidence"]

    if not result["unsafe"]:
        is_prize_scam, prize_conf = _check_prize_advance_fee_scam(text)
        if is_prize_scam:
            violations = [PRIZE_SCAM_CATEGORY]
            confidence = prize_conf

    unsafe = bool(violations)
    if unsafe:
        explanation = f"Content flagged as unsafe: {', '.join(violations)}."
    else:
        explanation = "No safety violations detected."

    payload = {
        "violations": violations,
        "confidence": round(confidence, 4),
        "explanation": explanation,
    }

    return {
        "listingId": canonical_doc["listingId"],
        "agent": "SafetyAgent",
        "version": MODEL,
        "producedAt": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }


if __name__ == "__main__":
    import sys

    doc = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {
        "listingId": "LST-TEST",
        "title": "Tactical Combat Knife 8-inch",
        "description": "Military-grade fixed blade knife, stainless steel.",
    }
    print(json.dumps(run_safety_agent(doc), indent=2))
