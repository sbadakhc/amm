# amm - Claude Code Project Instructions

@AGENTS.md

## Claude Code-Specific Notes

- For security-related changes (secrets, untrusted input), consult
  `.claude/rules/security.md`.
- For prose style (no em dashes), consult `.claude/rules/style.md`.
- Custom commands live under `.claude/commands/` (`commit-pr`, `finish-pr`).

## Session Guardrails

- **GPG signing**: never disable or bypass signing, and never run a signing command
  (`git commit`, `git merge` that creates a commit, etc.) directly yourself, even if
  told the key is warm or the agent cache is active -- that claim doesn't lift the
  rule. Stage the changes and hand the exact `git commit`/`git push` command to the
  operator to run themselves. Never use `--pinentry-mode loopback`, including in
  handed-off commands -- it bypasses the agent cache and forces repeated hand-offs.
- **Live credentials in chat**: if a real API key or connection string is pasted into
  a session, treat it as compromised once the task is done -- flag it for rotation
  rather than assuming deleting the message is sufficient.
- **Spec vs. reality**: this project's SPEC.md has already been corrected several
  times after testing agents against real model calls (wrong model name, wrong output
  schema, prompts that produced wrong answers). Don't trust an agent's spec section as
  ground truth without re-verifying if you change how it calls out to a model.
