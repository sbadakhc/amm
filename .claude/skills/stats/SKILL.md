---
name: stats
description: >
  Shows accuracy/performance stats for the automated pipeline -- decision
  distribution, moderator override rate, agent confidence, latency, failures by
  error, and policy rule hit counts -- derived from the artifact log
  (docs/decisions/0027). Triggers on "how's the pipeline doing", "show me stats",
  "pipeline stats", "how accurate is this", "show performance", or invoked directly
  as /stats [--since <ISO timestamp>].
argument-hint: "[--since <ISO timestamp>]"
compatibility: Claude Code
metadata:
  category: operations
  version: "1.0"
---

# Skill: stats

## Purpose

`db.get_stats()`/`scripts/pipeline_stats.py` aggregate the same artifact log
`/inspect` and `approve_listing`/`reject_listing` already write to -- no separate
metrics store. This skill exists so a moderator or operator can ask "how's this
actually doing" in plain language instead of remembering it's a Python script.

**Say what this measures, every time it's shown**: this is consistency between the
automated pipeline and moderators (an override rate), not correctness against
ground truth -- there's no labeled outcome data flowing into this system. A 0%
override rate could mean the pipeline is right every time, or that moderators are
rubber-stamping it; this stat alone can't tell the difference. Don't let it be read
as an accuracy score without that caveat.

## Steps

1. If `DATABASE_URL` isn't already set in this shell, load it (see README.md's
   "Configure & seed data" for the dev-db connection string).
2. If the moderator gave a time window ("since yesterday", "this week"), convert it
   to an ISO timestamp and run `python3 scripts/pipeline_stats.py --since
   <timestamp>`. Otherwise run it with no arguments for the all-time view.
3. Present the report as-is -- it's already formatted as readable markdown sections.
4. Restate the consistency-vs-correctness caveat above in your own words, briefly,
   especially if the moderator's question sounded like they wanted an accuracy
   number ("how good is it", "is it working well").
5. If `Pipeline failures` is non-empty, call that out specifically -- it's the same
   signal `/status` would catch proactively, so a non-trivial failure count here is
   worth a `/status` check if one hasn't been run recently.

## Notes

- Read-only, no real API calls -- just Postgres queries. Safe to run as often as
  useful.
- If the moderator wants to look at *why* a specific listing landed where it did
  (not just the aggregate), that's `/inspect <listingId>` or `explain_case`, not
  this skill.
- Underlying script stays named `scripts/pipeline_stats.py` -- only the
  slash-command name changed (`docs/decisions/0031`), not the script.
