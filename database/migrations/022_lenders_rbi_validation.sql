-- migration 022: RBI verification + contact validation columns on lenders
-- =========================================================================
-- Problem:
--   NBFC extraction pipeline (run_nbfc_extraction.py) now runs NBFCValidator
--   for every record but the lenders table has no columns to store those
--   results.  Without these columns:
--     - RBI verification status is lost after extraction
--     - Regulatory tier cannot be queried (needed for borrower matching)
--     - Contact quality signals (phone_valid, email_valid) are invisible
--     - Pan-India suspect flag cannot drive admin review queues
--     - Data provenance (per-domain source tags) is collapsed to one string
--
-- Solution:
--   Add 12 new columns to lenders, all with safe DEFAULT values so existing
--   rows are unaffected and no backfill is required before the migration runs.
--
-- Querying RBI-verified NBFCs:
--   SELECT company_name, rbi_verified_name, regulatory_tier
--   FROM lenders
--   WHERE rbi_status = 'verified' AND company_type = 'NBFC'
--   ORDER BY regulatory_tier;

-- ── 1. RBI identity & regulatory tier ────────────────────────────────────────

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS rbi_status TEXT NOT NULL DEFAULT 'unverified'
        CHECK (rbi_status IN (
            'verified',    -- reg number matched + name fuzzy-matched in RBI registry
            'active',      -- name fuzzy-matched, reg status = active
            'inactive',    -- name fuzzy-matched, reg status = inactive/cancelled
            'cancelled',   -- explicitly cancelled by RBI
            'merged',      -- merged into another entity
            'not_found',   -- searched registry, no match
            'unverified'   -- validator not run or registry unavailable
        ));

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS rbi_verified_name TEXT;   -- canonical name from RBI registry

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS regulatory_tier TEXT NOT NULL DEFAULT 'unknown'
        CHECK (regulatory_tier IN (
            'D',        -- NBFC-Deposit Taking
            'ND-SI',    -- Non-Deposit Systemically Important (AUM ≥ ₹500Cr)
            'ND-UL',    -- Upper Layer (SBR 2022)
            'ND-ML',    -- Middle Layer
            'ND-BL',    -- Base Layer
            'ND',       -- Non-Deposit (unclassified)
            'MFI',      -- Microfinance Institution
            'IFC',      -- Infrastructure Finance Company
            'Factor',   -- NBFC-Factor
            'P2P',      -- Peer-to-Peer Lending Platform
            'AA',       -- Account Aggregator
            'IC',       -- Investment Company
            'unknown'   -- not classified
        ));

-- ── 2. Contact validation flags ───────────────────────────────────────────────

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS phone_valid           BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS email_valid           BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS email_domain_matches  BOOLEAN NOT NULL DEFAULT FALSE;
    -- TRUE when email domain matches the lender's registered website domain

-- ── 3. Segment / capability mismatch ─────────────────────────────────────────

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS segment_category_mismatch BOOLEAN NOT NULL DEFAULT FALSE;
    -- TRUE when loan products offered conflict with the RBI category
    -- (e.g. an NBFC-AA offering direct lending, or NBFC-IC offering personal loans)

-- ── 4. Geographic integrity ───────────────────────────────────────────────────

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS pan_india_suspect BOOLEAN NOT NULL DEFAULT FALSE;
    -- TRUE when entity claims Pan-India coverage but AUM < ₹100Cr
    -- or only 1-2 operating states were found

-- ── 5. Data provenance (per-domain source tags) ───────────────────────────────
-- These replace the single data_source string with per-domain attribution
-- so downstream consumers can trust individual fields independently.

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS financial_source   TEXT NOT NULL DEFAULT 'none'
        CHECK (financial_source   IN ('scraper_high','scraper_med','gemini','manual','none'));

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS geo_source         TEXT NOT NULL DEFAULT 'none'
        CHECK (geo_source         IN ('scraper_high','scraper_med','gemini','manual','none'));

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS contact_source     TEXT NOT NULL DEFAULT 'none'
        CHECK (contact_source     IN ('scraper_high','scraper_med','gemini','manual','none'));

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS regulatory_source  TEXT NOT NULL DEFAULT 'none'
        CHECK (regulatory_source  IN ('rbi_registry','scraper_high','scraper_med','gemini','manual','none'));

-- ── 6. Indexes for admin review queues ───────────────────────────────────────

-- RBI status lookup (e.g. "show me all unverified NBFCs")
CREATE INDEX IF NOT EXISTS idx_lenders_rbi_status
    ON lenders (rbi_status)
    WHERE company_type = 'NBFC';

-- Regulatory tier filter (borrower matching by tier)
CREATE INDEX IF NOT EXISTS idx_lenders_regulatory_tier
    ON lenders (regulatory_tier)
    WHERE rbi_status IN ('verified', 'active');

-- Admin review: pan-India suspects + segment mismatches
CREATE INDEX IF NOT EXISTS idx_lenders_review_flags
    ON lenders (pan_india_suspect, segment_category_mismatch)
    WHERE pan_india_suspect = TRUE OR segment_category_mismatch = TRUE;

-- ── 7. Record migration ────────────────────────────────────────────────────────
