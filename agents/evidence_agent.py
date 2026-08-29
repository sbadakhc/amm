"""
Evidence Agent — facts only, no judgment. See SPEC.md §3.2.

OCR, object/brand detection, and document-level extraction (certificate/serial
numbers, expiry, country of origin) via a vision-language model. Compares detected
brand(s) against the canonical document's declaredBrand and flags a mismatch.
"""

import base64
import json
import logging
import os
from datetime import datetime, timezone

import requests

try:
    from images import fetch_image_bytes
except ImportError:  # running as a script, not a package -- repo root isn't on sys.path
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from images import fetch_image_bytes

logger = logging.getLogger("amm.evidence_agent")

NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
# nvidia/nemotron-nano-12b-v2-vl reached NVIDIA end-of-life 2026-08-26 and is no
# longer callable (410 Gone) -- see docs/decisions/0025.
MODEL = "meta/llama-3.2-11b-vision-instruct"

# Confirmed real-call latency for a single image's extraction is normally a few
# seconds (docs/decisions/0025's verification), not the old 60s. Shorter, with a
# retry, same reasoning as the other agents' timeout fallbacks (0022/0028): a hung
# backend gets a bounded wait, not an unbounded one. See docs/decisions/0029.
EVIDENCE_EXTRACTION_TIMEOUT = float(os.environ.get("EVIDENCE_EXTRACTION_TIMEOUT", "20"))

EXTRACTION_PROMPT = (
    "List every object you see, transcribe all visible text exactly (OCR), and name "
    "any brand logos or brand names visible in the image. Also note any certificate "
    "numbers, serial numbers, expiry dates, or country-of-origin markings visible on "
    "packaging/labels. Respond as strict JSON with keys: objects (array of strings), "
    "ocr (array of strings), brands (array of strings), certificateNumbers (array of "
    "strings), serialNumbers (array of strings), expiryDate (string or null), "
    "countryOfOrigin (string or null). No markdown fences, just the JSON object."
)


def _load_image_b64(url: str) -> tuple[str, str]:
    """Returns (base64 bytes, mime type) for an image URL (file:// or s3://, §3.1)."""
    data, mime = fetch_image_bytes(url)
    return base64.b64encode(data).decode("ascii"), mime


def _parse_json_object(text: str) -> dict:
    """Extracts the first JSON object from the model's response. Tolerant of markdown
    fences, including a stray duplicate closing fence the model occasionally emits
    (confirmed via real calls -- a naive rsplit-based fence strip only removes one
    trailing ``` and leaves the other as garbage that breaks json.loads). Uses
    raw_decode to parse just the first JSON value and ignore anything after it,
    rather than assuming the whole remaining string is clean JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
    obj, _ = json.JSONDecoder().raw_decode(text[text.index("{") :])
    return obj


def _extract_from_image(url: str) -> dict | None:
    """Extracts objects/OCR/brands/etc from one image. Retries once on a malformed
    (non-JSON) response -- same "fresh sample resolves stochastic non-compliance"
    reasoning as the other agents' retries. Returns None -- treated as "skip this
    image", not "no objects/brands found" -- on a timeout/connection failure, or two
    malformed responses in a row (docs/decisions/0029, same fail-open philosophy as
    0022/0028): this image's extraction failing must not silently read as "packaging
    confirmed nothing," which would manufacture a brandMismatch that was never
    actually checked."""
    b64, mime = _load_image_b64(url)
    last_error = None
    for _attempt in range(2):
        try:
            resp = requests.post(
                NVIDIA_API_URL,
                headers={
                    "Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MODEL,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": EXTRACTION_PROMPT},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                                },
                            ],
                        }
                    ],
                },
                timeout=EVIDENCE_EXTRACTION_TIMEOUT,
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException:
            logger.warning("evidence extraction unavailable for %s, skipping", url, exc_info=True)
            return None
        content = resp.json()["choices"][0]["message"]["content"]
        try:
            return _parse_json_object(content)
        except ValueError as e:  # covers json.JSONDecodeError and the "no { found" case
            last_error = e
    logger.warning("evidence extraction malformed twice for %s, skipping: %s", url, last_error)
    return None


def run_evidence_agent(canonical_doc: dict) -> dict:
    """
    Runs the Evidence Agent over a canonical listing document (§3.1) and returns an
    EvidenceAgent artifact (§5) ready to append to the `artifacts` table.

    Each image's extraction can independently be skipped (docs/decisions/0029) if its
    model call failed -- landing in `imagesSkipped`, not contributing objects/brands/
    OCR/etc. If *every* attempted image was skipped, `brandMismatch` stays `false`
    regardless of `declaredBrand` -- the same fail-open principle as 0022/0024/0028:
    "couldn't check" must not manufacture a violation. This is deliberately different
    from the *zero-images* case below, where a mismatch is still flagged: no images
    at all is itself a real signal about the listing, not an infrastructure failure.
    """
    objects: set[str] = set()
    brands: set[str] = set()
    ocr: list[str] = []
    certificate_numbers: list[str] = []
    serial_numbers: list[str] = []
    expiry_date = None
    country_of_origin = None
    images_skipped: list[str] = []

    attempted_images = canonical_doc.get("images", [])
    for url in attempted_images:
        extracted = _extract_from_image(url)
        if extracted is None:
            images_skipped.append(url)
            continue
        objects.update(o.strip() for o in extracted.get("objects", []) if o.strip())
        brands.update(b.strip() for b in extracted.get("brands", []) if b.strip())
        ocr.extend(extracted.get("ocr", []))
        certificate_numbers.extend(extracted.get("certificateNumbers", []))
        serial_numbers.extend(extracted.get("serialNumbers", []))
        expiry_date = expiry_date or extracted.get("expiryDate")
        country_of_origin = country_of_origin or extracted.get("countryOfOrigin")

    # Placeholder values for genuinely unbranded/commodity goods — not a brand claim,
    # so there's nothing for packaging to corroborate and no mismatch to flag.
    GENERIC_BRAND_PLACEHOLDERS = {"generic", "unbranded", "no brand", "none", "n/a"}

    declared_brand = (canonical_doc.get("declaredBrand") or "").strip().lower()
    detected_brands_lower = {b.lower() for b in brands}
    all_attempted_images_failed = bool(attempted_images) and len(images_skipped) == len(attempted_images)
    # No corroborating brand on any image counts as a mismatch, not just a conflicting
    # one — an undetected declared brand is exactly the counterfeit-branding signal
    # Policy Agent's C001 needs (§3.5). Not when every image's extraction failed,
    # though: that's "couldn't check," not "checked and found nothing."
    brand_mismatch = (
        not all_attempted_images_failed
        and bool(declared_brand)
        and declared_brand not in GENERIC_BRAND_PLACEHOLDERS
        and declared_brand not in detected_brands_lower
    )

    payload = {
        "objects": sorted(objects),
        "brandsDetected": sorted(brands),
        "ocr": ocr,
        "brandMismatch": brand_mismatch,
        "certificateNumbers": certificate_numbers,
        "serialNumbers": serial_numbers,
        "expiryDate": expiry_date,
        "countryOfOrigin": country_of_origin,
        "imagesSkipped": images_skipped,
    }

    return {
        "listingId": canonical_doc["listingId"],
        "agent": "EvidenceAgent",
        "version": MODEL,
        "producedAt": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }


if __name__ == "__main__":
    import sys

    doc = json.loads(sys.argv[1])
    print(json.dumps(run_evidence_agent(doc), indent=2))
