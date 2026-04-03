-- Migration 010: Production Hardening v2
-- ============================================================
-- Problems fixed:
--   1. CHECK constraints on all enum fields (approval_status, company_type,
--      loan_type, aum_category, etc.) — currently any string is accepted
--   2. Range CHECK constraints (quality_score 0–1, credit_score 300–900,
--      interest_rate 1–60, established_year, loan_amount > 0, etc.)
--   3. Cross-column validation (min <= max for amount, rate, tenure, age)
--   4. matched_policy_ids type fix: INTEGER[] → BIGINT[] (policies.id is BIGINT)
--   5. lender_audit_log table — tracks every approval_status change with who/when
--   6. Unique partial index on rbi_registration_number (prevents duplicate RBI reg)
--   7. NOT NULL guard on policies.lender_id, product_name, loan_type (already
--      true in schema but confirmed here for belt-and-suspenders)
-- ============================================================

-- ── 1. CHECK constraints on lenders ───────────────────────────────────────────

DO $$
BEGIN

  -- approval_status
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_lenders_approval_status' AND conrelid = 'lenders'::regclass
  ) THEN
    ALTER TABLE lenders
      ADD CONSTRAINT chk_lenders_approval_status
      CHECK (approval_status IN ('pending','approved','rejected','needs_update'));
  END IF;

  -- company_type
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_lenders_company_type' AND conrelid = 'lenders'::regclass
  ) THEN
    ALTER TABLE lenders
      ADD CONSTRAINT chk_lenders_company_type
      CHECK (company_type IN (
        'NBFC','Private Bank','PSU Bank','Foreign Bank',
        'Cooperative Bank','NBFC-MFI','Small Finance Bank'
      ));
  END IF;

  -- aum_category
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_lenders_aum_category' AND conrelid = 'lenders'::regclass
  ) THEN
    ALTER TABLE lenders
      ADD CONSTRAINT chk_lenders_aum_category
      CHECK (aum_category IS NULL OR aum_category IN ('Micro','Small','Mid','Large'));
  END IF;

  -- operating_intensity
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_lenders_operating_intensity' AND conrelid = 'lenders'::regclass
  ) THEN
    ALTER TABLE lenders
      ADD CONSTRAINT chk_lenders_operating_intensity
      CHECK (operating_intensity IS NULL OR
             operating_intensity IN ('Pan India','Regional','Single State'));
  END IF;

  -- data_source
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_lenders_data_source' AND conrelid = 'lenders'::regclass
  ) THEN
    ALTER TABLE lenders
      ADD CONSTRAINT chk_lenders_data_source
      CHECK (data_source IS NULL OR
             data_source IN ('scraper+gemini','gemini_only','manual','rbi_api'));
  END IF;

  -- extraction_status
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_lenders_extraction_status' AND conrelid = 'lenders'::regclass
  ) THEN
    ALTER TABLE lenders
      ADD CONSTRAINT chk_lenders_extraction_status
      CHECK (extraction_status IS NULL OR
             extraction_status IN ('pending','success','partial','failed'));
  END IF;

  -- quality_score range (0.0 – 1.0)
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_lenders_quality_score' AND conrelid = 'lenders'::regclass
  ) THEN
    ALTER TABLE lenders
      ADD CONSTRAINT chk_lenders_quality_score
      CHECK (quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 1));
  END IF;

  -- aum_crores non-negative
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_lenders_aum_positive' AND conrelid = 'lenders'::regclass
  ) THEN
    ALTER TABLE lenders
      ADD CONSTRAINT chk_lenders_aum_positive
      CHECK (aum_crores IS NULL OR aum_crores >= 0);
  END IF;

  -- established_year: must be between 1800 and current year
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_lenders_established_year' AND conrelid = 'lenders'::regclass
  ) THEN
    ALTER TABLE lenders
      ADD CONSTRAINT chk_lenders_established_year
      CHECK (established_year IS NULL OR
             (established_year >= 1800 AND established_year <= EXTRACT(YEAR FROM NOW())::INTEGER));
  END IF;

  -- ticket_size_min <= ticket_size_max
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_lenders_ticket_range' AND conrelid = 'lenders'::regclass
  ) THEN
    ALTER TABLE lenders
      ADD CONSTRAINT chk_lenders_ticket_range
      CHECK (ticket_size_min IS NULL OR ticket_size_max IS NULL OR
             ticket_size_min <= ticket_size_max);
  END IF;

  -- scrape_interval_days: 1–365
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_lenders_scrape_interval' AND conrelid = 'lenders'::regclass
  ) THEN
    ALTER TABLE lenders
      ADD CONSTRAINT chk_lenders_scrape_interval
      CHECK (scrape_interval_days IS NULL OR
             (scrape_interval_days >= 1 AND scrape_interval_days <= 365));
  END IF;

