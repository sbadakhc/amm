# 0031. Rename moderator-facing skills for brevity and clarity

## Status
Accepted

## Context

The three newest skills (`preflight`, `process-queue`, `pipeline-stats`, added in
the same session as `docs/decisions/0027`) had names that described their
implementation rather than the action a moderator would reach for. Asked directly
("let's rename the skills to make things more intuitive"), two renames were
proposed and two needed pushback before landing:

- `pipeline-stats` -> `stats`, `inspect-listing` -> `inspect`: straightforward
  shortenings, no ambiguity introduced.
- `process-queue` -> `queue` (as first proposed): rejected -- `/inspect --queue`
  already means "view the queue." A bare `/queue` doing something completely
  different (*run* the batch, not *view* it) is exactly the kind of near-miss that
  causes a moderator to fire off a real pipeline run while meaning to look at
  something. Renamed to `run` instead -- reads as an action, not a view.
- `preflight` -> `init` (as first proposed): rejected -- `init` conventionally means
  "set something up" (`git init`, `npm init`), and this skill doesn't initialize
  anything; it's a read-only status check. Renamed to `status` instead.

## Decision

| Old | New |
|---|---|
| `inspect-listing` | `inspect` |
| `pipeline-stats` | `stats` |
| `preflight` | `status` |
| `process-queue` | `run` |

Only the skill directory names and `name:`/`description:` frontmatter (i.e. what a
moderator types as a slash command) changed. Deliberately **not** renamed:
- The underlying scripts (`scripts/preflight_check.py`, `scripts/pipeline_stats.py`,
  `scripts/inspect_listing.py`) -- implementation detail, not what a moderator
  interacts with directly.
- ADR file names that reference the old names in their own titles/history
  (`0011-inspect-listing-...`, `0015-inspect-listing-...`, and 0026-0030's prose) --
  those are point-in-time decision records of what was true when they were written,
  not living documentation to keep in sync.

Forward-facing docs (README.md's worked walkthrough, SPEC.md §6/§8's live command
references, each skill's own cross-references to the others) were updated to the new
names; historical ADR prose was left as-is.

## Consequences

- Renamed: `.claude/skills/inspect-listing/` -> `.claude/skills/inspect/`,
  `.claude/skills/preflight/` -> `.claude/skills/status/`,
  `.claude/skills/process-queue/` -> `.claude/skills/run/`,
  `.claude/skills/pipeline-stats/` -> `.claude/skills/stats/` (via `git mv`,
  preserving history).
- `README.md`, `SPEC.md` §6/§8: slash-command references updated to the new names.
- `/run`'s own skill file now explicitly calls out the near-miss with `/inspect
  --queue` that motivated its name, so a future reader doesn't wonder why
  `process-queue` didn't just become `queue`.
- `/inspect`'s skill file also picked up an unrelated but related fix while being
  touched: step 2 now states plainly that "show"/"open"/"let me see" always mean
  inline rendering, not a `--serve` link -- a live correction from earlier the same
  session ("when I say show the listing I need to see it"), previously only saved to
  cross-session memory, now baked into the skill itself so it doesn't depend on
  memory recall.
