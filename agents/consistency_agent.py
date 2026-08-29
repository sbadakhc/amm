"""
Consistency Agent — cross-checks fields that should agree but are supplied
independently. See SPEC.md §3.3. Does its own lightweight image understanding rather
than reusing Evidence Agent's output, since it depends only on the canonical document
(§1) and must not wait on Evidence Agent.
"""

import base64
import json
import logging
import math
import os
import statistics
from datetime import datetime, timezone

import requests

try:
    from images import fetch_image_bytes
except ImportError:  # running as a script, not a package -- repo root isn't on sys.path
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from images import fetch_image_bytes

logger = logging.getLogger("amm.consistency_agent")

NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
TEXT_MODEL = "mistralai/mistral-nemotron"
# nvidia/nemotron-nano-12b-v2-vl reached NVIDIA end-of-life 2026-08-26 and is no
# longer callable (410 Gone) -- see docs/decisions/0025.
VISION_MODEL = "meta/llama-3.2-11b-vision-instruct"

# Confirmed real-call latency for these checks is normally well under 2s (same
# reasoning as agents/safety_agent.py's PRIZE_SCAM_CHECK_TIMEOUT). A shorter timeout
# than the old 30s is deliberate: `mistral-nemotron` has been confirmed to hang
# indefinitely with no timeout of its own (docs/decisions/0022) -- this agent must
# impose one rather than trust the backend to. See docs/decisions/0028.
CONSISTENCY_CHECK_TIMEOUT = float(os.environ.get("CONSISTENCY_CHECK_TIMEOUT", "10"))


def _load_image_data_url(url: str) -> str:
    """Returns a data: URL for an image URL (file:// or s3://, §3.1)."""
    data, mime = fetch_image_bytes(url)
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _verdict(content: list[dict]) -> tuple[bool, float]:
    """Finds the true/false answer token in a completion's logprobs and returns
    (answer, confidence) — confidence is the model's own probability for that token,
    not a separately requested score."""
    for tok in content:
        word = tok["token"].strip().strip("▁").lower()
        if word in ("true", "false"):
            return word == "true", math.exp(tok["logprob"])
    raise ValueError(f"No true/false token found in {content}")


