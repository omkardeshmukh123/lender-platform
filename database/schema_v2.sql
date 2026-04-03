-- ============================================================
-- LENDER PLATFORM — SCHEMA v2
-- Moves from bank-level → policy-level data
--
-- Architecture:
--   lenders        (1 row per institution)
--   policies       (1-N rows per lender, one per loan product variant)
--   matching_requests  (borrower queries for analytics)
--
-- Matching flow:
--   borrower profile → match_lenders() → ranked policy list
-- ============================================================


-- ============================================================
-- 1. LENDERS (extended from v1)
-- ============================================================

CREATE TABLE lenders (
  -- Internal
  id                      BIGSERIAL PRIMARY KEY,
  created_at              TIMESTAMP DEFAULT NOW(),
  last_updated            TIMESTAMP DEFAULT NOW(),

  -- Identity
  company_name            TEXT NOT NULL,
  company_type            TEXT NOT NULL,        -- NBFC / PSU Bank / Private Bank / etc.
  rbi_registration_number TEXT,
  rbi_category            TEXT,                 -- NBFC-ND / NBFC-MFI / etc.
  is_listed               BOOLEAN DEFAULT FALSE,
  stock_symbol            TEXT,

  -- Size
  aum_crores              NUMERIC,
  aum_category            TEXT,                 -- Micro / Small / Mid / Large
  last_year_revenue       NUMERIC,
  employee_count          INTEGER,
  branch_count            INTEGER,
  established_year        INTEGER,
  has_subsidiaries        BOOLEAN DEFAULT FALSE,

  -- Products (summary — detail is in policies table)
  primary_product         TEXT,
  primary_loan_segments   JSONB,                -- ["MSME Loan","Personal Loan"]
  product_types           JSONB,

  -- Ticket size (institution-level range across all products)
  ticket_size_min         NUMERIC,              -- Lakhs
  ticket_size_max         NUMERIC,              -- Lakhs

  -- Geography
  hq_location             TEXT,
  hq_state                TEXT,
  operating_states        JSONB,                -- ["Maharashtra","Gujarat",...]
  operating_intensity     TEXT,                 -- Pan India / Regional / Single State
  pan_india               BOOLEAN DEFAULT FALSE,
  rural_presence          BOOLEAN DEFAULT FALSE,

  -- Contact
  website                 TEXT,
  phone                   TEXT,
  email                   TEXT,

  -- Funding
  recent_funding          TEXT,
  recent_funding_amount   NUMERIC,
  recent_funding_year     INTEGER,

  -- Data quality
  quality_score           NUMERIC,              -- 0.0 – 1.0 from guardrails
  data_hash               TEXT,                 -- dedup fingerprint
  data_source             TEXT,                 -- scraper+gemini / gemini_only
  extraction_status       TEXT DEFAULT 'pending',

  -- Admin approval workflow
  approval_status         TEXT DEFAULT 'pending',
  -- pending   = freshly scraped, awaiting admin review
  -- approved  = admin verified, visible to users
  -- rejected  = admin rejected (data quality / not a lender)
  -- needs_update = data stale, flagged for re-scrape
  admin_notes             TEXT,
  approved_by             TEXT,
  approved_at             TIMESTAMP,

  -- Scrape scheduling
  last_scraped_at         TIMESTAMP,
  next_scrape_at          TIMESTAMP,            -- scheduler sets this
  scrape_interval_days    INTEGER DEFAULT 30
);

-- ── Indexes ────────────────────────────────────────────────
CREATE INDEX idx_lenders_company_type    ON lenders(company_type);
CREATE INDEX idx_lenders_hq_state        ON lenders(hq_state);
CREATE INDEX idx_lenders_aum             ON lenders(aum_crores);
CREATE INDEX idx_lenders_established     ON lenders(established_year);
CREATE INDEX idx_lenders_approval        ON lenders(approval_status);
CREATE INDEX idx_lenders_next_scrape     ON lenders(next_scrape_at);
CREATE INDEX idx_lenders_products        ON lenders USING GIN(primary_loan_segments);
CREATE INDEX idx_lenders_states          ON lenders USING GIN(operating_states);


