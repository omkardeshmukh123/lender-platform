-- migration 028: MCA21 company registration fields
-- ===================================================
-- Problem:
--   Company registration data (CIN, incorporation date, company status,
--   authorized/paid-up capital) is currently absent from the lenders table.
--   This data is authoritative from MCA21 (Ministry of Corporate Affairs)
--   and fills gaps that Gemini guesses poorly:
--     - established_year: Gemini infers from website; MCA has the exact date
--     - is_listed: Screener.in only covers listed companies; MCA covers all
--     - hq_state: scraper may find operational HQ; MCA has the registered office
--     - company_status: active vs struck-off vs dormant — cannot be inferred
--
-- Solution:
--   Add 6 new columns for MCA21 data, all with safe DEFAULT values so
--   existing rows are unaffected. Add a tracking column so we can query
--   which lenders have been enriched and when.
--
-- Usage after migration:
--   SELECT company_name, cin, company_status, established_year
--   FROM lenders WHERE mca21_status = 'enriched'
--   AND company_status != 'struck_off';

-- ── 1. CIN — Corporate Identification Number ──────────────────────────────────
-- Format: L/U + 5 digits (NIC code) + 2-letter state + 4-digit year + 3-letter
--         type (PLC/PTC/LLC) + 6-digit sequential number
-- Example: L65910MH1994PLC077895
-- NULL when not yet enriched or company predates mandatory CIN assignment.

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS cin TEXT;

CREATE INDEX IF NOT EXISTS idx_lenders_cin
    ON lenders (cin)
    WHERE cin IS NOT NULL;

-- ── 2. Company status from MCA21 registry ────────────────────────────────────
-- 'active'     — company is in good standing with MCA
-- 'struck_off' — struck off under Companies Act 2013 (Section 248)
-- 'dormant'    — dormant status declared under Section 455
-- 'dissolved'  — wound up or amalgamated
-- 'converted'  — converted to LLP or different entity type
-- NULL when not yet enriched.

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS company_status TEXT
        CHECK (company_status IN ('active', 'struck_off', 'dormant',
                                  'dissolved', 'converted', NULL));

-- ── 3. Capital structure ──────────────────────────────────────────────────────
-- Stored in ₹ Lakhs (MCA reports in actual rupees; divide by 1e5).
-- Authorized capital = maximum capital the company can raise.
-- Paid-up capital    = capital actually issued and paid by shareholders.
-- For NBFCs, paid-up capital >= ₹2 Cr (₹200 Lakhs) is an RBI requirement.

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS authorized_capital_lakhs NUMERIC;

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS paid_up_capital_lakhs NUMERIC;

-- ── 4. MCA21 enrichment tracking ─────────────────────────────────────────────
-- Allows admin to see which lenders have been enriched, when, and whether
-- the MCA21 lookup succeeded or failed.

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS mca21_status TEXT NOT NULL DEFAULT 'not_enriched'
        CHECK (mca21_status IN (
            'enriched',     -- MCA21 data found and written
            'not_found',    -- searched but no match at or above threshold
            'error',        -- network or parse error during enrichment
            'not_enriched'  -- enrichment not yet attempted
        ));

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS mca21_enriched_at TIMESTAMP;

-- ── 5. Indexes for admin queues ───────────────────────────────────────────────

-- Admin review: companies with problematic MCA status
CREATE INDEX IF NOT EXISTS idx_lenders_company_status
    ON lenders (company_status)
    WHERE company_status IN ('struck_off', 'dormant', 'dissolved');

-- Enrichment progress: find all lenders not yet checked against MCA21
CREATE INDEX IF NOT EXISTS idx_lenders_mca21_todo
    ON lenders (mca21_status)
    WHERE mca21_status = 'not_enriched';

-- Capital adequacy: find NBFCs below RBI minimum paid-up capital (₹200 Lakhs)
-- Useful for compliance review queue
CREATE INDEX IF NOT EXISTS idx_lenders_low_capital
    ON lenders (paid_up_capital_lakhs)
    WHERE paid_up_capital_lakhs IS NOT NULL
      AND paid_up_capital_lakhs < 200
      AND company_type = 'NBFC';

-- ── 6. Record migration ────────────────────────────────────────────────────────
-- migrate.py inserts the schema_versions row automatically (with checksum).
-- Do NOT add a manual INSERT here.
