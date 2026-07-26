# 0011. inspect-listing shows images via Claude's Read tool or a throwaway HTTP
# server, not an external OS viewer

## Status
Accepted (amended)

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
display (`$DISPLAY` unset, no `feh`/`eog`/`xdg-open`, `/mnt/wslg` has no live
wayland/X11 socket), so a native Linux GUI viewer isn't an option either, and WSLg
itself is inactive as a consequence of interop being off.

The user then asked for an actual pop-up window rather than inline rendering.
Enabling interop was one option but requires editing `/etc/wsl.conf` and a
`wsl --shutdown` restart -- a real config change, not something to do unprompted.
Instead: WSL2 forwards `127.0.0.1` ports to the Windows host's `localhost`
automatically (`localhostForwarding`, a network-level feature, independent of the
`[interop]` binary-execution setting), so a throwaway HTTP server bound to
`127.0.0.1` inside WSL is reachable from a Windows browser with no config changes at
all. Confirmed by the user opening a served image URL in a real Windows browser.

## Decision
`scripts/inspect_listing.py` supports two ways to view a listing's fetched images,
both writing to local temp files first via the existing `images.fetch_image_bytes`
helper:

- **Default**: print each image's local file path. The `inspect-listing` skill has
  Claude Code read each path with its own Read tool, rendering the image inline in
  the conversation. No external process, no GUI, no dependency on WSL interop or a
  display server -- works on any host Claude Code runs on.
- **`--serve`**: start a detached `ThreadingHTTPServer` on `127.0.0.1:<ephemeral
  port>` serving the fetched images, and print a `http://localhost:<port>/...` URL
  per image instead of a path. A new `--serve` call stops any server left running
  from a previous call first (state tracked in a small JSON file, no orphaned
  daemons). `--stop-server` tears it down explicitly. This gives a genuine pop-up
  browser window/tab, opened by the moderator themselves.

Pacing (one image at a time, paused for the moderator's go-ahead) lives in the
skill's instructions either way, not in the script.

## Consequences
- Both paths work today on this host with zero WSL config changes.
- Default (inline Read) is portable to any host Claude Code runs on regardless of
  networking/GUI capability -- it should stay the fallback the skill always has
  available.
- `--serve` depends on WSL2's `localhostForwarding` (on by default) when run under
  WSL; on native Linux/macOS `http://localhost:<port>/...` just works directly since
  there's no VM boundary to forward across.
- If WSL interop is ever enabled on this host, an `explorer.exe`-based path could
  still be added as a third option, but neither existing path requires it, so there's
  no forcing function to build it.
- Verified against a real Postgres instance + real fetched images (`file://` demo
  images standing in for `s3://`): `scripts/inspect_listing.py <listingId>` (default)
  prints listing text, agent artifacts, and image paths, each rendering correctly via
  Read; `--serve` printed a URL the user confirmed loading in a real Windows browser;
  a second `--serve` call was confirmed to kill the first instance's process;
  `--stop-server` was confirmed to tear down a running instance.
