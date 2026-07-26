# 0015. inspect-listing gains a queue-table view

## Status
Accepted

## Context
`.claude/skills/inspect-listing/` (ADR 0011, 0012... actually 0011) shipped a
per-listing deep-dive: text summary + images, one image at a time, paced by the
moderator. Walked a human moderator through all 5 demo listings this way. Direct
feedback afterward:

1. Too much friction -- each listing restarted the `--serve` HTTP server from
   scratch, one URL at a time.
2. No visibility into decision/confidence -- the per-listing flow showed images and
   raw text, but nothing summarizing where each listing landed (REVIEW/REJECT/
   APPROVE, confidence, which policy rules matched).

The moderator explicitly asked for "a table of images and links with status report."

## Decision
Added `--queue` to `scripts/inspect_listing.py`: lists listings (all statuses by
default, or filtered via `--status`), fetches every listing's images into one shared
temp directory, starts a **single** persistent image server covering all of them, and
prints one markdown table -- listing ID, title, status, decision, confidence, policy
rules, and an image link per row -- reading the latest `DecisionAgent` artifact per
listing (§5: the latest one is the listing's current decision) rather than requiring
the moderator to ask for each listing individually.

The existing per-listing deep-dive (inline or `--serve`, one image at a time) is
unchanged and still the right tool once a moderator has picked a specific case from
the table to look at closely -- `--queue` is an additional entry point, not a
replacement.

## Consequences
- `scripts/inspect_listing.py` gains a `db` import (previously only used indirectly
  via `cli.tools`) to call `list_listings_by_status`/`latest_artifact` directly.
- Verified against a real Postgres instance with pipeline-processed listings (real
  decisions, not just images): the table correctly showed REVIEW/REJECT decisions,
  confidence, and matched policy rules (e.g. `W001` for the weapon scenario, `C001`
  for counterfeit_brand, `C004` for inconsistent) alongside working image links.
- `.claude/skills/inspect-listing/SKILL.md` updated with a "surveying the queue" flow
  ahead of the existing "deep-diving one listing" flow, and trigger phrases extended
  ("what's pending", "show me the queue", "give me a table of listings").