-- ============================================================
-- 2. POLICIES  (the missing layer — policy-level eligibility)
-- ============================================================
-- Each row = one loan product variant for one lender.
-- A lender with 3 products (MSME / Personal / Gold) = 3 policy rows.
-- Eligibility engine queries this table, not lenders.
-- ============================================================

CREATE TABLE policies (
  id                    BIGSERIAL PRIMARY KEY,
  lender_id             BIGINT NOT NULL REFERENCES lenders(id) ON DELETE CASCADE,
  created_at            TIMESTAMP DEFAULT NOW(),
  last_updated          TIMESTAMP DEFAULT NOW(),

  -- Product identity
  product_name          TEXT NOT NULL,
  -- e.g. "MSME Term Loan – Secured", "Personal Loan – Salaried"
  loan_type             TEXT NOT NULL,
  -- canonical: MSME Loan / Personal Loan / Home Loan / etc.

  -- ── Loan amount (Lakhs) ───────────────────────────────────
  loan_amount_min       NUMERIC,               -- e.g. 1 (₹1 Lakh)
  loan_amount_max       NUMERIC,               -- e.g. 500 (₹500 Lakhs = ₹5 Cr)

  -- ── Borrower credit profile ───────────────────────────────
  credit_score_min      INTEGER,               -- CIBIL 300–900; NULL = no requirement
  credit_score_max      INTEGER,               -- rarely set; NULL = no upper limit

  -- ── Borrower demographics ─────────────────────────────────
  min_age               INTEGER,               -- years
  max_age               INTEGER,
  employment_types      TEXT[],
  -- salaried / self-employed / business / agriculture / student / nri

  -- ── Income / financials ───────────────────────────────────
  min_monthly_income    NUMERIC,               -- ₹ thousands
  min_annual_turnover   NUMERIC,               -- ₹ Lakhs (for business loans)
  min_business_vintage  INTEGER,               -- years in business

  -- ── Loan terms ────────────────────────────────────────────
  interest_rate_min     NUMERIC,               -- % per annum
  interest_rate_max     NUMERIC,
  tenure_min            INTEGER,               -- months
  tenure_max            INTEGER,
  processing_fee        NUMERIC,               -- % of loan amount
  prepayment_allowed    BOOLEAN,

  -- ── Collateral ────────────────────────────────────────────
  collateral_required   BOOLEAN DEFAULT FALSE,
  collateral_types      TEXT[],
  -- property / gold / fdr / stocks / vehicle / none

  -- ── Geography ─────────────────────────────────────────────
  eligible_states       TEXT[],
  -- NULL = all states where lender operates

  -- ── Special conditions / notes ────────────────────────────
  eligibility_notes     TEXT,
  -- free-text: "GSTIN mandatory", "ITR required for 2 years", etc.

  -- ── Data quality ──────────────────────────────────────────
  completeness_score    NUMERIC,               -- 0.0 – 1.0 (how many fields filled)
  data_source           TEXT,                  -- scraped / gemini / manual
  source_url            TEXT,                  -- page where this was found

  -- ── Admin workflow ────────────────────────────────────────
  is_active             BOOLEAN DEFAULT TRUE,
  approval_status       TEXT DEFAULT 'pending',
  -- pending / approved / rejected / needs_update

  CONSTRAINT fk_lender FOREIGN KEY (lender_id) REFERENCES lenders(id)
);

-- ── Indexes ────────────────────────────────────────────────
CREATE INDEX idx_policies_lender        ON policies(lender_id);
CREATE INDEX idx_policies_loan_type     ON policies(loan_type);
CREATE INDEX idx_policies_active        ON policies(is_active, approval_status);
CREATE INDEX idx_policies_amount        ON policies(loan_amount_min, loan_amount_max);
CREATE INDEX idx_policies_credit_score  ON policies(credit_score_min);
CREATE INDEX idx_policies_states        ON policies USING GIN(eligible_states);
CREATE INDEX idx_policies_employment    ON policies USING GIN(employment_types);


-- ============================================================
-- 3. MATCHING REQUESTS  (analytics + async matching)
-- ============================================================

