---
name: housekeeping
description: >
  Repository health check for amm: git hygiene, secrets, test suite, and SPEC.md
  drift. Use at session start, periodically, or before a PR. Triggers on
  "housekeeping", "repo health check", "what's the status".
argument-hint: <optional: specific step numbers>
compatibility: Claude Code
metadata:
  category: maintenance
  version: "1.0"
---

# Skill: housekeeping

## Purpose

Catch accumulated debt: stray untracked files, secrets that slipped past gitignore,
a broken test suite, or SPEC.md describing something the code no longer does. Report
findings and wait for direction -- take no corrective action until directed. Each
step is independent; a failure in one doesn't block the others.

## Steps

### Step 1 -- Git status

```bash
git status --short
git branch --show-current
```
Anything unexpected here (stray temp files, `.env`, `__pycache__` not caught by
`.gitignore`)? Note it, don't clean it up without confirming first.

### Step 2 -- Secrets scan

```bash
pre-commit run gitleaks --all-files
```
A finding here is real until proven otherwise -- don't loosen `.gitleaks.toml` to make
it pass.

### Step 3 -- Pre-commit hooks

```bash
pre-commit run --all-files
```

### Step 4 -- Test suite

```bash
pytest -v
```
If `DATABASE_URL` isn't set, integration tests (`tests/test_db_integration.py`) skip
automatically -- note that they were skipped, don't report the suite as fully green
without saying so. Spin up `scripts/dev-db.sh up` first if a full run is wanted.

### Step 5 -- SPEC.md drift

Spot-check that the model names and rule IDs referenced in `agents/*.py` still match
what SPEC.md §3 says. This project's SPEC.md has been corrected multiple times after
implementation revealed something different from the original assumption
(`docs/decisions/`) -- drift here is the normal failure mode to watch for, not an edge
case.

```bash
grep -n "^MODEL\|^TEXT_MODEL\|^VISION_MODEL" agents/*.py
grep -n "nvidia/\|mistralai/" SPEC.md
```

### Step 6 -- Sample data sync

```bash
python3 -c "
import json
data = json.load(open('listings.json'))
referenced = {img['url'].rsplit('/',1)[-1] for l in data for img in l['images']}
import os
on_disk = set(os.listdir('images'))
print('Referenced but missing:', referenced - on_disk)
print('On disk but orphaned:', on_disk - referenced)
"
```
Orphaned images accumulate fast during iterative development (this happened
multiple times before this repo's first commit) -- flag them, don't silently delete
without confirming.

### Step 7 -- Report

Summarize findings as a punch list: clean vs. needs attention. Do not fix anything
found in this pass without the human's go-ahead -- housekeeping is diagnostic.
