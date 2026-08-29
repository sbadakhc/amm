# 0027. Pipeline accuracy/performance stats from the existing artifact log

## Status
Accepted

## Context

Asked directly: "are we collecting stats that we can use to understand accuracy and
performance?" No. The raw ingredients were already sitting in the `artifacts` table
(§5) — `produced_at` timestamps, `confidence` scores, and (via `record_decision`,
`cli/tools.py`) a marker for every human decision — but nothing aggregated them.
There is no external ground truth for "is the automated decision correct" in this
system (no labeled outcome data flows back in), so the closest available accuracy
proxy is **how often a moderator's own verdict differs from what the automated
pipeline decided** — i.e. an override/disagreement rate, not a true precision/recall
number. That's a real limitation of what's measurable here, not an oversight.

## Decision

`db.get_stats(since=None)` — one function, several SQL queries against the existing
`artifacts`/`listings` tables, no new schema, no new metrics-store dependency. Exposed
via `cli.tools.get_stats()` (thin passthrough, consistent with every other tool in §6)
and a moderator-facing markdown report, `scripts/pipeline_stats.py`.

Design choices, each because the obvious alternative was wrong or untestable:

- **Automated vs. moderator decisions are distinguished by fields already on
  `DecisionAgent` artifacts**, not a new column: `version = 'fusion-v1'` (only
  `agents/decision_agent.py` sets this) marks automated; a non-null
  `payload->>'moderator'` marks anything from `cli.tools.record_decision` (used by
  every one of approve/reject/escalate/request_appeal/resolve_appeal). No migration
  needed.
- **Override rate only counts listings where both an automated APPROVE/REJECT *and* a
  later differing moderator APPROVE/REJECT exist** — not every `PENDING_REVIEW`
  listing a moderator resolves. The automated pipeline routing a listing to `REVIEW`
  and a moderator then deciding APPROVE or REJECT isn't a disagreement; `REVIEW` was
  never a verdict to agree or disagree with. Conflating the two would make the
  override rate meaningless (it would mostly just measure "how much of the review
  queue got resolved," not "how often was the automation wrong"). `REVIEW`-routed
  outcomes are reported separately (`humanReviewOutcomes`) as context, not folded into
  the override number.
- **`ESCALATE`/`REQUEST_APPEAL` are excluded from both metrics** — they're
  intermediate steps in the state machine (§8.2), not a verdict. Only the *latest*
  moderator-issued APPROVE/REJECT per listing (`DISTINCT ON ... ORDER BY produced_at
  DESC`) counts as "the" human verdict, so an escalation followed by a senior
  reviewer's final call is correctly attributed to the final call.
- **Latency is measured as (earliest of Evidence/Consistency/Safety's `produced_at`)
  to (the automated `DecisionAgent` artifact's `produced_at`)**, per listing, then
  averaged — the wall-clock span `pipeline.run_fusion`'s parallel fan-out/fan-in (§7)
  actually takes, not a sum of per-agent times (which would double-count the
  concurrent portion).
- **Failures are grouped by literal error message**, not a fixed taxonomy — every
  failure type this project has hit so far (`410 Gone` on a dead model, a timeout, a
  500) produces a distinct, stable string per cause, so grouping by the raw message
  clusters correctly without needing to anticipate every future failure shape.
- **`since` is a plain optional ISO timestamp filter on `listings.created_at`**, not a
  rolling-window default — an all-time view by default is more useful for a project
  this size (five-digit listing counts, not the kind of volume where "always show
  last 24h" is the sane default), and every query already threads `since` through
  identically, so a caller who wants a window just passes one.

Rejected: a separate metrics/analytics table or external store — the artifact log
already has everything needed, and this project's data volume doesn't remotely
justify the operational overhead of a second system.

## Consequences

- `db.py`: new `get_stats(since=None)`.
- `cli/tools.py`: new `get_stats(since=None)`, thin passthrough.
- New file: `scripts/pipeline_stats.py` -- prints the same data as a markdown report
  (`python3 scripts/pipeline_stats.py [--since <ISO timestamp>]`).
- SPEC.md §6 tool table updated.
- Verified against the real seeded Postgres instance (§ AGENTS.md's habit) with a mix
  of automated decisions and moderator overrides, not just reasoned about in the
  abstract.
- **Known limitation, stated plainly**: this measures *consistency* between the
  automated pipeline and moderators, not *correctness* against any ground truth --
  there isn't one flowing into this system. A 0% override rate could mean the
  pipeline is right every time, or that moderators are rubber-stamping it; this stat
  alone can't tell the difference. It's a useful signal, not a substitute for the
  real-call eval harnesses (0021, 0023) that test against corpora with actually-known
  outcomes.
