"""
Evidence Agent — facts only, no judgment. See SPEC.md §3.2.

OCR, object/brand detection, and document-level extraction (certificate/serial
numbers, expiry, country of origin) via a vision-language model. Compares detected
brand(s) against the canonical document's declaredBrand and flags a mismatch.
"""

import base64
import json
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "nvidia/nemotron-nano-12b-v2-vl"

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
    """Returns (base64 bytes, mime type) for an image URL. file:// for local dev/demo;
    s3:// is the production scheme (§3.1) and needs a signed URL or SDK call wired in
    here once real object storage is in place."""
    parsed = urlparse(url)
    if parsed.scheme == "file":
        path = Path(parsed.path)
        data = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
    elif parsed.scheme == "s3":
        raise NotImplementedError(f"s3:// image fetch not wired up yet: {url}")
    else:
        raise ValueError(f"Unsupported image URL scheme: {url}")
    return base64.b64encode(data).decode("ascii"), mime


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _extract_from_image(url: str) -> dict:
    b64, mime = _load_image_b64(url)
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
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(_strip_fences(content))


def run_evidence_agent(canonical_doc: dict) -> dict:
    """
    Runs the Evidence Agent over a canonical listing document (§3.1) and returns an
    EvidenceAgent artifact (§5) ready to append to the `artifacts` table.
    """
    objects: set[str] = set()
    brands: set[str] = set()
    ocr: list[str] = []
    certificate_numbers: list[str] = []
    serial_numbers: list[str] = []
    expiry_date = None
    country_of_origin = None

    for url in canonical_doc.get("images", []):
        extracted = _extract_from_image(url)
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
    # No corroborating brand on any image counts as a mismatch, not just a conflicting
    # one — an undetected declared brand is exactly the counterfeit-branding signal
    # Policy Agent's C001 needs (§3.5).
    brand_mismatch = (
        bool(declared_brand)
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
