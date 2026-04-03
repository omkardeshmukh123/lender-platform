-- migration 026: replace partitioned matching_requests with a single plain table
-- ===============================================================================
-- Reason: monthly partitions add complexity with no benefit at current scale.
-- No data exists in these tables so the drop is safe.

-- Drop all partition children first, then the parent
DROP TABLE IF EXISTS matching_requests_2025_12 CASCADE;
DROP TABLE IF EXISTS matching_requests_2026_01 CASCADE;
DROP TABLE IF EXISTS matching_requests_2026_02 CASCADE;
DROP TABLE IF EXISTS matching_requests_2026_03 CASCADE;
DROP TABLE IF EXISTS matching_requests_2026_04 CASCADE;
DROP TABLE IF EXISTS matching_requests_2026_05 CASCADE;
DROP TABLE IF EXISTS matching_requests_2026_06 CASCADE;
DROP TABLE IF EXISTS matching_requests_2026_07 CASCADE;
DROP TABLE IF EXISTS matching_requests_2026_08 CASCADE;
DROP TABLE IF EXISTS matching_requests_2026_09 CASCADE;
DROP TABLE IF EXISTS matching_requests_2026_10 CASCADE;
DROP TABLE IF EXISTS matching_requests_2026_11 CASCADE;
DROP TABLE IF EXISTS matching_requests_default CASCADE;
DROP TABLE IF EXISTS matching_requests CASCADE;

CREATE TABLE matching_requests (
    id                  BIGSERIAL       PRIMARY KEY,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    user_id             UUID,
    loan_type           TEXT,
    loan_amount         NUMERIC,
    credit_score        INTEGER,
    employment_type     TEXT,
    monthly_income      NUMERIC,
    age                 INTEGER,
    state               TEXT,
    business_vintage    INTEGER,
    matched_policy_ids  BIGINT[]        NOT NULL DEFAULT '{}',
    match_count         INTEGER         NOT NULL DEFAULT 0,
    schema_version      INTEGER         NOT NULL DEFAULT 2
);

CREATE INDEX idx_matching_requests_created_at ON matching_requests (created_at DESC);
CREATE INDEX idx_matching_requests_user_id    ON matching_requests (user_id) WHERE user_id IS NOT NULL;
