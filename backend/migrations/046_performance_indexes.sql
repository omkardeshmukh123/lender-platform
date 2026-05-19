-- Migration 046: Performance indexes for lender filter queries
-- Fixes full table scans on approval_status and common filter combinations

-- Single-column index — every query filters approval_status='approved' first
CREATE INDEX IF NOT EXISTS idx_lenders_approval_status
  ON lenders (approval_status);

-- Compound: approval + company_type (most common filter combo)
CREATE INDEX IF NOT EXISTS idx_lenders_approval_company_type
  ON lenders (approval_status, company_type);

-- Compound: approval + pan_india + hq_state (state/coverage filter)
CREATE INDEX IF NOT EXISTS idx_lenders_approval_pan_india_state
  ON lenders (approval_status, pan_india, hq_state);

-- Compound: approval + aum_category (AUM band filter)
CREATE INDEX IF NOT EXISTS idx_lenders_approval_aum_category
  ON lenders (approval_status, aum_category);

-- policies(lender_id) — speeds up JOIN in lender detail/compare queries
CREATE INDEX IF NOT EXISTS idx_policies_lender_id
  ON policies (lender_id);
