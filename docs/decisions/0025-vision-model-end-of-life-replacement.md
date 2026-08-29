# 0025. Vision model end-of-life: replace nemotron-nano-12b-v2-vl

## Status
Accepted

## Context

While running a live end-to-end demo (2026-08-29) of the moderator review workflow,
every listing that reached the pipeline failed processing with:

```json
{"type":"about:blank","title":"Gone","status":410,"detail":"The model
'nvidia/nemotron-nano-12b-v2-vl' has reached its end of life on
2026-08-26T09:00:00Z and is no longer available."}
```

`nvidia/nemotron-nano-12b-v2-vl` — the vision-language model both Evidence Agent
(§3.2, OCR/object/brand extraction) and Consistency Agent (§3.3, the three
image-based checks) depend on — was permanently removed from NVIDIA's catalog three
days earlier and no longer appears in `/v1/models` at all. Unlike the transient
`mistral-nemotron` flakiness found and mitigated earlier the same day (0022), this is
not an outage: the model is gone for good, and every image-bearing listing was
silently degrading to `pipeline.py`'s generic error handler — landing in
`PENDING_REVIEW` with a bare "Pipeline" error artifact and **zero actual agent
findings** (no Evidence, no Consistency, no Policy signal), defeating the purpose of
automated moderation for every listing with images.

## Decision

Replace `nvidia/nemotron-nano-12b-v2-vl` with `meta/llama-3.2-11b-vision-instruct` in
both `agents/evidence_agent.py` and `agents/consistency_agent.py`. Selection was by
direct real-call testing against NVIDIA's current catalog (`/v1/models`), not
guesswork:

- `meta/llama-3.2-90b-vision-instruct` timed out (30s) on a real call — ruled out.
- `microsoft/phi-3-vision-128k-instruct` was not tested further once
  `llama-3.2-11b-vision-instruct` succeeded cleanly on both required shapes below.
- `meta/llama-3.2-11b-vision-instruct` confirmed via real calls against
  `fixtures/real_photos/iphone-16-back.jpg`:
  - **Consistency Agent's shape** (image_url content + `logprobs`/`top_logprobs` for
    a true/false verdict): 200, correct answer, valid logprobs — `_verdict`'s parsing
    needs no changes.
  - **Evidence Agent's shape** (strict-JSON extraction prompt): 200, correct
    extraction (brand "Apple" detected, no brand mismatch for a declared-Apple
    listing) — but unlike the old model, the response includes explanatory prose
    *before* the JSON object rather than JSON alone. Confirmed this doesn't need a
    parser change: `_parse_json_object`'s existing `text.index("{")` +
    `json.JSONDecoder().raw_decode` already tolerates leading prose (it was written
    to tolerate markdown fences and trailing garbage, and this is the same class of
    tolerance).
  - Model precision is imperfect in a way that doesn't matter for this pipeline's
    purpose: it read the iPhone-16 fixture's model number as "iPhone 15" in one
    extraction — a labeling quirk, not a signal Evidence Agent's `brandMismatch` logic
    depends on (only the detected brand, "Apple", matters there).

Not switched: `mistralai/mistral-nemotron` (Consistency Agent's text check, Safety
Agent's prize-scam second-opinion check) — that model is still in the catalog, its
problem is transient flakiness (0022), not end-of-life.

## Consequences

- `agents/evidence_agent.py`: `MODEL` constant updated, with an inline comment noting
  the EOL and pointing here.
- `agents/consistency_agent.py`: `VISION_MODEL` constant updated, same comment.
- SPEC.md §3.2/§3.3 updated.
- `tests/test_evidence_agent.py`/`tests/test_consistency_agent.py`: docstrings
  updated to note the swap; no assertion changes needed, since no test asserts on the
  literal model-name string and the OpenAI-compatible response shape is unchanged.
- **Not addressed here, found during the same investigation:** `mistral-nemotron`
  (Consistency Agent's text check) hit real 500s, connection timeouts, and 429s
  during verification of this fix. Consistency Agent has no fail-open guard for this
  call at all (unlike Safety Agent's prize-scam check after 0022) — any failure
  propagates all the way to `pipeline.py`'s generic error handler, same
  zero-findings-into-review outcome this ADR fixes for the vision-model case. Flagged
  as a follow-up, not fixed in this change — extending 0022's pattern to Consistency
  Agent changes what a skipped check means for `inconsistencyScore` (a neutral value
  needs deciding, not just "keep the old verdict" like the retry case), which is a
  separate design decision.
- **Operational lesson, not yet acted on:** this model had been dead for three days
  before anyone noticed, only surfaced by chance during an unrelated demo. There's no
  monitoring/alerting on pipeline error rates or on NVIDIA model deprecation notices.
  Worth a follow-up if this pattern (silent degradation to zero-signal `PENDING_REVIEW`)
  recurs.
