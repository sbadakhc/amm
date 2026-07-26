# 0014. CONSISTENCY_THRESHOLD tuned from real model-call data (0.30 -> 0.48)

## Status
Accepted

## Context
`CONSISTENCY_THRESHOLD` (Policy Agent's C004 rule, §3.5) had been 0.30 since the
original spec -- documented explicitly as "a first pass from observed scores on the
demo's synthetic data, not tuned against real traffic." This project has no
production listings to observe real traffic from. Discussed with the human what
"tune against real traffic" should mean here; agreed it means real NVIDIA API calls
against the demo's current data (real photos where shipped per `docs/decisions/0013`,
synthetic elsewhere), gathered systematically, not literal production traffic.

**Data.** 8 real `run_consistency_agent` calls per demo scenario (40 total), using
the currently shipped images for each:

| Scenario | Mean | Range | Should trigger C004? |
|---|---|---|---|
| `inconsistent` | 0.652 | 0.505-0.712 | **Yes** -- genuine title/description contradiction |
| `weapon` | 0.392 | 0.360-0.461 | No (routes via W001 regardless) |
| `clean` | 0.374 | 0.315-0.447 | No |
| `counterfeit_brand` | 0.299 | 0.203-0.422 | No (routes via C001) |
| `risky_seller` | 0.299 | 0.089-0.432 | No |

At the old threshold (0.30), `clean` matched C004 in 8/8 samples and `risky_seller`
was roughly a coin flip -- the threshold sat inside the "shouldn't trigger" cluster,
not above it. The data shows a clean empirical gap: every "shouldn't trigger" sample
tops out at 0.461 (weapon's max); every "should trigger" sample (`inconsistent`)
starts at 0.505. Any threshold in [0.46, 0.50] separates them perfectly in this data.

## Decision
Set the default to **0.48** (midpoint of the gap, discussed with and confirmed by the
human before changing it -- this is a false-positive/false-negative tradeoff judgment
call, not a purely mechanical one). `agents/policy_agent.py`'s `CONSISTENCY_THRESHOLD`
env var default updated; still configurable via env var or per-call override,
unchanged mechanism (§4, `docs/decisions/0008`).

**Explicitly out of scope, flagged to the human before implementing:** this fixes
Policy Agent's C004 *rule accuracy* only -- it does not, on its own, get `clean` to
auto-approve. Auto-approve is gated by a separate, stricter bar
(`AUTO_APPROVE_THRESHOLD`, requires `1 - inconsistencyScore >= 0.90`, i.e. raw score
below 0.10), which `clean`'s ~0.37-0.43 measured range still fails regardless of this
threshold. That remains the open problem from `docs/decisions/0013`, unaffected by
this change. Verified directly: after this change, `clean` no longer matches any
Policy rule (previously falsely matched C004), but still routes to REVIEW at ~0.57
confidence, not APPROVE -- exactly as expected, not a regression.

## Consequences
- `counterfeit_brand` and `risky_seller` no longer risk a spurious C004 match
  alongside their intended rule (C001) or no rule at all -- confirmed via a full
  pipeline run against real Postgres: `counterfeit_brand` -> `["C001"]` only,
  `risky_seller` -> `[]`, `inconsistent` -> `["C004"]`, `weapon` -> `["W001"]` (REJECT,
  unaffected), `clean` -> `[]` (REVIEW, not APPROVE -- see above).
- `tests/test_policy_agent.py`'s threshold-override test comment updated to reference
  the new default; behavior unaffected since that test uses an explicit override.
- Like the original 0.30, this value is only as good as the demo scenarios it was
  measured against -- five scenarios, not a representative production distribution.
  Revisit once real listings exist to observe, same caveat the original threshold
  carried.
