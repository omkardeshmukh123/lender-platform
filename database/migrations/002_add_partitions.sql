-- Migration 002: Convert matching_requests to date-partitioned table
-- Partitioned by created_at (RANGE, monthly) — append-only analytics table.
-- UP

DO $$
DECLARE
    i     INT;
    sd    DATE;
    ed    DATE;
    pname TEXT;
    legacy_exists BOOLEAN;
BEGIN
    -- Check if original (non-partitioned) matching_requests exists
    legacy_exists := EXISTS (
        SELECT FROM information_schema.tables
        WHERE table_name = 'matching_requests'
          AND table_type = 'BASE TABLE'
    );

    -- Step 1: Rename existing table to legacy (if it exists and is not already partitioned)
    IF legacy_exists THEN
        ALTER TABLE matching_requests RENAME TO matching_requests_legacy;
    END IF;

    -- Step 2: Create partitioned parent table
    EXECUTE $t$
        CREATE TABLE IF NOT EXISTS matching_requests (
            id                  BIGSERIAL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            user_id             UUID,
            loan_type           TEXT,
            loan_amount         NUMERIC(15,2),
            credit_score        INTEGER,
            employment_type     TEXT,
            monthly_income      NUMERIC(15,2),
            age                 INTEGER,
            state               TEXT,
            business_vintage    INTEGER,
            matched_policy_ids  INTEGER[]   DEFAULT '{}',
            match_count         INTEGER     DEFAULT 0,
            schema_version      INTEGER     DEFAULT 2,
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
    $t$;

    -- Step 3: Create monthly partitions (3 months back → 8 months ahead)
    FOR i IN -3..8 LOOP
        sd    := DATE_TRUNC('month', NOW() + (i || ' months')::INTERVAL)::DATE;
        ed    := (sd + INTERVAL '1 month')::DATE;
        pname := 'matching_requests_' || TO_CHAR(sd, 'YYYY_MM');
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I PARTITION OF matching_requests '
            'FOR VALUES FROM (%L) TO (%L)',
            pname, sd, ed
        );
    END LOOP;

    -- Step 4: Migrate existing rows from legacy table (if it existed)
    IF legacy_exists THEN
        INSERT INTO matching_requests
            SELECT *, 1 AS schema_version FROM matching_requests_legacy;
    END IF;

END $$;

-- Step 5: Indexes (safe to run even if table was just created empty)
CREATE INDEX IF NOT EXISTS idx_mr_loan_type ON matching_requests (loan_type);
CREATE INDEX IF NOT EXISTS idx_mr_credit    ON matching_requests (credit_score);
CREATE INDEX IF NOT EXISTS idx_mr_state     ON matching_requests (state);

-- Step 6: Record migration
INSERT INTO schema_versions (version, name, checksum)
VALUES (2, 'add_date_partitions', md5('002_add_partitions'))
ON CONFLICT (version) DO NOTHING;
