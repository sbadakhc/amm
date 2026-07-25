# 0002. Moderator CLI is a Python tool layer, not a standalone chat loop

## Status
Accepted

## Context
SPEC.md §6 describes the Moderator CLI as "conversational, tool-driven." There are two
ways to build that:

1. Implement the 10 tools (`list_pending`, `get_listing`, `explain_case`, etc.) as
   plain Python functions, and have a moderator drive them by talking to Claude Code
   directly -- no separate chat loop to build or maintain.
2. Build a standalone REPL using the Anthropic Messages API with tool-use, so the CLI
   runs independently of Claude Code -- requires its own `ANTHROPIC_API_KEY`, an
   agentic loop, tool-use request/response handling, and conversation state.

## Decision
Option 1. The tools live in `cli/tools.py` as plain functions backed by `db.py` and
`pipeline.py`. There is no chat loop in this repo.

## Consequences
- No dependency on a second API key or a hand-rolled agentic loop.
- The "conversational" experience in §6's example transcript happens by a human
  talking to Claude Code (or any other agent with shell access) in this repo, which
  calls the tool functions directly.
- If this project ever needs to run as an independent service (e.g. a bot, a web
  backend) rather than through an interactive coding agent, that would be a deliberate
  follow-up decision, not an assumed extension of this one.
