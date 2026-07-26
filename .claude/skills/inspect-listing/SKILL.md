---
name: inspect-listing
description: >
  Lets a human moderator actually see a listing's images and read its full text/agent
  findings during review, instead of just getting raw s3:// URLs back from
  show_images. Use whenever a moderator wants to look at a specific case -- triggers
  on "inspect listing", "show me listing", "show images for", "let me see the images
  for", "view listing", "open case", or invoked directly as /inspect-listing
  <listingId>.
argument-hint: <listingId>
compatibility: Claude Code
metadata:
  category: moderation
  version: "1.0"
---

# Skill: inspect-listing

## Purpose

The moderator CLI (SPEC.md §6) is conversational, no web UI. `show_images` returns raw
`s3://`/`file://` URLs, which aren't directly viewable. This skill gives a moderator an
actual look at a case: the full canonical listing + latest agent artifacts as text,
then its images -- either rendered inline in the conversation, or opened as a real
pop-up in the moderator's browser -- one at a time, paced by the moderator rather than
dumped all at once.

Neither path launches an OS viewer directly: this host can't (WSL interop disabled,
no X/Wayland display -- `docs/decisions/0011`). Instead, `scripts/inspect_listing.py`
offers two modes:

- **Inline (default)**: fetches images to local temp files and prints their paths;
  Claude reads each one with its own Read tool, rendering it inline in the
  conversation. Always available, any host.
- **`--serve`**: fetches images to local temp files, starts a throwaway HTTP server on
  `127.0.0.1`, and prints a `http://localhost:<port>/...` URL per image instead. Under
  WSL2 this is reachable from a Windows browser automatically (`localhostForwarding`);
  on native Linux/macOS it just works directly. Gives an actual pop-up browser
  window/tab rather than inline rendering.

## Steps

1. Resolve the `listingId`. If not given, ask, or offer `list_pending()` (via
   `cli/tools.py`) to let the moderator pick from the review queue.
2. Ask (or infer from how they phrased the request) whether the moderator wants images
   inline in the conversation or opened in a browser. Default to inline if unclear --
   it's the lower-friction path and needs no follow-up action from them.
3. Run `python3 scripts/inspect_listing.py <listingId>`, adding `--serve` for the
   browser path. This prints:
   - The listing's title, description, category, seller, and status.
   - Every agent artifact (Safety/Evidence/Consistency/Policy/Decision) with its
     payload, in order -- same data `explain_case` would give you, just formatted for
     reading.
   - Either a list of local file paths (default) or a list of browser URLs (`--serve`),
     one per image, at the end.
4. Present the text summary to the moderator first.
5. For each image, in order:
   - Inline mode: use the Read tool on the printed path so the image renders inline.
   - `--serve` mode: give the moderator the printed URL to open themselves.
   - Tell the moderator which image this is ("Image 1 of N").
   - Wait for the moderator's actual reply (e.g. "next", "looks fine", a question)
     before moving to the next image. Do not loop through all images unprompted --
     the pacing is the point of this skill, not a nice-to-have.
6. If `--serve` was used, run `python3 scripts/inspect_listing.py --stop-server` once
   the moderator is done reviewing images for this case, to tear down the server
   rather than leaving it running indefinitely.
7. If the moderator wants only the text (no images), run the script with
   `--text-only` instead of fetching images at all.
8. This skill only displays information -- it never calls `approve_listing`,
   `reject_listing`, or `record_decision` itself. Those stay explicit moderator
   actions via the normal CLI tools.

## Notes

- `scripts/inspect_listing.py` requires `DATABASE_URL` (and, if the listing's images
  use `s3://` URLs, S3 credentials/`S3_ENDPOINT_URL`) set in the environment, same as
  any other tool in this project.
- A new `--serve` call automatically stops any server left running from a previous
  call, so it's safe to call repeatedly without manually stopping in between -- but
  still stop it explicitly (step 6) once the moderator is done, rather than leaving
  it listening.
