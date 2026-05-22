-- Migration 047: Composite index for correlated subquery pattern in lender search
-- The search query now uses correlated subqueries per page row instead of full CTEs.
-- This index lets each subquery use an index-only scan on (lender_id, is_active, approval_status).

CREATE INDEX IF NOT EXISTS idx_policies_lender_active_approved
  ON policies (lender_id, is_active, approval_status)
  WHERE is_active = true AND approval_status = 'approved';
