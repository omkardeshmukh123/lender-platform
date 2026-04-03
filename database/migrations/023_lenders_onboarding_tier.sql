-- migration 023: onboarding_tier column on lenders
-- ====================================================
-- Problem:
--   The product layer (borrower-facing API) needs to know which lenders
--   are safe to display to borrowers and which are pending manual review
--   or rejected.  Using extraction_status ('success'/'rejected') is not
--   sufficient because a successfully extracted lender may still be
--   unverified, provisional, or awaiting compliance sign-off.
--
-- Solution:
--   onboarding_tier — a four-value classification computed by
--   _compute_onboarding_tier() in run_nbfc_extraction.py after RBI
--   validation and contact/website checks.
--
-- Product layer usage:
--   Only 'verified_lender' and 'provisional_lender' are shown to borrowers.
--   'pending_review' is visible to internal admins only.
--   'rejected_lender' is excluded from all queries.
--
-- Tier definitions:
--   verified_lender   — RBI registry matched, category confirmed, contacts valid,
--                       website belongs to entity, quality ≥ 60%
--   provisional_lender — RBI reg number valid/unverified, has loan products,
--                       quality ≥ 45%; shown to borrowers with disclosure
--   pending_review    — Passed hard gates but has gaps (contact, website, quality);
--                       admin review required before promotion
--   rejected_lender   — Failed one or more hard gates; never shown to borrowers

-- ── 1. Add column ─────────────────────────────────────────────────────────────

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS onboarding_tier TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (onboarding_tier IN (
            'verified_lender',    -- RBI-matched, full data, contacts verified
            'provisional_lender', -- reg number present, products known, some gaps
            'pending_review',     -- passed hard gates but needs manual review
            'rejected_lender'     -- failed hard gate; excluded from product layer
        ));

-- ── 2. Index: product layer query (borrowers see only verified + provisional) ─

CREATE INDEX IF NOT EXISTS idx_lenders_onboarding_tier
    ON lenders (onboarding_tier)
    WHERE onboarding_tier IN ('verified_lender', 'provisional_lender');

-- ── 3. Index: admin review queue ──────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_lenders_pending_review
    ON lenders (last_updated DESC)
    WHERE onboarding_tier = 'pending_review';

-- ── 4. Update match_lenders() function to filter by tier ─────────────────────
-- match_lenders() is defined in schema_v2.sql.  This migration adds a WHERE
-- clause so the function only considers lenders available to borrowers.
-- NOTE: Run this only if match_lenders() already exists (safe with IF EXISTS).

DO $$
BEGIN
    -- Add onboarding_tier filter to the policies join with lenders
    -- This is handled at the application layer in loans.py for now;
    -- the DB function will be updated in a dedicated migration when
    -- match_lenders() is refactored to accept tier parameters.
    NULL;
END $$;

-- ── 5. Record migration ────────────────────────────────────────────────────────
