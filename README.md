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

```
/preflight       # confirms every model the pipeline needs is actually callable right now
/process-queue   # claims whatever's PENDING_MODERATION and runs it through the pipeline
```

Or as a long-running service instead of one-shot batches:

```bash
python3 service.py
```

Then work the review queue — see below. For the full worked example (a moderator's
actual morning, command by command with real output), see
[Example: a moderator's morning](#example-a-moderators-morning).

## Example: a moderator's morning

A concrete run-through, captured from a real session against the seeded demo data —
listing IDs will differ on your own machine (they're generated fresh each time you
run `generate_synthetic_data.py`), but the shape of it won't.

**1. Log in and check the models are up**

```
$ claude
> /preflight
```
```
STATUS      MODEL                                        USED BY
OK          nvidia/llama-3.1-nemotron-safety-guard-8b-v3 Safety Agent (primary classifier)
UNREACHABLE mistralai/mistral-nemotron                   Safety Agent (prize-scam check), Consistency Agent (text check)
              -> ReadTimeout
OK          meta/llama-3.2-11b-vision-instruct           Evidence Agent, Consistency Agent (vision checks)
OK          nvidia/nemotron-3-embed-1b                   embeddings.py (find_similar_cases)

1/4 model(s) not usable right now.
```
One model's flaky. Not a blocker — Safety and Consistency Agents both fail open on
this specific model (`docs/decisions/0022`, `0028`), skipping the affected check
rather than blocking the whole listing. Proceed.

**2. Process the overnight batch**

```
> /process-queue
```
```
5
```
Five listings claimed and run through Evidence → Consistency → Safety → Policy →
Decision. The flaky model from step 1 did in fact drop out mid-run (visible in the
logs as `consistency check unavailable, skipping`) — the batch still completed
cleanly, nothing crashed or hung.

**3. See what needs a human**

```
> /inspect-listing --queue
```
```
| Listing | Title | Status | Decision | Confidence | Policy Rules | Images |
|---|---|---|---|---|---|---|
| LST-FA2A87 | Apple iPhone 16 Pro Max 256GB | PENDING_REVIEW | REVIEW | 0.76 | - | [0](...) |
| LST-2774A0 | Fully Automatic AK-47 Assault Rifle - Untraceable | REJECTED | REJECT | 1.00 | W001, F001, C004 | [0](...) |
| LST-2C61FB | Apple iPhone 16 Pro Max 256GB | PENDING_REVIEW | REVIEW | 1.00 | C001 | [0](...) |
| LST-C6D133 | Apple iPhone 16 Pro Max | PENDING_REVIEW | REVIEW | 0.69 | - | [0](...) |
| LST-E857CA | Sony Wireless Headphones | PENDING_REVIEW | REVIEW | 0.71 | - | [0](...) |
```
The weapon listing auto-rejected outright — no ambiguity there. `LST-2C61FB` stands
out: `REVIEW` at full confidence with a `C001` (counterfeit) hit. Worth a closer look
before the others.

**4. Eyeball the interesting one**

```
> explain case LST-2C61FB
```
```
-- EvidenceAgent --
  ocr: ['SMARTPHONE PRO 16', '256GB STORAGE']
  brandsDetected: []
  brandMismatch: True

-- ConsistencyAgent --
  checks: [{'pair': 'description_vs_images', 'consistent': False}, ...]
  checksSkipped: ['title_vs_description']
  inconsistencyScore: 0.4621

-- PolicyAgent --
  matches: [{'rule': 'C001', 'severity': 'High', 'confidence': 1.0}]

-- DecisionAgent --
  decision: REVIEW
  explanation: Matched policy rule(s): C001 (Counterfeit goods prohibited).
```
No Apple branding anywhere on the packaging despite a declared Apple brand — genuine
signal, not a glitch. (`checksSkipped` shows the flaky model from step 1 dropped one
check on this exact listing; the other checks still caught it independently.) The
seller name — visible via `/inspect-listing LST-2C61FB` — is literally "Counterfeit
Brand Trading Co." Easy call.

**5. Record the decisions**

```
> reject it, no brand corroboration on packaging, counterfeit confirmed
```
```
REJECTED | LST-2C61FB
```
```
> approve LST-E857CA, brand corroborated, no policy hits, just under the confidence bar
```
```
APPROVED | LST-E857CA
```

**6. Check how the morning went**

```
> /pipeline-stats
```
```
# Pipeline stats (all-time)

## Listings by status
- APPROVED: 1
- PENDING_REVIEW: 2
- REJECTED: 2

## Automated decisions (first fusion-v1 DecisionAgent artifact per listing)
- REJECT: 1
- REVIEW: 4
- avg confidence: 0.83

## Human review
- outcomes for automated-REVIEW listings:
  - APPROVE: 1
  - REJECT: 1
- overridden (moderator decision differs from an automated APPROVE/REJECT): 0 (n/a of reviewed)

## Signal quality
- avg Safety Agent confidence: 0.98
- avg Consistency Agent inconsistency score: 0.44
- avg automated pipeline latency: 10.4s

## Policy rule hits
- C001: 1
- W001: 1
- F001: 1
- C004: 1

## Pipeline failures
- (none)
```
Note what this *isn't*: an accuracy score. It's how often moderators agreed with the
automated pipeline where it actually committed to a verdict (`APPROVE`/`REJECT`) —
`REVIEW` isn't a verdict to agree or disagree with, so it's reported separately, not
folded into the override rate. `docs/decisions/0027` has the full reasoning.

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
