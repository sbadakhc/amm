# 0030. Heuristic backstop for title_vs_description when the model check is skipped

## Status
Accepted

## Context

Live during a moderator-queue walkthrough (2026-08-29): `LST-C6D133`'s title read
"Apple iPhone 16 Pro Max" while its description read "Brand new Samsung Galaxy S24,
factory sealed" -- an obvious, direct contradiction. The `title_vs_description` check
that exists specifically to catch this got skipped (0028's fail-open) because
`mistral-nemotron` was down during that pipeline run. `description_vs_images` still
flagged inconsistent (pushing confidence to 0.64, still routed to `REVIEW`), but the
most direct signal for exactly this failure mode went silent, and Policy Agent found
no rule match at all.

Asked directly: should obvious cases like this skip human review entirely? No --
covered separately (that's a routing/severity question, C004 stays Medium on
purpose, see the conversation this ADR follows). But the *coverage gap* -- this
specific check having zero fallback when its model is down -- was worth fixing.

## Decision

`_heuristic_title_vs_description_contradiction(title, description)`: a narrow,
manually-maintained keyword heuristic, used **only** as a fallback when the real
`_text_check` call for `title_vs_description` returned `None` (skipped, 0028) --
never overriding a real model verdict.

Prototyped and stress-tested (offline, no API calls) against 10 cases before writing
the real implementation -- both the real target case and plausible false-positive
traps, since ADR 0020 already established that naive keyword heuristics for this
kind of task tend to misfire on ordinary language:

- A flat "any two disjoint brand names" check correctly caught the target case, but
  false-positived on comparison language ("sounds better than Apple AirPods"),
  compatibility language ("also compatible with Samsung chargers"), and barter
  listings naming both sides intentionally ("trade my Samsung for your iPhone") --
  all common, legitimate marketplace phrasing, not edge cases.
- Adding a small set of disqualifying phrase patterns (comparison/compatibility/
  barter framing) fixed all of the above while still catching the target case and a
  second synthetic one, 10/10 on the test set.

**Only ever asserts the negative finding.** The heuristic returns `False` for
everything it doesn't recognize, including genuine contradictions it structurally
can't see (wrong storage size, wrong condition, anything that isn't a named
competing brand). A `False` result is **not** treated as "confirmed consistent" --
`run_consistency_agent` still records the check as skipped in that case, not passing.
Only a `True` (an explicit competing brand found) gets recorded, and at
`HEURISTIC_BACKSTOP_CONFIDENCE` (default 0.7, env-configurable) -- deliberately below
typical real-call model confidence (usually >=0.9), so it doesn't read as equally
certain as an actual model judgment. New `method` field (`"model"` vs
`"heuristic-backstop"`) on each `checks` entry makes which path produced a given
finding visible to a moderator or `pipeline_stats.py`, not silently indistinguishable.

Rejected: running the heuristic unconditionally (risks the false-positive traps above
even when the real check succeeds and says "consistent" correctly -- no reason to
second-guess a working model call with a blunter tool); treating a heuristic miss as
"confirmed consistent" (would silently suppress genuine contradictions the heuristic
can't see, worse than the status quo of "no signal"); a broader NLP-based heuristic
(scope creep for a fallback path -- brand-name matching is the one pattern simple
enough to get right without a model, per the same reasoning ADR 0020 used to reject a
keyword heuristic for the much fuzzier prize-scam-intent problem).

## Consequences

- `agents/consistency_agent.py`: new `_BRAND_GROUPS`,
  `_BRAND_MENTION_DISQUALIFIERS`, `_detect_brand_groups`,
  `_heuristic_title_vs_description_contradiction`, `HEURISTIC_BACKSTOP_CONFIDENCE`.
  `run_consistency_agent`'s `title_vs_description` handling now branches three ways:
  model succeeded / model skipped + heuristic hit / model skipped + heuristic found
  nothing. `_record` gained a `method` parameter.
- New `method` field on `ConsistencyAgent.payload.checks[].{}`  entries.
- SPEC.md §3.3 updated.
- Tests added: `test_heuristic_backstop_catches_competing_brand_when_model_skipped`,
  `test_heuristic_backstop_does_not_false_positive_on_comparison_language`,
  `test_heuristic_backstop_only_runs_when_model_check_skipped`,
  `test_heuristic_backstop_regression_cases` (the 10-case prototype set, kept as a
  permanent regression suite).
- Verified against the real, live `LST-C6D133` case (reproduced with
  `mistral-nemotron` genuinely down at test time): heuristic fired, `checks` shows
  `method: "heuristic-backstop"`, `inconsistencyScore: 0.7`.
- Known limitation, stated plainly: this only ever catches an *explicit named
  competing brand* -- a wrong storage size, wrong condition, or any contradiction
  that isn't a brand name still goes unfound during a `mistral-nemotron` outage,
  exactly as before this change. Not a general contradiction detector, and not meant
  to become one -- see "Rejected" above.
