-- Migration 042: grievance_officers table
-- RBI mandates every NBFC publish a Grievance Redressal Officer.
-- Scraped from FPC pages + dedicated grievance/contact pages.

CREATE TABLE IF NOT EXISTS grievance_officers (
    id                  SERIAL PRIMARY KEY,
    lender_id           INTEGER NOT NULL REFERENCES lenders(id) ON DELETE CASCADE,
    name                VARCHAR(200),
    designation         VARCHAR(200),
    email               VARCHAR(200),
    phone               VARCHAR(50),
    source_url          TEXT,
    source_type         VARCHAR(50) DEFAULT 'website',  -- website / fpc_page / rbi_registry
    last_verified_at    TIMESTAMPTZ DEFAULT NOW(),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT grievance_officers_lender_unique UNIQUE (lender_id)
);

CREATE INDEX IF NOT EXISTS idx_grievance_officers_lender_id
    ON grievance_officers (lender_id);

-- RLS: read-only for public, full access for service role
ALTER TABLE grievance_officers ENABLE ROW LEVEL SECURITY;

CREATE POLICY grievance_officers_select ON grievance_officers
    FOR SELECT USING (true);

COMMENT ON TABLE grievance_officers IS
    'Grievance Redressal Officer contact per NBFC — scraped from FPC/website pages.';
