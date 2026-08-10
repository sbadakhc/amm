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

Then work the review queue conversationally through Claude Code — "what's
pending," "show me that case," "approve it." See SPEC.md §6/§8 for the full
tool reference and workflow.

## Tests

```bash
pytest -v
```

Offline tests always run. Integration tests need `DATABASE_URL` set (from
above) and skip automatically otherwise.