CREATE TABLE matching_requests (
  id                    BIGSERIAL PRIMARY KEY,
  created_at            TIMESTAMP DEFAULT NOW(),

  -- Borrower profile (what they submitted)
  loan_type             TEXT NOT NULL,
  loan_amount           NUMERIC NOT NULL,       -- Lakhs
  credit_score          INTEGER,
  employment_type       TEXT,
  monthly_income        NUMERIC,
  age                   INTEGER,
  state                 TEXT,
  business_vintage      INTEGER,

  -- Result
  matched_policy_ids    BIGINT[],
  match_count           INTEGER DEFAULT 0,
  user_id               UUID                    -- Supabase auth.users.id (nullable for anon)
);


-- ============================================================
-- 4. MATCHING ENGINE  (SQL function — server-side eligibility)
-- ============================================================
-- Called by: PostgREST /rpc/match_lenders
-- Returns:   ranked list of (lender, policy) pairs
--
-- Scoring (100 pts total):
--   30 — credit score eligibility
--   20 — loan amount in range
--   20 — state eligibility
--   15 — employment type match
--   15 — data completeness bonus
-- ============================================================

CREATE OR REPLACE FUNCTION match_lenders(
  p_loan_type         TEXT,
  p_loan_amount       NUMERIC,                  -- Lakhs
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
  loan_amount_min     NUMERIC,
  loan_amount_max     NUMERIC,
  interest_rate_min   NUMERIC,
  interest_rate_max   NUMERIC,
  credit_score_min    INTEGER,
  employment_types    TEXT[],
  tenure_min          INTEGER,
  tenure_max          INTEGER,
  collateral_required BOOLEAN,
  eligibility_notes   TEXT,
  match_score         INTEGER,
  match_reasons       TEXT[]
) AS $$
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
      p.loan_amount_min,
      p.loan_amount_max,
      p.interest_rate_min,
      p.interest_rate_max,
      p.credit_score_min,
      p.employment_types,
      p.tenure_min,
      p.tenure_max,
      p.collateral_required,
      p.eligibility_notes,

      -- ── Score calculation ─────────────────────────────────
      (
        -- 1. Credit score check (30 pts)
        CASE
          WHEN p_credit_score IS NULL         THEN 20   -- unknown = partial
          WHEN p.credit_score_min IS NULL     THEN 25   -- lender has no minimum = good
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
        -- 3. State eligibility (20 pts)
        CASE
          WHEN p_state IS NULL                THEN 10
          WHEN p.eligible_states IS NULL      THEN 15   -- not restricted
          WHEN p_state = ANY(p.eligible_states) THEN 20
          ELSE 0
        END
        +
        -- 4. Employment type (15 pts)
        CASE
          WHEN p_employment_type IS NULL       THEN 8
          WHEN p.employment_types IS NULL      THEN 10
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
        CASE WHEN p_loan_amount BETWEEN COALESCE(p.loan_amount_min,0)
                                    AND COALESCE(p.loan_amount_max,999999)
             THEN 'Loan amount in range' ELSE NULL END,
        CASE WHEN p_state IS NOT NULL
              AND (p.eligible_states IS NULL OR p_state = ANY(p.eligible_states))
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
      p.loan_type        = p_loan_type
      AND p.is_active    = TRUE
      AND p.approval_status = 'approved'
      AND l.approval_status = 'approved'

      -- Hard filters (eliminates ineligible, not scored)
      AND (p.loan_amount_min IS NULL OR p_loan_amount >= p.loan_amount_min)
      AND (p.loan_amount_max IS NULL OR p_loan_amount <= p.loan_amount_max)
      AND (p_age IS NULL OR p.min_age IS NULL OR p_age >= p.min_age)
      AND (p_age IS NULL OR p.max_age IS NULL OR p_age <= p.max_age)
      AND (p_monthly_income IS NULL OR p.min_monthly_income IS NULL
           OR p_monthly_income >= p.min_monthly_income)
      AND (p_business_vintage IS NULL OR p.min_business_vintage IS NULL
           OR p_business_vintage >= p.min_business_vintage)
  )
  SELECT
    s.lender_id, s.company_name, s.company_type, s.aum_crores, s.website,
    s.policy_id, s.product_name,
    s.loan_amount_min, s.loan_amount_max,
    s.interest_rate_min, s.interest_rate_max,
    s.credit_score_min, s.employment_types,
    s.tenure_min, s.tenure_max,
    s.collateral_required, s.eligibility_notes,
    s.match_score, s.match_reasons
  FROM scored s
  WHERE s.match_score > 0
  ORDER BY s.match_score DESC, s.interest_rate_min ASC NULLS LAST
  LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- ============================================================
