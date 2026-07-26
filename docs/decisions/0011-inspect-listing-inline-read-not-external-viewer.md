# 0011. inspect-listing shows images via Claude's Read tool, not an external OS viewer

## Status
Accepted

## Context
`show_images` only returns raw `s3://`/`file://` URLs -- no way for a moderator to
actually see a listing's images while reviewing a case through the Claude Code CLI
(SPEC.md §6). The original design assumed launching a real OS image viewer (this dev
host is WSL2 with the Windows filesystem mounted at `/mnt/c`, so `explorer.exe` seemed
like a natural way to pop a Photos-app window on a file).

That assumption failed on contact with the real host: `/etc/wsl.conf` has
`[interop] enabled=false`, so Windows binaries cannot be executed from this shell at
all (`explorer.exe` -> "cannot execute binary file", no `WSLInterop` binfmt handler
registered, no `cmd.exe`/`powershell.exe` on `PATH`). There is also no X/Wayland
display (`$DISPLAY` unset, no `feh`/`eog`/`xdg-open`), so a native Linux GUI viewer
isn't an option either.

## Decision
`scripts/inspect_listing.py` fetches each image (via the existing
`images.fetch_image_bytes` helper) to a local temp file and prints its path. The
`.claude/skills/inspect-listing/` skill then has Claude Code read each path with its
own Read tool, which renders images inline in the conversation -- no external process,
no GUI, no dependency on WSL interop or a display server. Pacing (one image at a time,
paused for the moderator's go-ahead) lives in the skill's instructions, not in the
script.

## Consequences
- Works today on this host with zero config changes, and is portable to any host
  Claude Code runs on (native Linux, macOS, a different WSL config) since it doesn't
  depend on host GUI capability at all.
- If WSL interop is enabled on this host in the future, an external-viewer path could
  be added as an alternative, but inline Read-tool rendering should stay the default --
  it's strictly less fragile.
- Verified against a real Postgres instance + real fetched images (`s3://`-equivalent
  `file://` demo images): `scripts/inspect_listing.py <listingId>` prints listing text,
  agent artifacts, and image paths; each path renders correctly via Read.
