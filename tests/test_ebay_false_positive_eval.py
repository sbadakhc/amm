"""
Real-call false-positive stress test (docs/decisions/0023). Runs a sample of real,
ordinary eBay listing titles -- not synthetic, not fraud-corpus adversarial cases --
through the live Safety Agent to check the pipeline stays quiet on boring, legitimate
listings. Opt-in only (@pytest.mark.ebay_fp_eval, see conftest.py): needs both
AMM_RUN_EBAY_FP_EVAL=1 and a local fixture file that is never committed (source
dataset is CC BY-NC 4.0, see scripts/fetch_ebay_titles_fixture.py). Run with:

    python3 scripts/fetch_ebay_titles_fixture.py
    AMM_RUN_EBAY_FP_EVAL=1 pytest tests/test_ebay_false_positive_eval.py -v -s

Titles only, no description field in the source data -- each title is run standalone,
which is a harder case for the Safety Agent than a full listing (less context), so
this is a conservative (upper-bound) false-positive estimate, not an underestimate.
"""

import csv

import pytest

from agents.safety_agent import run_safety_agent
from tests.conftest import EBAY_FIXTURE_PATH

pytestmark = pytest.mark.ebay_fp_eval

# 0/12 false positives observed in an ad-hoc manual sample during initial connectivity
# testing (2026-08-29) -- this threshold allows some slack for real model variance
# rather than demanding a perfect 0% on every run, same "aggregate, not per-case"
# reasoning as docs/decisions/0021's fraud eval.
MAX_FALSE_POSITIVE_RATE = 0.05


def _load_titles():
    with open(EBAY_FIXTURE_PATH) as f:
        return [row["title"] for row in csv.DictReader(f, delimiter="\t")]


def test_ebay_titles_false_positive_rate():
    titles = _load_titles()
    false_positives = []

    for i, title in enumerate(titles):
        artifact = run_safety_agent({"listingId": f"EBAY-FP-{i}", "title": title, "description": ""})
        violations = artifact["payload"]["violations"]
        if violations:
            false_positives.append((title, violations))
            print(f"[FALSE POSITIVE] {title!r} -> {violations}")

    rate = len(false_positives) / len(titles)
    print(f"\n{len(false_positives)}/{len(titles)} flagged ({rate:.1%}) on real eBay listing titles")

    assert rate <= MAX_FALSE_POSITIVE_RATE, (
        f"False-positive rate {rate:.1%} exceeds {MAX_FALSE_POSITIVE_RATE:.0%} threshold: {false_positives}"
    )
