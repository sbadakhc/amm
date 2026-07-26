# 0009. Moderator identity: known-registry authorization, not authentication

## Status
Accepted

## Context
`moderatorId` was an unchecked string passed into `approve_listing`/`reject_listing`/
`record_decision` -- anyone calling the tool could claim to be anyone, and there was no
record of who's actually an authorized moderator. Two real options:

1. A known-moderator registry (a `moderators` table: id, name, active) -- validate
   `moderatorId` against it, reject unknown/inactive ones. No passwords, no login flow.
2. Full authentication -- password hashes or API tokens per moderator, a login step,
   session/token validation on every call.

## Decision
Option 1. `moderators` table in `schema.sql`; `cli/tools._resolve_moderator` validates
against it in `approve_listing`/`reject_listing`/`record_decision`, raising clearly on
an unknown or inactive `moderatorId`. Falls back to a `MODERATOR_ID` env var when not
passed explicitly (same convention as git's `user.name`) -- still validated, an
unset/unknown/inactive default is still rejected. Added `whoami()` (not in SPEC.md's
original §6 table) so a moderator can confirm their own identity/active status.

This is proportionate because the Moderator CLI is a tool layer driven by a trusted
operator through Claude Code (ADR 0002) -- there is no network boundary where an
untrusted party could call these functions, so cryptographic proof of identity doesn't
add real protection here. It closes a real gap (literally anyone claiming to be
literally anyone) without building machinery (credential storage, token issuance and
revocation, session handling) this project has no use for yet.

## Consequences
- `generate_synthetic_data.py` seeds three demo moderators, including one inactive, so
  the rejection path is exercised by the same demo data flow as everything else, not a
  special-cased test fixture.
- If this ever becomes a network-exposed service with untrusted callers, this decision
  should be revisited -- option 2 above is the natural next step, not a given that this
  registry evolves into it on its own.
- Verified against a real Postgres instance: known/active moderator succeeds, unknown
  moderator_id is rejected, inactive moderator_id is rejected, `MODERATOR_ID` env var
  fallback resolves and is still validated.
