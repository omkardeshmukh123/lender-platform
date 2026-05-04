-- Migration 043: leads table
-- Captures borrower intent when they click "Website" on a LenderCard.
-- Minimal: phone (optional), loan_type, lender_name.

INSERT INTO schema_versions (version, name) VALUES (43, '043_leads');

CREATE TABLE IF NOT EXISTS leads (
    id           SERIAL PRIMARY KEY,
    lender_name  VARCHAR(300) NOT NULL,
    loan_type    VARCHAR(100),
    phone        VARCHAR(20),
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_leads_lender_name ON leads (lender_name);
CREATE INDEX IF NOT EXISTS idx_leads_created_at  ON leads (created_at DESC);

ALTER TABLE leads ENABLE ROW LEVEL SECURITY;

-- Only service role can read; no public SELECT needed
CREATE POLICY leads_insert ON leads FOR INSERT WITH CHECK (true);

COMMENT ON TABLE leads IS
    'Borrower intent captured from LenderCard website-click modal.';
