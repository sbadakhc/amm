# amm — Agentic Marketplace Moderator

Multi-agent moderation pipeline for marketplace listings (Postgres + Claude Code +
NVIDIA Nemotron 3.5 Content Safety). See SPEC.md for the full build spec.

## Contents
- `SPEC.md` — build spec: architecture, agent schemas, routing, storage model, CLI
- `schema.sql` — Postgres schema (listings + artifact log)
- `generate_synthetic_data.py` — synthetic listing generator for the demo
- `listings.json`, `images/` — sample generated output

## Quickstart
```bash
psql "$DATABASE_URL" -f schema.sql
export DATABASE_URL="postgresql://user:pass@localhost:5432/moderator"
python3 generate_synthetic_data.py
```
See README section in SPEC.md and inline script docstring for details.
