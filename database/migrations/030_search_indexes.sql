-- migration 030: composite indexes for common search/filter patterns
-- Covers the WHERE clauses used by GET /v1/lenders/search

-- Most queries filter approval_status first
CREATE INDEX IF NOT EXISTS idx_lenders_approved_aum
  ON lenders(approval_status, aum_crores DESC NULLS LAST)
  WHERE approval_status = 'approved';

CREATE INDEX IF NOT EXISTS idx_lenders_approved_quality
  ON lenders(approval_status, quality_score DESC NULLS LAST)
  WHERE approval_status = 'approved';

CREATE INDEX IF NOT EXISTS idx_lenders_company_type
  ON lenders(company_type)
  WHERE approval_status = 'approved';

CREATE INDEX IF NOT EXISTS idx_lenders_aum_category
  ON lenders(aum_category)
  WHERE approval_status = 'approved';

CREATE INDEX IF NOT EXISTS idx_lenders_hq_state
  ON lenders(hq_state)
  WHERE approval_status = 'approved';

CREATE INDEX IF NOT EXISTS idx_lenders_pan_india
  ON lenders(pan_india)
  WHERE approval_status = 'approved';

CREATE INDEX IF NOT EXISTS idx_lenders_is_listed
  ON lenders(is_listed)
  WHERE approval_status = 'approved';

CREATE INDEX IF NOT EXISTS idx_lenders_established_year
  ON lenders(established_year)
  WHERE approval_status = 'approved';

-- GIN indexes for array containment queries (loan_type, operating_states)
CREATE INDEX IF NOT EXISTS idx_lenders_loan_segments_gin
  ON lenders USING GIN(primary_loan_segments);

CREATE INDEX IF NOT EXISTS idx_lenders_operating_states_gin
  ON lenders USING GIN(operating_states);

-- Full-text search on company_name for ILIKE queries
CREATE INDEX IF NOT EXISTS idx_lenders_name_trgm
  ON lenders USING GIN(company_name gin_trgm_ops);

-- Enable pg_trgm extension if not already enabled
CREATE EXTENSION IF NOT EXISTS pg_trgm;
