---
project: agentic-marketplace-moderator
type: system-spec
version: 0.3.0
status: draft
audience: ai-coding-agent
---

# Agentic Marketplace Moderator — Build Spec

A multi-agent moderation pipeline for marketplace listings. A seller submits a listing;
a chain of agents evaluates it and routes it to **APPROVE**, **REJECT**, or **REVIEW**
(human-in-the-loop). Moderators work the review queue entirely through a conversational
CLI — no web UI.

Stack: **Postgres** (storage), **Claude Code** (orchestration + CLI), **NVIDIA Nemotron
3.5 Content Safety** (called via its hosted API for the Safety Agent step).

---

## 1. Architecture

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

Intake runs first (produces the canonical document). Evidence, Consistency, Safety, and
Policy then run in parallel off that document — Consistency depends only on the canonical
document too, not on Evidence's output, so it doesn't need to wait in line behind it.
Decision Agent waits on all four. Single process, async fan-out/fan-in — no broker, no
separate API service.

---

## 2. Listing State Machine

Listings arrive in the DB with `status: "PENDING_MODERATION"` — that value is the
workflow trigger, not a status the workflow assigns.

```
PENDING_MODERATION → PROCESSING →
    APPROVED               (terminal)
    REJECTED                (terminal)
    PENDING_REVIEW →
        APPROVED (by moderator)   (terminal)
        REJECTED (by moderator)   (terminal)
    FAILED (agent error) → PENDING_REVIEW   (never silent-approve on failure)
```

### 2.1 Claiming Listings (Locking Model)

The poller claims work with a single atomic transaction — no lock table, no broker:

```sql
UPDATE listings
SET status = 'PROCESSING', updated_at = now()
WHERE listing_id IN (
    SELECT listing_id FROM listings
    WHERE status = 'PENDING_MODERATION'
    ORDER BY created_at
    LIMIT :batch_size
    FOR UPDATE SKIP LOCKED
)
RETURNING *;
```

`FOR UPDATE SKIP LOCKED` means a row already locked by another in-flight transaction is
skipped, not blocked on — two poller instances (or a restarted poller racing its own
predecessor) can never claim the same listing twice. This is what makes it safe to run
more than one poller even though each individual pipeline run stays single-process
fan-out/fan-in (§1); no cross-process coordination beyond the one `UPDATE` is needed.

**Stale claims.** A worker that crashes mid-pipeline leaves its row in `PROCESSING`
indefinitely. A sweep, run on a timer (e.g. every minute), resets any row that has been
`PROCESSING` longer than a lease timeout (default 5 minutes, configurable) to
`PENDING_REVIEW` flagged `FAILED` — the same terminal-on-error path as §4, not a special
case:

```sql
UPDATE listings
SET status = 'PENDING_REVIEW', updated_at = now()
WHERE status = 'PROCESSING' AND updated_at < now() - interval '5 minutes';
```

Requires an `updated_at` column on `listings`, touched on every status transition
(added to `schema.sql`).

---

## 3. Agents

### 3.1 Intake Agent
Reads a listing row where `status = "PENDING_MODERATION"` and maps it to the canonical
document every other agent consumes. Does not need to introspect the schema — this is
the actual listing shape:

**In (DB row)**
```json
{
  "listingId": "LST-100234",
  "seller": { "sellerId": "SUP-9281", "companyName": "Global Trade Ltd",
              "country": "United Kingdom", "verified": true, "rating": 4.8,
              "previousViolations": 0 },
  "title": "Apple iPhone 16 Pro Max 256GB",
  "description": "Brand new, factory sealed with international warranty.",
  "category": { "id": "electronics.mobile", "name": "Mobile Phones" },
  "price": { "amount": 899.99, "currency": "GBP" },
  "quantity": 100,
  "condition": "new",
  "brand": "Apple",
  "model": "iPhone 16 Pro Max",
  "sku": "APL-IP16PM-256",
  "images": [ { "id": "img-1", "url": "s3://listings/img1.jpg" },
              { "id": "img-2", "url": "s3://listings/img2.jpg" } ],
  "attributes": { "colour": "Black", "storage": "256GB", "origin": "China" },
  "shipping": { "location": "London", "leadTimeDays": 5 },
  "createdAt": "2026-07-24T20:45:31Z",
  "status": "PENDING_MODERATION"
}
```