-- 5. HELPER FUNCTIONS
-- ============================================================

-- Policy completeness score (called after INSERT/UPDATE on policies)
CREATE OR REPLACE FUNCTION compute_policy_completeness(p policies)
RETURNS NUMERIC AS $$
DECLARE
  score   NUMERIC := 0;
  total   INTEGER := 10;
BEGIN
  IF p.loan_amount_min IS NOT NULL THEN score := score + 1; END IF;
  IF p.loan_amount_max IS NOT NULL THEN score := score + 1; END IF;
  IF p.credit_score_min IS NOT NULL THEN score := score + 1; END IF;
  IF p.interest_rate_min IS NOT NULL THEN score := score + 1; END IF;
  IF p.interest_rate_max IS NOT NULL THEN score := score + 1; END IF;
  IF p.tenure_min IS NOT NULL THEN score := score + 1; END IF;
  IF p.tenure_max IS NOT NULL THEN score := score + 1; END IF;
  IF p.employment_types IS NOT NULL AND array_length(p.employment_types, 1) > 0
     THEN score := score + 1; END IF;
  IF p.eligibility_notes IS NOT NULL AND length(p.eligibility_notes) > 10
     THEN score := score + 1; END IF;
  IF p.eligible_states IS NOT NULL THEN score := score + 1; END IF;
  RETURN ROUND(score::NUMERIC / total, 2);
END;
$$ LANGUAGE plpgsql;

-- Auto-update completeness_score on INSERT/UPDATE
CREATE OR REPLACE FUNCTION trg_policy_completeness()
RETURNS TRIGGER AS $$
BEGIN
  NEW.completeness_score := compute_policy_completeness(NEW);
  NEW.last_updated       := NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_policy_completeness
  BEFORE INSERT OR UPDATE ON policies
  FOR EACH ROW EXECUTE FUNCTION trg_policy_completeness();

-- Auto-update lenders.last_updated
CREATE OR REPLACE FUNCTION trg_lender_updated()
RETURNS TRIGGER AS $$
BEGIN
  NEW.last_updated := NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_lender_updated
  BEFORE UPDATE ON lenders
  FOR EACH ROW EXECUTE FUNCTION trg_lender_updated();


-- ============================================================
-- 6. ROW LEVEL SECURITY
-- ============================================================

ALTER TABLE lenders          ENABLE ROW LEVEL SECURITY;
ALTER TABLE policies         ENABLE ROW LEVEL SECURITY;
ALTER TABLE matching_requests ENABLE ROW LEVEL SECURITY;

-- Public can only see approved lenders
CREATE POLICY "public_read_approved_lenders"
  ON lenders FOR SELECT TO anon
  USING (approval_status = 'approved');

-- Public can only see approved, active policies
CREATE POLICY "public_read_approved_policies"
  ON policies FOR SELECT TO anon
  USING (approval_status = 'approved' AND is_active = TRUE);

-- Authenticated users can read all (for admin panel)
CREATE POLICY "auth_read_all_lenders"
  ON lenders FOR SELECT TO authenticated
  USING (true);

CREATE POLICY "auth_read_all_policies"
  ON policies FOR SELECT TO authenticated
  USING (true);

-- Authenticated users can insert/update matching_requests
CREATE POLICY "auth_matching_requests"
  ON matching_requests FOR ALL TO authenticated
  USING (user_id = auth.uid());

-- Anon matching requests allowed (no user_id)
CREATE POLICY "anon_matching_requests"
  ON matching_requests FOR INSERT TO anon
  WITH CHECK (user_id IS NULL);


-- ============================================================
-- 7. INDEXES  (performance for matching engine)
-- ============================================================

-- Matching engine hot path
CREATE INDEX idx_policies_match_core ON policies(loan_type, is_active, approval_status)
  WHERE is_active = TRUE AND approval_status = 'approved';

CREATE INDEX idx_policies_credit     ON policies(credit_score_min) WHERE credit_score_min IS NOT NULL;
CREATE INDEX idx_policies_rate       ON policies(interest_rate_min) WHERE interest_rate_min IS NOT NULL;
CREATE INDEX idx_lenders_approved    ON lenders(id) WHERE approval_status = 'approved';
