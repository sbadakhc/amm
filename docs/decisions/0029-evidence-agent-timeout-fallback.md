# 0029. Evidence Agent extraction fails open on timeout, not blocking

## Status
Accepted

## Context

0022 and 0028 gave Safety Agent's and Consistency Agent's model calls fail-open
timeouts. Evidence Agent's vision extraction (`_extract_from_image`, one call per
image) never got the same treatment -- and the very first live pipeline run after
0028 landed hit it directly: of 5 real demo listings, 2 failed via `pipeline.py`'s
generic error handler, one from each of Evidence Agent's two unguarded failure modes:

- A 60s timeout (`HTTPSConnectionPool(...): Read timed out. (read timeout=60)`) --
  the same "backend accepts the connection but never responds" pattern 0022/0028
  already found for other models, just not yet fixed here.
- `ValueError: substring not found` -- `_parse_json_object`'s `text.index("{")`
  assumes the model's response always contains a JSON object somewhere; the new
  vision model (0025's `meta/llama-3.2-11b-vision-instruct`) occasionally returns
  pure prose with no `{` at all, a failure mode the old model apparently didn't
  trigger (or wasn't caught testing before its unrelated end-of-life).

Both crashed `run_evidence_agent` entirely, taking the whole listing down with them
regardless of how many other images might have succeeded.

## Decision

Same fail-open shape as 0022/0028, adapted for Evidence Agent's per-image structure:

- `_extract_from_image` returns `dict | None`, retrying once on a malformed response
  (same "fresh sample resolves stochastic non-compliance" pattern as the text-check
  retries elsewhere) before giving up. Returns `None` -- not an empty dict, and not
  raising -- on a timeout/connection failure or two malformed responses in a row.
- New `EVIDENCE_EXTRACTION_TIMEOUT` env var (default 20s, up from the old 60s --
  confirmed real-call latency for a single image is normally a few seconds; 20s is
  generous slack, not a tight cutoff, and a bounded wait beats an unbounded one).
- `run_evidence_agent` now skips a `None` result per-image (added to a new
  `imagesSkipped` list) instead of crashing -- the other images in the same listing
  are unaffected, same independence principle as 0028's per-check skipping.
- **The design question specific to this agent**: what does a skipped image mean for
  `brandMismatch`? Evidence Agent's existing (pre-this-change) behavior already
  treats *zero images at all* as a mismatch when a brand is declared ("no corroborating
  brand" -- SPEC.md §3.2) -- deliberately, since that's a real content signal. But
  "every attempted image's *extraction* failed" is not the same situation: it's an
  infrastructure failure, and treating it identically would flag `brandMismatch` on
  every listing during any Evidence Agent outage, manufacturing counterfeit signal
  from a backend problem. So: `brandMismatch` stays `false` when every attempted
  image was skipped, but the existing zero-images-at-all behavior is unchanged.
  Partial failure (some images succeed, some skip) uses whatever brands the
  successful images found -- partial real evidence is still evidence, not discarded
  just because one image out of several failed.

Rejected: treating a skip the same as "no brand found" (the false-positive-storm
problem above); requiring *all* images to succeed before trusting any evidence
(would discard real evidence over one flaky call, same overcorrection 0028 rejected
for Consistency Agent).

## Consequences

- `agents/evidence_agent.py`: `_extract_from_image` returns `Optional[dict]`, retries
  once, catches `requests.exceptions.RequestException` and `ValueError` (covers
  `json.JSONDecodeError` and the "no `{`" case). `run_evidence_agent` tracks
  `attempted_images`/`images_skipped`; `brand_mismatch` gated on
  `not all_attempted_images_failed`. New `EVIDENCE_EXTRACTION_TIMEOUT` env var and
  `amm.evidence_agent` logger.
- New `imagesSkipped` field on the `EvidenceAgent` payload.
- SPEC.md §3.2 updated.
- `tests/test_evidence_agent.py`: added
  `test_timeout_skips_image_and_does_not_manufacture_mismatch`,
  `test_malformed_twice_skips_image`,
  `test_partial_failure_uses_evidence_from_successful_images` -- the first two are
  direct regression tests for the two real failures this session's live run hit.
- Verified against real, degraded live conditions (not just mocks): a real call
  during an active backend outage skipped cleanly in ~20s with `brandMismatch: false`
  and `imagesSkipped` populated, instead of hanging ~60s and crashing the listing.
- All three agents that make real-time model calls (Safety, Consistency, Evidence)
  now share the same fail-open philosophy: a check that couldn't run contributes
  nothing, in either direction, and is recorded as skipped rather than silently
  absorbed into a false verdict.
