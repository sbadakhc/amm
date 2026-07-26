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

```
Seller → submit listing → Postgres
                              │
                     triggers workflow (in-process)
                              │
                        Intake Agent
                              │
        ┌───────────────┬────┴────┬───────────────┐
        ▼                ▼        ▼               ▼
  Evidence Agent  Consistency Agent  Safety Agent    Policy Agent
        └───────────────┴────┬────┴───────────────┘
                              ▼
                        Decision Agent
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
           APPROVE          REVIEW          REJECT
                              │                │
                    (REVIEW only) Human Queue → CLI
                              │                │
                        ESCALATED ──┐    APPEAL_REQUESTED
                        (senior     │    (moderator-relayed,
                         review)    │     no seller-facing
                              │     │     surface exists, §8.1)
                              └─────┴──→ APPROVE / REJECT
```

Single process, async fan-out/fan-in — no broker, no separate API service. The
escalation and appeal paths (SPEC.md §8) are moderator-only — the automated Decision
Agent never produces `ESCALATED` or `APPEAL_REQUESTED`, and both resolve back to a
plain APPROVE/REJECT rather than separate terminal states.

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

In practice, a moderator does all of this conversationally through Claude Code —
"show me what's pending," "explain this case," "approve it" — not by writing Python.
The tool layer (`cli/tools.py`) and the `/inspect-listing` skill are what Claude Code
calls on the moderator's behalf. See SPEC.md §6/§8 for the full tool table and
example transcripts; this section is the narrative version.

**Survey the queue.** `/inspect-listing --queue` (optionally `--status
PENDING_REVIEW` or another status) prints one table across every listing that
matches — status, the automated decision, confidence, matched policy rules, and an
image link — backed by a single image server rather than one per listing:

```
> what's pending?

| Listing     | Title                | Status         | Decision | Confidence | Policy Rules | Images |
|-------------|-----------------------|-----------------|----------|------------|--------------|--------|
| LST-4F58A1  | AK-47 ...             | REJECTED        | REJECT   | 1.00       | W001         | [0]    |
| LST-A79214  | iPhone 16 Pro Max     | PENDING_REVIEW  | REVIEW   | 1.00       | C001         | [0]    |
```

**Deep-dive one case.** `/inspect-listing <listingId>` prints the listing's full
text plus every agent artifact (Safety/Evidence/Consistency/Policy/Decision), then
shows its image(s) one at a time, paced by the moderator — either rendered inline in
the conversation, or via `--serve`, which pops a real browser tab (works even though
this dev environment has no way to launch a GUI viewer directly, see
`docs/decisions/0011`).

**Decide.** The routine actions (`from cli import tools`, same layer Claude Code
calls under the hood):
```python
tools.approve_listing(listing_id, "mod-1", "Looks fine on manual review.")
tools.reject_listing(listing_id, "mod-1", "Counterfeit branding confirmed.")
```
A REJECT here (or an automated one) increments the seller's placeholder violation
counter (§8.3) — a live count, not the static snapshot taken when the listing was
first submitted.

**Escalate for a second opinion.** Any `PENDING_REVIEW` case can go to a senior-review
tier instead of being decided immediately:
```python
tools.escalate_case(listing_id, "Ambiguous branding, want a second opinion.")
```
Resolving an escalated case needs no separate tool — `approve_listing`/
`reject_listing` already transition any listing regardless of current status.

**Handle an appeal.** This system has no seller-facing surface at all (no portal, no
API) — `request_appeal` is a moderator-invoked tool relaying an appeal that reached a
human through some other channel, not something a seller triggers directly:
```python
tools.request_appeal(listing_id, "Seller provided proof of authenticity.")
tools.resolve_appeal(listing_id, "APPROVE", "Proof accepted, overturning rejection.")
# or: tools.resolve_appeal(listing_id, "REJECT", "Upheld on review.")
```
Only `REJECTED` listings can be appealed. Resolving reuses `APPROVED`/`REJECTED` as
the real outcome rather than separate appeal-specific statuses — the artifact log's
`version` field (`"moderator-appeal-resolution"`) is what marks it as an appeal
outcome. Denying an appeal does **not** double-count the seller's violation; it was
already counted when the listing was first rejected.

**Seller account actions.** For a seller with a pattern of violations:
```python
tools.list_seller_cases(seller_id)                          # every listing from this seller
tools.suspend_seller(seller_id, "Repeated policy violations.")
tools.reinstate_seller(seller_id, "Investigation cleared them.")
```
`sellers` is an explicit placeholder (§8.1, `docs/decisions/0017`) — not assumed to
be a real customer backend's actual source of truth for seller/account identity, and
suspending a seller doesn't cascade to their existing listings (each stays
independently decided).

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
