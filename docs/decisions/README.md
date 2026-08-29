# Architecture Decision Records

Lightweight ADRs for choices that weren't obvious from SPEC.md alone, or where SPEC.md
under-specified something enough that an agent implementing it would have had to
invent a scheme. Each one is Status / Context / Decision / Consequences, numbered in
the order they were made.

New ADRs: copy the format of an existing one. Only write one when a choice is
non-obvious, was genuinely contested, or cost real effort (a live API test, a
rejected alternative) to arrive at -- not for routine implementation detail already
covered by SPEC.md.

| # | Decision |
|---|---|
| [0001](0001-safety-agent-model-choice.md) | Safety Agent uses safety-guard-8b-v3, not nemotron-3.5-content-safety |
| [0002](0002-cli-tool-layer-not-chat-loop.md) | Moderator CLI is a Python tool layer, not a standalone chat loop |
| [0003](0003-decision-fusion-formula.md) | Decision fusion is a deterministic 3-step formula |
| [0004](0004-locking-model.md) | Claim listings with `FOR UPDATE SKIP LOCKED`, no broker |
| [0005](0005-find-similar-cases-heuristic.md) | find_similar_cases is a heuristic, not embeddings *(superseded by 0010)* |
| [0006](0006-s3-storage-self-hosted-minio.md) | S3 image storage: boto3 against a configurable endpoint, self-hosted via MinIO for dev |
| [0007](0007-poller-service-design.md) | Poller service: instance state, bounded runs for testability, cycle-boundary shutdown |
| [0008](0008-env-var-thresholds.md) | Decision/Policy thresholds: env vars, not a DB config table |
| [0009](0009-moderator-auth-registry.md) | Moderator identity: known-registry authorization, not authentication |
| [0010](0010-embeddings-for-find-similar-cases.md) | find_similar_cases: real embeddings via pgvector, superseding the heuristic |
| [0011](0011-inspect-listing-inline-read-not-external-viewer.md) | Show a case's images via Claude's Read tool (inline) or a throwaway HTTP server (`--serve`), no external OS viewer |
| [0012](0012-safety-taxonomy-confirmed-new-rules.md) | Confirmed Safety Agent taxonomy via real calls; added F001 (fraud) and S001 (minors, autoReject), extended C001 |
| [0013](0013-real-product-photography.md) | Real product photography for demo listings -- fixed a brand-detection false-positive risk, description_vs_images noise remains open |
| [0014](0014-consistency-threshold-tuned-from-real-data.md) | CONSISTENCY_THRESHOLD tuned from real model-call data, 0.30 -> 0.48 |
| [0015](0015-inspect-listing-queue-table.md) | inspect-listing gains a `--queue` table view, one shared image server instead of restarting per listing |
| [0016](0016-listing-embeddings-hnsw-index.md) | HNSW index on listing_embeddings via halfvec, not plain vector (pgvector's 2000-dim cap) |
| [0017](0017-escalation-appeals-seller-accounts-scope-boundary.md) | Escalation/appeals/seller accounts: portable rules logic built now, real backend integration deferred |
| [0018](0018-map-criminal-planning-illegal-activity-to-f001.md) | Map Criminal Planning/Confessions and Illegal Activity to F001, revising 0012 |
| [0019](0019-safety-agent-low-confidence-retry.md) | Safety Agent retries a low-confidence safe verdict once |
| [0020](0020-prize-advance-fee-scam-targeted-check.md) | Targeted second-opinion check for prize/advance-fee scams |
| [0021](0021-fraud-eval-harness.md) | Checked-in real-call fraud eval harness, opt-in, aggregate thresholds |
| [0022](0022-prize-scam-check-timeout-fallback.md) | Prize-scam second-opinion check fails open on timeout, not blocking |
| [0023](0023-ebay-titles-false-positive-fixture.md) | Real eBay listing titles as a local-only (never-committed) false-positive fixture |
| [0024](0024-symmetric-low-confidence-retry.md) | Low-confidence retry extended to the unsafe direction (found via 0023's eval) |
| [0025](0025-vision-model-end-of-life-replacement.md) | Vision model end-of-life: replace nemotron-nano-12b-v2-vl with meta/llama-3.2-11b-vision-instruct |
| [0026](0026-preflight-model-check.md) | Preflight model check before real-call tests -- distinguishes permanent (GONE) from transient (UNREACHABLE) failures |
| [0027](0027-pipeline-stats.md) | Pipeline accuracy/performance stats from the existing artifact log -- decision distribution, moderator override rate, latency, failures |
| [0028](0028-consistency-agent-timeout-fallback.md) | Consistency Agent checks fail open on timeout, not blocking -- skipped checks excluded from inconsistencyScore, not counted either way |
| [0029](0029-evidence-agent-timeout-fallback.md) | Evidence Agent extraction fails open on timeout, not blocking -- skipped images excluded from brandMismatch, not counted either way |
| [0030](0030-title-vs-description-heuristic-backstop.md) | Heuristic backstop for title_vs_description when the model check is skipped -- narrow brand-name matching only, never overrides a real model verdict |
| [0031](0031-rename-moderator-cli-skills.md) | Rename moderator-facing skills for brevity -- inspect/stats/status/run, with pushback on two proposed names that collided or misled |
| [0032](0032-rerun-analysis-decision-agent-produced-at-bug.md) | Fix rerun_analysis(agent="DecisionAgent") KeyError -- DB row shape vs. agent-output shape mismatch, zero prior test coverage |
