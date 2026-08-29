---
name: release
description: >
  Promotes dev into main and closes out the full cycle around it: verify the
  triggering PR actually merged (never trust a claim it did), sync dev, promote to
  main via a signed merge commit (handed off, never run directly -- GPG), sync
  local/remote main and dev, delete the now-merged feature branch everywhere, and
  retro-fit a tracking issue plus labels/assignees for anything that shipped
  without one. This project has no version numbers or tags -- "release" here means
  the dev-to-main promotion cycle, nothing more. Triggers on "promote to main",
  "promote and retro fix", "ship this to main", "cut a release", or invoked
  directly as /release [PR number].
argument-hint: "[PR number, if ambiguous which one]"
compatibility: Claude Code
metadata:
  category: operations
  version: "1.0"
---

# Skill: release

## Purpose

Every PR in this project lands in `dev`, never `main` directly (AGENTS.md §2).
Getting it from "merged into dev" to "live on main, everything synced, nothing left
dangling" turned out to be seven repeatable steps, done manually enough times in one
session that the pattern was worth capturing. This skill is that sequence, with the
two guardrails that kept getting violated or re-explained baked in as hard
preconditions rather than reminders:

1. **Never run a signing command directly.** `git commit`, `git merge --no-ff` (it
   creates a commit), and `git tag -s` all require GPG signing in this project.
   Stage/verify everything up to that point, then hand the exact command to the
   operator -- every single time, no exceptions for "the key is probably warm."
2. **Never trust "it's merged" without checking.** A human (or a prior turn) saying
   a PR merged is a claim, not a fact -- verify with `gh pr view <n> --json
   state,mergedAt` before treating it as true. This project's own history has real
   examples of a PR being reported merged when it wasn't yet.

## Steps

### 1. Verify the trigger

If a PR number was given (`$ARGUMENTS` or asked directly), or the human says "the PR
is merged": confirm with
```bash
gh pr list --search "head:<branch>" --state all --json number,state,mergedAt,baseRefName
```
or `gh pr view <n> --json number,state,mergedAt,baseRefName`. If `state` isn't
exactly `MERGED`, STOP and report -- do not proceed to promotion on an open or
closed-unmerged PR. Confirm `baseRefName` is `dev` (this project's PRs never target
`main` directly).

### 2. Sync dev and check divergence

```bash
git fetch origin
git log origin/main..origin/dev --oneline   # what's about to be promoted
```
If empty, there's nothing to promote -- report and stop.

### 3. Sync local main

```bash
git checkout main
git merge origin/main --ff-only
```
Always `git fetch origin` immediately before this (a stale local `origin/main` ref
makes `--ff-only` silently report "Already up to date" instead of erroring --
`docs/decisions/`-documented failure mode in this project).

### 4. Hand off the promotion merge

Every prior promotion in this repo's history is a genuine two-parent merge commit
(verify with `git log -1 --format="Merge: %P" origin/main` if in doubt), not a
fast-forward -- `dev` and `main` diverge slightly by design since the merge doesn't
get replayed back onto `dev`. Hand off exactly:
```bash
git merge --no-ff origin/dev -m "Promote dev into main"
git push origin main
```
Wait for confirmation it ran. Don't assume -- verify:
```bash
git fetch origin
git log origin/main -1 --format="%H %s%nMerge: %P"
```
Confirm the merge commit's two parents are the previous `origin/main` tip and
`origin/dev`'s tip from step 2.

### 5. Sync local and remote, both branches

```bash
git checkout main && git merge origin/main --ff-only
git checkout dev && git merge origin/main --ff-only
```
`origin/dev` typically lags behind by the merge commit itself (the promotion never
gets pushed back to `dev` automatically) -- check, and if local `dev` is now a
fast-forward ahead of `origin/dev` (`git merge-base --is-ancestor origin/dev dev`),
push it:
```bash
git push origin dev
```
Verify all four refs match: `git rev-parse origin/main origin/dev dev main`.

### 6. Clean up branches

```bash
git branch -a
```
Any branch other than `main`/`dev` whose PR is confirmed `MERGED` (step 1, or check
via `gh pr list --search "head:<branch>" --state all`) gets deleted both ways:
```bash
git branch -D <branch>
git push origin --delete <branch>
git fetch --prune origin
```
Never delete a branch without confirming its PR state first -- a branch can look
stale but still be in-flight.

### 7. Retro-fit tracking

Check every PR that just got promoted (and any others sitting without one -- this
tends to accumulate) for a backing issue, label, and assignee:
```bash
gh pr list --state all --limit 100 --json number,labels,assignees | jq -r '.[] | select((.labels|length==0) or (.assignees|length==0)) | .number'
gh issue list --state all --limit 100 --json number,labels,assignees | jq -r '.[] | select((.labels|length==0) or (.assignees|length==0)) | .number'
```
For any PR with no backing issue: create one, closed, referencing the PR and
summarizing what shipped (see recent closed issues in this repo for the exact
template -- one-paragraph summary + `Closes #<PR>`), labeled by what the PR actually
did (`bug`/`enhancement`/`documentation`/`task`, not a default), assigned to the PR's
author. Label and assign the PR itself to match. Re-run the two commands above until
both come back empty.

### 8. Report

One final state check as the closing summary: the promotion commit hash, confirmation
all four refs match, which branches got deleted, which issues got filed, and
confirmation the label/assignee sweep came back clean.

## Notes

- This is the dev→main promotion cycle, not a versioned release -- this project has
  no version files or git tags. If that ever changes, this skill needs a version-bump
  step added, not renamed.
- Steps 1-3 and 5-8 are safe to run without asking; step 4 always stops and hands
  off, no exceptions, regardless of what the operator says about key state.
- If step 1's verification finds the PR is NOT merged, stop the whole skill there --
  don't improvise a partial promotion.