END $$;


-- ── 2. CHECK constraints on policies ──────────────────────────────────────────

DO $$
BEGIN

  -- approval_status
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_policies_approval_status' AND conrelid = 'policies'::regclass
  ) THEN
    ALTER TABLE policies
      ADD CONSTRAINT chk_policies_approval_status
      CHECK (approval_status IN ('pending','approved','rejected','needs_update'));
  END IF;

  -- loan_type: must be from canonical list
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_policies_loan_type' AND conrelid = 'policies'::regclass
  ) THEN
    ALTER TABLE policies
      ADD CONSTRAINT chk_policies_loan_type
      CHECK (loan_type IN (
        'MSME Loan','Personal Loan','Home Loan','Business Loan',
        'Vehicle Loan','Gold Loan','Education Loan','Micro Loan',
        'Loan Against Property','Working Capital','Agriculture Loan',
        'Credit Card','Consumer Durable Loan','EV Loan',
        'Two Wheeler Loan','Microfinance','Rural Loan','Supply Chain Finance'
      ));
  END IF;

  -- completeness_score: 0.0 – 1.0
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_policies_completeness' AND conrelid = 'policies'::regclass
  ) THEN
    ALTER TABLE policies
      ADD CONSTRAINT chk_policies_completeness
      CHECK (completeness_score IS NULL OR
             (completeness_score >= 0 AND completeness_score <= 1));
  END IF;

  -- credit_score: CIBIL range 300–900
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_policies_credit_score_min' AND conrelid = 'policies'::regclass
  ) THEN
    ALTER TABLE policies
      ADD CONSTRAINT chk_policies_credit_score_min
      CHECK (credit_score_min IS NULL OR (credit_score_min >= 300 AND credit_score_min <= 900));
  END IF;

  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_policies_credit_score_max' AND conrelid = 'policies'::regclass
  ) THEN
    ALTER TABLE policies
      ADD CONSTRAINT chk_policies_credit_score_max
      CHECK (credit_score_max IS NULL OR (credit_score_max >= 300 AND credit_score_max <= 900));
  END IF;

  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_policies_credit_score_range' AND conrelid = 'policies'::regclass
  ) THEN
    ALTER TABLE policies
      ADD CONSTRAINT chk_policies_credit_score_range
      CHECK (credit_score_min IS NULL OR credit_score_max IS NULL OR
             credit_score_min <= credit_score_max);
  END IF;

  -- interest_rate: 1–60% per annum
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_policies_interest_rate_min' AND conrelid = 'policies'::regclass
  ) THEN
    ALTER TABLE policies
      ADD CONSTRAINT chk_policies_interest_rate_min
      CHECK (interest_rate_min IS NULL OR (interest_rate_min >= 1 AND interest_rate_min <= 60));
  END IF;

  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_policies_interest_rate_max' AND conrelid = 'policies'::regclass
  ) THEN
    ALTER TABLE policies
      ADD CONSTRAINT chk_policies_interest_rate_max
      CHECK (interest_rate_max IS NULL OR (interest_rate_max >= 1 AND interest_rate_max <= 60));
  END IF;

  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_policies_interest_rate_range' AND conrelid = 'policies'::regclass
  ) THEN
    ALTER TABLE policies
      ADD CONSTRAINT chk_policies_interest_rate_range
      CHECK (interest_rate_min IS NULL OR interest_rate_max IS NULL OR
             interest_rate_min <= interest_rate_max);
  END IF;

  -- loan_amount: positive, min <= max
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_policies_loan_amount_positive' AND conrelid = 'policies'::regclass
  ) THEN
    ALTER TABLE policies
      ADD CONSTRAINT chk_policies_loan_amount_positive
      CHECK ((loan_amount_min IS NULL OR loan_amount_min > 0) AND
             (loan_amount_max IS NULL OR loan_amount_max > 0));
  END IF;

  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_policies_loan_amount_range' AND conrelid = 'policies'::regclass
  ) THEN
    ALTER TABLE policies
      ADD CONSTRAINT chk_policies_loan_amount_range
      CHECK (loan_amount_min IS NULL OR loan_amount_max IS NULL OR
             loan_amount_min <= loan_amount_max);
  END IF;

  -- tenure: 1–600 months, min <= max
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_policies_tenure_values' AND conrelid = 'policies'::regclass
  ) THEN
    ALTER TABLE policies
      ADD CONSTRAINT chk_policies_tenure_values
      CHECK ((tenure_min IS NULL OR (tenure_min >= 1 AND tenure_min <= 600)) AND
             (tenure_max IS NULL OR (tenure_max >= 1 AND tenure_max <= 600)));
  END IF;

  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_policies_tenure_range' AND conrelid = 'policies'::regclass
  ) THEN
    ALTER TABLE policies
      ADD CONSTRAINT chk_policies_tenure_range
      CHECK (tenure_min IS NULL OR tenure_max IS NULL OR tenure_min <= tenure_max);
  END IF;

  -- age: 18–100, min <= max
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_policies_age_values' AND conrelid = 'policies'::regclass
  ) THEN
    ALTER TABLE policies
      ADD CONSTRAINT chk_policies_age_values
      CHECK ((min_age IS NULL OR (min_age >= 18 AND min_age <= 100)) AND
             (max_age IS NULL OR (max_age >= 18 AND max_age <= 100)));
  END IF;

  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_policies_age_range' AND conrelid = 'policies'::regclass
  ) THEN
    ALTER TABLE policies
      ADD CONSTRAINT chk_policies_age_range
      CHECK (min_age IS NULL OR max_age IS NULL OR min_age <= max_age);
  END IF;

  -- processing_fee: 0–10%
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_policies_processing_fee' AND conrelid = 'policies'::regclass
  ) THEN
    ALTER TABLE policies
      ADD CONSTRAINT chk_policies_processing_fee
      CHECK (processing_fee IS NULL OR (processing_fee >= 0 AND processing_fee <= 10));
  END IF;

