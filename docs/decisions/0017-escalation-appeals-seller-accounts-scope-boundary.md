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

## Update: third piece implemented (appeal flow), narrowed twice before building

`APPEAL_REQUESTED` (§2/§8.2) and `cli.tools.request_appeal`/`resolve_appeal` (§8.4)
landed third. Discussed and narrowed twice with the human before implementing, both
times per this ADR's "don't build what we won't use" principle already in play for
the sellers-table scoping:

1. **No `initiatedBy: seller | moderator` field.** This system has no seller-facing
   surface at all (SPEC.md: "no web UI"). In a real system appeals originate from
   the seller, but here `request_appeal` is a plain moderator-invoked tool relaying
   whatever reached a human through some other channel (the marketplace app,
   support, etc.) -- there's no seller identity/auth in this system to back a real
   `initiatedBy` distinction with, so it wasn't added.
2. **Reused `APPROVED`/`REJECTED` as the appeal's actual outcome**, not this ADR's
   originally-scoped separate `APPEAL_APPROVED`/`APPEAL_DENIED` terminal states --
   nothing needs a listing in any status but those two to determine liveness, and
   §5's append-only artifact log already distinguishes an appeal resolution via its
   `version` field (`"moderator-appeal-resolution"`) without new status vocabulary.

Two more decisions made and stated plainly rather than silently assumed: only
`REJECTED` listings can be appealed, not `APPROVED` (no real use case for contesting
an approval); and denying an appeal must not increment the seller's violation count
a second time (`record_decision` gained a `count_violation` parameter for this,
defaulting to the existing behavior everywhere except `resolve_appeal`'s denial
path).

Verified against real Postgres, all four scenarios: appeal denied (REJECTED →
APPEAL_REQUESTED → REJECTED, violation count stayed at 1, not double-counted);
appeal approved (REJECTED → APPEAL_REQUESTED → APPROVED, overturning); guard against
appealing an `APPROVED` listing; guard against resolving a non-`APPEAL_REQUESTED`
listing; guard against an invalid `resolve_appeal` decision value.

After this PR merged, the human questioned whether appeals should have been built at
all ("didn't we say we didn't want appeals?") -- the "don't build what we won't use"
principle used to scope the sellers table could reasonably be read either narrowly
(don't model seller identity/auth, which is how it was applied) or broadly (don't
build appeals at all, since nothing feeds an appeal into this system yet). Decided to
keep it as built after discussing the ambiguity directly -- moderator-invoked-only,
zero seller-identity assumptions, still useful for relaying an appeal through any
future channel. Lesson captured for next time: confirm which reading of a general
scoping principle is meant *before* building, not after
([[confirm-scope-explicitly-not-inferred]]).

## Update: fourth piece implemented (account actions) -- all of §8 now built

`list_seller_cases`, `suspend_seller`, `reinstate_seller` (§8.4) landed last, closing
out §8. Confirmed explicitly with the human before starting (applying the lesson
above): unlike appeals, these have no external-channel dependency -- a moderator can
use "show me everything from this seller" or "suspend this account" today, from data
already in Postgres.

`sellers` gained `status_reason`/`status_changed_by` columns (not in the original
§8.3 sketch) so a status change carries the same audit completeness as every other
mutating action in this project (`reject_listing`, `escalate_case`, etc. all require
a `reason`) -- added a CHECK constraint restricting `status` to
`ACTIVE`/`SUSPENDED`/`TERMINATED` at the same time. `suspend_seller` only valid from
`ACTIVE`, `reinstate_seller` only valid from `SUSPENDED`. No `terminate_seller` tool
-- `TERMINATED` is a valid schema value nothing produces yet, not requested. Neither
tool cascades to the seller's existing listings (e.g. auto-rejecting pending ones on
suspension) -- each listing is still decided independently, stated as a known
simplification rather than silently assumed.

Verified against real Postgres: suspend ACTIVE → SUSPENDED with reason/moderator
recorded; reinstate SUSPENDED → ACTIVE; guards against suspending an
already-suspended seller, reinstating an already-active seller, an unknown seller,
and an unknown moderator all raise correctly.
