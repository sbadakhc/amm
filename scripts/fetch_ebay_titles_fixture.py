"""
Pulls a random sample of real eBay listing titles for use as a local-only
false-positive fixture (docs/decisions/0023). NOT committed to the repo: the source
dataset (eBay/ImageGuidedTranslationDataset) is CC BY-NC 4.0 (NonCommercial), and this
project has commercial intent, so the sampled titles stay on disk only, gitignored,
and are never shipped as part of the product.

Usage:
    python3 scripts/fetch_ebay_titles_fixture.py [sample_size]

Writes tests/fixtures/ebay_titles.local.tsv (one title per line, header row first).
"""

import csv
import random
import sys
import urllib.request

SOURCE_URL = (
    "https://raw.githubusercontent.com/eBay/ImageGuidedTranslationDataset/main/"
    "dataset/listingtitle-image-mappings/listingtitles_with_matched_images.en-de.tsv"
)
OUTPUT_PATH = "tests/fixtures/ebay_titles.local.tsv"
DEFAULT_SAMPLE_SIZE = 200


def main():
    sample_size = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SAMPLE_SIZE

    print(f"Fetching {SOURCE_URL} ...")
    with urllib.request.urlopen(SOURCE_URL) as resp:
        text = resp.read().decode("utf-8")

    rows = list(csv.DictReader(text.splitlines(), delimiter="\t"))
    print(f"{len(rows)} listing titles available, sampling {sample_size}")

    sample = random.sample(rows, min(sample_size, len(rows)))
    with open(OUTPUT_PATH, "w", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["title"])
        for row in sample:
            writer.writerow([row["source"]])

    print(f"Wrote {len(sample)} titles to {OUTPUT_PATH} (gitignored, local-only)")


if __name__ == "__main__":
    main()
