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


def run_safety_agent(canonical_doc: dict) -> dict:
    """
    Runs the Safety Agent over a canonical listing document (§3.1) and returns a
    SafetyAgent artifact (§5) ready to append to the `artifacts` table.
    """
    text = f"{canonical_doc['title']}\n{canonical_doc['description']}"
    result = _classify(text)

    if result["unsafe"]:
        explanation = f"Content flagged as unsafe: {', '.join(result['categories'])}."
    else:
        explanation = "No safety violations detected."

    payload = {
        "violations": result["categories"] if result["unsafe"] else [],
        "confidence": round(result["confidence"], 4),
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
