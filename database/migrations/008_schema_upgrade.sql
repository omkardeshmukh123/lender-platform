-- Migration 008: Schema upgrade — add missing v2 columns and create policies table
--
-- Context: The lenders table was initialised with an older schema that is missing
-- several columns required by the API and admin workflow. The policies table does
-- not exist at all. Migrations 005-007 depend on both; this migration bridges the gap.
--
-- Changes:
--   1. Add missing columns to lenders (all with DEFAULT — backward-compat safe)
--   2. Set approval_status = 'approved' for all existing rows so they remain visible
--   3. Create policies table (IF NOT EXISTS)
--   4. Create helper functions (completeness scoring, auto-update triggers)
--   5. Create triggers on policies and lenders
--   6. Enable RLS on policies and create read policies
--   7. Create baseline indexes
-- UP

-- ── 1. Add missing columns to lenders ─────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'lenders') THEN
        ALTER TABLE lenders ADD COLUMN IF NOT EXISTS approval_status     TEXT      DEFAULT 'pending';
        ALTER TABLE lenders ADD COLUMN IF NOT EXISTS quality_score       NUMERIC   DEFAULT NULL;
        ALTER TABLE lenders ADD COLUMN IF NOT EXISTS data_hash           TEXT      DEFAULT NULL;
        ALTER TABLE lenders ADD COLUMN IF NOT EXISTS admin_notes         TEXT      DEFAULT NULL;
        ALTER TABLE lenders ADD COLUMN IF NOT EXISTS approved_by         TEXT      DEFAULT NULL;
        ALTER TABLE lenders ADD COLUMN IF NOT EXISTS approved_at         TIMESTAMP DEFAULT NULL;
        ALTER TABLE lenders ADD COLUMN IF NOT EXISTS last_scraped_at     TIMESTAMP DEFAULT NULL;
        ALTER TABLE lenders ADD COLUMN IF NOT EXISTS next_scrape_at      TIMESTAMP DEFAULT NULL;
        ALTER TABLE lenders ADD COLUMN IF NOT EXISTS scrape_interval_days INTEGER  DEFAULT 30;
    END IF;
END $$;

-- ── 2. Mark all existing lenders as approved ───────────────────────────────────
-- These were already in the DB before the approval workflow was introduced.
-- Setting to 'approved' keeps them visible to the API immediately.
UPDATE lenders
SET    approval_status = 'approved'
WHERE  approval_status IS NULL OR approval_status = 'pending';

-- ── 3. Create policies table ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS policies (
  id                    BIGSERIAL PRIMARY KEY,
  lender_id             BIGINT NOT NULL REFERENCES lenders(id) ON DELETE CASCADE,
  created_at            TIMESTAMP DEFAULT NOW(),
  last_updated          TIMESTAMP DEFAULT NOW(),
  schema_version        INTEGER   DEFAULT 2,

  -- Product identity
  product_name          TEXT NOT NULL,
  loan_type             TEXT NOT NULL,

  -- Loan amount (Lakhs)
  loan_amount_min       NUMERIC,
  loan_amount_max       NUMERIC,

  -- Borrower credit profile
  credit_score_min      INTEGER,
  credit_score_max      INTEGER,

  -- Borrower demographics
  min_age               INTEGER,
  max_age               INTEGER,
  employment_types      TEXT[],

  -- Income / financials
  min_monthly_income    NUMERIC,
  min_annual_turnover   NUMERIC,
  min_business_vintage  INTEGER,

  -- Loan terms
  interest_rate_min     NUMERIC,
  interest_rate_max     NUMERIC,
  tenure_min            INTEGER,
  tenure_max            INTEGER,
  processing_fee        NUMERIC,
  prepayment_allowed    BOOLEAN DEFAULT TRUE,

  -- Collateral
  collateral_required   BOOLEAN DEFAULT FALSE,
  collateral_types      TEXT[],

  -- Geography
  eligible_states       TEXT[],

  -- Notes
  eligibility_notes     TEXT,

  -- Data quality
  completeness_score    NUMERIC,
  data_source           TEXT,
  source_url            TEXT,

  -- Admin workflow
  is_active             BOOLEAN DEFAULT TRUE,
  approval_status       TEXT    DEFAULT 'pending',

  CONSTRAINT fk_lender FOREIGN KEY (lender_id) REFERENCES lenders(id)
);

