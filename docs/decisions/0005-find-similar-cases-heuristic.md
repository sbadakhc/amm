# 0005. find_similar_cases is a category + rule-overlap heuristic, not embeddings

## Status
Superseded by [0010](0010-embeddings-for-find-similar-cases.md) -- kept for the
reasoning trail, not current behavior.

## Context
SPEC.md §6 lists `find_similar_cases(listingId, k?)` among the moderator CLI tools
with no defined similarity method. Real semantic similarity would mean an embedding
model and a vector index -- meaningful infrastructure for what is currently a
five-listing demo dataset.

## Decision
`cli/tools.find_similar_cases` ranks other listings in the same `categoryId` by how
many policy rules their latest `DecisionAgent` artifact shares with the target
listing's, then by recency. No embeddings, no vector search.

## Consequences
- Works today with zero extra infrastructure, and the ranking signal (shared policy
  rules) is directly interpretable to a moderator ("these were flagged for the same
  reason"), which a raw embedding-distance score wouldn't be on its own.
- It will not surface genuinely similar listings that don't share a category or a
  matched rule (e.g. two counterfeit listings in different categories, or two clean
  listings that just look alike). If case volume or category diversity grows to where
  that matters, swap in an embedding-based similarity search -- the tool's signature
  doesn't need to change, only its implementation.
