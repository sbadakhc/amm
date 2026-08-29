# 0019. Safety Agent retries a low-confidence safe verdict once

## Status
Accepted

## Context

Issue #55 (a follow-up from #54's F001-mapping fix) reported that the Safety Agent was
non-deterministic across repeated calls with identical scam listing text, and asked for
its own real-call investigation before deciding on a fix, per this project's
verify-against-reality convention.

That investigation (real calls to `nvidia/llama-3.1-nemotron-safety-guard-8b-v3` via
`agents/safety_agent.py` directly, no mock) found the picture was more nuanced than #55
assumed:

- With #54/0018's F001 mapping already live, repeating the same job-scam and
  real-estate-scam listing text 5x each was caught **5/5** both times -- the three
  categories mapped to F001 (`Fraud/Deception`, `Criminal Planning/Confessions`,
  `Illegal Activity`) effectively act as an ensemble; at least one fires almost every
  call, so #54 already fixed most of what #55 was worried about.
- A genuinely stochastic miss was found on a different scam pattern (an "invest $100,
  get $1000 in 2 days" advance-fee pitch): one call returned a well-formed `safe`
  verdict at confidence **0.023** -- the model's own probability that it was safe was
  near zero, yet `safe` was still the top token. Other calls on the same text correctly
  flagged it unsafe at confidence ~0.98.
- Every genuinely-safe listing tested (5 clean listings across categories, repeated 5x;
  several edgy-but-legal listings -- knives, a licensed hunting rifle, vague investment
  talk, currency exchange) had safe-verdict confidence **>= 0.70**, with zero false
  positives and stable confidence across repeats. This gives a wide, defensible gap
  between a "this is actually safe" verdict and the one confirmed "wrong safe" outlier
  -- same threshold-tuning pattern as `CONSISTENCY_THRESHOLD` (`docs/decisions/0014`).
- Separately, a lottery/advance-fee prize scam pattern was found to be a **systematic**
  blind spot, not a stochastic one -- 0/9 across 3 phrasings x 3 repeats each, with
  stable (not low) confidence. Retrying doesn't help a consistent wrong answer. Tracked
  separately as issue #57, out of scope for this decision.

## Decision

Add a low-confidence-safe retry to `agents/safety_agent.py`: if the first call returns
`safe` with confidence below `SAFE_RETRY_CONFIDENCE_THRESHOLD` (default `0.5`, env var
`SAFETY_SAFE_RETRY_THRESHOLD`), retry once. If the retry says `unsafe`, use the retry's
result. If the retry also says `safe`, keep whichever of the two safe verdicts has
higher confidence. This mirrors the retry-once pattern already established for
Consistency Agent (`docs/decisions/0013`), but for a different failure mode -- a
well-formed wrong answer, not a malformed/rambling one.

Verified against real calls: repeating the previously-flaky advance-fee test case 10x
with the mitigation live caught it **9/10** (the one remaining miss was a confident
`unsafe` verdict that picked an unmapped category, a category-selection issue the retry
isn't designed to fix, not a confidence issue).

## Consequences

- `agents/safety_agent.py`: added `SAFE_RETRY_CONFIDENCE_THRESHOLD` constant and retry
  logic in `run_safety_agent`.
- SPEC.md §3.4 updated with the retry behavior and the known lottery-scam limitation.
- Tests added: `test_high_confidence_safe_does_not_retry`,
  `test_low_confidence_safe_retries_and_keeps_higher_confidence_safe`,
  `test_low_confidence_safe_retry_catches_violation`.
- Issue #57 filed for the lottery-scam systematic blind spot -- not addressed by this
  change, needs its own (likely non-ML, rule-based) mitigation.