**Out (canonical)**
```json
{
  "listingId": "LST-100234",
  "title": "Apple iPhone 16 Pro Max 256GB",
  "description": "Brand new, factory sealed with international warranty.",
  "images": ["s3://listings/img1.jpg", "s3://listings/img2.jpg"],
  "sellerId": "SUP-9281",
  "sellerVerified": true,
  "sellerPreviousViolations": 0,
  "categoryId": "electronics.mobile",
  "declaredBrand": "Apple",
  "condition": "new"
}
```
Images are `s3://` URIs, not HTTP URLs — Evidence and Consistency Agents fetch them via
`images.fetch_image_bytes` (a shared `boto3` helper), not a plain web fetch. Works
against real AWS S3 or any S3-compatible store (self-hosted via MinIO for local
dev/test — see `docs/decisions/0006-s3-storage-self-hosted-minio.md`).

`declaredBrand` is carried through specifically so the Evidence Agent can cross-check it
against whatever brand it detects from the images/OCR (see §3.2) — that mismatch is what
drives the "counterfeit branding" case already used as the CLI example in §6.

`sellerPreviousViolations` is carried through as a plain field, not a separate agent —
the Decision Agent factors seller history directly into confidence/severity (§3.6)
without needing a dedicated "risk agent" to compute it.

### 3.2 Evidence Agent
Facts only, no judgment. OCR, image understanding, brand/object detection, and
document-level extraction (certificate numbers, serial numbers, expiry dates, country of
origin where visible on packaging/labels) via vision-language model
`nvidia/nemotron-nano-12b-v2-vl`, one call per image, results merged across all of a
listing's images. Compares detected brand(s) against the canonical document's
`declaredBrand` and flags a mismatch if they disagree — that's the input the Policy
Agent needs to catch counterfeit listings. **No brand detected on any image at all also
counts as a mismatch** when a brand is declared — an undeclared logo and a genuinely
absent one are both "the packaging doesn't corroborate the claim," which is exactly the
counterfeit-branding signal C001 needs; it is not required that a *different* brand be
detected. Exception: `declaredBrand` values of `generic`, `unbranded`, `no brand`,
`none`, or `n/a` (case-insensitive) aren't a brand claim at all, so they never trigger a
mismatch — otherwise every legitimately unbranded/commodity listing would falsely match
C001. Written as an `EvidenceAgent` artifact per §5 (this is its `payload`):

**Out**
```json
{ "objects": ["smartphone", "retail box"], "brandsDetected": ["Apple"],
  "ocr": ["Apple", "iPhone", "256GB"], "brandMismatch": false,
  "certificateNumbers": [], "serialNumbers": [], "expiryDate": null,
  "countryOfOrigin": "China" }
```

Implemented in `agents/evidence_agent.py`. Images are fetched via the shared
`images.fetch_image_bytes` helper — `file://` for local dev/demo, `s3://` (the
production scheme, §3.1) via `boto3`.

### 3.3 Consistency Agent
Cross-checks fields that should agree with each other but are supplied independently:
title↔description, description↔images, images↔declared brand, category↔detected
objects. Doesn't judge policy — just surfaces disagreement for the Decision Agent to
weigh. Does its own lightweight image understanding for the three image-based checks
rather than reusing Evidence Agent's output — required by §1: Consistency depends only
on the canonical document, not on Evidence's output.

Each check is one true/false model call (text model `mistralai/mistral-nemotron` for
title↔description, vision model `nvidia/nemotron-nano-12b-v2-vl` for the three
image-based checks); with more than one image, a check is `consistent` if *any* image
confirms it. `inconsistencyScore` is not a separate judgment call — it's the mean, over
all checks, of the probability mass the model itself placed on the "inconsistent"
answer (1 − confidence when the verdict was consistent, confidence itself when it
wasn't), so a run of confidently-consistent checks produces a score near 0 without
that number being invented.

Note: `description_vs_images` is measurably noisier than the other three checks —
tested (not assumed) whether real product photography would fix this, per
`docs/decisions/0013`. Result was mixed, not a clean fix:

- It did resolve a related, more serious problem: with the demo's original synthetic
  images, Evidence Agent's brand detection was reliable, but real photos exposed a
  false-positive risk (misreading incidental text near a logo as a fabricated brand)
  that's now fixed by picking unambiguous photos and verified reliable (10/10 real
  calls).
