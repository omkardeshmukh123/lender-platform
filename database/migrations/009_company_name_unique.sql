-- Migration 009: Unique constraint on lenders.company_name
-- Required for ON CONFLICT upsert in upload_lenders.py.
-- Safe: verified no duplicates exist before adding.
-- UP

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_constraint WHERE conname = 'uq_lenders_company_name'
    ) THEN
        ALTER TABLE lenders ADD CONSTRAINT uq_lenders_company_name UNIQUE (company_name);
    END IF;
END $$;

INSERT INTO schema_versions (version, name, checksum)
VALUES (9, 'company_name_unique', md5('009_company_name_unique'))
ON CONFLICT (version) DO NOTHING;
