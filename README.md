# amm — Agentic Marketplace Moderator

A multi-agent moderation pipeline for marketplace listings. A seller submits a
listing; a chain of agents evaluates it and routes it to **APPROVE**, **REJECT**, or
**REVIEW** (human-in-the-loop). Moderators work the review queue through a
conversational CLI driven by talking to Claude Code — no web UI, no separate chat
service.

See [SPEC.md](SPEC.md) for the full build spec (architecture, agent contracts,
routing rules, storage model, CLI tool table) and
[docs/decisions/](docs/decisions/) for why several real implementation choices
ended up different from the original spec.

## Prerequisites

- **[Claude Code](https://claude.com/claude-code)**, installed and authenticated —
  this project's orchestration and Moderator CLI are driven entirely by talking to
  it; there's no separate chat service or web UI
  (`docs/decisions/0002-cli-tool-layer-not-chat-loop.md`).
- **Python 3.11+**
- **[Docker](https://docs.docker.com/get-docker/)** — for `scripts/dev-db.sh`'s
  throwaway Postgres (`pgvector/pgvector:pg16` image, needed for
  `listing_embeddings`/its HNSW index, §6 / `docs/decisions/0016`).
- **`git` and the [GitHub CLI](https://cli.github.com/) (`gh`)** — if you're
  following this project's issue → branch → PR → merge → cleanup workflow
  (`.claude/commands/commit-pr.md`, `finish-pr.md`).
- **An NVIDIA API key** — every model call in this pipeline goes through NVIDIA's
  hosted API (`https://integrate.api.nvidia.com`). Get one at
  [build.nvidia.com](https://build.nvidia.com). The specific model per agent is
  hardcoded, not configurable via env var — each was chosen by verifying its actual
  behavior live before committing to it (see Tech Stack below and
  `docs/decisions/0001-safety-agent-model-choice.md` for the clearest example of why
  that mattered: the originally-planned model for the Safety Agent turned out to
  return the wrong output shape entirely).

## Architecture

```mermaid
flowchart TD
    Seller[Seller submits listing] --> DB[(Postgres)]
    DB -->|PENDING_MODERATION| Poller[service.py poller]
    Poller --> Intake[Intake Agent]

    Intake --> Evidence[Evidence Agent]
    Intake --> Consistency[Consistency Agent]
    Intake --> Safety[Safety Agent]
    Intake -.->|fans out alongside,<br/>doesn't feed Decision| Embed[Embedding<br/>find_similar_cases only]

    Evidence --> Policy[Policy Agent]
    Consistency --> Policy
    Safety --> Policy

    Policy --> Decision[Decision Agent]

    Decision -->|high confidence,<br/>no rules matched| Approved[APPROVED]
    Decision -->|critical rule,<br/>high confidence| Rejected[REJECTED]
    Decision -->|otherwise| Review[PENDING_REVIEW]

    Review --> Moderator{Moderator}
    Moderator -->|approve| Approved
    Moderator -->|reject| Rejected
    Moderator -->|escalate| Escalated[ESCALATED]

    Escalated --> Senior{Senior reviewer}
    Senior -->|approve| Approved
    Senior -->|reject| Rejected

    Rejected -.->|appeal relayed,<br/>no seller-facing surface, §8.1| Appeal[APPEAL_REQUESTED]
    Appeal -->|approve| Approved
    Appeal -->|reject| Rejected
```

Single process, async fan-out/fan-in — no broker, no separate API service. The
escalation and appeal paths (SPEC.md §8) are moderator-only — the automated Decision
Agent never produces `ESCALATED` or `APPEAL_REQUESTED`, and both resolve back to a
plain APPROVED/REJECTED rather than separate terminal states.

## Tech Stack

- **Postgres** (with `pgvector`) — the raw listing row, an append-only artifact log
  (one immutable row per agent run, SPEC.md §5), the moderator registry, and listing
  embeddings for `find_similar_cases` all live in the same instance — no separate
  vector database.
- **Claude Code** — orchestration and the Moderator CLI. There's no separate chat
  loop; the CLI is a plain Python tool layer (`cli/tools.py`) driven by talking to
  Claude Code directly (see `docs/decisions/0002-cli-tool-layer-not-chat-loop.md`).
- **NVIDIA-hosted models** (via `https://integrate.api.nvidia.com`), one per agent
  that needs one:
  - **Safety Agent**: `nvidia/llama-3.1-nemotron-safety-guard-8b-v3` — content-safety
    classification with a category, not the originally-planned
    `nemotron-3.5-content-safety` (that model only returns a bare safe/unsafe verdict
    with no category — see `docs/decisions/0001-safety-agent-model-choice.md`).
  - **Evidence Agent** and **Consistency Agent**'s image checks:
    `nvidia/nemotron-nano-12b-v2-vl` (vision-language model) — OCR, brand/object
    detection, and the three image-based consistency checks.
  - **Consistency Agent**'s text check: `mistralai/mistral-nemotron` — the
    title-vs-description contradiction check.
  - **`find_similar_cases`**: `nvidia/llama-nemotron-embed-1b-v2` (text embeddings,
    `embeddings.py`), stored/queried via `pgvector`.
  - Policy Agent and Decision Agent are deterministic rule logic — no model call.

## Repo Layout

```
agents/               Evidence, Consistency, Safety, Policy, Decision agents
cli/tools.py          Moderator CLI tools (§6/§8): list_pending, explain_case, approve_listing,
                      escalate_case, request_appeal, resolve_appeal, suspend_seller, ...
intake.py             raw DB row -> canonical document mapping (§3.1)
db.py                 Postgres access: atomic claim/lock (§2.1), artifact log (§5), sellers (§8.3)
pipeline.py           orchestration: parallel fan-out -> Policy -> Decision
service.py            long-running poller + stale-claim sweep (§7)
images.py             shared file:// / s3:// image fetch helper
embeddings.py          text-embedding helper for find_similar_cases (§6)
scripts/inspect_listing.py  moderator image/queue inspection helper (§6, .claude/skills/inspect-listing/)
generate_synthetic_data.py   synthetic listing generator (5 demo scenarios)
fixtures/real_photos/        real, attributed product photos used by the generator (docs/decisions/0013)
listings.json, images/       sample generated output
schema.sql            Postgres schema (listings, artifacts, moderators, sellers, listing_embeddings)
tests/                pytest suite: offline (mocked model calls) + real-Postgres integration
docs/decisions/       ADRs for the non-obvious calls made while building this
scripts/dev-db.sh     one-command throwaway Postgres for local dev/testing
SPEC.md               the full build spec
```

## Quickstart

### 1. Install dependencies

```bash
pip install -r requirements.txt        # runtime only
# or, for development (adds pytest, pre-commit):
pip install -r requirements-dev.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Fill in `.env`:
- `DATABASE_URL` — see step 3 for a one-command local Postgres
- `NVIDIA_API_KEY` — get one at https://build.nvidia.com
- `MODERATOR_ID` (optional) — default moderator identity for the CLI tools in step 7,
  e.g. `mod-1` (one of the demo moderators seeded in step 4)

### 3. Spin up a local Postgres

```bash
scripts/dev-db.sh up     # starts a throwaway container, applies schema.sql
```

This prints the `DATABASE_URL` to export (or put in `.env`). Tear it down later with
`scripts/dev-db.sh down`.

### 4. Seed sample data

```bash
export DATABASE_URL="postgresql://amm:amm@127.0.0.1:55432/moderator"  # from step 3
python3 generate_synthetic_data.py
```

Writes `listings.json` + `images/` and inserts 5 demo listings into Postgres, each
exercising a different branch of the pipeline (clean, weapon, counterfeit brand,
inconsistent listing, risky seller history) — plus 3 demo moderators (`mod-1`,
`mod-2`, and an inactive `mod-inactive` for exercising the rejection path) into the
`moderators` table used by the CLI's moderator authorization (§6).

### 5. Run a single agent standalone

Every agent takes a canonical document (§3.1) as JSON on the command line:

```bash
python3 agents/safety_agent.py '{"listingId": "LST-DEMO", "title": "Tactical Combat Knife 8-inch", "description": "Military-grade fixed blade knife, stainless steel."}'
```

```json
{
  "listingId": "LST-DEMO",
  "agent": "SafetyAgent",
  "version": "nvidia/llama-3.1-nemotron-safety-guard-8b-v3",
  "producedAt": "2026-07-25T23:59:32.238916+00:00",
  "payload": {
    "violations": ["Guns and Illegal Weapons"],
    "confidence": 0.7772,
    "explanation": "Content flagged as unsafe: Guns and Illegal Weapons."
  }
}
```

### 6. Run the full pipeline

One-shot (processes whatever's pending right now, then returns):

```bash
python3 -c "from pipeline import poll_and_process; print(poll_and_process())"
```

Or as a long-running service -- polls for new `PENDING_MODERATION` listings and
periodically sweeps stale claims (§2.1), stops cleanly on Ctrl-C:

```bash
python3 service.py
```

Either way, claims listings, fans out to Evidence/Consistency/Safety in parallel, then
Policy, then Decision, writing one artifact per agent run and updating each listing's
status (`APPROVED` / `REJECTED` / `PENDING_REVIEW`).

### 7. Work the review queue

Everything below is conversational through Claude Code in practice — "what's
pending," "show me that case," "approve it." The tool layer (`cli/tools.py`) and the
`/inspect-listing` skill are what Claude Code calls on your behalf; the commands
shown here are what actually runs under the hood, for reference. Full tool table and
transcripts: SPEC.md §6/§8.

#### Routing model: what reaches you, and why

A moderator never sees every listing — most are decided automatically by the
pipeline. Only three statuses need a human:

| Status | How it got there | What it means for you |
|---|---|---|
| `PENDING_REVIEW` | Decision Agent's confidence was too low to auto-approve, or a policy rule matched at non-critical severity | Needs a first-pass human decision |
| `ESCALATED` | A moderator (possibly you, earlier) flagged a `PENDING_REVIEW` case for a second opinion | Needs a senior/second reviewer's decision |
| `APPEAL_REQUESTED` | A moderator relayed a dispute against a `REJECTED` listing that reached them through some other channel — this system has no seller-facing portal (§8.1) | Needs an appeal outcome: uphold or overturn |

Everything else — `APPROVED`, `REJECTED` with no appeal, anything still
`PENDING_MODERATION`/`PROCESSING` — needs nothing from you; the pipeline decided it,
or a prior decision stands. `APPROVED` listings can never come back to you either —
appeals only apply to `REJECTED` (§8.2), since there's no real use case for
contesting an approval.

#### Step 1 — Confirm your identity

`whoami` resolves your `moderatorId` (env var or explicit) against the `moderators`
registry and confirms you're active before you act on anything:

```
> whoami
{"moderator_id": "mod-1", "name": "Alex Moderator", "active": true}
```

#### Step 2 — Survey what needs you

`/inspect-listing --queue` prints one table across every listing in the three
statuses above — status, the automated decision, confidence, matched policy rules,
and an image link per row — backed by a single image server rather than one per
listing:

```
> what's pending?

| Listing    | Title             | Status          | Decision | Confidence | Policy Rules | Images |
|------------|-------------------|-----------------|----------|------------|--------------|--------|
| LST-A79214 | iPhone 16 Pro Max | PENDING_REVIEW  | REVIEW   | 1.00       | C001         | [0]    |
| LST-C65999 | Sony Headphones   | ESCALATED       | REVIEW   | 0.83       | -            | [0]    |
| LST-F7BEC4 | AK-47 Rifle       | APPEAL_REQUESTED| REJECT   | 1.00       | W001         | [0]    |
```

Filter to one status at a time if you only want one kind of work right now:

```
/inspect-listing --queue --status PENDING_REVIEW
/inspect-listing --queue --status ESCALATED
/inspect-listing --queue --status APPEAL_REQUESTED
```

#### Step 3 — Pick a case and look closely

`/inspect-listing <listingId>` prints the full listing text, every agent's artifact
(Safety/Evidence/Consistency/Policy/Decision), and shows its image(s) one at a
time — inline in the conversation, or via `--serve` for a real browser tab (works
even though this dev environment has no way to launch a GUI viewer directly, see
`docs/decisions/0011`).

#### Step 4 — Cross-check before deciding (optional)

`tools` below is `cli.tools` (`from cli import tools`), the same layer Claude Code
calls under the hood — you'd never write this Python directly, it's shown for
reference.

```python
tools.find_similar_cases(listing_id)   # how were comparable listings handled?
tools.search_policy("C001")            # what does this rule actually cover?
```

#### Step 5 — Act, based on which queue the case came from

**From `PENDING_REVIEW`** — decide it:

```python
tools.approve_listing(listing_id, "mod-1", "Looks fine on manual review.")
tools.reject_listing(listing_id, "mod-1", "Counterfeit branding confirmed.")
```

A REJECT increments the seller's violation count (§8.3) — live, not the static
snapshot taken at submission. If you're unsure, escalate instead of guessing:

```python
tools.escalate_case(listing_id, "Ambiguous branding, want a second opinion.")
```

**From `ESCALATED`** — same two decision tools as above; nothing special is needed
to resolve an escalation, `approve_listing`/`reject_listing` transition any listing
regardless of current status.

**From `APPEAL_REQUESTED`** — resolve the appeal, not a plain approve/reject:

```python
tools.resolve_appeal(listing_id, "APPROVE", "Proof accepted, overturning rejection.")
tools.resolve_appeal(listing_id, "REJECT", "Upheld on review.")
```

Denying (`REJECT`) does **not** double-count the seller's violation — it was already
counted when the listing was first rejected.

#### Step 6 — Repeat

Back to Step 2 until the queue's clear.

#### Step 7 — Seller-level actions (as needed)

Whenever a pattern across listings warrants it, rather than a single case:

```python
tools.list_seller_cases(seller_id)     # everything from this seller
tools.suspend_seller(seller_id, "Repeated policy violations.")
tools.reinstate_seller(seller_id, "Investigation cleared them.")
```

`sellers` is an explicit placeholder (§8.1, `docs/decisions/0017`) — not assumed to
be a real customer backend's actual source of truth — and suspending doesn't cascade
to the seller's existing listings; each stays independently decided.

### 8. Run the tests

```bash
pytest -v
```

Offline tests (mocked model calls, deterministic Policy/Decision logic) always run.
`tests/test_db_integration.py` needs a real Postgres (`DATABASE_URL` set, e.g. from
step 3) and skips itself automatically otherwise.

## Development

See [AGENTS.md](AGENTS.md) for the working conventions this project follows —
notably: verify every agent against a real model call and real data before trusting
its behavior, not just against a mock. `CLAUDE.md` has Claude Code-specific notes,
`.claude/skills/add-agent` walks through adding a new pipeline agent the same way the
existing ones were built.
