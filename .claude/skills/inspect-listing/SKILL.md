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
then each image rendered inline in the conversation, one at a time, paced by the
moderator rather than dumped all at once.

Images can't be opened in an external OS viewer on this host: WSL interop is disabled
(`/etc/wsl.conf`, `[interop] enabled=false`) and there is no X/Wayland display, so
`explorer.exe`/`xdg-open`/GUI viewers all fail. Instead, images are fetched to local
temp files and displayed with Claude Code's own Read tool, which renders images inline
wherever this session supports it (terminal/IDE image protocols).

## Steps

1. Resolve the `listingId`. If not given, ask, or offer `list_pending()` (via
   `cli/tools.py`) to let the moderator pick from the review queue.
2. Run `python3 scripts/inspect_listing.py <listingId>`. This prints:
   - The listing's title, description, category, seller, and status.
   - Every agent artifact (Safety/Evidence/Consistency/Policy/Decision) with its
     payload, in order -- same data `explain_case` would give you, just formatted for
     reading.
   - A list of local file paths, one per image, at the end.
3. Present the text summary to the moderator first.
4. For each image path printed, in order:
   - Use the Read tool on that path so the image renders inline.
   - Tell the moderator which image this is ("Image 1 of N").
   - Wait for the moderator's actual reply (e.g. "next", "looks fine", a question)
     before reading the next image. Do not loop through all images unprompted -- the
     pacing is the point of this skill, not a nice-to-have.
5. If the moderator wants only the text (no images), run the script with
   `--text-only` instead of fetching images at all.
6. This skill only displays information -- it never calls `approve_listing`,
   `reject_listing`, or `record_decision` itself. Those stay explicit moderator
   actions via the normal CLI tools.

## Notes

- `scripts/inspect_listing.py` requires `DATABASE_URL` (and, if the listing's images
  use `s3://` URLs, S3 credentials/`S3_ENDPOINT_URL`) set in the environment, same as
  any other tool in this project.
- If WSL interop is ever enabled on this host in the future, an external-viewer path
  could be added back in, but the inline Read-tool approach works regardless of host
  capabilities and should stay the default.
