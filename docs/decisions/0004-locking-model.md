# 0004. Claim listings with `FOR UPDATE SKIP LOCKED`, no broker

## Status
Accepted

## Context
The architecture (§1) is explicitly single-process fan-out/fan-in with no broker and
no separate API service. That still leaves a real question: if more than one poller
instance runs (or a poller restarts while a previous run is still in flight), what
stops two workers from claiming and processing the same `PENDING_MODERATION` listing
twice? This was flagged as the second highest-risk gap in an early spec review,
alongside the fusion formula (0003).

## Decision
The poller claims work in one atomic transaction (SPEC.md §2.1, `db.claim_pending`):

```sql
UPDATE listings SET status = 'PROCESSING', updated_at = now()
WHERE listing_id IN (
    SELECT listing_id FROM listings WHERE status = 'PENDING_MODERATION'
    ORDER BY created_at LIMIT :batch_size FOR UPDATE SKIP LOCKED
)
RETURNING *;
```

`FOR UPDATE SKIP LOCKED` means a row already locked by another in-flight transaction
is skipped, not blocked on -- safe for multiple concurrent pollers with zero
additional coordination.

A stale-claim sweep (`db.sweep_stale_processing`) resets any row stuck in
`PROCESSING` past a lease timeout (default 5 minutes) back to `PENDING_REVIEW` --
the same terminal-on-error path as an in-flight agent failure, not a special case.
This requires the `updated_at` column added to `listings` in `schema.sql`.

## Consequences
- No lock table, no broker, no distributed coordination service -- consistent with
  the "single process, no broker" architecture goal, just extended to make multiple
  poller instances safe.
- A crashed worker's claimed listings recover automatically (after the lease timeout)
  rather than sitting in `PROCESSING` forever.
- Verified against a real Postgres instance, not just reasoned about:
  `tests/test_db_integration.py::test_claim_pending_does_not_reclaim_already_processing`
  and `::test_sweep_stale_processing_resets_old_rows`.
