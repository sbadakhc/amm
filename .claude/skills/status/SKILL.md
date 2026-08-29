---
name: status
description: >
  Checks whether every NVIDIA-hosted model this pipeline depends on is actually
  callable right now, before running the pipeline, a demo, or a real-call test
  suite. Distinguishes a permanently removed model (GONE -- needs a code fix, not a
  retry) from a transiently down one (UNREACHABLE -- may just need a moment).
  Triggers on "check the models", "are the models up", "is nvidia down", "status",
  "preflight", "preflight check", "are we ready to run", or invoked directly as
  /status.
argument-hint: (no arguments)
compatibility: Claude Code
metadata:
  category: operations
  version: "1.0"
---

# Skill: status

## Purpose

Two real incidents drove this (docs/decisions/0022, 0025, 0026): a model
intermittently hanging with no response and no timeout of its own, and a different
model being permanently removed from NVIDIA's catalog days before anyone noticed --
both first discovered by accident, mid-task. `scripts/preflight_check.py` checks the
whole model roster against NVIDIA's live catalog plus a real minimal call, so a
moderator or operator finds out *before* running the pipeline, not partway through
it.

This is a read-only status check. It never modifies anything, and a bad result isn't
necessarily an emergency -- most agents fail open on a down model now (0022/0028/0029),
so an UNREACHABLE result usually means "proceed, but expect some signals to be
missing," not "stop."

## Steps

1. If `NVIDIA_API_KEY` isn't already set in this shell, load it from `.env`:
   `set -a; source .env; set +a`.
2. Run `python3 scripts/preflight_check.py` and present its table as-is -- it's
   already human-readable.
3. Summarize plainly: how many of the 4 models are OK, and name any that aren't.
4. For each model that isn't OK, explain what it means and what to do:
   - **GONE**: permanently removed from NVIDIA's catalog. This needs an actual code
     change (a replacement model, verified via real calls -- see
     docs/decisions/0025 for the pattern), not a retry. The script prints candidate
     replacements from the current catalog; make clear these are unverified starting
     points, not a recommendation -- don't wire one in without testing it first
     (real call, same shape the agent actually uses).
   - **UNREACHABLE**: down or flaky right now, but still in the catalog. Usually
     transient. Mention that Safety/Consistency/Evidence Agents fail open on this
     (skip the affected check/image rather than blocking), so it's often safe to
     proceed -- but Evidence/Safety's *primary* classification calls are hard
     dependencies with no fail-open, so a listing that needs those will still route
     to `PENDING_REVIEW` via the pipeline's generic error handler if they're down.
5. Don't take corrective action unprompted (don't wire in a candidate replacement,
   don't retry in a loop) -- report and let the moderator/operator decide.

## Notes

- Costs a handful of real, cheap API calls (one per model) -- negligible, but not
  literally free, so don't run it in a tight loop.
- `scripts/preflight_check.py --quiet` suppresses the table for scripting; not
  typically what a moderator wants, prefer the default verbose form here.
- This skill is about model *availability*, not moderation queue state -- for "what's
  pending" or a specific case, use `/inspect` instead.
- Underlying script stays named `scripts/preflight_check.py` -- only the
  slash-command name changed (`docs/decisions/0031`), not the script.
