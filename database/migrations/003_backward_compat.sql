-- Migration 003: Backward-compatibility guarantees
--
-- RULES enforced by this migration:
--   1. All new columns have DEFAULT values — old pipelines continue inserting
--   2. Renamed columns get a view alias — old SELECT queries still work
--   3. Dropped columns become nullable with DEFAULT NULL instead of being removed
--   4. Constraint changes are additive only
-- UP

DO $$
BEGIN
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'lenders') THEN
        ALTER TABLE lenders ADD COLUMN IF NOT EXISTS branch_count    INTEGER  DEFAULT NULL;
        ALTER TABLE lenders ADD COLUMN IF NOT EXISTS rural_presence  BOOLEAN  DEFAULT FALSE;
        ALTER TABLE lenders ADD COLUMN IF NOT EXISTS data_source     TEXT     DEFAULT 'gemini_only';
    END IF;
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'policies') THEN
        ALTER TABLE policies ADD COLUMN IF NOT EXISTS prepayment_allowed BOOLEAN DEFAULT TRUE;
        ALTER TABLE policies ADD COLUMN IF NOT EXISTS source_url         TEXT    DEFAULT NULL;
    END IF;
END $$;

-- View: lenders_compat — only create if lenders table exists
DO $$
BEGIN
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'lenders') THEN
        EXECUTE $v$
            CREATE OR REPLACE VIEW lenders_compat AS
            SELECT *, primary_loan_segments AS loan_segments,
                      rbi_registration_number AS rbi_reg
            FROM lenders
        $v$;
    END IF;
END $$;

-- Function: safe_add_column — idempotent column addition for pipelines
CREATE OR REPLACE FUNCTION safe_add_column(
    tbl   TEXT,
    col   TEXT,
    dtype TEXT,
    dflt  TEXT DEFAULT NULL
) RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = tbl AND column_name = col
    ) THEN
        IF dflt IS NOT NULL THEN
            EXECUTE format('ALTER TABLE %I ADD COLUMN %I %s DEFAULT %s', tbl, col, dtype, dflt);
        ELSE
            EXECUTE format('ALTER TABLE %I ADD COLUMN %I %s', tbl, col, dtype);
        END IF;
    END IF;
END $$;

-- Record migration
INSERT INTO schema_versions (version, name, checksum)
VALUES (3, 'backward_compat', md5('003_backward_compat'))
ON CONFLICT (version) DO NOTHING;
