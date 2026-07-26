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
                              │
                    (REVIEW only) Human Queue → CLI
```

Single process, async fan-out/fan-in — no broker, no separate API service.

## Tech Stack

- **Postgres** — the raw listing row plus an append-only artifact log (one immutable
  row per agent run); see SPEC.md §5.
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
  - Policy Agent and Decision Agent are deterministic rule logic — no model call.

## Repo Layout

```
agents/               Evidence, Consistency, Safety, Policy, Decision agents
cli/tools.py          Moderator CLI tools (§6): list_pending, explain_case, approve_listing, ...
intake.py             raw DB row -> canonical document mapping (§3.1)
db.py                 Postgres access: atomic claim/lock (§2.1), artifact log (§5)
pipeline.py           orchestration: parallel fan-out -> Policy -> Decision
generate_synthetic_data.py   synthetic listing generator (5 demo scenarios)
listings.json, images/       sample generated output
schema.sql            Postgres schema (listings + artifacts)
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
inconsistent listing, risky seller history).

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

```bash
python3 -c "from pipeline import poll_and_process; print(poll_and_process())"
```

Claims every `PENDING_MODERATION` listing, fans out to Evidence/Consistency/Safety in
parallel, then Policy, then Decision, writing one artifact per agent run and updating
each listing's status (`APPROVED` / `REJECTED` / `PENDING_REVIEW`).

### 7. Work the review queue via the CLI tools

```python
from cli import tools

pending = tools.list_pending()
listing_id = pending[0]["listing_id"]

tools.explain_case(listing_id)          # every agent's artifact, in order
tools.approve_listing(listing_id, "mod-1", "Looks fine on manual review.")
```

In practice, a moderator does this conversationally through Claude Code rather than
writing Python directly — e.g. "show me the next pending case," "explain it," "approve
it." See SPEC.md §6 for the full tool table and example transcript.

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
