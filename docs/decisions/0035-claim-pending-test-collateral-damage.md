# 0035. `claim_pending` integration tests were stranding real dev data

## Status
Accepted

## Context

Found live via `/housekeeping`: right after a clean `scripts/dev-db.sh up` +
`generate_synthetic_data.py` reseed (5 fresh `PENDING_MODERATION` demo listings),
running the project's own documented `pytest -v` (README, AGENTS.md §5) against
that same real `DATABASE_URL` left all 5 listings stuck in `PROCESSING` with zero
artifacts -- not processed, not explainable, just stranded.

Root cause: `tests/test_db_integration.py`'s `seeded_listing` fixture inserts its
own uniquely-named `LST-TEST-*` row, but `test_claim_pending_moves_to_processing`,
`test_claim_pending_does_not_reclaim_already_processing`, and
`test_sweep_stale_processing_resets_old_rows` all call the real
`db.claim_pending(batch_size=50)` -- which claims *any* `PENDING_MODERATION` row
system-wide (§2.1's locking design, correctly under test), not just the fixture's
own. Against an empty test DB this is harmless; against a shared dev DB with real
demo data sitting in `PENDING_MODERATION`, it collaterally claims that data too and
leaves it in `PROCESSING`, since the tests never run the full pipeline on what they
claim. Nothing swept it back: `db.sweep_stale_processing` exists for exactly this
recovery, but is only ever called from `service.py`'s continuous loop, not from the
one-shot `/run` (`poll_and_process`) most of this project's actual usage goes
through.

## Decision

- Immediate: swept the 5 stranded listings back to a sane state with
  `db.sweep_stale_processing(timeout_minutes=0)` (routes to `PENDING_REVIEW`, its
  designed behavior for anything found stuck in `PROCESSING` -- not back to
  `PENDING_MODERATION`, since a listing that got claimed and never finished is
  treated as suspicious, not silently retried).
- Real fix: new `restore_collateral_claims` fixture in `test_db_integration.py`,
  used by the three tests that call `claim_pending` directly. Resets anything still
  `PROCESSING` after the test back to `PENDING_MODERATION` in teardown, so a shared
  dev DB's real data is restored to its pre-test state regardless of what the test
  collaterally claimed. Doesn't change `claim_pending`'s actual production
  behavior or scope it down for the test -- the whole point of these tests is
  exercising the real system-wide claim semantics against a real database, not a
  narrowed stand-in.

## Consequences

- Running `pytest -v` against a real `DATABASE_URL` (the project's own default,
  documented workflow) no longer has a side effect of corrupting whatever demo/dev
  data happens to be sitting in `PENDING_MODERATION` at the time.
- `db.sweep_stale_processing` is still only wired into `service.py`'s loop, not
  `/run`'s one-shot `poll_and_process` -- noted here as a real gap, not solved: a
  listing stranded in `PROCESSING` by a crash mid-pipeline (not just a test) stays
  stuck until either `service.py` runs or someone calls the sweep manually. Out of
  scope for this fix, which is specifically about test isolation.