- It did **not** reduce `description_vs_images` noise for the `clean` listing —
  mean `inconsistencyScore` across 6 real-call samples went from 0.171 (old synthetic
  placeholder) to ~0.395 (real photo), i.e. noisier, not less. The synthetic
  placeholder's OCR-readable text let the vision model "match" by literally reading
  text back, not by genuine visual reasoning; a real photo that shows only the bare
  device (no screen, no box, nothing distinguishing "Pro Max 256GB" from any other
  iPhone 16) is honestly harder to visually confirm against a specific title/spec
  claim, not easier. Remains open — a photo with more distinguishing visual detail
  (screen on, retail packaging with model/storage text) is the next thing to try, not
  assumed to be the fix without testing it too.

Written as a `ConsistencyAgent` artifact per §5.

**Out**
```json
{ "checks": [
    { "pair": "title_vs_description", "consistent": true },
    { "pair": "description_vs_images", "consistent": true },
    { "pair": "images_vs_declaredBrand", "consistent": true },
    { "pair": "category_vs_detectedObjects", "consistent": true }
  ],
  "inconsistencyScore": 0.02 }
```

Implemented in `agents/consistency_agent.py`.

### 3.4 Safety Agent
Content-safety classification, model `nvidia/llama-3.1-nemotron-safety-guard-8b-v3`
(not `nemotron-3.5-content-safety` — that model only returns a binary safe/unsafe
verdict with no category, and Policy Agent needs a category to pick a rule). Classifies
`title` + `description` text; images/OCR-based safety checks are Evidence Agent's job
(§3.2), not this agent's. Written as a `SafetyAgent` artifact per §5.

`confidence` is the model's own log-probability for the safe/unsafe token it emitted
(`logprobs: true` on the chat completion), not a separately requested score. `violations`
is the model's raw `Safety Categories` string, split on `,`.

Full taxonomy confirmed empirically (`docs/decisions/0012`) — real calls to the real
model with one prompt per suspected category, not a scraped model-card page.
Categories observed: `Guns and Illegal Weapons`, `Controlled/Regulated Substances`,
`Criminal Planning/Confessions`, `Illegal Activity`, `Fraud/Deception`, `Sexual`,
`Sexual (minor)`, `PII/Privacy`, `Hate/Identity Hate`, `Immoral/Unethical`,
`Suicide and Self Harm`, `Violence`, `Harassment`, `Profanity`,
`Copyright/Trademark/Plagiarism`, `Political/Misinformation/Conspiracy`,
`Needs Caution`. Only the subset that's clearly marketplace-listing-policy relevant
maps to a Policy Agent rule (§3.5) — most of the rest are general chat-safety
categories (violence, hate speech, profanity, misinformation) not obviously
actionable on a product listing; revisit if this pipeline's scope ever grows beyond
listing text. `explanation` is generated deterministically from the category list,
not model-written prose.

**Out**
```json
{ "violations": ["Guns and Illegal Weapons"], "confidence": 0.97,
  "explanation": "Content flagged as unsafe: Guns and Illegal Weapons." }
```

Implemented in `agents/safety_agent.py`.

### 3.5 Policy Agent
Maps evidence/safety/consistency findings to policy rules, keyed off `categoryId` (e.g.
`electronics.mobile`). Rule sets are looked up per category rather than one global
policy — a listing under `electronics.*` and one under `finance.*` check different rule
sets. Returns an **array** — a listing can match more than one rule.

| Rule ID | Description | Severity | Triggered by |
|---|---|---|---|
| W001 | Weapons prohibited | Critical | Safety Agent `violations` contains `Guns and Illegal Weapons` |
| C001 | Counterfeit goods prohibited | High | Evidence Agent `brandMismatch: true`, or Safety Agent `violations` contains `Copyright/Trademark/Plagiarism` |
| C004 | Misleading product information | Medium | Consistency Agent `inconsistencyScore` above threshold |
| D001 | Illegal drugs prohibited | Critical | Safety Agent `violations` contains `Controlled/Regulated Substances` |
| F001 | Fraud or deceptive listings prohibited | High | Safety Agent `violations` contains `Fraud/Deception` |
| S001 | Sexual content involving minors prohibited | Critical, **autoReject** | Safety Agent `violations` contains `Sexual (minor)` |

C001's two triggers are deduped to at most one match (the higher of the two
confidences) — never two separate `C001` entries in `matches` even if both signals
fire. The `Copyright/Trademark/Plagiarism` text signal was confirmed unreliable in
testing (`docs/decisions/0012`) — treat it as a secondary signal, not a substitute for
Evidence Agent's image-based `brandMismatch`.

