---
description: Commit changes and create a PR against main
disable-model-invocation: true
allowed-tools: Bash(git add *), Bash(git commit *), Bash(git push *), Bash(gh pr create *)
---

## Context

- Current branch: !`git branch --show-current`
- Current status: !`git status --short`
- Recent commits: !`git log --oneline -5`

## Your Task

Based on the above changes:

1. **Stage the relevant changes** -- explicit paths, not a blanket `git add -A`; review
   `git status` for anything unexpected (stray temp files, `.env`, credentials) first.

2. **Create a conventional commit**:
   - Format: `<type>: <description>` (under 72 chars), e.g. `feat: add Consistency Agent`
   - Reference an issue if one exists: `Fixes #<issue-number>`

3. **GPG signing**: never disable or bypass signing (`--no-gpg-sign`, `--no-verify`).
   If a signed commit is needed and pinentry isn't available in this session, stage the
   changes and hand the exact `git commit`/`git push` command to the operator to run
   themselves -- do not attempt to work around the signing prompt.

4. **Push the branch**:
   ```bash
   git push -u origin <branch-name>
   ```

5. **Create a pull request** targeting `main`:
   ```bash
   gh pr create --base main --title "<description>" --body "Fixes #<issue-number>"
   ```
   If nothing in the PR needs an issue reference, drop the `Fixes #` line -- don't
   invent an issue number.

6. Report the PR URL. Do not merge it yourself.

## Safety Rules

- `git push --force` to `main` or `dev` is denied
- `rm -rf` and other destructive operations require explicit human approval
- All changes to agent contracts (SPEC.md §3), routing rules (§4), or the schema (§2.1)
  should be reflected in SPEC.md in the same PR, not as a follow-up
