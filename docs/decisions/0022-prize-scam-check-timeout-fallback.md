# 0022. Prize-scam second-opinion check fails open on timeout, not blocking

## Status
Accepted

## Context

Live real-call testing on 2026-08-29 found `integrate.api.nvidia.com`'s
`mistralai/mistral-nemotron` backend -- the model `_check_prize_advance_fee_scam`
(0020) calls -- intermittently hangs: TLS handshake and request both complete, but the
server never sends a response at all, no error, no timeout of its own. Repeated probes
minutes apart alternated between healthy (~0.4-4s) and fully hung past 60s, with no
official NVIDIA status page to check (`status.nvidia.com` covers licensing, not the
inference API) -- developer-forum reports (504s, 429s, unannounced model changes)
confirm this is a recurring pattern for this API, not a one-off.

Before this fix, `_check_prize_advance_fee_scam` used the same 30s timeout as the
primary safety-guard classifier, with a 2-attempt retry loop that only handled
malformed *responses*, not missing ones -- a hung backend meant up to ~60s blocked per
listing, for a check that runs on every listing the primary classifier already calls
safe (the common case, per 0020).

## Decision

`_check_prize_advance_fee_scam` now:
- Uses a shorter, dedicated timeout (`PRIZE_SCAM_CHECK_TIMEOUT`, default 10s, env var
  per 0008's pattern) instead of reusing the primary classifier's 30s -- real-call
  latency for this check is normally well under 2s, so 10s is already generous
  slack, not a tight cutoff.
- Catches `requests.exceptions.RequestException` (covers timeout and connection
  failures) and returns `None` instead of raising.
- Also returns `None` after two malformed (non-true/false) responses, instead of
  raising -- same fail-open treatment as a network failure, since the underlying
  concern (the check itself is unavailable) is the same.
- Logs a `logger.warning` (new `amm.safety_agent` logger) on skip, so it's visible in
  service logs rather than silently absorbed.

`run_safety_agent` treats `None` as "skip, not not-a-scam": the primary classifier's
verdict stands unchanged. This never *invents* a violation and never *suppresses* one
the primary classifier already found -- it only means one specific fraud pattern
(prize/advance-fee scams the primary classifier structurally misses per 0020) goes
unchecked for that one listing if the backend was unavailable at call time.

Rejected: retrying through the outage (doesn't help against a true hang, only doubles
the wait), and treating a skip as "flag for review" (would fail closed on every
transient blip, defeating the purpose of an optional second opinion).

## Consequences

- `agents/safety_agent.py`: `_check_prize_advance_fee_scam` returns
  `tuple[bool, float] | None`; `run_safety_agent` checks for `None` before unpacking.
- New env var `PRIZE_SCAM_CHECK_TIMEOUT` (default 10), documented in `.env.example`.
- New `amm.safety_agent` logger, first use of `logging` inside an agent module (the
  poller service already used it, agents hadn't needed it before).
- Tests added: `test_prize_scam_check_timeout_skips_instead_of_raising`,
  `test_prize_scam_check_connection_error_skips_instead_of_raising`.
- SPEC.md §3.4 updated.
- Known trade-off, accepted: during a `mistral-nemotron` outage, prize/advance-fee
  scam recall silently reverts to the primary classifier's pre-0020 blind spot (0/9 in
  0020's testing) for the duration of the outage. No alerting beyond the log line
  exists yet -- acceptable for now given this is one signal among several (Policy
  Agent's other F001 triggers, Consistency Agent, human review) rather than the only
  fraud defense, but worth revisiting if outages prove frequent enough to matter.
