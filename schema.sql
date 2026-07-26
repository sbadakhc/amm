-- Agentic Marketplace Moderator — demo schema
-- Matches the real listing document shape + the §5 append-only artifact log.

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