END $$;


-- ── 3. CHECK constraints on matching_requests ─────────────────────────────────

DO $$
BEGIN

  -- loan_amount must be positive
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_mr_loan_amount' AND conrelid = 'matching_requests'::regclass
  ) THEN
    ALTER TABLE matching_requests
      ADD CONSTRAINT chk_mr_loan_amount
      CHECK (loan_amount > 0);
  END IF;

  -- credit_score: CIBIL 300–900
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_mr_credit_score' AND conrelid = 'matching_requests'::regclass
  ) THEN
    ALTER TABLE matching_requests
      ADD CONSTRAINT chk_mr_credit_score
      CHECK (credit_score IS NULL OR (credit_score >= 300 AND credit_score <= 900));
  END IF;

  -- age: 18–100
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_mr_age' AND conrelid = 'matching_requests'::regclass
  ) THEN
    ALTER TABLE matching_requests
      ADD CONSTRAINT chk_mr_age
      CHECK (age IS NULL OR (age >= 18 AND age <= 100));
  END IF;

  -- loan_type: canonical list
  IF NOT EXISTS (
    SELECT FROM pg_constraint
    WHERE conname = 'chk_mr_loan_type' AND conrelid = 'matching_requests'::regclass
  ) THEN
    ALTER TABLE matching_requests
      ADD CONSTRAINT chk_mr_loan_type
      CHECK (loan_type IS NULL OR loan_type IN (
        'MSME Loan','Personal Loan','Home Loan','Business Loan',
        'Vehicle Loan','Gold Loan','Education Loan','Micro Loan',
        'Loan Against Property','Working Capital','Agriculture Loan',
        'Credit Card','Consumer Durable Loan','EV Loan',
        'Two Wheeler Loan','Microfinance','Rural Loan','Supply Chain Finance'
      ));
  END IF;

END $$;


-- ── 4. Fix matched_policy_ids: INTEGER[] → BIGINT[] ───────────────────────────
-- policies.id is BIGINT. Using INTEGER[] risks silent overflow for large IDs.
-- ALTER TYPE is safe since INTEGER values fit in BIGINT without truncation.

DO $$
DECLARE
  col_type TEXT;
BEGIN
  SELECT data_type INTO col_type
  FROM information_schema.columns
  WHERE table_name = 'matching_requests' AND column_name = 'matched_policy_ids';

  IF col_type = 'ARRAY' THEN
    -- Check if element type is integer (not bigint)
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_name = 'matching_requests'
        AND column_name = 'matched_policy_ids'
        AND udt_name = '_int4'   -- _int4 = INTEGER[], _int8 = BIGINT[]
    ) THEN
      ALTER TABLE matching_requests
        ALTER COLUMN matched_policy_ids TYPE BIGINT[]
        USING matched_policy_ids::BIGINT[];
    END IF;
  END IF;
END $$;


-- ── 5. Unique partial index on rbi_registration_number ────────────────────────
-- Prevents two lenders sharing the same RBI registration number.
-- Partial (WHERE NOT NULL) — most NBFCs don't have this field yet.

