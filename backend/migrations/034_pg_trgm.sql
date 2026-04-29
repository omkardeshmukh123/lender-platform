-- Migration 034: Enable pg_trgm for fuzzy name search
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_lenders_name_trgm
  ON lenders USING GiST (company_name gist_trgm_ops);
