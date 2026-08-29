# AMM - Agentic Marketplace Moderator

A multi-agent moderation circuit for marketplace listings. A seller submits a
listing; a chain of agents evaluates it and routes it to **APPROVE**, **REJECT**,
or **REVIEW** (human-in-the-loop). Moderators work the review queue through a
conversational CLI driven by talking to Claude Code. No web UI, no separate
chat service.

For architecture, agent contracts, routing rules, and the full CLI tool
reference, see [SPEC.md](SPEC.md). For why real implementation choices diverged
from the original spec, see [docs/decisions/](docs/decisions/).

## Prerequisites

- [Claude Code](https://claude.com/claude-code), installed and authenticated
- Python 3.11+
- Docker (for the throwaway local Postgres)
- An NVIDIA API key. Get one at [build.nvidia.com](https://build.nvidia.com)

## Install

```bash
git clone git@github.com:sbadakhc/amm.git
cd amm
pip install -r requirements.txt
cp .env.example .env
```

Fill in `NVIDIA_API_KEY` in `.env`. `DATABASE_URL` is set for you in the next step.

## Configure & seed data

```bash
scripts/dev-db.sh up              # starts a throwaway Postgres, applies schema.sql
export DATABASE_URL="postgresql://amm:amm@127.0.0.1:55432/moderator"
python3 generate_synthetic_data.py # seeds 5 demo listings + 3 demo moderators
```

## Run

```bash
python3 -c "from pipeline import poll_and_process; print(poll_and_process())"
```

Or as a long-running service:

```bash
python3 service.py
```

Then work the review queue — see below.

## Working the review queue

Every listing lands in one of three places: **APPROVE**/**REJECT** (fully
automated) or **REVIEW** — the human-in-the-loop queue this section covers.
There's no web UI; a moderator works the queue by talking to Claude Code,
which calls the tool layer (`cli/tools.py`) on their behalf. Below are the
actual commands behind that conversation — say the plain-English version and
Claude Code will call these for you, or invoke them directly.

**1. See what needs review**

```
/inspect-listing --queue
```
Prints one markdown table — listing ID, title, status, decision, confidence,
matched policy rules, and an image link per row — across the whole queue (add
`--status PENDING_REVIEW` to filter). Conversationally: *"what's pending"* /
*"show me the queue"*.

**2. Eyeball a specific case**

```
/inspect-listing <listingId>
```
Prints the listing's text and every agent's findings, then shows its images
inline in the conversation (Claude Code reads each one directly — no browser
needed). Add `--serve` if you'd rather view them in an actual browser tab
(works under WSL2 via automatic port forwarding; `--stop-server` tears it
down after). Conversationally: *"show me listing <id>"* / *"let me see the
images for that one"*.

For the agent-by-agent reasoning without the images, ask to *"explain case
<id>"* — one section per agent (Evidence, Consistency, Safety, Policy,
Decision), read straight off the append-only artifact log, not a separate
summary.

**3. Record a decision**

Once you've looked it over, say what you want to do — *"approve it"*,
*"reject it, counterfeit confirmed"*, *"escalate this one for a second
opinion"*, *"the seller is appealing, they say ..."*. Each maps to one call
(`approve_listing`, `reject_listing`, `escalate_case`, `request_appeal`,
`resolve_appeal`) that appends a new decision artifact and updates the
listing's status — nothing is edited in place, so the full history stays
intact.

See SPEC.md §6 for the complete tool table and §8 for escalation/appeals, and
`docs/decisions/0011`/`0015` for why images render this way (inline Read vs.
`--serve`, and the queue-table view) rather than an external image viewer.

## Tests

```bash
pytest -v
```

Offline tests always run. Integration tests need `DATABASE_URL` set (from
above) and skip automatically otherwise.
