# 0020. Targeted second-opinion check for prize/advance-fee scams

## Status
Accepted

## Context

Issue #57 (a follow-up from #55/`docs/decisions/0019`) found the safety-guard model has
a systematic, not stochastic, blind spot for lottery/advance-fee prize scams ("you won a
prize, pay a fee to claim it") -- 0/9 flagged across 3 phrasings x 3 repeats, with stable
(not low) confidence, meaning 0019's low-confidence retry doesn't help: the model isn't
uncertain here, it's consistently wrong.

Two mitigation approaches were prototyped and tested against the same real-call corpus
(5 scam variants across languages/phrasings, 8-13 clean/legitimate listings including
deliberately tricky ones -- a disclosed legitimate raffle, a business bragging about
winning an award, marketing copy, currency exchange):

1. **Keyword/regex heuristic** ("prize/winner" framing AND "advance payment" framing
   both present): 3/5 recall, 0/13 false positives. Missed an English phrasing and a
   new Arabic phrasing ("مبروك"/"الفائز") not in the hand-written keyword list --
   confirms the expected weakness of keyword matching: it doesn't generalize to
   phrasing it wasn't written for, and needs ongoing list maintenance as scam language
   evolves.
2. **Targeted model question**: a direct, narrowly-scoped true/false question ("does
   this listing describe receiving something of value contingent on the recipient
   first sending money?") to `mistralai/mistral-nemotron` -- the same model and "ask one
   specific question" pattern Consistency Agent already uses (§3.3), not the
   safety-guard classifier. 5/5 recall (including both variants the keyword approach
   missed), 1/8 false positives -- the one false positive was a disclosed legitimate
   raffle (buy a ticket, enter a draw), which structurally *is* "pay money for a chance
   to receive something of value," a defensible edge case to route to review rather
   than an outright error.

## Decision

Implemented the targeted-question approach in `agents/safety_agent.py`. Only runs when
the primary safety-guard classifier already returned `safe` (after 0019's retry logic)
 -- no reason to spend the extra call on a listing already flagged unsafe by something
else. A `true` result adds a synthetic `Prize/Advance-Fee Scam` category (not part of
the safety-guard model's own taxonomy, see `docs/decisions/0012`) to `violations`,
mapped to F001 in `agents/policy_agent.py`'s `SAFETY_CATEGORY_TO_RULE`, same as the
other three F001 triggers.

Retries once on a malformed (non-true/false) response, same failure mode Consistency
Agent's `_post_for_verdict` already handles -- this is a different robustness concern
than 0019's low-confidence retry (malformed output vs. a well-formed wrong answer).

**Honest recall number, not the best-case single test:** repeating the wired-up
end-to-end check 5x each across 3 real lottery-scam variants caught **13/15 (~87%)**,
up from 0/9 before this check existed. This is a large improvement, not a complete fix
 -- the targeted check is itself a probabilistic model call and won't catch every call on
every phrasing. One test run in isolation showed a single miss on the first variant
before the 5x-repeat data corrected that impression; reporting the repeated-sample
number here rather than the more favorable single-shot result.

## Consequences

- `agents/safety_agent.py`: added `_check_prize_advance_fee_scam`, wired into
  `run_safety_agent` to run only on an already-safe verdict.
- `agents/policy_agent.py`: `SAFETY_CATEGORY_TO_RULE` gets a `"Prize/Advance-Fee
  Scam": "F001"` entry.
- SPEC.md §3.4 and §3.5 updated.
- Tests added: `test_prize_advance_fee_scam_detected_when_primary_classifier_missed_it`,
  `test_unsafe_listing_skips_prize_scam_check`,
  `test_prize_advance_fee_scam_violation_maps_to_f001`; existing safe-path tests updated
  for the new mandatory second call.
- Cost: one extra API call per listing that the primary classifier calls safe (the
  common case) -- accepted as the trade-off for closing a confirmed, systematic fraud
  detection gap; not run at all on listings already flagged unsafe.
