#!/usr/bin/env python3
"""
Inspection helper for the moderator CLI's inspect-listing skill (see SPEC.md §6,
.claude/skills/inspect-listing/). Not itself a CLI tool per SPEC.md's tool table --
a one-shot script Claude Code shells out to on a moderator's behalf.

Prints the full canonical listing plus its latest agent artifacts/decision as text,
then fetches each of the listing's images (via the shared `images.fetch_image_bytes`
helper used by Evidence/Consistency Agents) to local temp files and prints their
paths, one per line, in listing order.

Images are written to local files rather than opened directly because this host has
no way to launch a GUI viewer (WSL interop disabled, no X/Wayland display -- verified
2026-07-26). Claude Code's own Read tool renders images inline in the conversation,
so the skill reads each printed path itself rather than this script opening anything.

Usage:
    python3 scripts/inspect_listing.py LST-100234           # text + fetch all images
    python3 scripts/inspect_listing.py LST-100234 --text-only
"""

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.tools import explain_case, get_listing  # noqa: E402
from images import fetch_image_bytes  # noqa: E402


def print_listing_text(listing_id: str) -> None:
    data = get_listing(listing_id)
    row = data["listing"]

    print(f"Listing {listing_id}")
    print(f"Status: {row['status']}")
    print(f"Title: {row['title']}")
    print(f"Description: {row['description']}")
    print(f"Category: {row['category']['id']} ({row['category']['name']})")
    print(f"Seller: {row['seller']['sellerId']} ({row['seller']['companyName']})")
    print()

    artifacts = explain_case(listing_id)
    if not artifacts:
        print("(no agent artifacts yet -- listing hasn't been through the pipeline)")
        return

    for artifact in artifacts:
        print(f"-- {artifact['agent']} ({artifact['version']}, {artifact['produced_at']}) --")
        for key, value in artifact["payload"].items():
            print(f"  {key}: {value}")
        print()


def fetch_images_to_temp(listing_id: str) -> list[str]:
    data = get_listing(listing_id)
    images = data["listing"]["images"]
    if not images:
        return []

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"amm-inspect-{listing_id}-"))
    paths = []
    for i, img in enumerate(images):
        url = img["url"]
        content, mime = fetch_image_bytes(url)
        ext = mime.split("/")[-1] if "/" in mime else "png"
        out_path = tmp_dir / f"image-{i}.{ext}"
        out_path.write_bytes(content)
        paths.append(str(out_path))
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("listing_id")
    parser.add_argument("--text-only", action="store_true", help="skip fetching images")
    args = parser.parse_args()

    print_listing_text(args.listing_id)

    if args.text_only:
        return

    paths = fetch_images_to_temp(args.listing_id)
    if not paths:
        print("(listing has no images)")
        return

    print(f"Fetched {len(paths)} image(s):")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
