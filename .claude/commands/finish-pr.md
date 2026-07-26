---
description: Post-merge cleanup -- verify merge state, sync dev, delete branch
---

The human says a PR is merged. VERIFY IT FIRST -- a PR can be CLOSED without being
merged, and humans are sometimes mistaken:

!`gh pr list --state all --limit 5 --json number,state,title --jq '.[] | "#\(.number) \(.state) \(.title)"'`

For the PR in question (ask which one if ambiguous, or take it from $ARGUMENTS):

1. Confirm the state is exactly `MERGED`: `gh pr view <N> --json state`
   - If it is `OPEN` or `CLOSED`, STOP and report -- do not delete anything
2. Sync: `git checkout dev && git fetch origin && git merge origin/main --ff-only`
   (this repo merges PRs into `main` directly; `dev` fast-forwards to match).
   **Always `git fetch origin` immediately before the `--ff-only` merge** -- a stale
   local `origin/main` ref makes `--ff-only` silently report "Already up to date"
   instead of erroring, which has actually happened in this repo (no failure signal,
   just wrong state, caught only by a missing file downstream).
3. Delete the merged branch locally (`git branch -d <branch>`) and on origin
   (`git push origin --delete <branch>`) -- it may already be auto-deleted, which is fine
4. Close the linked issue if one exists, with a comment referencing the PR:
   `gh issue close <N> --comment "Resolved by PR #<PR>."`
5. Verify final state: clean working tree, no leftover local branches

Report what was done and the final state.