def _post_for_verdict(json_body: dict) -> tuple[bool, float] | None:
    """POSTs a chat completion request and parses a true/false verdict, retrying once
    on failure. Confirmed via real calls: the model occasionally ignores the
    single-word instruction and rambles instead ("Given the information provided...")
    with no true/false token inside the token budget -- stochastic non-compliance, not
    a bad prompt, so a retry (a fresh sample) resolves it far more often than not
    without masking a genuine failure.

    Returns None -- treated as "skip this check", not "consistent" or "inconsistent"
    -- on a timeout/connection failure, or two malformed responses in a row
    (docs/decisions/0028, same fail-open philosophy as agents/safety_agent.py's
    0022): `mistral-nemotron` has been confirmed to hang indefinitely with no
    response and no timeout of its own, and this check is one signal among several
    Decision Agent weighs, not the only one -- it must not block the whole pipeline
    run on one flaky model endpoint."""
    last_error = None
    for _attempt in range(2):
        try:
            resp = requests.post(
                NVIDIA_API_URL,
                headers={
                    "Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}",
                    "Content-Type": "application/json",
                },
                json=json_body,
                timeout=CONSISTENCY_CHECK_TIMEOUT,
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException:
            logger.warning("consistency check unavailable, skipping", exc_info=True)
            return None
        choice = resp.json()["choices"][0]
        try:
            return _verdict(choice["logprobs"]["content"])
        except ValueError as e:
            last_error = e
    logger.warning("consistency check malformed twice, skipping: %s", last_error)
    return None


def _text_check(prompt: str) -> tuple[bool, float] | None:
    return _post_for_verdict(
        {
            "model": TEXT_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 10,
            "logprobs": True,
            "top_logprobs": 1,
        }
    )


def _vision_check(prompt: str, image_url: str) -> tuple[bool, float] | None:
    return _post_for_verdict(
        {
            "model": VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            "max_tokens": 10,
            "logprobs": True,
            "top_logprobs": 1,
        }
    )


def _aggregate_across_images(results: list[tuple[bool, float] | None]) -> tuple[bool, float] | None:
    """Multiple images: consistent if any image confirms it; confidence is the
    strongest evidence for whichever verdict wins. Skipped (None) per-image results
    are dropped first -- an image whose check failed contributes nothing, neither
    confirming nor denying. If every image's check failed, the whole aggregate is
    skipped (None) rather than fabricated from zero evidence."""
    usable = [r for r in results if r is not None]
    if not usable:
        return None
    overall = any(a for a, _ in usable)
    matching = [c for a, c in usable if a == overall]
    return overall, (max(matching) if overall else statistics.mean(matching))


def _disagreement(consistent: bool, confidence: float) -> float:
    """Probability mass on "inconsistent" for this check — 1 - confidence when the
    verdict was consistent, confidence itself when it wasn't."""
    return (1 - confidence) if consistent else confidence


def run_consistency_agent(canonical_doc: dict) -> dict:
    """
    Runs the Consistency Agent over a canonical listing document (§3.1) and returns a
    ConsistencyAgent artifact (§5) ready to append to the `artifacts` table.

    Each check can independently be skipped (docs/decisions/0028) if its model call
    failed -- landing in `checksSkipped`, not `checks`, and contributing nothing to
    `inconsistencyScore` (excluded from the mean, not counted as consistent or
    inconsistent). If every check was skipped, `inconsistencyScore` defaults to 0.0
    (no evidence of inconsistency) rather than raising on an empty mean -- the same
    fail-open choice as a skipped check contributing nothing: a check that couldn't
    run must not manufacture a violation, but `checksSkipped` makes the gap visible
    rather than silently indistinguishable from "checked, found consistent."
    """
    title = canonical_doc["title"]
    description = canonical_doc["description"]
    declared_brand = canonical_doc.get("declaredBrand") or ""
    category_id = canonical_doc.get("categoryId", "")
    images = [_load_image_data_url(u) for u in canonical_doc.get("images", [])]

    checks = []
    checks_skipped = []
    disagreements = []

    def _record(pair: str, result: tuple[bool, float] | None) -> None:
        if result is None:
            checks_skipped.append(pair)
            return
        consistent, conf = result
        checks.append({"pair": pair, "consistent": consistent})
        disagreements.append(_disagreement(consistent, conf))

    title_vs_description = _text_check(
        "Does the description explicitly name a different product, brand, or model "
        "than the title (a direct contradiction)? If the description does not "
        "mention a specific competing product name, that is NOT a contradiction. "
        "Answer with exactly one word: true or false.\n"
        f"Title: {title}\nDescription: {description}"
    )
    _record(
        "title_vs_description",
        None if title_vs_description is None else (not title_vs_description[0], title_vs_description[1]),
    )

    if images:
        results = [
            _vision_check(
                "Does this image visually match what this product description "
                f"says the product is? Description: {description}\n"
                "Answer with exactly one word: true or false.",
                img,
            )
            for img in images
        ]
        _record("description_vs_images", _aggregate_across_images(results))

        if declared_brand:
            results = [
                _vision_check(
                    f"Is the brand '{declared_brand}' visible in this image (logo, "
                    "packaging text, etc)? Answer with exactly one word: true or false.",
                    img,
                )
                for img in images
            ]
            _record("images_vs_declaredBrand", _aggregate_across_images(results))

        results = [
            _vision_check(
                "Does this image show a product belonging to the category "
                f"'{category_id}'? Answer with exactly one word: true or false.",
                img,
            )
            for img in images
        ]
        _record("category_vs_detectedObjects", _aggregate_across_images(results))

    payload = {
        "checks": checks,
        "checksSkipped": checks_skipped,
        "inconsistencyScore": round(statistics.mean(disagreements), 4) if disagreements else 0.0,
    }

    return {
        "listingId": canonical_doc["listingId"],
        "agent": "ConsistencyAgent",
        "version": f"{TEXT_MODEL}+{VISION_MODEL}",
        "producedAt": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }


if __name__ == "__main__":
    import sys

    doc = json.loads(sys.argv[1])
    print(json.dumps(run_consistency_agent(doc), indent=2))
