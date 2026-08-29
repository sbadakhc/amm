"""
Preflight check: confirms every NVIDIA-hosted model this pipeline depends on is
actually callable before running real-call tests or the pipeline itself
(docs/decisions/0026). Exists because of two real incidents found the same day
(2026-08-29): `mistral-nemotron` intermittently hanging/500/429 (transient, see
docs/decisions/0022), and `nvidia/nemotron-nano-12b-v2-vl` being permanently removed
by NVIDIA three days before anyone noticed (docs/decisions/0025) -- in both cases the
first symptom was a real workflow hanging or silently degrading, not a clear error.

Two distinct failure modes, reported separately because they need different
responses:
- GONE: the model isn't in NVIDIA's current catalog (`/v1/models`) at all, or
  responds 410. Permanent -- the code needs a replacement, not a retry.
- UNREACHABLE: the model is listed but a live call failed (timeout, 5xx, 429).
  Could be transient -- worth retrying later, not necessarily a code change.

Usage:
    python3 scripts/preflight_check.py            # human-readable table, exits 1 if any model is GONE or UNREACHABLE
    python3 scripts/preflight_check.py --quiet     # same, no table, just the exit code (for scripting)
"""

import os
import sys

import requests

NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_EMBEDDINGS_URL = "https://integrate.api.nvidia.com/v1/embeddings"
NVIDIA_MODELS_URL = "https://integrate.api.nvidia.com/v1/models"
CHECK_TIMEOUT = 15

# Every model this pipeline depends on, kept in sync manually with agents/*.py and
# embeddings.py -- deliberately not imported from those modules, so a preflight run
# doesn't itself depend on agent code being importable/correct.
CHAT_MODELS = {
    "nvidia/llama-3.1-nemotron-safety-guard-8b-v3": "Safety Agent (primary classifier)",
    "mistralai/mistral-nemotron": "Safety Agent (prize-scam check), Consistency Agent (text check)",
    "meta/llama-3.2-11b-vision-instruct": "Evidence Agent, Consistency Agent (vision checks)",
}
EMBEDDING_MODELS = {
    "nvidia/nemotron-3-embed-1b": "embeddings.py (find_similar_cases)",
}


def _fetch_catalog() -> set[str] | None:
    try:
        resp = requests.get(
            NVIDIA_MODELS_URL,
            headers={"Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}"},
            timeout=CHECK_TIMEOUT,
        )
        resp.raise_for_status()
        return {m["id"] for m in resp.json()["data"]}
    except requests.exceptions.RequestException:
        return None


def _suggest_candidates(model_id: str, catalog: set[str]) -> list[str]:
    """Cheap heuristic, not a recommendation: same-family (owner prefix) or
    same-modality-keyword matches from the live catalog, for a human/agent to
    evaluate with real calls -- same process used in docs/decisions/0025, never
    auto-adopted without verification."""
    owner = model_id.split("/")[0]
    keywords = [k for k in ("vision", "vl", "embed", "vlm", "safety") if k in model_id.lower()]
    candidates = {m for m in catalog if m.split("/")[0] == owner}
    for kw in keywords:
        candidates |= {m for m in catalog if kw in m.lower()}
    candidates.discard(model_id)
    return sorted(candidates)[:8]


def check_all() -> list[dict]:
    """Returns one result dict per configured model: {model, used_by, status,
    detail}. status is one of OK / GONE / UNREACHABLE / UNKNOWN (couldn't even fetch
    the catalog to check)."""
    catalog = _fetch_catalog()
    results = []

    for model_id, used_by in CHAT_MODELS.items():
        results.append(_check_chat_model(model_id, used_by, catalog))
    for model_id, used_by in EMBEDDING_MODELS.items():
        results.append(_check_embedding_model(model_id, used_by, catalog))

    return results


def _check_chat_model(model_id: str, used_by: str, catalog: set[str] | None) -> dict:
    if catalog is not None and model_id not in catalog:
        return {"model": model_id, "used_by": used_by, "status": "GONE", "detail": "not in /v1/models catalog"}
    try:
        resp = requests.post(
            NVIDIA_API_URL,
            headers={
                "Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}",
                "Content-Type": "application/json",
            },
            json={"model": model_id, "messages": [{"role": "user", "content": "hello"}], "max_tokens": 5},
            timeout=CHECK_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        return {"model": model_id, "used_by": used_by, "status": "UNREACHABLE", "detail": type(e).__name__}
    if resp.status_code == 410:
        return {"model": model_id, "used_by": used_by, "status": "GONE", "detail": resp.json().get("detail", "410 Gone")}
    if resp.ok:
        return {"model": model_id, "used_by": used_by, "status": "OK", "detail": f"{resp.elapsed.total_seconds():.1f}s"}
    return {"model": model_id, "used_by": used_by, "status": "UNREACHABLE", "detail": f"HTTP {resp.status_code}"}


def _check_embedding_model(model_id: str, used_by: str, catalog: set[str] | None) -> dict:
    if catalog is not None and model_id not in catalog:
        return {"model": model_id, "used_by": used_by, "status": "GONE", "detail": "not in /v1/models catalog"}
    try:
        resp = requests.post(
            NVIDIA_EMBEDDINGS_URL,
            headers={
                "Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}",
                "Content-Type": "application/json",
            },
            json={"model": model_id, "input": ["preflight check"], "input_type": "passage"},
            timeout=CHECK_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        return {"model": model_id, "used_by": used_by, "status": "UNREACHABLE", "detail": type(e).__name__}
    if resp.status_code == 410:
        return {"model": model_id, "used_by": used_by, "status": "GONE", "detail": resp.json().get("detail", "410 Gone")}
    if resp.ok:
        return {"model": model_id, "used_by": used_by, "status": "OK", "detail": f"{resp.elapsed.total_seconds():.1f}s"}
    return {"model": model_id, "used_by": used_by, "status": "UNREACHABLE", "detail": f"HTTP {resp.status_code}"}


def main():
    quiet = "--quiet" in sys.argv
    results = check_all()
    catalog = _fetch_catalog() or set()

    if not quiet:
        print(f"{'STATUS':<12}{'MODEL':<45}{'USED BY'}")
        for r in results:
            print(f"{r['status']:<12}{r['model']:<45}{r['used_by']}")
            if r["status"] != "OK":
                print(f"{'':<12}  -> {r['detail']}")
                if r["status"] == "GONE" and catalog:
                    candidates = _suggest_candidates(r["model"], catalog)
                    if candidates:
                        print(f"{'':<12}  -> unverified candidates from current catalog (test before adopting):")
                        for c in candidates:
                            print(f"{'':<12}       {c}")

    unusable = [r for r in results if r["status"] in ("GONE", "UNREACHABLE")]
    if unusable and not quiet:
        print(f"\n{len(unusable)}/{len(results)} model(s) not usable right now.")
    sys.exit(1 if unusable else 0)


if __name__ == "__main__":
    main()