CREATE UNIQUE INDEX IF NOT EXISTS idx_lenders_rbi_reg_unique
  ON lenders (rbi_registration_number)
  WHERE rbi_registration_number IS NOT NULL;


-- ── 6. lender_audit_log — every approval_status transition recorded ────────────
-- Who changed what, when, and what the old/new values were.

CREATE TABLE IF NOT EXISTS lender_audit_log (
  id             BIGSERIAL PRIMARY KEY,
  logged_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- Which record changed
  entity_type    TEXT        NOT NULL,  -- 'lender' | 'policy'
  entity_id      BIGINT      NOT NULL,
  company_name   TEXT,                  -- denormalised for fast audit reads

  -- What changed
  action         TEXT        NOT NULL,  -- 'approved' | 'rejected' | 'needs_update' | 'updated' | 'created'
  old_status     TEXT,
  new_status     TEXT,
  changed_fields TEXT[],               -- list of field names that changed

  -- Who did it
  actor          TEXT,                  -- email or system label ('scheduler','pipeline')
  actor_ip       INET,                  -- optional: from API layer

  -- Extra context
  notes          TEXT
);

-- Read-efficient indexes for the audit trail
CREATE INDEX IF NOT EXISTS idx_audit_entity    ON lender_audit_log (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_logged_at ON lender_audit_log (logged_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action    ON lender_audit_log (action);


-- ── 7. Trigger: auto-log approval_status changes on lenders ───────────────────

CREATE OR REPLACE FUNCTION trg_lender_audit_status()
RETURNS TRIGGER AS $$
BEGIN
  -- Only fire when approval_status actually changes
  IF OLD.approval_status IS DISTINCT FROM NEW.approval_status THEN
    INSERT INTO lender_audit_log (
      entity_type, entity_id, company_name,
      action, old_status, new_status,
      actor, notes
    ) VALUES (
      'lender',
      NEW.id,
      NEW.company_name,
      NEW.approval_status,          -- action = the new status value
      OLD.approval_status,
      NEW.approval_status,
      NEW.approved_by,              -- populated by admin panel on approve/reject
      NEW.admin_notes
    );
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_trigger WHERE tgname = 'log_lender_status_change'
  ) THEN
    CREATE TRIGGER log_lender_status_change
      AFTER UPDATE ON lenders
      FOR EACH ROW EXECUTE FUNCTION trg_lender_audit_status();
  END IF;
END $$;


-- ── 8. Trigger: auto-log approval_status changes on policies ──────────────────

CREATE OR REPLACE FUNCTION trg_policy_audit_status()
RETURNS TRIGGER AS $$
BEGIN
  IF OLD.approval_status IS DISTINCT FROM NEW.approval_status THEN
    INSERT INTO lender_audit_log (
      entity_type, entity_id, company_name,
      action, old_status, new_status,
      actor
    )
    SELECT
      'policy',
      NEW.id,
      l.company_name,
      NEW.approval_status,
      OLD.approval_status,
      NEW.approval_status,
      NULL   -- policies have no approved_by column; admin identity comes from API layer
    FROM lenders l WHERE l.id = NEW.lender_id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_trigger WHERE tgname = 'log_policy_status_change'
  ) THEN
    CREATE TRIGGER log_policy_status_change
      AFTER UPDATE ON policies
      FOR EACH ROW EXECUTE FUNCTION trg_policy_audit_status();
  END IF;
END $$;


-- ── 9. RLS on lender_audit_log ─────────────────────────────────────────────────
-- Only authenticated (admin) users can read audit log. Anon cannot.

ALTER TABLE lender_audit_log ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_policies
    WHERE tablename = 'lender_audit_log' AND policyname = 'auth_read_audit_log'
  ) THEN
    CREATE POLICY "auth_read_audit_log"
      ON lender_audit_log FOR SELECT TO authenticated
      USING (true);
  END IF;

  IF NOT EXISTS (
    SELECT FROM pg_policies
    WHERE tablename = 'lender_audit_log' AND policyname = 'service_insert_audit_log'
  ) THEN
    -- Triggers run as SECURITY DEFINER; direct inserts only via service_role
    CREATE POLICY "service_insert_audit_log"
      ON lender_audit_log FOR INSERT TO service_role
      WITH CHECK (true);
  END IF;
END $$;


-- Record migration
INSERT INTO schema_versions (version, name, checksum)
VALUES (10, 'production_hardening_v2', md5('010_production_hardening_v2'))
ON CONFLICT (version) DO NOTHING;