Each match also carries a `confidence`, attributed from whichever upstream agent's signal
triggered the rule — Policy Agent passes through that agent's number rather than
inventing its own probability:

| Rule triggered by | `confidence` source |
|---|---|
| SafetyAgent violation (W001, D001, F001, S001) | `SafetyAgent.payload.confidence` |
| EvidenceAgent `brandMismatch: true` and/or SafetyAgent `Copyright/Trademark/Plagiarism` (C001) | `max(1.0 if brandMismatch, SafetyAgent.payload.confidence if the category fired)` |
| ConsistencyAgent `inconsistencyScore` above threshold (C004) | `ConsistencyAgent.payload.inconsistencyScore` |

Written as a `PolicyAgent` artifact per §5.

**Out**
```json
{ "matches": [
    { "rule": "C001", "severity": "High", "autoReject": false, "confidence": 1.0 }
] }
```

Deterministic — pure rule logic over the three upstream payloads, no model call.
Implemented in `agents/policy_agent.py`. `autoReject` is `true` only for S001 — the
hard-override lever reserved since the original spec (§4 step 1) for a rule that should
bypass confidence-based routing entirely; every other rule leaves it `false`.
`CONSISTENCY_THRESHOLD` (0.48) for C004 is tuned from real model-call data — 8 real
Consistency Agent runs per demo scenario showed a clean gap between the one scenario
that should trigger C004 (`inconsistent`, scores 0.505-0.712) and every scenario that
shouldn't (0.089-0.461); see `docs/decisions/0014`. Fixes C004 rule accuracy (e.g. the
`clean` scenario no longer falsely matches it) but does **not** on its own get `clean`
to auto-approve — that's gated by the separate, stricter `AUTO_APPROVE_THRESHOLD` bar
(§4), still an open question (`docs/decisions/0013`). Configurable via the
`CONSISTENCY_THRESHOLD` env var (read once at process start) or a per-call override on
`run_policy_agent` — see `docs/decisions/0008-env-var-thresholds.md`.

### 3.6 Decision Agent
Aggregates `PolicyAgent.matches` into a single decision and confidence using the fusion
algorithm in §4 — it combines the confidences Policy Agent already attributed per match,
it does not re-derive them from raw agent outputs. Seller history
(`sellerPreviousViolations`) shifts confidence toward REVIEW/REJECT for otherwise-
borderline cases (§4) rather than triggering its own rule. Written as a `DecisionAgent`
artifact per §5 — this is that artifact's `payload`:

**Out**
```json
{ "decision": "REVIEW", "confidence": 0.73, "policyRules": ["C001"],
  "explanation": "Brand detected but authenticity cannot be verified from supplied images." }
```

Deterministic — pure fusion logic per §4, no model call. `explanation` is generated
from the matched rules' own descriptions (or the residual inconsistency score when
none matched) plus the seller-history adjustment if one applied, not model-written
prose — same pattern as Safety and Consistency Agents. Implemented in
`agents/decision_agent.py`.

---

## 4. Decision Fusion & Confidence Routing

Deterministic, in three steps — no learned weighting, no free-form LLM judgment call on
the final number:

**Step 1 — aggregate confidence.**
- `matches` non-empty → `confidence = max(match.confidence for match in matches)`.
- `matches` empty → `confidence = 1 - ConsistencyAgent.payload.inconsistencyScore` — the
  only residual risk signal available when no policy rule fired.

**Step 2 — seller history adjustment.** If `sellerPreviousViolations > 0`, compute
`adjustment = min(0.05 * sellerPreviousViolations, 0.20)`:
- Would this route to APPROVE (see Step 3) → subtract `adjustment` from `confidence`
  first (repeat-violation sellers lose the benefit of the doubt, more of their
  borderline listings fall through to REVIEW).
- Would this route to REJECT → add `adjustment` to `confidence` first (repeat-violation
  sellers need less certainty to confirm a reject).
- This is the only effect seller history has — it never manufactures a policy match of
  its own (§3.1).

**Step 3 — routing**, using the adjusted `confidence`:

| Order | Condition | Result |
|---|---|---|
| 1 | any match has `autoReject: true` | Reject (hard override, ignores confidence) |
| 2 | any match has `severity: "Critical"` and `confidence ≥ 0.95` | Reject |
| 3 | `matches` empty and `confidence ≥ 0.90` | Approve |
| 4 | otherwise | Review |

