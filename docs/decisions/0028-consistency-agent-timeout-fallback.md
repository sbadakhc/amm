# 0028. Consistency Agent checks fail open on timeout, not blocking

## Status
Accepted

## Context

0022 gave Safety Agent's optional prize-scam second-opinion check a fail-open
timeout after `mistral-nemotron` was confirmed to hang indefinitely with no response
and no timeout of its own. Consistency Agent uses the same model (its
`title_vs_description` text check) and the vision model (its three image-based
checks), with the same 30s-timeout, raise-on-failure design 0022 replaced -- but
never got the same fix, because Consistency Agent's checks aren't optional the way
the prize-scam check is: they're what `inconsistencyScore` is built from. A skip here
needed a real design answer (what does a skipped check contribute to the score?), not
just a shorter timeout, so it was deliberately deferred while 0022/0025/0026/0027 got
built first.

Cost of leaving it unfixed, confirmed the same day: a live pipeline run against real
seeded demo listings hit `mistral-nemotron`'s ongoing flakiness (500s, timeouts, 429s
across the session) and 4 of 5 listings landed in `PENDING_REVIEW` via
`pipeline.py`'s generic error handler -- zero Evidence/Consistency/Safety/Policy
findings, same "silently degrades to a review queue with no help provided" outcome
0025 fixed for the vision-model case, here caused by a transient backend issue
instead of a permanent one.

## Decision

Same fail-open shape as 0022, applied to every Consistency Agent check independently:

- `_post_for_verdict` returns `tuple[bool, float] | None` -- `None` on a
  timeout/connection failure (`requests.exceptions.RequestException`) or two
  malformed responses in a row, logged via a new `amm.consistency_agent` logger.
  Previously it raised on both.
- New `CONSISTENCY_CHECK_TIMEOUT` env var (default 10s, same reasoning as 0022's
  `PRIZE_SCAM_CHECK_TIMEOUT` -- confirmed real-call latency is normally well under
  2s), replacing the old shared 30s.
- `_aggregate_across_images` drops skipped (`None`) per-image results before
  aggregating; if every image's check failed, the whole aggregate is `None` rather
  than fabricated from zero evidence.
- **The actual design answer for "what does a skipped check contribute to
  `inconsistencyScore`": nothing.** A skipped check is excluded from the mean
  entirely -- not counted as consistent (would suppress a real inconsistency) and not
  counted as inconsistent (would invent one from a check that never ran). This is the
  same principle 0022/0024 already established for Safety Agent: a failed check must
  not manufacture a verdict in either direction.
- New `checksSkipped` field on the `ConsistencyAgent` payload (alongside the existing
  `checks`) lists which pairs were skipped, so this is visible to a moderator reading
  `explain_case` or to `pipeline_stats.py` (0027) -- not silently indistinguishable
  from "checked, found consistent."
- If *every* check was skipped, `inconsistencyScore` defaults to `0.0` (no evidence
  of inconsistency) rather than raising on `statistics.mean([])`. This is a real,
  stated trade-off, not an oversight: it means Decision Agent's `confidence = 1 -
  inconsistency_score` (§4, no policy matches case) reads a fully-degraded
  Consistency Agent the same as a genuinely consistent listing. Accepted because the
  alternative (treating "couldn't check" as "definitely inconsistent") would be
  worse -- it would route every listing through a `mistral-nemotron` outage to
  REVIEW regardless of actual content, turning a transient backend issue into a
  blanket false-positive generator across the whole queue.

Rejected: retrying through the outage (0022 already established this doesn't help a
true hang); treating a skip as automatic inconsistency (the false-positive-storm
problem above); a separate "degraded" status on the artifact instead of a
per-check list (more machinery than needed -- `checksSkipped` being non-empty already
says this artifact is degraded).

## Consequences

- `agents/consistency_agent.py`: `_post_for_verdict`/`_text_check`/`_vision_check`
  return `Optional`; `_aggregate_across_images` drops `None`s; `run_consistency_agent`
  routes each check through a new `_record` helper that appends to `checks` or
  `checksSkipped`; `inconsistencyScore` defaults to `0.0` on an empty mean. New
  `CONSISTENCY_CHECK_TIMEOUT` env var and `amm.consistency_agent` logger.
- SPEC.md §3.3 updated.
- `tests/test_consistency_agent.py`:
  `test_raises_after_two_consecutive_rambling_responses` rewritten as
  `test_skips_after_two_consecutive_rambling_responses` (behavior changed from raise
  to skip); added `test_skips_on_timeout_instead_of_raising` and
  `test_skipped_check_does_not_affect_other_checks`.
- Fixed two unrelated pre-existing test-isolation gaps discovered while verifying this
  change against a real, non-empty dev Postgres instance (populated by an earlier live
  demo this session): `test_find_similar_by_embedding_ranks_by_cosine_distance`
  assumed no other embedded listings existed; the `stats_listings` fixture's tests
  (0027) asserted unscoped global counts. Both now scope to their own fixture data
  rather than assuming an empty shared database.
- Known trade-off, stated above: a total `mistral-nemotron` outage makes Consistency
  Agent read as "fully consistent" (score 0.0) rather than "couldn't check" in the
  Decision Agent's confidence math, even though `checksSkipped` records what actually
  happened. Not revisited here -- Decision Agent's fusion algorithm (§4) doesn't
  currently look at `checksSkipped` at all, only `inconsistencyScore`; teaching it to
  route degraded-signal listings differently is a separate, larger change.
