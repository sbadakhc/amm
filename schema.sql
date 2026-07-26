-- Agentic Marketplace Moderator — demo schema
-- Matches the real listing document shape + the §5 append-only artifact log.

-- For find_similar_cases (§6, §10 in docs/decisions). Requires a Postgres build with
-- pgvector available -- scripts/dev-db.sh uses the pgvector/pgvector:pg16 image.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS listings (
    listing_id      TEXT PRIMARY KEY,
    seller          JSONB NOT NULL,   -- { sellerId, companyName, country, verified, rating, previousViolations }
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    category        JSONB NOT NULL,   -- { id, name }
    price           JSONB NOT NULL,   -- { amount, currency }
    quantity        INTEGER,
    condition       TEXT,
    brand           TEXT,
    model           TEXT,
    sku             TEXT,
    images          JSONB NOT NULL,   -- [ { id, url } ]
    attributes      JSONB,
    shipping        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),   -- touched on every status transition, see spec §2.1
    status          TEXT NOT NULL DEFAULT 'PENDING_MODERATION'
);

-- One immutable row per agent run. See spec §5.
CREATE TABLE IF NOT EXISTS artifacts (
    id              BIGSERIAL PRIMARY KEY,
    listing_id      TEXT NOT NULL REFERENCES listings(listing_id),
    agent           TEXT NOT NULL,    -- IntakeAgent | EvidenceAgent | ConsistencyAgent |
                                       -- SafetyAgent | PolicyAgent | DecisionAgent
    version         TEXT NOT NULL,
    produced_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload         JSONB NOT NULL,
    based_on        JSONB             -- array of upstream artifact refs, DecisionAgent only
);

CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);
CREATE INDEX IF NOT EXISTS idx_artifacts_listing ON artifacts(listing_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_agent ON artifacts(listing_id, agent);

-- Known-moderator registry (§6). Authorization only -- no passwords/tokens; see
-- docs/decisions/0009-moderator-auth-registry.md.
CREATE TABLE IF NOT EXISTS moderators (
    moderator_id    TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    active          BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One embedding per listing (title+description, model nvidia/llama-nemotron-embed-1b-v2,
-- see embeddings.py), computed during pipeline.run_fusion. Backs find_similar_cases
-- (§6) via pgvector's cosine distance operator (<=>), replacing the category+rule-
-- overlap heuristic (docs/decisions/0005, superseded by 0010).
-- halfvec, not vector: pgvector's HNSW/ivfflat index types cap plain `vector` at 2000
-- dimensions and this model produces 2048. halfvec (half-precision) raises that cap
-- to 4000 -- verified against the real pgvector version this project's dev-db image
-- ships (0.8.5) before committing to it, not assumed from docs. See
-- docs/decisions/0016 for the precision trade-off.
CREATE TABLE IF NOT EXISTS listing_embeddings (
    listing_id      TEXT PRIMARY KEY REFERENCES listings(listing_id),
    model           TEXT NOT NULL,
    embedding       halfvec(2048) NOT NULL,
    produced_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_listing_embeddings_hnsw
    ON listing_embeddings USING hnsw (embedding halfvec_cosine_ops);

-- Placeholder seller entity (§8.3, docs/decisions/0017) -- NOT assumed to be the
-- real source of truth for seller/account identity; a stand-in until this project's
-- eventual integration with a real customer backend (if any) is known. `violation_count`
-- is a live counter incremented on REJECT (pipeline.process_listing, cli.tools
-- record_decision), unlike `listings.seller->>'previousViolations'`, which stays a
-- static snapshot taken at listing submission time and is not touched by this table.
-- Not yet read by Decision Agent's confidence fusion (§4) -- that remains a
-- separate, later change.
CREATE TABLE IF NOT EXISTS sellers (
    seller_id       TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'ACTIVE',
    violation_count INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
