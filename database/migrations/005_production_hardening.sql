-- Migration 005: Production hardening
-- 1. Default partition (safety net when monthly partition is missing)
-- 2. Unique constraint on policies (prevent duplicate products per lender)
-- 3. Updated match_lenders() — adds processing_fee + loan_type to return type
--    so the API router needs no redundant JOINs.
-- UP

-- ── 1. Default partition ────────────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT FROM information_schema.tables
        WHERE table_name = 'matching_requests'
    ) THEN
        IF NOT EXISTS (
            SELECT FROM pg_class c
            JOIN pg_inherits i ON i.inhrelid = c.oid
            JOIN pg_class p   ON p.oid = i.inhparent
            WHERE p.relname = 'matching_requests'
              AND c.relname = 'matching_requests_default'
        ) THEN
            EXECUTE 'CREATE TABLE matching_requests_default
                     PARTITION OF matching_requests DEFAULT';
        END IF;
    END IF;
END $$;

-- ── 2. Unique constraint on policies ───────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT FROM information_schema.tables WHERE table_name = 'policies'
    ) THEN
        IF NOT EXISTS (
            SELECT FROM pg_constraint WHERE conname = 'uq_policy_product'
        ) THEN
            ALTER TABLE policies
                ADD CONSTRAINT uq_policy_product
                UNIQUE (lender_id, product_name, loan_type);
        END IF;
    END IF;
END $$;

-- ── 3. Updated match_lenders() ─────────────────────────────────────────────
-- Adds processing_fee and loan_type to the RETURNS TABLE.
-- The API router can now use SELECT * FROM match_lenders() with no extra JOINs.
DO $$
BEGIN
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'policies')
    AND EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'lenders')
    THEN
        -- DROP required because CREATE OR REPLACE cannot change a function's return type.
        -- schema_v2.sql defines match_lenders() without loan_type/processing_fee columns.
        -- Attempting CREATE OR REPLACE without DROP raises:
        --   "ERROR: cannot change return type of existing function"
        EXECUTE 'DROP FUNCTION IF EXISTS match_lenders CASCADE';
        EXECUTE $func$
