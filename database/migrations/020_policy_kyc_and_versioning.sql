-- migration 020: KYC fields + auto-versioning + IR floor exception
-- =================================================================
-- Problems addressed:
--   1. No structured KYC fields — PAN/Aadhaar/GSTIN/ITR buried in free-text
--      eligibility_notes, making compliance checks and application flow impossible.
--   2. version field exists but never increments — policy changes are silent overwrites.
--   3. interest_rate_min >= 5% CHECK constraint (018) breaks Consumer Durable Loan
--      zero-cost EMI schemes which legitimately have 0% rates.

-- ── 1. KYC / documentation columns ────────────────────────────────────────

ALTER TABLE policies
    ADD COLUMN IF NOT EXISTS kyc_pan_required     BOOLEAN,
    ADD COLUMN IF NOT EXISTS kyc_aadhaar_required BOOLEAN,
    ADD COLUMN IF NOT EXISTS kyc_gstin_required   BOOLEAN,
    ADD COLUMN IF NOT EXISTS kyc_itr_years        INTEGER;

-- Guard against nonsensical ITR year values
ALTER TABLE policies
    ADD CONSTRAINT chk_kyc_itr_years
    CHECK (kyc_itr_years IS NULL OR (kyc_itr_years >= 1 AND kyc_itr_years <= 5));

-- ── 2. Auto-increment version on substantive policy changes ───────────────
-- Triggered only when underwriting-critical fields change (not metadata).
-- Also clears is_verified — a changed policy needs re-verification.

CREATE OR REPLACE FUNCTION trg_increment_policy_version()
RETURNS TRIGGER AS $$
BEGIN
    IF (
        OLD.interest_rate_min       IS DISTINCT FROM NEW.interest_rate_min  OR
        OLD.interest_rate_max       IS DISTINCT FROM NEW.interest_rate_max  OR
        OLD.credit_score_min        IS DISTINCT FROM NEW.credit_score_min   OR
        OLD.loan_amount_min         IS DISTINCT FROM NEW.loan_amount_min    OR
        OLD.loan_amount_max         IS DISTINCT FROM NEW.loan_amount_max    OR
        OLD.employment_types        IS DISTINCT FROM NEW.employment_types   OR
        OLD.collateral_required     IS DISTINCT FROM NEW.collateral_required OR
        OLD.tenure_min              IS DISTINCT FROM NEW.tenure_min         OR
        OLD.tenure_max              IS DISTINCT FROM NEW.tenure_max         OR
        OLD.min_monthly_income      IS DISTINCT FROM NEW.min_monthly_income OR
        OLD.kyc_pan_required        IS DISTINCT FROM NEW.kyc_pan_required   OR
        OLD.kyc_aadhaar_required    IS DISTINCT FROM NEW.kyc_aadhaar_required OR
        OLD.kyc_gstin_required      IS DISTINCT FROM NEW.kyc_gstin_required OR
        OLD.kyc_itr_years           IS DISTINCT FROM NEW.kyc_itr_years
    ) THEN
        NEW.version      := OLD.version + 1;
        NEW.is_verified  := FALSE;   -- re-verification required after substantive change
        NEW.last_updated := NOW();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_policy_version ON policies;
CREATE TRIGGER trg_policy_version
BEFORE UPDATE ON policies
FOR EACH ROW EXECUTE FUNCTION trg_increment_policy_version();

-- ── 3. Fix interest rate floor — exempt Consumer Durable Loan ─────────────
-- The constraint added in 018 blocked zero-cost EMI schemes which are valid
-- and common for Consumer Durable Loans (0% interest, cost embedded in price).

ALTER TABLE policies DROP CONSTRAINT IF EXISTS chk_interest_rate_floor;

ALTER TABLE policies
    ADD CONSTRAINT chk_interest_rate_floor
    CHECK (
        interest_rate_min IS NULL
        OR loan_type = 'Consumer Durable Loan'
        OR interest_rate_min >= 5.0
    );

-- ── 4. Index to support KYC-based admin queries ───────────────────────────
CREATE INDEX IF NOT EXISTS idx_policies_kyc
    ON policies (lender_id)
    WHERE kyc_pan_required IS NULL
       OR kyc_aadhaar_required IS NULL;

-- ── 5. Record migration ────────────────────────────────────────────────────
