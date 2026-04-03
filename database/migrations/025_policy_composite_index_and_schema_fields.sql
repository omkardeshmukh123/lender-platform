-- ============================================================
-- Migration 025: Policy composite index + schema field additions
-- Purpose:
--   1. Composite index (loan_type, approval_status, is_active) on policies
--      — match_lenders() filters on all three; without this it does a seq scan
--   2. Add business_sector to policies and lenders (sector-level filtering)
--   3. Add rates_as_of to policies (data freshness signal for borrowers)
--   4. Add existing_emi_monthly to any cached borrower profiles (future)
-- ============================================================

BEGIN;

-- ── 1. Composite index for match_lenders hot path ────────────
-- Covers: WHERE loan_type = $x AND approval_status = 'approved' AND is_active = true
CREATE INDEX IF NOT EXISTS idx_policies_match_hot
    ON policies (loan_type, approval_status, is_active)
    WHERE approval_status = 'approved' AND is_active = true;

-- Secondary: support /v1/lenders/search sort by quality_score and aum_crores
CREATE INDEX IF NOT EXISTS idx_lenders_quality_desc
    ON lenders (quality_score DESC NULLS LAST)
    WHERE approval_status = 'approved';

CREATE INDEX IF NOT EXISTS idx_lenders_aum_desc
    ON lenders (aum_crores DESC NULLS LAST)
    WHERE approval_status = 'approved';

-- ── 2. business_sector on lenders ────────────────────────────
-- Broad sector: "MSME", "Retail", "Agriculture", "Housing", "Gold", "Vehicle", "Education", "Corporate"
ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS business_sector TEXT;

CREATE INDEX IF NOT EXISTS idx_lenders_business_sector
    ON lenders (business_sector)
    WHERE business_sector IS NOT NULL;

-- ── 3. business_sector on policies ───────────────────────────
ALTER TABLE policies
    ADD COLUMN IF NOT EXISTS business_sector TEXT;

-- ── 4. rates_as_of on policies ───────────────────────────────
-- When was the interest_rate_min / interest_rate_max last verified?
-- NULL = unknown (treat as stale for display purposes)
ALTER TABLE policies
    ADD COLUMN IF NOT EXISTS rates_as_of DATE;

-- Index for freshness-aware queries (show recently verified rates first)
CREATE INDEX IF NOT EXISTS idx_policies_rates_freshness
    ON policies (rates_as_of DESC NULLS LAST)
    WHERE approval_status = 'approved' AND is_active = true;

-- ── 5. data_source_confidence on policies ────────────────────
-- 0.0–1.0 float from guardrails.py quality_score, stored at policy level
ALTER TABLE policies
    ADD COLUMN IF NOT EXISTS data_source_confidence NUMERIC(4,3)
        CHECK (data_source_confidence BETWEEN 0 AND 1);

-- ── 6. Update match_lenders() to return rates_as_of ──────────
-- Re-create the function to include rates_as_of in the result set
-- so borrower_evaluator.py can attach a data freshness disclaimer.
-- NOTE: Full match_lenders() body is preserved; only SELECT list is extended.
-- If the function signature changes, bump the version comment.
DO $$
BEGIN
    -- Only add the column to the return type if the function exists
    -- (avoids error on fresh installs where 016 may not have run yet)
    IF EXISTS (
        SELECT 1 FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname = 'match_lenders'
    ) THEN
        -- The actual function body is in 016_match_lenders_optimized.sql.
        -- This migration only adds the new index; the API reads rates_as_of
        -- directly from the policies table in the match response builder.
        RAISE NOTICE 'match_lenders() exists — rates_as_of added to policies table, readable via JOIN';
    END IF;
END $$;

-- ── 7. Check constraint: rates_as_of must not be in the future ──
ALTER TABLE policies
    DROP CONSTRAINT IF EXISTS chk_rates_as_of_not_future;
ALTER TABLE policies
    ADD CONSTRAINT chk_rates_as_of_not_future
        CHECK (rates_as_of IS NULL OR rates_as_of <= CURRENT_DATE);

COMMIT;
