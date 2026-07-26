# 0008. Decision/Policy thresholds: env vars, not a DB config table

## Status
Accepted

## Context
SPEC.md §4 says routing thresholds are "configurable, not hard-coded," but
`agents/decision_agent.py` and `agents/policy_agent.py` defined them as fixed Python
module constants with no way to change them short of editing code. Two real options:

1. Env vars, following the exact pattern `service.py` already established for its own
   config (`POLL_INTERVAL_SECONDS` etc.) -- read once at process start, change requires
   a restart.
2. A DB-backed config table (or admin CLI tool) -- live-editable without a restart, but
   real added complexity: a schema table, cache invalidation, something to write to it.

## Decision
Env vars (option 1): `CRITICAL_REJECT_THRESHOLD`, `AUTO_APPROVE_THRESHOLD`,
`SELLER_HISTORY_ADJUSTMENT_PER_VIOLATION`, `SELLER_HISTORY_ADJUSTMENT_CAP`
(`decision_agent.py`), `CONSISTENCY_THRESHOLD` (`policy_agent.py`) -- all read once at
import with the current values as defaults, consistent with every other piece of
config in this project (`.env` for `DATABASE_URL`, `NVIDIA_API_KEY`, the S3 vars,
`service.py`'s poll/sweep intervals).

`run_decision_agent`/`run_policy_agent` also accept the same thresholds as optional
per-call overrides (`None` -> module constant), mirroring `PollerService.__init__`'s
override pattern from `docs/decisions/0007`. This exists specifically so the loop/fusion
logic is testable by passing an explicit value rather than needing to reload a module
after monkeypatching `os.environ` -- proving the override actually changes behavior is
a much stronger test than proving the unchanged default still works.

## Consequences
- No new infrastructure (no config table, no admin tool) -- proportionate to this
  project's current scale, consistent with every other config decision made so far.
- Changing a threshold requires restarting whatever process imported the module
  (`service.py`, a one-off script, a test process) -- not a live toggle. If moderators
  need to tune thresholds without a deploy, that's a deliberate follow-up (likely
  option 2 above), not an assumed extension of this decision.
- Verified live, not just via the offline override-parameter tests: reprocessed the
  same "clean" demo listing (which sits at ~0.82-0.86 confidence, just under the
  default 0.90 auto-approve bar because of placeholder-image noise, see ADR-adjacent
  discussion in §3.3) with `AUTO_APPROVE_THRESHOLD=0.80` set before the process
  started, and watched it flip from `REVIEW` to `APPROVE` for real.
