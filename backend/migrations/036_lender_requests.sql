-- Migration 036: Lender enrichment request queue
CREATE TABLE IF NOT EXISTS lender_requests (
  id            SERIAL PRIMARY KEY,
  company_name  TEXT NOT NULL,
  cin           TEXT,
  requested_by  TEXT,
  notes         TEXT,
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'in_progress', 'done', 'rejected'))
);

CREATE INDEX IF NOT EXISTS idx_lender_requests_status
  ON lender_requests (status, created_at DESC);
