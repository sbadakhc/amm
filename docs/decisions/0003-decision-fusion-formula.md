# 0003. Decision fusion is a deterministic 3-step formula, not a model judgment call

## Status
Accepted

## Context
SPEC.md originally said the Decision Agent "combines all upstream outputs, applies
thresholds" without specifying how -- no actual formula for turning multiple policy
matches, each with their own confidence, into one final decision + confidence number.
That ambiguity was flagged as the highest-risk gap in an early review of the spec:
an agent implementing it from that description alone would have to invent a fusion
scheme, and that's exactly the kind of choice that produces silently divergent
behavior later.

## Decision
Three deterministic steps (SPEC.md §4):

1. **Aggregate confidence** -- `max(match.confidence)` across `PolicyAgent.matches` if
   any matched; otherwise `1 - ConsistencyAgent.inconsistencyScore` (the only signal
   left when no rule fired).
2. **Seller history adjustment** -- if `sellerPreviousViolations > 0`, nudge
   confidence by `min(0.05 * previousViolations, 0.20)`, direction depending on which
   way the *pre-adjustment* confidence would tentatively route (toward REVIEW from
   APPROVE, or toward REJECT with more certainty). No adjustment if the tentative
   route is already REVIEW.
3. **Route** on the adjusted confidence: any `autoReject: true` match forces REJECT
   regardless of confidence; a Critical-severity match needs confidence >= 0.95 to
   reject; no matches needs confidence >= 0.90 to approve; everything else is REVIEW.

Policy Agent attributes `confidence` per match (from whichever upstream agent
triggered the rule) rather than Decision Agent re-deriving it from raw agent outputs
-- Decision Agent only combines what's already there.

## Consequences
- No model call in Decision Agent -- fully deterministic, unit-testable without
  mocking anything (see `tests/test_decision_agent.py`).
- `explanation` is generated from the matched rules' own descriptions (or the
  residual inconsistency score) plus the adjustment if one applied -- not
  model-written prose, consistent with Safety/Consistency Agent's explanation style.
- Thresholds (0.95, 0.90, the 0.20 adjustment cap) are named constants in
  `agents/decision_agent.py`, not tuned against real traffic -- a first pass, expected
  to move.
