# amm - Claude Code Project Instructions

@AGENTS.md

## Claude Code-Specific Notes

- For security-related changes (secrets, untrusted input), consult
  `.claude/rules/security.md`.
- Custom commands live under `.claude/commands/` (`commit-pr`, `finish-pr`).

## Session Guardrails

- **GPG signing**: never disable or bypass signing. When a signed commit is needed and
  pinentry is unavailable in this session, stage the changes and hand the exact `git
  commit`/`git push` command to the operator to run themselves.
- **Live credentials in chat**: if a real API key or connection string is pasted into
  a session, treat it as compromised once the task is done -- flag it for rotation
  rather than assuming deleting the message is sufficient.
- **Spec vs. reality**: this project's SPEC.md has already been corrected several
  times after testing agents against real model calls (wrong model name, wrong output
  schema, prompts that produced wrong answers). Don't trust an agent's spec section as
  ground truth without re-verifying if you change how it calls out to a model.
