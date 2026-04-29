-- Migration 033: Add admin workflow columns to policies table
-- Required by POST /admin/policies/{id}/approve and /reject endpoints

ALTER TABLE policies
  ADD COLUMN IF NOT EXISTS is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS approved_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS approved_by   TEXT,
  ADD COLUMN IF NOT EXISTS admin_notes   TEXT;

-- Backfill: mark already-approved policies as active
UPDATE policies
SET is_active = TRUE
WHERE approval_status = 'approved' AND is_active IS NOT TRUE;

UPDATE policies
SET is_active = FALSE
WHERE approval_status = 'rejected';
