-- Migration 006: Performance indexes
-- Adds composite and partial indexes that make production queries fast.
-- Safe to run on existing databases — all use CREATE INDEX IF NOT EXISTS.
-- UP

-- ── 1. Composite partial index for match_lenders() hot path ────────────────
-- The match function always filters: loan_type = $1 AND is_active AND approved
-- This partial index covers exactly that predicate, eliminating a separate filter step.
CREATE INDEX IF NOT EXISTS idx_policies_match_lenders
  ON policies (loan_type, is_active, approval_status)
  WHERE is_active = TRUE AND approval_status = 'approved';

-- ── 2. Approved lenders sorted by AUM ──────────────────────────────────────
-- The default dashboard query is: WHERE approved ORDER BY aum_crores DESC
-- Without this, Postgres filters then sorts — two steps.
-- With this partial index, it's a single index scan in order.
CREATE INDEX IF NOT EXISTS idx_lenders_approved_aum
  ON lenders (aum_crores DESC NULLS LAST)
  WHERE approval_status = 'approved';

-- ── 3. State + AUM for geographic filtering ────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_lenders_state_approved
  ON lenders (hq_state, aum_crores DESC NULLS LAST)
  WHERE approval_status = 'approved';

-- ── 4. Company type + AUM for category filtering ───────────────────────────
CREATE INDEX IF NOT EXISTS idx_lenders_type_approved
  ON lenders (company_type, aum_crores DESC NULLS LAST)
  WHERE approval_status = 'approved';

-- ── 5. Stale lender detection for scheduler ────────────────────────────────
-- scheduler.py queries: WHERE next_scrape_at < NOW() AND approval_status = 'approved'
CREATE INDEX IF NOT EXISTS idx_lenders_scrape_due
  ON lenders (next_scrape_at)
  WHERE approval_status = 'approved' AND next_scrape_at IS NOT NULL;

-- ── 6. Policy amount range lookup ──────────────────────────────────────────
-- Composite for: loan_amount_min <= $x AND loan_amount_max >= $x
CREATE INDEX IF NOT EXISTS idx_policies_amount_range
  ON policies (loan_amount_min, loan_amount_max)
  WHERE is_active = TRUE AND approval_status = 'approved';

-- Record migration
INSERT INTO schema_versions (version, name, checksum)
VALUES (6, 'perf_indexes', md5('006_perf_indexes'))
ON CONFLICT (version) DO NOTHING;
