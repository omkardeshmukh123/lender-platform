-- Migration 035: RBI reference registry table (read-only, never shown as real lenders)
CREATE TABLE IF NOT EXISTS rbi_registry (
  id                      SERIAL PRIMARY KEY,
  company_name            TEXT NOT NULL,
  cin                     TEXT,
  rbi_registration_number TEXT,
  regulatory_tier         TEXT,
  hq_state                TEXT,
  established_year        INTEGER,
  created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rbi_registry_name_trgm
  ON rbi_registry USING GiST (company_name gist_trgm_ops);

CREATE UNIQUE INDEX IF NOT EXISTS idx_rbi_registry_name_unique
  ON rbi_registry (lower(company_name));
