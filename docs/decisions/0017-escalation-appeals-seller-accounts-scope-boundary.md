# 0017. Escalation, appeals, and seller accounts: portable logic vs. placeholder storage

## Status
Accepted (scoping only -- no implementation in this change)

## Context
Walked through a day-in-the-life description of the moderator workflow this project
already supports. That surfaced two distinct kinds of gap:

**Friction in the existing flow** (not addressed by this ADR, noted for later):
no case ownership/locking between multiple human moderators (`FOR UPDATE SKIP LOCKED`
only guards the automated pipeline's claim of `PENDING_MODERATION`, §2.1); no
SLA/aging or severity sort in `--queue`; no batch actions; no persisted "where was I"
across sessions; `sellerPreviousViolations` is a static snapshot copied onto each
listing at submission time, not a counter anything increments when a REJECT happens.

**A structural gap, this ADR's actual subject**: `schema.sql` has no `sellers` table.
Seller data is a JSONB blob embedded per `listings` row. This means there is nothing
to attach an account-level action to (no seller identity independent of a listing),
no way to see "every listing from this seller" without an ad hoc scan, and no appeal
mechanism -- `record_decision` (§5/§6) moves a listing to a terminal status with no
defined transition back out; `rerun_analysis` re-runs agents on a terminal listing,
but that's re-analysis, not a seller-initiated appeal with its own audit requirements.

**Real-world pattern, checked rather than assumed** (trust & safety literature):
escalation is typically tiered -- automated classification, confidence-based routing,
then a *separate* senior-review tier for high-stakes or repeat cases, not just a
binary auto/human split. Account-level consequences escalate progressively (warning
→ listing suppression → account suspension → termination), usually keyed to a strike
count within a time window, not a single static field. Appeals are a first-class,
tracked flow with their own metrics (appeal rate, overturn rate, time-to-decision),
not a side effect of re-running the automated pipeline. A named mitigation against
automation bias: randomly spot-check *non-appealed* automated decisions too, not only
respond to the ones people contest. (Sources: PartnerHero's moderation-appeals best
practices, Unit21's account-suspension writeup, flagged.online on escalating to human
review -- general industry pattern, not project-specific citations.)

**The actual question this ADR answers**: how much of this can be built now, given
this project doesn't know what real customer backend it might eventually integrate
with (if any)? A real marketplace almost certainly already owns seller/account
identity, KYC, and enforcement state somewhere -- building an opinionated `sellers`
table now risks it being wrong in ways that force a rewrite once the real
integration contract is known.

## Decision
Split what gets built now from what's deferred, along a logic/storage boundary:

- **Portable now** (backend-agnostic, valuable regardless of eventual integration):
  the escalation-tiering *rules* (what makes a case senior-review-worthy), the
  appeal *state machine* (what states/transitions exist, who can trigger them), and
  the audit-trail *shape* (this project's existing append-only artifact log pattern,
  §5, already generalizes to appeal records without modification).
- **Explicit placeholder, not a foundation to build on top of**: any `sellers` table
  this project adds is a stand-in for whatever seller/account system a real
  integration would actually own -- not assumed to be the source of truth. Treated
  the same way ADR 0009 scoped moderator auth ("adequate for now, here's exactly what
  assumption breaks it") rather than silently treated as permanent.
- This ADR documents intent only. SPEC.md §8 (new) captures the planned state machine
  extension, seller-account model, and CLI tools, explicitly marked **not yet
  implemented**. No code changes in this PR -- implementation is a separate,
  follow-up scoping decision once this direction is confirmed.

## Consequences
- If/when a real customer backend's seller/account API becomes known, integrating it
  means writing a sync/adapter layer and migrating this project's placeholder
  `sellers` table -- a bounded, mechanical refactor. The escalation rules and appeal
  state machine built against it should not need to change, since they operate on
  seller *attributes* (status, violation count), not on how those attributes are
  stored or sourced.
- The friction points noted above (case locking, SLA visibility, batch actions) are
  real but separate from this ADR's scope -- they affect the existing single-listing
  flow regardless of seller-account work, and can be picked up independently.
- `.claude/skills/inspect-listing/SKILL.md` will need extending once escalation
  tiers/appeal states/seller entities exist (e.g. `--queue` gaining an escalation-tier
  or appeal-status column, a seller-level view) -- flagged in the skill's Notes
  section now as a forward reference, not built yet.

## Update: first piece implemented

The placeholder `sellers` table and live `violation_count` counter (§8.3) landed as
the first PR, confirmed with the human to build one dependency-ordered piece at a
time (sellers table → escalation → appeals → account-action tools). Verified against
real Postgres: `violation_count` increments correctly on both the automated
Decision-Agent REJECT path and a moderator's `reject_listing` override, and is left
untouched by APPROVE. Decision Agent's confidence fusion still reads only the static
`sellerPreviousViolations` snapshot, per this ADR's decision to keep that a separate,
later change rather than bundling it into the additive foundation piece.

## Update: second piece implemented (escalation tier)

`ESCALATED` (§2/§8.2) and `cli.tools.escalate_case` (§8.4) landed second, per the
agreed sequencing. `pipeline.DECISION_TO_STATUS` gained `"ESCALATE": "ESCALATED"` --
moderator-only, the automated Decision Agent's `_route` (§4) never produces it.
Resolving an escalated case needed no new code: `approve_listing`/`reject_listing`
already transition any listing regardless of current status, confirmed against real
Postgres (escalate a PENDING_REVIEW case → ESCALATED, then `reject_listing` on it →
REJECTED, with the seller's violation count incrementing once, from the reject, not
the escalation). Also confirmed: re-escalating an already-`ESCALATED` listing is
rejected rather than silently re-escalated. As flagged when this was scoped, there is
still no senior-reviewer role distinction in the `moderators` table -- any active
moderator can resolve an escalated case, same as a `PENDING_REVIEW` one.
