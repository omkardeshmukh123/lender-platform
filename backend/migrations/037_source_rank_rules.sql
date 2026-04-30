-- 037_source_rank_rules.sql
CREATE TABLE IF NOT EXISTS source_rank_rules (
    source       TEXT PRIMARY KEY,
    rank         SMALLINT NOT NULL,
    display_name TEXT
);

INSERT INTO source_rank_rules (source, rank, display_name) VALUES
    ('bse_xbrl',    4, 'BSE/NSE XBRL Filing'),
    ('fpc_pdf',     3, 'RBI Fair Practice Code PDF'),
    ('bankbazaar',  2, 'BankBazaar/PaisaBazaar'),
    ('legacy',      1, 'Legacy policy columns (pre-migration)')
ON CONFLICT (source) DO NOTHING;
