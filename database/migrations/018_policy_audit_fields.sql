-- migration 018: policy audit fields + cross-field consistency constraints
-- =========================================================================
-- Problems addressed:
--   1. No source lineage: impossible to distinguish AI-guessed vs scraped data
--   2. No versioning: overwrites happen silently with no history
--   3. No verification flag: admin has no way to mark a policy as human-verified
--   4. collateral_required DEFAULT FALSE: NULL (unknown) was coerced to False,
--      which misclassifies secured loans as unsecured — a financial risk
--   5. No cross-field CHECK constraints: DB allowed loan_amount_min > max, etc.

-- ── 1. Audit / lineage columns ─────────────────────────────────────────────

ALTER TABLE policies
    ADD COLUMN IF NOT EXISTS source_confidence TEXT    NOT NULL DEFAULT 'low',
    ADD COLUMN IF NOT EXISTS version           INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS is_verified       BOOLEAN NOT NULL DEFAULT FALSE;

-- source_confidence values: low (Gemini-only) / medium (scraped+Gemini) / high (manual/PDF)
ALTER TABLE policies
    ADD CONSTRAINT chk_source_confidence
    CHECK (source_confidence IN ('low', 'medium', 'high'));

-- ── 2. Fix collateral_required — allow NULL to mean "not extracted" ─────────
-- The column already lacks NOT NULL, so NULL is already storable.
-- Remove the DEFAULT FALSE so NULL propagates correctly on upsert.
ALTER TABLE policies
    ALTER COLUMN collateral_required DROP DEFAULT;

-- ── 3. Cross-field range consistency CHECKs ────────────────────────────────
-- NULL in either side short-circuits the check (PostgreSQL CHECK semantics:
-- CHECK only fails on FALSE, not NULL), so single-sided ranges are allowed.

ALTER TABLE policies
    ADD CONSTRAINT chk_loan_amount_range
    CHECK (loan_amount_min  IS NULL OR loan_amount_max  IS NULL
           OR loan_amount_min  <= loan_amount_max);

ALTER TABLE policies
    ADD CONSTRAINT chk_interest_rate_range
    CHECK (interest_rate_min IS NULL OR interest_rate_max IS NULL
           OR interest_rate_min <= interest_rate_max);

ALTER TABLE policies
    ADD CONSTRAINT chk_tenure_range
    CHECK (tenure_min IS NULL OR tenure_max IS NULL
           OR tenure_min <= tenure_max);

ALTER TABLE policies
    ADD CONSTRAINT chk_credit_score_range
    CHECK (credit_score_min IS NULL OR credit_score_max IS NULL
           OR credit_score_min <= credit_score_max);

ALTER TABLE policies
    ADD CONSTRAINT chk_age_range
    CHECK (min_age IS NULL OR max_age IS NULL
           OR min_age <= max_age);

-- ── 4. Interest rate floor — 5% minimum for Indian retail lending ───────────
ALTER TABLE policies
    ADD CONSTRAINT chk_interest_rate_floor
    CHECK (interest_rate_min IS NULL OR interest_rate_min >= 5.0);

-- ── 5. Index for admin verification workflow ───────────────────────────────
CREATE INDEX IF NOT EXISTS idx_policies_unverified
    ON policies (lender_id, is_verified)
    WHERE is_verified = FALSE AND approval_status = 'approved';

CREATE INDEX IF NOT EXISTS idx_policies_confidence
    ON policies (source_confidence, approval_status);

-- ── 6. Record migration ────────────────────────────────────────────────────
