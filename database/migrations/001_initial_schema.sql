-- Migration 001: Initial schema (lenders + policies + matching_requests)
-- This mirrors schema_v2.sql — the baseline all future migrations build on.
-- UP

-- Schema version tracking (created by migration runner before first migration)
CREATE TABLE IF NOT EXISTS schema_versions (
    version     INTEGER PRIMARY KEY,
    name        TEXT        NOT NULL,
    checksum    TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Extraction schema version tag on lenders/policies (only if tables already exist)
DO $$
BEGIN
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'lenders') THEN
        ALTER TABLE lenders ADD COLUMN IF NOT EXISTS schema_version INTEGER DEFAULT 1;
    END IF;
    IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'policies') THEN
        ALTER TABLE policies ADD COLUMN IF NOT EXISTS schema_version INTEGER DEFAULT 1;
    END IF;
END $$;

-- Record this migration
INSERT INTO schema_versions (version, name, checksum)
VALUES (1, 'initial_schema', md5('001_initial_schema'))
ON CONFLICT (version) DO NOTHING;
