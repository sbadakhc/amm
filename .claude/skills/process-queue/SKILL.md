---
name: process-queue
description: >
  Runs the automated moderation pipeline against whatever's PENDING_MODERATION in
  Postgres right now -- the "overnight batch just arrived" step. Wraps
  pipeline.poll_and_process() so it reads as a normal action, not a Python
  one-liner. Triggers on "process the queue", "run the pipeline", "process new
  listings", "run moderation", "check for new listings", or invoked directly as
  /process-queue.
argument-hint: (no arguments)
compatibility: Claude Code
metadata:
  category: operations
  version: "1.0"
---

# Skill: process-queue

## Purpose

`pipeline.poll_and_process()` claims a batch of `PENDING_MODERATION` listings and
runs each through Evidence/Consistency/Safety → Policy → Decision, per SPEC.md §7.
This is the one step in the moderator's day that otherwise looks like raw Python
glued together (`python3 -c "from pipeline import poll_and_process; ..."`) rather
than a normal command -- this skill exists so a moderator never has to type or read
that.

This does not review anything itself -- it's the automated half of the pipeline.
Results land in `PENDING_REVIEW`/`APPROVED`/`REJECTED`; use `/inspect-listing` next to
actually look at what happened.

## Steps

1. If `DATABASE_URL`/`NVIDIA_API_KEY` aren't already set in this shell, load them:
   `set -a; source .env; set +a` (and `export DATABASE_URL=...` if it's still blank
   in `.env` -- see README.md's "Configure & seed data" for the dev-db connection
   string).
2. Optionally, if it hasn't been checked recently in this session, suggest running
   `/preflight` first -- not mandatory (most agents fail open on a down model
   anyway), just a useful heads-up before a real batch run.
3. Run `python3 -c "from pipeline import poll_and_process; print(poll_and_process())"`.
   The printed number is how many listings were claimed and processed in this batch
   (not necessarily how many succeeded cleanly -- some may have landed in
   `PENDING_REVIEW` via a Pipeline error artifact if an agent call failed).
4. Report the count plainly, and point at `/inspect-listing --queue` as the natural
   next step to see what actually happened to them.
5. If the command errors outright (not just a printed failure count -- an actual
   traceback that aborts before printing a number), that's a real problem worth
   surfacing directly, not retrying silently.

## Notes

- This is one poll cycle, not a long-running service -- for continuous processing,
  `python3 service.py` is the long-running poller (see README.md); this skill wraps
  the one-shot form, which is what a moderator manually triggering a batch wants.
- Safe to run repeatedly -- if there's nothing `PENDING_MODERATION`, it just returns
  0 and does nothing else.
