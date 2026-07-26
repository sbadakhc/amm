# AGENTS.md

Instructions for any coding agent (Claude Code, or otherwise) working in this repo.

## 0. Read First

- `SPEC.md` is the canonical build spec -- architecture, agent contracts, routing
  rules, storage model, CLI tool table. If code and SPEC.md disagree, that's a bug in
  one of them; figure out which, don't just pick one silently.
- `README.md` for the quickstart.

## 1. What This Is

A multi-agent moderation pipeline for marketplace listings (Postgres + Claude Code
orchestration + NVIDIA-hosted models). See SPEC.md §1 for the architecture diagram.

## 2. Branch Strategy

- `main` is stable -- no direct commits, no force-push.
- `dev` is the integration branch for ongoing work.
- Feature branches off `dev`, PRs target `main` (or `dev`, depending on what's being
  landed -- check with the human if unclear).
- Use `.claude/commands/commit-pr.md` and `finish-pr.md` for the commit/PR/cleanup
  workflow rather than improvising each time.

## 3. Working Style For This Project

This codebase was built by verifying every agent against **real** model calls and a
**real** Postgres instance, not by writing to the spec and assuming it worked --
several of the spec's original assumptions turned out to be wrong once tested for
real (wrong model name, wrong output schema, a prompt framing that produced wrong
answers on the "clean" case). SPEC.md's per-agent sections document these corrections
as they were found. Keep that habit:

- When implementing or changing an agent, test it against a real model call (this
  project uses NVIDIA's hosted API) and real sample data (`listings.json`), not just a
  mock.
- When a database change is involved, verify against a real Postgres instance (a
  throwaway Docker container is fine -- `postgres:16-alpine`, apply `schema.sql`,
  tear it down after) rather than reasoning about the SQL in the abstract.
- If reality disagrees with SPEC.md, update SPEC.md in the same change -- don't leave
  the spec describing something that isn't what the code does.

## 4. Safe Areas

Freely editable with normal care: `agents/*.py`, `cli/tools.py`, `pipeline.py`,
`intake.py`, `db.py`, `images.py`, `generate_synthetic_data.py`, `SPEC.md`.

Handle with more caution:
- `schema.sql` -- changing column types/constraints on `listings`/`artifacts` affects
  every agent; check §5 (artifact log) and §2.1 (locking) in SPEC.md first.
- `.gitleaks.toml` / `.pre-commit-config.yaml` -- don't loosen these to make a commit
  pass; fix the actual finding.

Never touch: `.env` (real credentials, gitignored -- never commit, never print into a
commit/issue/PR body). See `.claude/rules/security.md`.

## 5. Essential Commands

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in DATABASE_URL and NVIDIA_API_KEY

python3 generate_synthetic_data.py   # seeds listings.json + images/, and Postgres if DATABASE_URL is set

# run one agent standalone against a canonical doc:
python3 agents/safety_agent.py '{"listingId": "...", "title": "...", "description": "..."}'

# run the full pipeline against whatever is PENDING_MODERATION in Postgres:
python3 -c "from pipeline import poll_and_process; print(poll_and_process())"
```

Throwaway Postgres for testing:
```bash
docker run -d --name amm-postgres --network bridge \
  -e POSTGRES_USER=amm -e POSTGRES_PASSWORD=amm -e POSTGRES_DB=moderator \
  -p 55432:5432 postgres:16-alpine
psql "postgresql://amm:amm@127.0.0.1:55432/moderator" -f schema.sql
# ... test ...
docker rm -f amm-postgres
```

## 6. Definition of Done

- [ ] Tested against a real model call and/or real Postgres instance, not just a mock
- [ ] SPEC.md updated if an agent contract, schema, or routing rule changed
- [ ] No secrets in the diff (`.env`, API keys, connection strings)
- [ ] Commit follows conventional format; PR uses `.github/PULL_REQUEST_TEMPLATE.md`

## 7. GPG-Signed Commits

Never disable or bypass commit signing (`--no-gpg-sign`, `--no-verify`). If a signed
commit is needed and pinentry isn't available in an agent's session, stage the changes
and hand the exact `git commit`/`git push` command to the operator to run themselves.
