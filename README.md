# amm — Agentic Marketplace Moderator

A multi-agent moderation pipeline for marketplace listings. A seller submits a
listing; a chain of agents evaluates it and routes it to **APPROVE**, **REJECT**,
or **REVIEW** (human-in-the-loop). Moderators work the review queue through a
conversational CLI driven by talking to Claude Code — no web UI, no separate
chat service.

📄 **[SPEC.md](SPEC.md)** — full build spec (architecture, agent contracts, routing rules, storage model, CLI tool table)
📄 **[docs/decisions/](docs/decisions/)** — ADRs explaining where the real implementation diverged from the spec, and why

## Table of Contents

- [Prerequisites](#prerequisites)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Repo Layout](#repo-layout)
- [Quickstart](#quickstart)
  1. [Install dependencies](#1-install-dependencies)
  2. [Configure credentials](#2-configure-credentials)
  3. [Spin up a local Postgres](#3-spin-up-a-local-postgres)
  4. [Seed sample data](#4-seed-sample-data)
  5. [(Optional) Local S3 storage for image URLs](#5-optional-local-s3-storage-for-image-urls)
  6. [Run a single agent standalone](#6-run-a-single-agent-standalone)
  7. [Run the full pipeline](#7-run-the-full-pipeline)
  8. [Work the review queue](#8-work-the-review-queue)
  9. [Run the tests](#9-run-the-tests)
- [CLI Tool Reference](#cli-tool-reference)
- [Development](#development)

---

## Prerequisites

| Requirement | Why you need it |
|---|---|
| **[Claude Code](https://claude.com/claude-code)**, installed and authenticated | Drives this project's orchestration and Moderator CLI directly — there's no separate chat service or web UI ([`docs/decisions/0002`](docs/decisions/0002-cli-tool-layer-not-chat-loop.md)) |
| **Python 3.11+** | Runtime for the pipeline and agents |
| **[Docker](https://docs.docker.com/get-docker/)** | Runs `scripts/dev-db.sh`'s throwaway Postgres (`pgvector/pgvector:pg16`), needed for `listing_embeddings` and its HNSW index (SPEC §6 / [`docs/decisions/0016`](docs/decisions/)). Also runs `scripts/dev-minio.sh` if you want to test `s3://` image URLs locally — see [step 5](#5-optional-local-s3-storage-for-image-urls) |
| **`git` + [GitHub CLI](https://cli.github.com/) (`gh`)** | Needed if you follow this project's issue → branch → PR → merge → cleanup workflow (`.claude/commands/commit-pr.md`, `finish-pr.md`) |
| **An NVIDIA API key** | Every model call goes through NVIDIA's hosted API (`https://integrate.api.nvidia.com`). Get one at [build.nvidia.com](https://build.nvidia.com) |

> **Note:** the model used per agent is hardcoded, not configurable via env var —
> each was chosen by verifying its actual behavior live before committing to it.
> See [Tech Stack](#tech-stack) and [`docs/decisions/0001`](docs/decisions/0001-safety-agent-model-choice.md)
> for the clearest example of why that mattered: the originally-planned Safety
> Agent model turned out to return the wrong output shape entirely.

## Architecture

Single process, async fan-out/fan-in — no broker, no separate API service.

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

    Decision -->|autoReject rule matched,<br/>any confidence| Rejected[REJECTED]
    Decision -->|critical rule,<br/>confidence >= threshold| Rejected
    Decision -->|no rules matched,<br/>confidence >= threshold| Approved[APPROVED]
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

A few behaviors worth knowing before you read the code:

- **Confidence thresholds are configurable, not hard-coded** — `CRITICAL_REJECT_THRESHOLD`,
  `AUTO_APPROVE_THRESHOLD`, and the seller-history adjustment are all env vars
  (defaults below, see [step 2](#2-configure-credentials)). One policy rule can be
  flagged `autoReject`, which rejects regardless of confidence — everything else
  is confidence-gated.
- **No silent approvals on failure** — if any agent errors or times out, the
  listing routes to `PENDING_REVIEW` with a `Pipeline` artifact recording the
  stage and error, visible via `explain_case`/`/inspect-listing`. It never falls
  through to APPROVED.
- **Crash recovery lands on a human, not a retry** — `service.py` periodically
  sweeps listings stuck in `PROCESSING` past a lease timeout and moves them to
  `PENDING_REVIEW` (not back to `PENDING_MODERATION`), so a crashed worker's
  in-flight listing doesn't retry automatically — a moderator sees it.
- The escalation and appeal paths (SPEC.md §8) are **moderator-only** — the
  automated Decision Agent never produces `ESCALATED` or `APPEAL_REQUESTED`, and
  both resolve back to a plain `APPROVED`/`REJECTED` rather than separate
  terminal states.

## Tech Stack

**Postgres** (with `pgvector`) holds everything in one instance — no separate
vector database: the raw listing row, an append-only artifact log (one
immutable row per agent run, SPEC.md §5), the moderator and seller registries,
and listing embeddings for `find_similar_cases`.

**Claude Code** drives orchestration and the Moderator CLI. There's no
separate chat loop — the CLI is a plain Python tool layer (`cli/tools.py`)
called by talking to Claude Code directly (see
[`docs/decisions/0002`](docs/decisions/0002-cli-tool-layer-not-chat-loop.md)).

**NVIDIA-hosted models**, one per agent that needs one (via
`https://integrate.api.nvidia.com`):

| Agent / Function | Model | Purpose |
|---|---|---|
| Safety Agent | `nvidia/llama-3.1-nemotron-safety-guard-8b-v3` | Content-safety classification *with* a category — not the originally-planned `nemotron-3.5-content-safety`, which only returns a bare safe/unsafe verdict ([`docs/decisions/0001`](docs/decisions/0001-safety-agent-model-choice.md)) |
| Evidence Agent (all image analysis) + Consistency Agent (image checks) | `nvidia/nemotron-nano-12b-v2-vl` | Vision-language model: OCR, object/brand detection, certificate/serial/expiry/country-of-origin extraction (Evidence Agent), plus Consistency Agent's own lightweight image checks (it doesn't reuse Evidence Agent's output, so it isn't blocked waiting on it) |
| Consistency Agent (text check) | `mistralai/mistral-nemotron` | Title-vs-description contradiction check |
| `find_similar_cases` | `nvidia/llama-nemotron-embed-1b-v2` | Text embeddings (`embeddings.py`) on title+description, stored/queried via `pgvector`'s cosine distance operator; computed once per listing during `pipeline.run_fusion` |
| Policy Agent, Decision Agent | — | Deterministic rule logic, no model call |

## Repo Layout

| Path | What it is |
|---|---|
| `agents/` | Evidence, Consistency, Safety, Policy, Decision agents |
| `cli/tools.py` | Moderator CLI tools (§6/§8) — full reference in [CLI Tool Reference](#cli-tool-reference) below |
| `intake.py` | Raw DB row → canonical document mapping (§3.1) |
| `db.py` | Postgres access: atomic claim/lock (§2.1), artifact log (§5), sellers (§8.3) |
| `pipeline.py` | Orchestration: parallel fan-out → Policy → Decision; per-agent failure handling |
| `service.py` | Long-running poller + stale-claim sweep (§7) |
| `images.py` | Shared `file://` / `s3://` image fetch helper |
| `embeddings.py` | Text-embedding helper for `find_similar_cases` (§6) |
| `scripts/inspect_listing.py` | Moderator image/queue inspection helper (§6, `.claude/skills/inspect-listing/`) |
| `scripts/dev-db.sh` | One-command throwaway Postgres for local dev/testing (`up` / `seed` / `psql` / `down`) |
| `scripts/dev-minio.sh` | One-command throwaway MinIO (S3-compatible) for testing `s3://` image URLs (`up` / `upload-demo-images` / `down`) |
| `generate_synthetic_data.py` | Synthetic listing generator (5 demo scenarios) |
| `fixtures/real_photos/` | Real, attributed product photos used by the generator ([`docs/decisions/0013`](docs/decisions/)) |
| `listings.json`, `images/` | Sample generated output |
| `schema.sql` | Postgres schema (listings, artifacts, moderators, sellers, listing_embeddings) |
| `tests/` | Pytest suite — offline (mocked model calls) + real-Postgres integration |
| `docs/decisions/` | ADRs for the non-obvious calls made while building this |
| `SPEC.md` | The full build spec |

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

**Required:**

| Variable | Notes |
|---|---|
| `DATABASE_URL` | See [step 3](#3-spin-up-a-local-postgres) for a one-command local Postgres |
| `NVIDIA_API_KEY` | Get one at [build.nvidia.com](https://build.nvidia.com) |

**Optional — S3 image storage** (only needed if you want to test `s3://` URLs rather than the default local `file://` demo images — see [step 5](#5-optional-local-s3-storage-for-image-urls)):

| Variable | Notes |
|---|---|
| `S3_ENDPOINT_URL` | Leave unset for real AWS S3's default credential/endpoint resolution. Set to a self-hosted S3-compatible store, e.g. MinIO |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Credentials for the store above |
| `AWS_REGION` | Defaults to `us-east-1` |

**Optional — Decision/Policy Agent thresholds** (§4, §3.5 — read once at process
start, so a change requires restarting `service.py` or whatever imported them):

| Variable | Default | Purpose |
|---|---|---|
| `CRITICAL_REJECT_THRESHOLD` | `0.95` | Confidence needed to auto-reject on a Critical-severity rule match |
| `AUTO_APPROVE_THRESHOLD` | `0.90` | Confidence needed to auto-approve when no rule matched |
| `SELLER_HISTORY_ADJUSTMENT_PER_VIOLATION` | `0.05` | Confidence shift per prior seller violation, toward whichever way it's already trending |
| `SELLER_HISTORY_ADJUSTMENT_CAP` | `0.20` | Maximum total shift from seller history, regardless of violation count |
| `CONSISTENCY_THRESHOLD` | `0.48` | Inconsistency score above which Policy Agent's C004 rule ("misleading product information") matches. Tuned from real model-call data — if you see `0.30` anywhere (including a stale comment in `.env.example`), that's the pre-tuning value; `0.48` is what the code actually defaults to ([`docs/decisions/0014`](docs/decisions/0014-consistency-threshold-tuned-from-real-data.md)) |

**Optional — poller service** (`service.py`, all read once at process start):

| Variable | Default | Purpose |
|---|---|---|
| `POLL_INTERVAL_SECONDS` | `5` | Delay between poll cycles |
| `SWEEP_INTERVAL_SECONDS` | `60` | How often to sweep stale `PROCESSING` claims |
| `SWEEP_TIMEOUT_MINUTES` | `5` | Lease timeout before a `PROCESSING` listing is considered stale |
| `POLL_BATCH_SIZE` | `10` | Listings claimed per poll cycle |

**Default moderator identity:**

| Variable | Notes |
|---|---|
| `MODERATOR_ID` *(optional)* | Default moderator identity for the CLI tools in [step 8](#8-work-the-review-queue), e.g. `mod-1` (seeded in step 4). Still validated against the `moderators` table on every call — this is a default, not a bypass |

### 3. Spin up a local Postgres

```bash
scripts/dev-db.sh up     # starts a throwaway container, applies schema.sql
```

Prints the `DATABASE_URL` to export (or put in `.env`). Other subcommands:

```bash
scripts/dev-db.sh seed   # runs generate_synthetic_data.py against it (same as step 4)
scripts/dev-db.sh psql   # opens a psql shell against it
scripts/dev-db.sh down   # stops and removes the container
```

### 4. Seed sample data

```bash
export DATABASE_URL="postgresql://amm:amm@127.0.0.1:55432/moderator"  # from step 3
python3 generate_synthetic_data.py
```

(Equivalent to `scripts/dev-db.sh seed` if `DATABASE_URL` is already exported.)

Writes `listings.json` + `images/` and inserts:
- **5 demo listings**, each exercising a different pipeline branch (clean, weapon, counterfeit brand, inconsistent listing, risky seller history)
- **3 demo moderators** into the `moderators` table (`mod-1`, `mod-2`, and an inactive `mod-inactive` for exercising the rejection path)

Demo images use `file://` URLs by default — no S3/MinIO needed for this path.

### 5. (Optional) Local S3 storage for image URLs

Only needed if you want Evidence/Consistency Agents to fetch images over
`s3://` instead of the default local `file://` demo path:

```bash
scripts/dev-minio.sh up                  # starts a throwaway MinIO container, creates a bucket, prints S3 env vars
scripts/dev-minio.sh upload-demo-images  # pushes images/*.png, prints their s3:// URLs
```

Set the printed `S3_ENDPOINT_URL` / `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
in your shell or `.env`, then re-point a listing's `images[].url` at one of the
printed `s3://` URLs to exercise that path. Tear down with:

```bash
scripts/dev-minio.sh down
```

### 6. Run a single agent standalone

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

### 7. Run the full pipeline

**One-shot** (processes whatever's pending right now, then returns):

```bash
python3 -c "from pipeline import poll_and_process; print(poll_and_process())"
```

**Long-running service** — polls for new `PENDING_MODERATION` listings,
periodically sweeps stale claims to `PENDING_REVIEW` (see [Architecture](#architecture)),
stops cleanly on Ctrl-C:

```bash
python3 service.py
```

Tunable via the poller env vars in [step 2](#2-configure-credentials). Either
way: claims listings → fans out to Evidence/Consistency/Safety in parallel →
Policy → Decision, writing one artifact per agent run and updating each
listing's status (`APPROVED` / `REJECTED` / `PENDING_REVIEW`).

### 8. Work the review queue

Everything below is conversational through Claude Code in practice — *"what's
pending," "show me that case," "approve it."* The tool layer (`cli/tools.py`)
and the `/inspect-listing` skill are what Claude Code calls on your behalf;
the commands shown here are what actually runs under the hood, for reference.
Full tool table below and in SPEC.md §6/§8.

#### Routing model: what reaches you, and why

A moderator never sees every listing — most are decided automatically by the
pipeline. Only three statuses need a human:

| Status | How it got there | What it means for you |
|---|---|---|
| `PENDING_REVIEW` | Decision Agent's confidence was too low to auto-approve, a policy rule matched at non-critical severity, an agent errored/timed out, or a stale `PROCESSING` claim was swept | Needs a first-pass human decision |
| `ESCALATED` | A moderator (possibly you, earlier) flagged a `PENDING_REVIEW` case for a second opinion | Needs a senior/second reviewer's decision |
| `APPEAL_REQUESTED` | A moderator relayed a dispute against a `REJECTED` listing that reached them through some other channel — this system has no seller-facing portal (§8.1) | Needs an appeal outcome: uphold or overturn |

Everything else — `APPROVED`, `REJECTED` with no appeal, anything still
`PENDING_MODERATION`/`PROCESSING` — needs nothing from you; the pipeline
decided it, or a prior decision stands. `APPROVED` listings can never come
back to you either — appeals only apply to `REJECTED` (§8.2), since there's no
real use case for contesting an approval.

#### Step 1 — Confirm your identity

`whoami` resolves your `moderatorId` (env var or explicit) against the
`moderators` registry and confirms you're active before you act on anything:

```
> whoami
{"moderator_id": "mod-1", "name": "Alex Moderator", "active": true}
```

#### Step 2 — Survey what needs you

`/inspect-listing --queue` prints one table across every listing in the three
statuses above — status, the automated decision, confidence, matched policy
rules, and an image link per row — backed by a single image server rather
than one per listing:

```
> what's pending?

| Listing    | Title             | Status          | Decision | Confidence | Policy Rules | Images |
|------------|-------------------|-----------------|----------|------------|--------------|--------|
| LST-A79214 | iPhone 16 Pro Max | PENDING_REVIEW  | REVIEW   | 1.00       | C001         | [0]    |
| LST-C65999 | Sony Headphones   | ESCALATED       | REVIEW   | 0.83       | -            | [0]    |
| LST-F7BEC4 | AK-47 Rifle       | APPEAL_REQUESTED| REJECT   | 1.00       | W001         | [0]    |
```

Filter to one status at a time if you only want one kind of work right now:

```bash
/inspect-listing --queue --status PENDING_REVIEW
/inspect-listing --queue --status ESCALATED
/inspect-listing --queue --status APPEAL_REQUESTED
```

#### Step 3 — Pick a case and look closely

`/inspect-listing <listingId>` prints the full listing text, every agent's
artifact (Safety/Evidence/Consistency/Policy/Decision), and shows its
image(s) one at a time — inline in the conversation, or via `--serve` for a
real browser tab (works even though this dev environment has no way to launch
a GUI viewer directly, see [`docs/decisions/0011`](docs/decisions/)).

#### Step 4 — Cross-check before deciding *(optional)*

`tools` below is `cli.tools` (`from cli import tools`), the same layer Claude
Code calls under the hood — you'd never write this Python directly, it's
shown for reference.

```python
tools.find_similar_cases(listing_id)   # how were comparable listings handled? (needs the pipeline to have already run on this listing)
tools.search_policy("C001")            # what does this rule actually cover?
tools.get_listing(listing_id)          # full document + every artifact
```

#### Step 5 — Act, based on which queue the case came from

**From `PENDING_REVIEW`** — decide it:

```python
tools.approve_listing(listing_id, "mod-1", "Looks fine on manual review.")
tools.reject_listing(listing_id, "mod-1", "Counterfeit branding confirmed.")
```

A `REJECT` increments the seller's violation count (§8.3) — live, not the
static snapshot taken at submission. If you're unsure, escalate instead of
guessing:

```python
tools.escalate_case(listing_id, "Ambiguous branding, want a second opinion.")
```

**From `ESCALATED`** — same two decision tools as above; nothing special is
needed to resolve an escalation. `approve_listing`/`reject_listing` transition
any listing regardless of current status.

**From `APPEAL_REQUESTED`** — resolve the appeal, not a plain approve/reject:

```python
tools.resolve_appeal(listing_id, "APPROVE", "Proof accepted, overturning rejection.")
tools.resolve_appeal(listing_id, "REJECT", "Upheld on review.")
```

Denying (`REJECT`) does **not** double-count the seller's violation — it was
already counted when the listing was first rejected.

#### Step 6 — Repeat

Back to Step 2 until the queue's clear.

#### Step 7 — Seller-level actions *(as needed)*

Whenever a pattern across listings warrants it, rather than a single case:

```python
tools.list_seller_cases(seller_id)     # everything from this seller
tools.suspend_seller(seller_id, "Repeated policy violations.")
tools.reinstate_seller(seller_id, "Investigation cleared them.")
```

`sellers` is an explicit placeholder (§8.1, [`docs/decisions/0017`](docs/decisions/))
— not assumed to be a real customer backend's actual source of truth — and
suspending doesn't cascade to the seller's existing listings; each stays
independently decided. There's no `terminate_seller` tool yet — `TERMINATED`
is a valid schema status that nothing currently produces.

#### Step 8 — Re-run analysis *(as needed)*

If a model is upgraded or an agent's logic changes, you can re-analyze a
listing without losing its history — this appends new artifacts rather than
overwriting, so it's safe to call even on a terminal (`APPROVED`/`REJECTED`)
listing:

```python
tools.rerun_analysis(listing_id)                    # full Evidence/Consistency/Safety/Policy/Decision chain
tools.rerun_analysis(listing_id, agent="SafetyAgent")  # just one agent
```

### 9. Run the tests

```bash
pytest -v
```

Offline tests (mocked model calls, deterministic Policy/Decision logic) always
run. `tests/test_db_integration.py` needs a real Postgres (`DATABASE_URL` set,
e.g. from step 3) and skips itself automatically otherwise.

## CLI Tool Reference

The full `cli/tools.py` surface, grouped by what it's for. All authorization
(not authentication — no passwords/tokens, see
[`docs/decisions/0009`](docs/decisions/0009-moderator-auth-registry.md)) checks
the given or `MODERATOR_ID`-defaulted moderator against the `moderators` table.

| Function | Signature | What it does |
|---|---|---|
| `whoami` | `(moderator_id=None)` | Confirms your moderator identity and active status |
| `list_pending` | `(limit=None, category=None)` | The `PENDING_REVIEW` queue |
| `get_listing` | `(listing_id)` | Full listing document + every artifact |
| `explain_case` | `(listing_id)` | All artifacts for a listing, per-agent, straight from the artifact log |
| `show_images` | `(listing_id)` | Raw `file://`/`s3://` image URLs (use `/inspect-listing` to actually view them) |
| `search_policy` | `(query)` | Looks up rules in the policy registry by ID or description |
| `find_similar_cases` | `(listing_id, k=5)` | Nearest listings by embedding similarity — requires the pipeline to have already run on this listing |
| `record_decision` | `(listing_id, decision, reason, moderator_id=None, ...)` | Low-level primitive behind every decision tool below — appends a moderator-override `DecisionAgent` artifact and updates status |
| `approve_listing` | `(listing_id, moderator_id=None, note=None)` | Approves any listing regardless of current status |
| `reject_listing` | `(listing_id, moderator_id=None, reason)` | Rejects and increments the seller's violation count; `reason` is required |
| `escalate_case` | `(listing_id, reason, moderator_id=None)` | `PENDING_REVIEW` → `ESCALATED`; fails if not currently `PENDING_REVIEW` |
| `request_appeal` | `(listing_id, reason, moderator_id=None)` | `REJECTED` → `APPEAL_REQUESTED`; fails if not currently `REJECTED` |
| `resolve_appeal` | `(listing_id, decision, reason, moderator_id=None)` | Closes an `APPEAL_REQUESTED` case as `APPROVE`/`REJECT`; doesn't double-count the violation |
| `list_seller_cases` | `(seller_id)` | Every listing tied to one seller |
| `suspend_seller` | `(seller_id, reason, moderator_id=None)` | `ACTIVE` → `SUSPENDED`; doesn't cascade to existing listings |
| `reinstate_seller` | `(seller_id, reason, moderator_id=None)` | `SUSPENDED` → `ACTIVE` |
| `rerun_analysis` | `(listing_id, agent=None)` | Re-runs the full chain or one named agent, appending new artifacts — safe on terminal listings |

## Development

See [AGENTS.md](AGENTS.md) for the working conventions this project follows —
notably: verify every agent against a real model call and real data before
trusting its behavior, not just against a mock.

- `CLAUDE.md` — Claude Code-specific notes
- `.claude/skills/add-agent` — walks through adding a new pipeline agent the same way the existing ones were built
