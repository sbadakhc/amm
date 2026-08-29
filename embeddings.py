"""
Shared text-embedding helper for `find_similar_cases` (§6). See
docs/decisions/0010-embeddings-for-find-similar-cases.md.
"""

import os

import requests

NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/embeddings"
# nvidia/llama-nemotron-embed-1b-v2 was removed from NVIDIA's catalog (found via
# scripts/preflight_check.py, docs/decisions/0026) -- replacement confirmed via a
# real call to return the same 2048 dimensions, no schema change needed.
MODEL = "nvidia/nemotron-3-embed-1b"
DIMENSIONS = 2048


def embed_text(text: str) -> list[float]:
    """Returns a DIMENSIONS-length embedding vector for `text`. Uses `input_type:
    passage` for both indexing and comparison -- listings are compared
    document-to-document, not as a short query against a long document, so the
    query/passage asymmetric embedding NVIDIA's retrieval models support doesn't
    apply here."""
    resp = requests.post(
        NVIDIA_API_URL,
        headers={
            "Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}",
            "Content-Type": "application/json",
        },
        json={"model": MODEL, "input": [text], "input_type": "passage"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(embed_text(sys.argv[1])))