CREATE OR REPLACE FUNCTION match_lenders(
  p_loan_type         TEXT,
  p_loan_amount       NUMERIC,
  p_credit_score      INTEGER  DEFAULT NULL,
  p_employment_type   TEXT     DEFAULT NULL,
  p_state             TEXT     DEFAULT NULL,
  p_age               INTEGER  DEFAULT NULL,
  p_monthly_income    NUMERIC  DEFAULT NULL,
  p_business_vintage  INTEGER  DEFAULT NULL,
  p_limit             INTEGER  DEFAULT 20
)
RETURNS TABLE (
  lender_id           BIGINT,
  company_name        TEXT,
  company_type        TEXT,
  aum_crores          NUMERIC,
  website             TEXT,
  policy_id           BIGINT,
  product_name        TEXT,
  loan_type           TEXT,
  loan_amount_min     NUMERIC,
  loan_amount_max     NUMERIC,
  interest_rate_min   NUMERIC,
  interest_rate_max   NUMERIC,
  processing_fee      NUMERIC,
  credit_score_min    INTEGER,
  employment_types    TEXT[],
  tenure_min          INTEGER,
  tenure_max          INTEGER,
  collateral_required BOOLEAN,
  eligibility_notes   TEXT,
  match_score         INTEGER,
  match_reasons       TEXT[]
) AS $body$
BEGIN
  RETURN QUERY
  WITH scored AS (
    SELECT
      l.id                          AS lender_id,
      l.company_name,
      l.company_type,
      l.aum_crores,
      l.website,
      p.id                          AS policy_id,
      p.product_name,
      p.loan_type,
      p.loan_amount_min,
      p.loan_amount_max,
      p.interest_rate_min,
      p.interest_rate_max,
      p.processing_fee,
      p.credit_score_min,
      p.employment_types,
      p.tenure_min,
      p.tenure_max,
      p.collateral_required,
      p.eligibility_notes,

      -- ── Score calculation (100 pts total) ─────────────────
      (
        -- 1. Credit score (30 pts)
        CASE
          WHEN p_credit_score IS NULL             THEN 20
          WHEN p.credit_score_min IS NULL         THEN 25
          WHEN p_credit_score >= p.credit_score_min THEN 30
          ELSE 0
        END
        +
        -- 2. Loan amount in range (20 pts)
        CASE
          WHEN p.loan_amount_min IS NULL AND p.loan_amount_max IS NULL THEN 10
          WHEN p.loan_amount_min IS NULL AND p_loan_amount <= p.loan_amount_max THEN 20
          WHEN p.loan_amount_max IS NULL AND p_loan_amount >= p.loan_amount_min THEN 20
          WHEN p_loan_amount BETWEEN COALESCE(p.loan_amount_min, 0)
                                 AND COALESCE(p.loan_amount_max, 999999) THEN 20
          ELSE 0
        END
        +
        -- 3. State eligibility (20 pts) — pan_india overrides eligible_states
        CASE
          WHEN p_state IS NULL                           THEN 10
          WHEN l.pan_india = TRUE                        THEN 20
          WHEN p.eligible_states IS NULL                 THEN 15
          WHEN p_state = ANY(p.eligible_states)          THEN 20
          ELSE 0
        END
        +
        -- 4. Employment type (15 pts)
        CASE
          WHEN p_employment_type IS NULL                 THEN 8
          WHEN p.employment_types IS NULL                THEN 10
          WHEN p_employment_type = ANY(p.employment_types) THEN 15
          ELSE 0
        END
        +
        -- 5. Data completeness bonus (15 pts)
        CASE
          WHEN COALESCE(p.completeness_score, 0) >= 0.8 THEN 15
          WHEN COALESCE(p.completeness_score, 0) >= 0.5 THEN 8
          ELSE 3
        END
      )::INTEGER AS match_score,

      -- ── Match reason tags ─────────────────────────────────
      ARRAY_REMOVE(ARRAY[
        CASE WHEN p_credit_score IS NOT NULL
              AND p.credit_score_min IS NOT NULL
              AND p_credit_score >= p.credit_score_min
             THEN 'Credit score eligible' ELSE NULL END,
        CASE WHEN p_loan_amount BETWEEN COALESCE(p.loan_amount_min, 0)
                                    AND COALESCE(p.loan_amount_max, 999999)
             THEN 'Loan amount in range' ELSE NULL END,
        CASE WHEN p_state IS NOT NULL
              AND (l.pan_india = TRUE
                   OR p.eligible_states IS NULL
                   OR p_state = ANY(p.eligible_states))
             THEN 'Available in your state' ELSE NULL END,
        CASE WHEN p_employment_type IS NOT NULL
              AND (p.employment_types IS NULL
                   OR p_employment_type = ANY(p.employment_types))
             THEN 'Employment type accepted' ELSE NULL END,
        CASE WHEN p.collateral_required = FALSE
             THEN 'No collateral needed' ELSE NULL END
      ], NULL) AS match_reasons

    FROM policies p
    JOIN lenders  l ON l.id = p.lender_id

    WHERE
      p.loan_type           = p_loan_type
      AND p.is_active       = TRUE
      AND p.approval_status = 'approved'
      AND l.approval_status = 'approved'
      -- Hard eligibility filters
      AND (p.loan_amount_min IS NULL OR p_loan_amount >= p.loan_amount_min)
      AND (p.loan_amount_max IS NULL OR p_loan_amount <= p.loan_amount_max)
      -- Credit score hard filter: exclude lenders whose minimum exceeds borrower score
      AND (p_credit_score IS NULL OR p.credit_score_min IS NULL
           OR p_credit_score >= p.credit_score_min)
      -- Employment type hard filter: exclude lenders that don't accept the employment type
      AND (p_employment_type IS NULL
           OR p.employment_types IS NULL
           OR p.employment_types = '{}'
           OR p_employment_type = ANY(p.employment_types))
      AND (p_age IS NULL OR p.min_age IS NULL OR p_age >= p.min_age)
      AND (p_age IS NULL OR p.max_age IS NULL OR p_age <= p.max_age)
      AND (p_monthly_income IS NULL OR p.min_monthly_income IS NULL
           OR p_monthly_income >= p.min_monthly_income)
      AND (p_business_vintage IS NULL OR p.min_business_vintage IS NULL
           OR p_business_vintage >= p.min_business_vintage)
  )
  SELECT
    s.lender_id, s.company_name, s.company_type, s.aum_crores, s.website,
    s.policy_id, s.product_name, s.loan_type,
    s.loan_amount_min, s.loan_amount_max,
    s.interest_rate_min, s.interest_rate_max,
    s.processing_fee,
    s.credit_score_min, s.employment_types,
    s.tenure_min, s.tenure_max,
    s.collateral_required, s.eligibility_notes,
    s.match_score, s.match_reasons
  FROM scored s
  WHERE s.match_score > 0
  ORDER BY s.match_score DESC, s.interest_rate_min ASC NULLS LAST
  LIMIT p_limit;
END;
$body$ LANGUAGE plpgsql SECURITY DEFINER;
        $func$;
    END IF;
END $$;

-- Record migration
INSERT INTO schema_versions (version, name, checksum)
VALUES (5, 'production_hardening', md5('005_production_hardening'))
ON CONFLICT (version) DO NOTHING;
