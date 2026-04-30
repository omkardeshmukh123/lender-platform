-- Migration 030: Policy schema hardening for fintech production
-- Fixes: rate_type, scraped_at vs rates_as_of, rate_flagged, last_verified_at,
--        interest rate ceiling, scraper_checkpoints table

-- 1. New columns on policies
ALTER TABLE policies
  ADD COLUMN IF NOT EXISTS rate_type        TEXT        CHECK (rate_type IN ('fixed','floating','mclr_linked','repo_linked','hybrid')),
  ADD COLUMN IF NOT EXISTS rate_flagged     BOOLEAN     NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS scraped_at       TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ;

-- 2. Tighten interest rate ceiling (existing floor check stays)
ALTER TABLE policies DROP CONSTRAINT IF EXISTS chk_interest_rate_ceiling;
ALTER TABLE policies ADD CONSTRAINT chk_interest_rate_ceiling
  CHECK (interest_rate_max IS NULL OR interest_rate_max <= 42.0);

-- 3. Scraper checkpoint table (replaces flat JSON file)
CREATE TABLE IF NOT EXISTS scraper_checkpoints (
  job_name   TEXT        PRIMARY KEY,
  done_ids   JSONB       NOT NULL DEFAULT '[]',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 4. Index for staleness queries
CREATE INDEX IF NOT EXISTS idx_policies_scraped_at ON policies (scraped_at);
CREATE INDEX IF NOT EXISTS idx_policies_rate_flagged ON policies (rate_flagged) WHERE rate_flagged = TRUE;
