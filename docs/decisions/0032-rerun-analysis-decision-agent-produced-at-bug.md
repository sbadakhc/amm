# 0032. Fix rerun_analysis(agent="DecisionAgent") KeyError on real Postgres rows

## Status
Accepted

## Context

Found live while re-deriving `LST-C6D133`'s decision after the title/description
heuristic backstop fix (0030): `cli.tools.rerun_analysis(listingId,
agent="DecisionAgent")` crashed with `KeyError: 'producedAt'`.

Cause: `db.latest_artifact()` returns a raw Postgres row (`RealDictCursor`) --
snake_case `produced_at`, a native `datetime` object, matching `schema.sql`'s column
name. `run_decision_agent()` expects the same shape every `run_*_agent()` function
returns -- camelCase `producedAt`, an ISO-format string -- to build its `basedOn`
references (§5: `f"EvidenceAgent@{evidence_artifact['producedAt']}"`, one per
upstream artifact). `rerun_analysis`'s `PolicyAgent` branch doesn't hit this
(`run_policy_agent` only reads `.payload`, no `producedAt`), so this path had never
been exercised — confirmed via a repo-wide search that `rerun_analysis` had zero test
coverage at all before this fix.

## Decision

New `cli.tools._with_produced_at(artifact_row)`: bridges a raw DB row to the
agent-output shape by adding `producedAt` (`.isoformat()` on the row's
`produced_at`), applied to all four upstream artifacts in `rerun_analysis`'s
`DecisionAgent` branch before calling `run_decision_agent`.

Rejected: changing `db.latest_artifact`'s return shape to camelCase globally --
every other caller (`cli.tools.record_decision`, `scripts/inspect_listing.py`) reads
`produced_at`/`payload` directly off the raw row and doesn't need `producedAt` at
all; changing the shared shape would just move the mismatch to those callers instead
of fixing it. A local bridge at the one call site that actually needs it is a smaller,
more honest fix than reshaping a function every other caller already uses correctly.

## Consequences

- `cli/tools.py`: new `_with_produced_at` helper, used in `rerun_analysis`'s
  `DecisionAgent` branch.
- New test: `test_rerun_analysis_decision_agent_reads_real_db_rows`
  (`tests/test_cli_tools_integration.py`) -- real Postgres, not mocks, the first test
  coverage `rerun_analysis` has ever had.
- Verified against the real `LST-C6D133` case that indirectly surfaced this bug:
  `rerun_analysis` now returns a correct decision with four well-formed `basedOn`
  references instead of crashing.
