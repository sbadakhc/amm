# 0023. Real eBay listing titles as a local-only false-positive fixture

## Status
Accepted

## Context

Every existing real-call eval corpus (`tests/test_fraud_eval.py`, 0021) is
adversarial by construction -- hand-written to trigger specific rules, plus a handful
of deliberately tricky negatives. None of it answers "does the pipeline stay quiet on
large volumes of real, boring, legitimate listing text it wasn't specifically written
to handle?" -- the false-positive question, at scale, on real language rather than
hand-authored language.

`eBay/ImageGuidedTranslationDataset` (public GitHub repo) is 67,729 real eBay listing
titles across many categories, exactly this shape. A manual ad-hoc sample of 12 titles
during initial connectivity testing (2026-08-29) found 0 false positives, suggesting
it's a workable fixture -- but the dataset is licensed **CC BY-NC 4.0
(NonCommercial)**, and this project has commercial intent (the alsoug.com pilot
work). Committing the sampled titles to the repo would ship NC-licensed third-party
text as part of a commercial tool's test suite -- a real conflict, not a technicality.

## Decision

Use the data, but keep it strictly local and never committed:

- `scripts/fetch_ebay_titles_fixture.py` downloads the source TSV fresh each time and
  writes a random sample to `tests/fixtures/ebay_titles.local.tsv` -- gitignored (see
  `.gitignore`), same pattern already used for `generate_synthetic_data.py`'s output
  (`/listings.json`, `/images/`).
- `tests/test_ebay_false_positive_eval.py` is opt-in on two conditions, both required:
  `AMM_RUN_EBAY_FP_EVAL=1` (real API cost/time, same reasoning as 0021's
  `AMM_RUN_FRAUD_EVAL`) AND the local fixture file existing (a fresh clone has
  neither by default). `tests/conftest.py`'s `pytest_collection_modifyitems` gives a
  distinct skip reason for each missing condition rather than one generic skip.
- Threshold-based assertion (`MAX_FALSE_POSITIVE_RATE = 0.05`), not a demand for a
  perfect 0% -- same "aggregate, not per-case" reasoning as 0021, since this is still
  a probabilistic real model call per title.
- Titles only (the source data has no description field) -- run standalone through
  `run_safety_agent` with an empty description. This is a *harder* case than a real
  listing (less context than title+description together), so it's a conservative
  upper-bound false-positive estimate, not an underestimate.

Rejected: committing a sample to the repo (the NC-license conflict); scraping/using
Amazon or other paid dataset providers found during the same search (no free/open
sample large enough to be useful); treating this as a CI-blocking gate (real API
cost/time makes it belong in the same opt-in category as the fraud eval, not the
default suite).

## Consequences

- New files: `scripts/fetch_ebay_titles_fixture.py`,
  `tests/test_ebay_false_positive_eval.py`.
- `tests/conftest.py`: new `EBAY_FIXTURE_PATH` constant and `ebay_fp_eval` skip
  branch in `pytest_collection_modifyitems`.
- `pytest.ini`: new `ebay_fp_eval` marker.
- `.gitignore`: `/tests/fixtures/ebay_titles.local.tsv` added, with the licensing
  reason stated inline (not just "generated," so a future contributor doesn't assume
  it's safe to commit if they regenerate a smaller/different sample).
- First real run (20-title sample, 2026-08-29) found 1/20 (5.0%) flagged --
  `PII/Privacy` on a plain clothing listing ("NWT $44.00 US Claiborne XL & 2XL Short
  Sleeve Woven Men's Shirt White off, Blue!"), passing the 5% threshold exactly. Not
  investigated further as part of this change -- noted as a candidate follow-up, not
  fixed here.
- Not wired into CI, same as 0021's fraud eval -- real cost/time, opt-in by design.
