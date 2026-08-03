# 0021. Checked-in real-call fraud eval harness

## Status
Accepted

## Context

Issues #54, #55, and #57 each involved real-call testing to validate Safety Agent
fraud detection, done via ad-hoc scratch Python scripts that were never checked into
the repo. That testing was methodologically sound (real API calls against the actual
production model constants, not assumptions) but had no durability: nothing re-verifies
those recall numbers (e.g. #57's 13/15 lottery-scam recall) against the live models
later, and a model provider can change behavior without notice. Discussed directly:
given this goes into production for a real prospective customer (alsoug.com), a
one-time snapshot test isn't a complete production-readiness story on its own.

The existing corpus was also narrow, built incrementally while chasing one bug at a
time: heavy on job/real-estate/lottery scams, with no true-negative coverage for
several of alsoug.com's actual listing categories (cars, barter, animals, industrial
equipment, home/furniture, general goods) and only one fraud archetype beyond
advance-fee and lottery scams.

## Decision

1. **Broadened the corpus** (`tests/test_fraud_eval.py`): one true-positive and one
   true-negative listing per representative alsoug.com category (cars, real estate,
   jobs, electronics, services, barter, home/furniture, animals, industrial equipment,
   general goods), covering fraud archetypes beyond advance-fee/lottery (pay-before-
   viewing, fake escrow, phishing link, pet-shipping scam, guaranteed-return investment
   pitch), plus three deliberately tricky true negatives (a disclosed legitimate
   raffle, a business bragging about an award, a currency-exchange ad) that stress
   precision, not just recall. Not exhaustive by design — real strengthening of this
   corpus is expected to come from pilot review-queue data once available, not further
   synthetic expansion now.

   **Post-review correction:** a "double your money in a week" investment pitch was
   initially filed as a true-negative (treated as vague marketing hype). Reviewed
   after the first eval run flagged it — an unrealistic guaranteed-return promise is
   itself a classic scam pattern, not just aggressive marketing, so it was re-labeled
   as a true-positive. The eval "false positive" against it was in fact correct
   behavior being mis-scored by the corpus, not a model error.
2. **Added a checked-in eval test** that runs the full corpus through the real,
   live `run_safety_agent` + `run_policy_agent` pipeline end-to-end, not mocks. Marked
   `@pytest.mark.fraud_eval`, skipped by default (including in CI) and only runs on
   explicit opt-in (`AMM_RUN_FRAUD_EVAL=1`), since a 25-case corpus makes ~35-45 real
   API calls per run — real cost and time, not something to run on every commit.
3. **Aggregate thresholds, not per-case**: individual real-call outcomes are inherently
   probabilistic — 0020 measured 13/15, not 15/15, on repeated identical input — so the
   eval asserts overall recall (>= 60%) and false-positive rate (<= 20%) across the
   whole corpus, not that every single case passes every single run. A run below
   threshold is a signal to investigate, not necessarily a hard regression.

First real run (this session, before the investment-pitch relabel): **10/11 recall
(91%)**, **2/14 false positives (14%)**. Second run (after relabeling, corpus now 12
TP / 13 TN): **10/12 recall (83%)**, **2/13 false positives (15%)** — the relabeled
`vague_investment_pitch` case was correctly caught this time (via the
`Prize/Advance-Fee Scam` check, not a lottery-specific signal — a reasonable
generalization), confirming the relabel was right. Both runs stayed within threshold.
Different specific cases missed/flagged between the two runs
(`jobs_advance_fee`/`industrial_equipment_advance_payment` missed the second time,
`industrial_equipment_clean` false-positived the second time but not the first) — this
is expected real-call variance (see docs/decisions/0020), not a regression; the
individual cases will differ run to run, the aggregate is what to watch. `_run` retries
a case up to 3 times on a connection-level failure (not per-HTTP-call) — real-call
testing during this session confirmed a 25-case corpus is meaningfully more exposed to
a transient network timeout than any single smaller test, and one blip shouldn't fail
the whole eval run.

## Consequences

- `tests/test_fraud_eval.py` added.
- `pytest.ini`: new `fraud_eval` marker.
- `tests/conftest.py`: `pytest_collection_modifyitems` extended to skip
  `fraud_eval`-marked tests unless `AMM_RUN_FRAUD_EVAL` is set, mirroring the existing
  `integration` marker's `DATABASE_URL`-gated pattern.
- Not wired into CI — intentionally opt-in/manual for now. Revisit if a periodic
  scheduled run (not blocking every PR) turns out to be worth the API cost once in
  pilot.
