# 0007. Poller service: instance state, bounded runs for testability, cycle-boundary shutdown

## Status
Accepted

## Context
`pipeline.poll_and_process()` only ran when manually invoked once -- nothing kept the
system "operating" continuously the way SPEC.md §7's end-to-end flow describes. Adding
a real long-running loop raised a few design questions worth recording:

1. How do you unit-test an infinite loop without mocking `time.sleep` forever or
   actually sleeping in the test suite?
2. If a SIGINT/SIGTERM arrives mid-batch (multiple listings claimed in one
   `poll_and_process()` call), does the service abort immediately or finish?
3. Module-level `_shutdown` flag vs. instance state?

## Decision
- `PollerService` is a class, not module-level functions with a module-level
  `_shutdown` flag -- so tests can instantiate a fresh instance per test with no
  state leaking between them, and don't need to reset a global after each test.
- `run(max_iterations=None)` -- production usage leaves it `None` and relies on a
  signal; tests pass a small number and mock `time.sleep`/`time.monotonic`, making the
  loop logic itself fully testable without ever really sleeping or running forever.
- Shutdown is checked once per cycle (top of the `while` loop), not mid-batch. A
  signal arriving while `poll_and_process()` is processing several already-claimed
  listings lets that batch finish rather than aborting partway through one. This is
  bounded by `POLL_BATCH_SIZE` (default 10) and each listing's own processing is a
  handful of API calls, so the worst-case shutdown latency is small and predictable,
  not "wait for an arbitrarily long batch."

## Consequences
- Verified against a real throwaway Postgres and the real NVIDIA API, not just the
  offline unit tests: ran the service for bounded iterations and confirmed it
  actually claimed and processed all 5 seeded listings, confirmed the sweep path by
  forcing a stale `PROCESSING` row and watching it reset, and sent a real `SIGINT` to
  a running process and confirmed a clean exit (code 0) after the in-flight cycle.
- No mid-batch cancellation -- acceptable for this project's scale; would need
  revisiting if `POLL_BATCH_SIZE` or per-listing processing time grows large enough
  that shutdown latency becomes noticeable.
