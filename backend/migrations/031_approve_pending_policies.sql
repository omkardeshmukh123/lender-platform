-- Migration 031: Backfill machine-scraped policies to approved status
-- Root cause: enrich_policies_db.py never set approval_status, so rows defaulted
-- to 'pending' and were hidden by the API filter (approval_status = 'approved').

-- 1. Backfill all pending/null policies to approved (they passed scraper guardrails)
UPDATE policies
SET approval_status = 'approved'
WHERE approval_status IS NULL OR approval_status = 'pending';

-- 2. Change DB default so future bare inserts are approved automatically
ALTER TABLE policies
  ALTER COLUMN approval_status SET DEFAULT 'approved';
