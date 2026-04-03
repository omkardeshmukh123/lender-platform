-- migration 021: policy version history table
-- =============================================
-- Problem:
--   version field increments (migration 020) but the old data is permanently
--   overwritten on each upsert.  Banks require immutable audit history:
--   "what did policy X say on date Y?"
--
-- Solution:
--   policies_history — a snapshot table.
--   A BEFORE UPDATE trigger on policies writes the CURRENT row into
--   policies_history BEFORE applying the change, whenever any
--   underwriting-critical field is being modified.
--
--   Old versions are NEVER deleted.  The history table is append-only.
--
-- Querying history:
--   SELECT * FROM policies_history
--   WHERE policy_id = 42
--   ORDER BY changed_at DESC;

-- ── 1. History table ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS policies_history (
  -- Surrogate key for the history row
  history_id       BIGSERIAL PRIMARY KEY,

  -- Identity of the original row
  policy_id        BIGINT NOT NULL,
  lender_id        BIGINT NOT NULL,
  version_snapshot INTEGER NOT NULL,   -- version number of THIS snapshot
  changed_at       TIMESTAMP NOT NULL DEFAULT NOW(),

  -- Snapshot of the full policy at the moment it was superseded
  product_name          TEXT,
  product_name_normalized TEXT,
  loan_type             TEXT,
  loan_amount_min       NUMERIC,
  loan_amount_max       NUMERIC,
  credit_score_min      INTEGER,
  credit_score_max      INTEGER,
  min_age               INTEGER,
  max_age               INTEGER,
  employment_types      TEXT[],
  min_monthly_income    NUMERIC,
  min_annual_turnover   NUMERIC,
  min_business_vintage  INTEGER,
  interest_rate_min     NUMERIC,
  interest_rate_max     NUMERIC,
  tenure_min            INTEGER,
  tenure_max            INTEGER,
  processing_fee        NUMERIC,
  prepayment_allowed    BOOLEAN,
  collateral_required   BOOLEAN,
  collateral_types      TEXT[],
  eligible_states       TEXT[],
  eligibility_notes     TEXT,
  kyc_pan_required      BOOLEAN,
  kyc_aadhaar_required  BOOLEAN,
  kyc_gstin_required    BOOLEAN,
  kyc_itr_years         INTEGER,
  completeness_score    NUMERIC,
  data_source           TEXT,
  source_url            TEXT,
  source_confidence     TEXT,
  approval_status       TEXT,
  is_verified           BOOLEAN,
  review_priority       TEXT,
  anomaly_flags         TEXT[],
  eligibility_notes_raw TEXT   -- alias, kept for clarity
);

-- No deletes ever — history is immutable
ALTER TABLE policies_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY "deny_delete_history"
    ON policies_history FOR DELETE TO authenticated
    USING (FALSE);

-- ── 2. Trigger: write snapshot BEFORE substantive UPDATE ─────────────────────

CREATE OR REPLACE FUNCTION trg_archive_policy_version()
RETURNS TRIGGER AS $$
BEGIN
    -- Only archive when underwriting-critical fields change
    -- (same condition list as trg_increment_policy_version in migration 020)
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
        INSERT INTO policies_history (
            policy_id, lender_id, version_snapshot, changed_at,
            product_name, product_name_normalized, loan_type,
            loan_amount_min, loan_amount_max,
            credit_score_min, credit_score_max,
            min_age, max_age,
            employment_types,
            min_monthly_income, min_annual_turnover, min_business_vintage,
            interest_rate_min, interest_rate_max,
            tenure_min, tenure_max,
            processing_fee, prepayment_allowed,
            collateral_required, collateral_types,
            eligible_states, eligibility_notes,
            kyc_pan_required, kyc_aadhaar_required,
            kyc_gstin_required, kyc_itr_years,
            completeness_score, data_source, source_url,
            source_confidence, approval_status, is_verified,
            review_priority, anomaly_flags
        ) VALUES (
            OLD.id, OLD.lender_id, OLD.version, NOW(),
            OLD.product_name, OLD.product_name_normalized, OLD.loan_type,
            OLD.loan_amount_min, OLD.loan_amount_max,
            OLD.credit_score_min, OLD.credit_score_max,
            OLD.min_age, OLD.max_age,
            OLD.employment_types,
            OLD.min_monthly_income, OLD.min_annual_turnover, OLD.min_business_vintage,
            OLD.interest_rate_min, OLD.interest_rate_max,
            OLD.tenure_min, OLD.tenure_max,
            OLD.processing_fee, OLD.prepayment_allowed,
            OLD.collateral_required, OLD.collateral_types,
            OLD.eligible_states, OLD.eligibility_notes,
            OLD.kyc_pan_required, OLD.kyc_aadhaar_required,
            OLD.kyc_gstin_required, OLD.kyc_itr_years,
            OLD.completeness_score, OLD.data_source, OLD.source_url,
            OLD.source_confidence, OLD.approval_status, OLD.is_verified,
            OLD.review_priority, OLD.anomaly_flags
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- This trigger fires BEFORE trg_policy_version (migration 020), so it captures
-- the OLD row before the version number is incremented.
DROP TRIGGER IF EXISTS trg_archive_before_version_bump ON policies;
CREATE TRIGGER trg_archive_before_version_bump
BEFORE UPDATE ON policies
FOR EACH ROW EXECUTE FUNCTION trg_archive_policy_version();

-- ── 3. Indexes for history queries ────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_policy_history_policy_id
    ON policies_history (policy_id, version_snapshot DESC);

CREATE INDEX IF NOT EXISTS idx_policy_history_lender_id
    ON policies_history (lender_id, changed_at DESC);

-- ── 4. Convenience view: latest policy with change count ─────────────────────

CREATE OR REPLACE VIEW policies_with_history_count AS
SELECT
    p.*,
    COUNT(h.history_id) AS version_count
FROM policies p
LEFT JOIN policies_history h ON h.policy_id = p.id
GROUP BY p.id;

-- ── 5. Record migration ────────────────────────────────────────────────────────
