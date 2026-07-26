# 0016. HNSW index on listing_embeddings via halfvec

## Status
Accepted

## Context
ADR 0010 shipped `listing_embeddings` with no index: pgvector's `vector` type caps
HNSW/ivfflat at 2000 dimensions, and this project's embedding model
(`nvidia/llama-nemotron-embed-1b-v2`) produces 2048. That ADR flagged `halfvec`
(half-precision vectors, raising the cap to 4000) as the known workaround, deferred
until real data volume justified the work.

Picked this up as candidate work item #4. Verified live before committing to
anything (this project's standing practice, `AGENTS.md` §3), against the exact
`pgvector/pgvector:pg16` image `scripts/dev-db.sh` already uses:
- Installed pgvector extension: 0.8.5 -- confirmed supports `halfvec` with HNSW
  indexing at 2048 dimensions (created a real `halfvec(2048)` column + HNSW index,
  not assumed from documentation).
- Installed Python `pgvector` package: 0.5.0 -- already supports the `HalfVector`
  wrapper, and `register_vector` auto-registers the `halfvec` type when present, so
  no dependency bump was needed.
- Plain Python `list[float]` inserts into a `halfvec` column work exactly like
  `vector` (no wrapper needed on insert); reading back returns a `HalfVector`, which
  already exposes `.to_list()`, same interface as `Vector` -- `db.get_listing_embedding`
  needed no logic change, just a comment fix.

**Found and fixed a real problem along the way, not assumed away:** `find_similar_by_embedding`'s
existing self-join query (`JOIN listing_embeddings le2 ON le2.listing_id !=
le1.listing_id`) never uses an ANN index at all, regardless of data volume -- confirmed
via real `EXPLAIN` (`SET enable_seqscan = off` to force the planner's hand): the plan
was a nested-loop join + explicit `Sort`, the HNSW index never appearing anywhere in
it. Adding the index alone would have been a no-op. Rewrote the query to look up the
target embedding via a scalar subquery instead of a join partner; re-verified with
`EXPLAIN`, which now shows `Index Scan using idx_listing_embeddings_hnsw ... Order
By: (embedding <=> $0)` -- a genuine ANN search.

## Decision
- `schema.sql`: `listing_embeddings.embedding` changed from `vector(2048)` to
  `halfvec(2048)`; added `CREATE INDEX ... USING hnsw (embedding halfvec_cosine_ops)`.
- `db.py`'s `find_similar_by_embedding` rewritten to use a scalar subquery for the
  target listing's embedding, so the query planner can actually use the index.
- No other code changes needed -- `upsert_listing_embedding` and
  `get_listing_embedding` work unchanged against the new column type.

## Consequences
- **Precision trade-off, stated plainly**: `halfvec` stores half-precision (16-bit)
  floats instead of `vector`'s full 32-bit floats. Verified this doesn't change
  ranking correctness for this use case: re-ran the same 5-listing similarity check
  from ADR 0010 against real embeddings and got the identical distances (0.0000,
  0.2271, 0.6678, 0.7117) as the original full-precision `vector` column produced.
  Cosine-similarity ranking for "find roughly similar listings" is far more tolerant
  of this than exact nearest-neighbor guarantees would be.
- HNSW is an approximate index -- past a certain data volume, results can very
  occasionally differ slightly from an exact sequential scan. Not observable at this
  project's current demo-scale data volume; worth remembering if this is ever
  benchmarked against a genuinely large real dataset.
- Caught a test-isolation artifact while verifying (not a code bug): manually
  seeding demo data into the same persistent dev-db instance used for the
  integration test suite made `test_find_similar_by_embedding_ranks_by_cosine_distance`
  fail, because its `k=5` assumption implicitly depends on the embeddings table being
  otherwise empty. Truncating before running the suite (already this project's
  convention per `AGENTS.md`) resolved it; no test or product code change was needed.