Thresholds (`0.95`, `0.90`, the `0.20` adjustment cap) are configurable, not hard-coded
— via `CRITICAL_REJECT_THRESHOLD` / `AUTO_APPROVE_THRESHOLD` /
`SELLER_HISTORY_ADJUSTMENT_PER_VIOLATION` / `SELLER_HISTORY_ADJUSTMENT_CAP` env vars
(read once at process start, same pattern as `service.py`'s config) or per-call
overrides on `run_decision_agent` — see `docs/decisions/0008-env-var-thresholds.md`.
Critical-severity matches never auto-approve regardless of score — they resolve to
REJECT or REVIEW only.

If any agent errors or times out, the listing goes to `PENDING_REVIEW` (flagged `FAILED`)
rather than defaulting to approve.

---

## 5. Storage: Append-Only Artifact Log

The raw listing row is never mutated. Each agent run writes one immutable artifact
instead — this is what makes single-stage reruns (`rerun_analysis`) safe and gives a full
audit trail without a flat "decision + evidence" blob that different agents all write
into.

```json
{
  "listingId": "LST-100234",
  "agent": "SafetyAgent",
  "version": "nemotron-3.5",
  "producedAt": "2026-07-24T20:45:55Z",
  "payload": { "violations": ["Weapons"], "confidence": 0.97, "explanation": "string" }
}
```

One row per agent run, `agent` + `version` identifying what produced it. A rerun
(e.g. Safety Agent on a newer model) appends a new artifact rather than overwriting the
old one — prior runs stay queryable for comparison.

**Decision artifacts** are the same shape, with the Decision Agent's output as payload,
and reference the specific upstream artifacts they were computed from:

```json
{
  "listingId": "LST-100234",
  "agent": "DecisionAgent",
  "version": "string",
  "producedAt": "2026-07-24T20:46:02Z",
  "payload": {
    "decision": "REVIEW",
    "confidence": 0.73,
    "policyRules": ["C001"],
    "explanation": "Brand detected but authenticity cannot be verified from supplied images.",
    "moderator": "string | null"
  },
  "basedOn": ["EvidenceAgent@<producedAt>", "ConsistencyAgent@<producedAt>",
              "SafetyAgent@<producedAt>", "PolicyAgent@<producedAt>"]
}
```

`policyRules` stays the single source of truth for what was violated — no separate free-
text `violations` list duplicating it; any human-readable line is derived from the rule's
own description plus `explanation` for the case-specific reasoning.

The **latest** `DecisionAgent` artifact per listing is the listing's current decision.
Moderator overrides append a new `DecisionAgent` artifact with `moderator` set, rather
than editing the automated one.

`show case` (§6) assembles a view from: the raw listing row + all artifacts for that
`listingId`, ordered by `producedAt`.

---

## 6. Moderator CLI

Conversational, tool-driven, no direct DB access. Implemented as a plain Python tool
layer in `cli/tools.py` (backed by `db.py` and `pipeline.py`, orchestration in
`pipeline.py`, Intake mapping in `intake.py`) — there is no separate chat loop; a
moderator drives it by talking to Claude Code, which calls these functions directly.
Verified end-to-end against a real Postgres instance: seeded via
`generate_synthetic_data.py`, processed with `pipeline.poll_and_process()`, then every
tool below exercised against it (`list_pending`, `get_listing`, `explain_case`,
`show_images`, `search_policy`, `find_similar_cases`, `approve_listing`,
`reject_listing`, `rerun_analysis`) — moderator overrides confirmed to append a new
`DecisionAgent` artifact rather than overwrite the automated one (§5).

**Example**
```
> show next case

Listing 98342 — Rolex Watch
Status: Pending Review · Confidence: 74%
Issues: Counterfeit branding, Missing serial number
Recommendation: Manual verification required.

> explain case 98342

Evidence Agent
✓ Apple logo detected
✓ OCR found "iPhone 16"

Consistency Agent
⚠ Description mentions a different model than the title

Safety Agent
✓ No prohibited content

Policy Agent
Rule C004: Misleading product information

Decision Agent
REVIEW (Confidence: 0.74)

> approve
```
`explain case` reads straight off the artifact log (§5) — one section per agent artifact
for that listing, in order — rather than a separate reasoning trace to maintain.

**Tools**
| Tool | In | Out |
|---|---|---|
| `list_pending()` | `{ limit?, category? }` | `Listing[]` |
| `get_listing(listingId)` | `{ listingId }` | full document + agent outputs |
| `explain_case(listingId)` | `{ listingId }` | all artifacts for the listing, per-agent |
| `approve_listing(listingId, moderatorId?, note?)` | — | updated status |
| `reject_listing(listingId, moderatorId?, reason)` | — | updated status |
| `search_policy(query)` | `{ query }` | matching rule(s) |
| `find_similar_cases(listingId, k?)` | — | `Case[]` |
| `show_images(listingId)` | — | image URLs |
| `rerun_analysis(listingId, agent?)` | — | new agent output |
| `record_decision(listingId, decision, reason)` | — | audit entry (§5 schema) |
| `whoami(moderatorId?)` | — | moderator's own registry entry |

**Inspecting a case's images.** `show_images` returns raw `s3://`/`file://` URLs, not
viewable pixels. `.claude/skills/inspect-listing/` (invoked as `/inspect-listing
<listingId>` or proactively when a moderator asks to see a case) runs
`scripts/inspect_listing.py`, which prints the listing's text + latest agent artifacts
and fetches each image to a local temp file, then shows images one of two ways, paced
by the moderator: by default Claude Code reads each path with its own Read tool,
rendering inline in the conversation; with `--serve`, the script instead starts a
throwaway HTTP server on `127.0.0.1` and prints a browser URL per image (`--stop-server`
tears it down) -- a real pop-up window/tab, reachable from a Windows browser under WSL2
via automatic `localhostForwarding`, no interop or display server required either way.
Not an external OS image viewer directly -- see
`docs/decisions/0011-inspect-listing-inline-read-not-external-viewer.md` for why (WSL
interop disabled, no display server on the dev host) and how both fallbacks were
verified against a real Windows browser.

**Moderator identity.** `moderatorId` is checked against a `moderators` table
(`moderator_id`, `name`, `active`) — authorization, not authentication: no passwords,
no tokens, no login flow, because the CLI is a tool layer driven by a trusted operator
through Claude Code (§2 note in §6's intro), not a network-exposed service with
untrusted callers. `approve_listing`/`reject_listing`/`record_decision` reject an
unknown or inactive `moderatorId` outright. When omitted, `moderatorId` defaults to the
`MODERATOR_ID` environment variable (same convention as git's `user.name`) — still
validated against the table, an unset/unknown/inactive default is still rejected, just
without needing to pass it on every call. `whoami()` returns the resolved moderator's
own registry row, letting a moderator confirm their identity/active status before
acting. See `docs/decisions/0009-moderator-auth-registry.md`.

`approve_listing`/`reject_listing` are thin wrappers over `record_decision` — all three
append a new `DecisionAgent` artifact and move the listing to the matching terminal
status; the two convenience wrappers just fix `decision` to `APPROVE`/`REJECT` and set
`moderator` from `moderatorId`.

**`find_similar_cases`** ranks by real semantic similarity: a text embedding
(title + description, model `nvidia/llama-nemotron-embed-1b-v2`, `embeddings.py`) is
computed for each listing during `pipeline.run_fusion` and stored in Postgres via
`pgvector` (`listing_embeddings` table); the tool queries nearest neighbors by cosine
distance (`<=>`). Replaces the category+rule-overlap heuristic this project shipped
first (`docs/decisions/0005`, superseded by `docs/decisions/0010`). A listing that
hasn't been through the pipeline yet has no embedding and `find_similar_cases` raises
rather than silently returning nothing.

---

## 7. End-to-End Flow

1. A listing exists in Postgres with `status: "PENDING_MODERATION"`.
2. Workflow picks it up and triggers in-process -- concretely, `service.py`'s poller
   loop, calling `pipeline.poll_and_process()` on an interval (default 5s) and
   `db.sweep_stale_processing()` on a separate interval (default 60s) for stale
   `PROCESSING` claims (§2.1). Run it with `python3 service.py`; `POLL_INTERVAL_SECONDS`
   / `SWEEP_INTERVAL_SECONDS` / `SWEEP_TIMEOUT_MINUTES` / `POLL_BATCH_SIZE` configure it.
   Stops cleanly on SIGINT/SIGTERM after finishing the in-flight cycle.
3. Intake maps the row to the canonical document (§3.1).
4. Evidence, Consistency, Safety, Policy run in parallel.
5. Decision Agent applies thresholds → APPROVE / REJECT / REVIEW.
6. Decision + audit record persisted.
7. If REVIEW → listing enters the CLI queue.
8. Moderator reviews and calls `record_decision()` to close it out.
