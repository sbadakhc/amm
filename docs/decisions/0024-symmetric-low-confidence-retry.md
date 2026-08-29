# 0024. Low-confidence retry extended to the unsafe direction

## Status
Accepted

## Context

0023's eBay false-positive eval (real calls, 20 real listing titles) found a live
false positive on its first run: a plain clothing listing ("NWT $44.00 US Claiborne
XL & 2XL Short Sleeve Woven Men's Shirt White off, Blue!") flagged `unsafe` with
categories `Criminal Planning/Confessions` and `PII/Privacy` -- neither remotely
applicable to the text.

Direct repeated real calls to `_classify` on the same title reproduced it: **1/15
calls (~6.7%)** returned this spurious `unsafe` verdict, at confidence **0.0086** --
the model's own probability for the token it emitted was near zero, the same
"well-formed but wrong answer" failure mode 0019 already found and fixed, just in the
opposite direction. 0019's retry only triggers on a low-confidence `safe` verdict;
a low-confidence `unsafe` verdict, however close to 0, was trusted immediately with no
retry. A follow-up 8-repeat sweep across all 20 fixture titles (160 calls) found 0
further occurrences, and a later 60-call targeted repeat of the same title also found
0 -- consistent with this being real but low-frequency stochastic noise (a single-digit
percentage, not a reliable per-title reproduction), not something specific to that
title's content (brand name, price format, etc.).

## Decision

Extend `run_safety_agent`'s retry logic to trigger on `result["confidence"] <
SAFE_RETRY_CONFIDENCE_THRESHOLD` regardless of the verdict's direction, but keep the
two directions' *resolution* asymmetric on purpose:

- Low-confidence `safe` (0019, unchanged): prefer the retry if it comes back unsafe at
  all, or if it's a more confident safe verdict. This deliberately biases toward
  catching fraud the first call missed -- retrying never invents a violation from a
  safe-then-safe pair.
- Low-confidence `unsafe` (this decision): no directional bias -- keep whichever of
  the two calls the model was more confident in. Preferring "unsafe" here the same way
  0019 prefers it would make the retry pointless for its purpose (correcting a
  spurious flag), since the first call is already unsafe by definition.

Rejected: leaving it asymmetric (the status quo that let this false positive through);
applying the same "prefer unsafe" bias in both directions (would never correct a
spurious low-confidence unsafe verdict, defeating the purpose).

**Verification note:** unlike 0019/0020/0021, this fix's real-call verification is a
mock-based deterministic test, not a repeated live reproduction -- the underlying
spurious-flag event proved too rare (~6.7% on the one title where it was first
observed, 0/160 and 0/60 on later repeats) to force again within a reasonable number
of live calls. The mocked test uses the actual real-call response shape captured
during the initial reproduction (confidence 0.0086, the two spurious categories),
consistent with this project's existing pattern of mock fixtures built from real
captured shapes (see `tests/conftest.py`), not invented ones.

## Consequences

- `agents/safety_agent.py`: `run_safety_agent`'s retry condition changed from `not
  result["unsafe"] and result["confidence"] < THRESHOLD` to `result["confidence"] <
  THRESHOLD`, with the resolution logic branching on `result["unsafe"]` afterward.
- SPEC.md §3.4 updated.
- Tests added: `test_low_confidence_unsafe_retries_and_corrects_spurious_flag`,
  `test_low_confidence_unsafe_retry_keeps_violation_if_still_more_confident`.
- Cost: on a low-confidence unsafe first verdict (previously zero extra calls), one
  extra API call, same as the existing low-confidence-safe case -- expected to be rare
  given confirmed real-call confidence is normally high (0019: every genuine safe
  verdict observed >= 0.70; genuine unsafe verdicts in this project's other real-call
  testing are consistently >= 0.9 outside this one confirmed noise event).
