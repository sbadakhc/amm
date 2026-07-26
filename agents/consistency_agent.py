"""
Consistency Agent — cross-checks fields that should agree but are supplied
independently. See SPEC.md §3.3. Does its own lightweight image understanding rather
than reusing Evidence Agent's output, since it depends only on the canonical document
(§1) and must not wait on Evidence Agent.
"""

import base64
import json
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

NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
TEXT_MODEL = "mistralai/mistral-nemotron"
VISION_MODEL = "nvidia/nemotron-nano-12b-v2-vl"


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


def _text_check(prompt: str) -> tuple[bool, float]:
    resp = requests.post(
        NVIDIA_API_URL,
        headers={
            "Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}",
            "Content-Type": "application/json",
        },
        json={
            "model": TEXT_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 10,
            "logprobs": True,
            "top_logprobs": 1,
        },
        timeout=30,
    )
    resp.raise_for_status()
    choice = resp.json()["choices"][0]
    return _verdict(choice["logprobs"]["content"])


def _vision_check(prompt: str, image_url: str) -> tuple[bool, float]:
    resp = requests.post(
        NVIDIA_API_URL,
        headers={
            "Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}",
            "Content-Type": "application/json",
        },
        json={
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
        },
        timeout=30,
    )
    resp.raise_for_status()
    choice = resp.json()["choices"][0]
    return _verdict(choice["logprobs"]["content"])


def _aggregate_across_images(results: list[tuple[bool, float]]) -> tuple[bool, float]:
    """Multiple images: consistent if any image confirms it; confidence is the
    strongest evidence for whichever verdict wins."""
    overall = any(a for a, _ in results)
    matching = [c for a, c in results if a == overall]
    return overall, (max(matching) if overall else statistics.mean(matching))


def _disagreement(consistent: bool, confidence: float) -> float:
    """Probability mass on "inconsistent" for this check — 1 - confidence when the
    verdict was consistent, confidence itself when it wasn't."""
    return (1 - confidence) if consistent else confidence


def run_consistency_agent(canonical_doc: dict) -> dict:
    """
    Runs the Consistency Agent over a canonical listing document (§3.1) and returns a
    ConsistencyAgent artifact (§5) ready to append to the `artifacts` table.
    """
    title = canonical_doc["title"]
    description = canonical_doc["description"]
    declared_brand = canonical_doc.get("declaredBrand") or ""
    category_id = canonical_doc.get("categoryId", "")
    images = [_load_image_data_url(u) for u in canonical_doc.get("images", [])]

    checks = []
    disagreements = []

    contradicts, conf = _text_check(
        "Does the description explicitly name a different product, brand, or model "
        "than the title (a direct contradiction)? If the description does not "
        "mention a specific competing product name, that is NOT a contradiction. "
        "Answer with exactly one word: true or false.\n"
        f"Title: {title}\nDescription: {description}"
    )
    consistent = not contradicts
    checks.append({"pair": "title_vs_description", "consistent": consistent})
    disagreements.append(_disagreement(consistent, conf))

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
        consistent, conf = _aggregate_across_images(results)
        checks.append({"pair": "description_vs_images", "consistent": consistent})
        disagreements.append(_disagreement(consistent, conf))

        if declared_brand:
            results = [
                _vision_check(
                    f"Is the brand '{declared_brand}' visible in this image (logo, "
                    "packaging text, etc)? Answer with exactly one word: true or false.",
                    img,
                )
                for img in images
            ]
            consistent, conf = _aggregate_across_images(results)
            checks.append({"pair": "images_vs_declaredBrand", "consistent": consistent})
            disagreements.append(_disagreement(consistent, conf))

        results = [
            _vision_check(
                "Does this image show a product belonging to the category "
                f"'{category_id}'? Answer with exactly one word: true or false.",
                img,
            )
            for img in images
        ]
        consistent, conf = _aggregate_across_images(results)
        checks.append({"pair": "category_vs_detectedObjects", "consistent": consistent})
        disagreements.append(_disagreement(consistent, conf))

    payload = {
        "checks": checks,
        "inconsistencyScore": round(statistics.mean(disagreements), 4),
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
