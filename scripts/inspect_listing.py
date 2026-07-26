#!/usr/bin/env python3
"""
Inspection helper for the moderator CLI's inspect-listing skill (see SPEC.md §6,
.claude/skills/inspect-listing/). Not itself a CLI tool per SPEC.md's tool table --
a one-shot script Claude Code shells out to on a moderator's behalf.

Prints the full canonical listing plus its latest agent artifacts/decision as text,
then fetches each of the listing's images (via the shared `images.fetch_image_bytes`
helper used by Evidence/Consistency Agents) to local temp files.

Two ways to view the images -- neither can launch a GUI viewer directly, since this
host has no way to do that (WSL interop disabled, no X/Wayland display -- verified
2026-07-26, see docs/decisions/0011):

  - Default: print each image's local file path. Claude Code's own Read tool renders
    images inline in the conversation from a path like this.
  - --serve: start a throwaway HTTP server on 127.0.0.1 (an ephemeral port) serving
    the fetched images, and print a browser URL per image instead of a path. WSL2
    forwards 127.0.0.1 to the Windows host's localhost automatically, independent of
    the interop setting, so a Windows browser can open the printed URL directly. A new
    --serve call stops any server left running from a previous call first.

Usage:
    python3 scripts/inspect_listing.py LST-100234              # text + local file paths
    python3 scripts/inspect_listing.py LST-100234 --text-only  # text only, no images
    python3 scripts/inspect_listing.py LST-100234 --serve      # text + browser URLs
    python3 scripts/inspect_listing.py --stop-server           # tear down a --serve instance
"""

import argparse
import functools
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.tools import explain_case, get_listing  # noqa: E402
from images import fetch_image_bytes  # noqa: E402

SERVER_STATE_PATH = Path(tempfile.gettempdir()) / "amm-inspect-server.json"


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


def _run_server_daemon(directory: str, state_path: str) -> None:
    """Blocks forever serving `directory` on 127.0.0.1; run as a detached subprocess
    by start_server(), never called directly by a user."""
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    handler = functools.partial(SimpleHTTPRequestHandler, directory=directory)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    Path(state_path).write_text(json.dumps({"pid": os.getpid(), "port": port, "dir": directory}))
    signal.signal(signal.SIGTERM, lambda *_args: sys.exit(0))
    httpd.serve_forever()


def stop_server() -> bool:
    """Stops a --serve instance left running from a previous call, if any. Returns
    whether one was actually running."""
    if not SERVER_STATE_PATH.exists():
        return False
    try:
        state = json.loads(SERVER_STATE_PATH.read_text())
        os.kill(state["pid"], signal.SIGTERM)
        was_running = True
    except (ProcessLookupError, json.JSONDecodeError, KeyError):
        was_running = False
    SERVER_STATE_PATH.unlink(missing_ok=True)
    return was_running


def start_server(directory: str) -> int:
    """Stops any previously running instance, then starts a fresh detached server for
    `directory` and returns the port it's listening on."""
    stop_server()
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--serve-daemon", directory, str(SERVER_STATE_PATH)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(50):  # up to ~5s for the daemon to bind and write its state
        if SERVER_STATE_PATH.exists():
            return json.loads(SERVER_STATE_PATH.read_text())["port"]
        time.sleep(0.1)
    raise RuntimeError("Image server did not start in time")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("listing_id", nargs="?")
    parser.add_argument("--text-only", action="store_true", help="skip fetching images")
    parser.add_argument("--serve", action="store_true", help="serve images over HTTP instead of printing file paths")
    parser.add_argument("--stop-server", action="store_true", help="stop a running --serve instance and exit")
    parser.add_argument("--serve-daemon", nargs=2, metavar=("DIR", "STATE_PATH"), help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.serve_daemon:
        _run_server_daemon(*args.serve_daemon)
        return

    if args.stop_server:
        print("Stopped image server." if stop_server() else "No image server was running.")
        return

    if not args.listing_id:
        parser.error("listing_id is required unless --stop-server is given")

    print_listing_text(args.listing_id)

    if args.text_only:
        return

    paths = fetch_images_to_temp(args.listing_id)
    if not paths:
        print("(listing has no images)")
        return

    if args.serve:
        port = start_server(str(Path(paths[0]).parent))
        print(f"Serving {len(paths)} image(s) at http://localhost:{port}/ -- open in a browser:")
        for path in paths:
            print(f"http://localhost:{port}/{Path(path).name}")
        print("Run with --stop-server when done reviewing.")
    else:
        print(f"Fetched {len(paths)} image(s):")
        for path in paths:
            print(path)


if __name__ == "__main__":
    main()
