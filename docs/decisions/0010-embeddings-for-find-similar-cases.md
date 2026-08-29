# 0010. find_similar_cases: real embeddings via pgvector, superseding the heuristic

## Status
Accepted

## Context
ADR 0005 shipped `find_similar_cases` as a category+rule-overlap heuristic, explicitly
as a placeholder: "revisit if case volume grows past what the heuristic ranks well."
Implementing real similarity raised a few questions:

- Which embedding model? Verified live before writing any code (this project's
  established practice, see AGENTS.md §3): `nvidia/llama-nemotron-embed-1b-v2`
  (2048-dim) via `https://integrate.api.nvidia.com/v1/embeddings` produces sensible
  cosine similarity -- identical text scored 1.0, a same-category-different-product
  pair (iPhone vs. Samsung) scored 0.64, an unrelated pair (iPhone vs. a weapon
  listing) scored 0.30.
- Where to store and query the vectors? A separate vector database is more
  infrastructure than this project has needed for anything else; `pgvector` keeps
  everything in the same Postgres instance already used for listings/artifacts/
  moderators, consistent with the project's Postgres-first storage philosophy.
- pgvector's HNSW (and ivfflat) index types cap at 2000 dimensions; this model
  produces 2048. Rather than reduce dimensionality or switch to `halfvec` to fit an
  index, this project's actual data volume (a handful of demo listings) doesn't need
  one at all yet -- a sequential scan over `<=>` is trivially fast at this scale.

## Decision
- `embeddings.py`: `embed_text(text) -> list[float]`, called with `input_type:
  passage` for both indexing and comparison (listings are compared document-to-
  document, not as a short query against a long document, so the query/passage
  asymmetric embedding this model supports for retrieval doesn't apply here).
- `listing_embeddings` table (`listing_id`, `model`, `embedding vector(2048)`,
  `produced_at`), no index -- added later if real data volume justifies one (see
  Context).
- `scripts/dev-db.sh` switched from `postgres:16-alpine` to `pgvector/pgvector:pg16`
  -- a drop-in replacement image, nothing else about local dev changes.
- The embedding is computed as part of `pipeline.run_fusion`'s existing parallel
  fan-out (alongside Evidence/Consistency/Safety) since it needs the same canonical
  document and nothing else, not as a separate pass.
- `cli/tools.find_similar_cases` now queries `db.find_similar_by_embedding` (pgvector
  cosine distance) instead of the category+rule-overlap heuristic, and raises clearly
  if the target listing has no embedding yet (hasn't been through the pipeline) rather
  than silently returning an empty or misleading result.

## Consequences
- Verified against real data, not just the offline tests: ran the actual pipeline on
  the 5 demo listings (which computes real embeddings via the live API) and confirmed
  `find_similar_cases` ranks them correctly -- the `clean` and `counterfeit_brand`
  listings (which share identical title/description text in the demo data) came back
  as an exact match (cosine distance 0.0000), the phone-adjacent `inconsistent`
  listing next (0.2271), headphones third (0.5457), and the unrelated weapon listing
  last (0.7117) -- a real, non-fabricated semantic ranking.
- Caught two real bugs building this, both now covered by regression tests:
  `pgvector`'s Python wrapper returns a `Vector` object from a `SELECT`, not a plain
  `list[float]` (`db.get_listing_embedding` now calls `.to_list()`); and the test
  fixtures' teardown order violated `listing_embeddings`' foreign key by deleting
  `listings` before `listing_embeddings`.
- No vector index yet means `find_similar_cases` is a full sequential scan with
  cosine-distance computed per row -- fine at current volume, revisit (index, or
  `halfvec` to fit an HNSW/ivfflat index within pgvector's 2000-dim cap) if that stops
  being true.
