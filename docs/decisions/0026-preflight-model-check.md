# 0026. Preflight model check before real-call tests

## Status
Accepted

## Context

Two real incidents the same day (2026-08-29), both discovered by accident rather than
by any deliberate check:

1. `mistral-nemotron` intermittently hanging, 500-ing, or 429-ing (0022) — a live
   pipeline run and a live test run both stalled on it before the cause was clear.
2. `nvidia/nemotron-nano-12b-v2-vl` permanently end-of-lifed by NVIDIA three days
   earlier (0025) — every image-bearing listing had been silently degrading to a
   zero-findings `PENDING_REVIEW` the whole time, undetected.

Investigating those two led to a third, previously unknown case: running
`scripts/preflight_check.py` (built as part of this decision) immediately found
`nvidia/llama-nemotron-embed-1b-v2` (`embeddings.py`, `find_similar_cases`) was also
gone from the catalog — fixed in the same change (replaced with
`nvidia/nemotron-3-embed-1b`, confirmed via a real call to return the same 2048
dimensions).

The common failure pattern: nothing in this codebase checked, before doing real work,
whether the models it depends on were actually callable. The first signal was always
a hang, a confusing stack trace, or (worse) silent degradation — never a clear,
early "this won't work, here's why."

## Decision

`scripts/preflight_check.py`: a standalone script that checks every model this
pipeline depends on (kept as a manually-maintained list, deliberately not imported
from the agent modules, so a preflight run doesn't depend on agent code being
importable/correct) two ways:
1. Is it still in NVIDIA's model catalog (`GET /v1/models`)? A model missing here, or
   returning `410 Gone`, is reported as **GONE** — permanent, needs a code change
   (a replacement model), not a retry.
2. A live minimal call (chat completion or embedding, matching each model's real
   usage shape) — a timeout, connection failure, or non-2xx that isn't a `410` is
   reported as **UNREACHABLE** — could be transient, worth retrying, not necessarily
   a code change.

Distinguishing these two matters: conflating them either causes needless code churn
chasing a transient blip, or (worse, what actually happened here) lets a permanent
removal get miscategorized as "flaky, will recover" and go unfixed for days.

On a `GONE` result, the script also lists candidate replacements from the live
catalog (same owner prefix, or matching a modality keyword extracted from the dead
model's name) — explicitly **not** a recommendation, just a starting point for the
same real-call verification process used in 0025. Nothing here auto-adopts a
candidate.

Wired into `tests/conftest.py`: `fraud_eval` and `ebay_fp_eval` tests (the two
opt-in real-call suites) now run the preflight once per session, but only when their
other opt-in conditions are already satisfied (env var set, fixture present for the
eBay case) -- a normal `pytest -v` run triggers zero extra network calls. Each
suite's *hard* model dependency is checked (`nvidia/llama-3.1-nemotron-safety-guard-
8b-v3`), and the test is skipped with a clear preflight-failure reason instead of
running (and possibly hanging) if that model is down. `mistral-nemotron` is
deliberately **not** a hard dependency for either suite: after 0022, a Safety Agent
run degrades gracefully (skips the optional prize-scam check) rather than failing
when that model is unavailable, so gating the test suite on it would skip runnable
tests over an already-mitigated soft dependency.

Not gated: the normal offline suite (mocks `requests.post`, no real calls, must stay
fast and network-independent) and `tests/test_db_integration.py` (Postgres, not an
NVIDIA model concern).

## Consequences

- New file: `scripts/preflight_check.py` (also runnable standalone --
  `python3 scripts/preflight_check.py`, exits 1 if any model is GONE/UNREACHABLE, for
  use before a manual pipeline run or demo, not just inside pytest).
- `tests/conftest.py`: new `REQUIRED_MODELS_BY_MARKER`, `_preflight_status_by_model`,
  and a preflight-skip branch in `pytest_collection_modifyitems`.
- `embeddings.py`: `MODEL` updated (`nvidia/nemotron-3-embed-1b`), found and fixed as
  a direct result of building this check, not a separately investigated issue.
- AGENTS.md: preflight documented as the first step before running real-call tests.
- `tests/test_preflight_check.py` (added 2026-08-29, coverage follow-up): pins down
  the GONE-vs-UNREACHABLE classification itself with `requests.get`/`requests.post`
  mocked out -- not in catalog -> GONE without even attempting a live call; in
  catalog but a live call fails/times out/returns non-2xx (non-410) -> UNREACHABLE;
  `410` -> GONE; catalog fetch itself failing (`catalog=None`) falls through to a
  live call rather than assuming either way. Also covers `_suggest_candidates`'
  same-owner/keyword matching and `check_all`'s aggregation. No real NVIDIA calls.
- **No programmatic early-warning exists for NVIDIA model EOL.** Investigated as part
  of this decision: `/v1/models` returns no deprecation/sunset-date field (just `id`,
  `object`, `created`, `owned_by`) -- there is no field to poll ahead of time. The
  only pre-EOL signal found is a manually-checked notice on a model's own
  `build.nvidia.com/<model>/deploy` page ("This API will be deprecated on ..."), and
  community reports on NVIDIA's developer forums -- neither is something this
  pipeline currently watches. This preflight check can only detect EOL *after* it
  happens (via `410`/catalog removal), not before. A real early-warning system would
  mean periodically diffing the catalog or scraping deploy-page notices -- out of
  scope here, noted as a real gap.