-- ── 4. Helper functions ────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION compute_policy_completeness(p policies)
RETURNS NUMERIC AS $$
DECLARE
  score NUMERIC := 0;
  total INTEGER := 10;
BEGIN
  IF p.loan_amount_min    IS NOT NULL THEN score := score + 1; END IF;
  IF p.loan_amount_max    IS NOT NULL THEN score := score + 1; END IF;
  IF p.credit_score_min   IS NOT NULL THEN score := score + 1; END IF;
  IF p.interest_rate_min  IS NOT NULL THEN score := score + 1; END IF;
  IF p.interest_rate_max  IS NOT NULL THEN score := score + 1; END IF;
  IF p.tenure_min         IS NOT NULL THEN score := score + 1; END IF;
  IF p.tenure_max         IS NOT NULL THEN score := score + 1; END IF;
  IF p.employment_types IS NOT NULL AND array_length(p.employment_types, 1) > 0
     THEN score := score + 1; END IF;
  IF p.eligibility_notes IS NOT NULL AND length(p.eligibility_notes) > 10
     THEN score := score + 1; END IF;
  IF p.eligible_states IS NOT NULL THEN score := score + 1; END IF;
  RETURN ROUND(score::NUMERIC / total, 2);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION trg_policy_completeness()
RETURNS TRIGGER AS $$
BEGIN
  NEW.completeness_score := compute_policy_completeness(NEW);
  NEW.last_updated       := NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION trg_lender_updated()
RETURNS TRIGGER AS $$
BEGIN
  NEW.last_updated := NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ── 5. Triggers ────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_trigger WHERE tgname = 'set_policy_completeness'
    ) THEN
        CREATE TRIGGER set_policy_completeness
          BEFORE INSERT OR UPDATE ON policies
          FOR EACH ROW EXECUTE FUNCTION trg_policy_completeness();
    END IF;

    IF NOT EXISTS (
        SELECT FROM pg_trigger WHERE tgname = 'set_lender_updated'
    ) THEN
        CREATE TRIGGER set_lender_updated
          BEFORE UPDATE ON lenders
          FOR EACH ROW EXECUTE FUNCTION trg_lender_updated();
    END IF;
END $$;

-- ── 6. RLS on policies ─────────────────────────────────────────────────────────
ALTER TABLE policies ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_policies WHERE tablename = 'policies' AND policyname = 'public_read_approved_policies'
    ) THEN
        CREATE POLICY "public_read_approved_policies"
          ON policies FOR SELECT TO anon
          USING (approval_status = 'approved' AND is_active = TRUE);
    END IF;

    IF NOT EXISTS (
        SELECT FROM pg_policies WHERE tablename = 'policies' AND policyname = 'auth_read_all_policies'
    ) THEN
        CREATE POLICY "auth_read_all_policies"
          ON policies FOR SELECT TO authenticated
          USING (true);
    END IF;
END $$;

-- ── 7. Baseline indexes ────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_policies_lender       ON policies(lender_id);
CREATE INDEX IF NOT EXISTS idx_policies_loan_type    ON policies(loan_type);
CREATE INDEX IF NOT EXISTS idx_policies_active       ON policies(is_active, approval_status);
CREATE INDEX IF NOT EXISTS idx_policies_amount       ON policies(loan_amount_min, loan_amount_max);
CREATE INDEX IF NOT EXISTS idx_policies_credit_score ON policies(credit_score_min);
CREATE INDEX IF NOT EXISTS idx_policies_states       ON policies USING GIN(eligible_states);
CREATE INDEX IF NOT EXISTS idx_policies_employment   ON policies USING GIN(employment_types);

CREATE INDEX IF NOT EXISTS idx_lenders_approval      ON lenders(approval_status);
CREATE INDEX IF NOT EXISTS idx_lenders_approved      ON lenders(id) WHERE approval_status = 'approved';
CREATE INDEX IF NOT EXISTS idx_lenders_next_scrape   ON lenders(next_scrape_at);

-- Record migration
INSERT INTO schema_versions (version, name, checksum)
VALUES (8, 'schema_upgrade', md5('008_schema_upgrade'))
ON CONFLICT (version) DO NOTHING;
