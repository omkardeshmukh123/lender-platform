-- migration 024: bank-grade validation columns + unique constraint
-- =================================================================
-- Problem:
--   RBI banks extraction v5 adds parent_bank (merger lineage), risk_flags
--   (AML signals), schema_version, validation_version, and enforces a
--   DB-level unique constraint so in-memory dedup cannot be bypassed by
--   parallel pipeline runs or CSV re-imports.
--
-- Unique constraint rationale:
--   In-memory dedup (hashing) is best-effort and session-scoped.
--   A DB-level constraint is the last line of defence: it makes duplicates
--   physically impossible to INSERT regardless of how many pipeline instances
--   run concurrently.  The constraint keys on (normalized company name,
--   rbi_registration_number) — the two identifiers that together establish
--   legal identity.

-- ── 1. New columns ────────────────────────────────────────────────────────────

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS parent_bank TEXT NOT NULL DEFAULT '';
    -- Populated for merged/discontinued entities; references the absorbing bank.
    -- Enables lineage queries: "which banks were merged into Bank of Baroda?"

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS risk_flags JSONB NOT NULL DEFAULT '[]';
    -- Array of AML/compliance signal tags emitted during extraction:
    --   no_website | no_contact | weak_rbi_validation | rbi_restricted |
    --   email_domain_mismatch
    -- Used by admin review queues and downstream risk scoring.

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS schema_version TEXT NOT NULL DEFAULT 'bank_v2';

ALTER TABLE lenders
    ADD COLUMN IF NOT EXISTS validation_version TEXT NOT NULL DEFAULT 'v5';

-- ── 2. Unique constraint — DB-level deduplication ─────────────────────────────
-- Normalisation: strip spaces/case so "State Bank Of India" == "state bank of india".
-- We use a functional index on lower(trim(company_name)) paired with
-- rbi_registration_number.  NULL reg numbers are excluded (no constraint on
-- banks whose reg is unknown — they are caught by pipeline gates instead).

CREATE UNIQUE INDEX IF NOT EXISTS idx_lenders_unique_identity
    ON lenders (lower(trim(company_name)), rbi_registration_number)
    WHERE rbi_registration_number IS NOT NULL
      AND rbi_registration_number <> '';

-- ── 3. Indexes for risk review ─────────────────────────────────────────────────

-- Admin queue: any record with at least one risk flag
CREATE INDEX IF NOT EXISTS idx_lenders_risk_flags
    ON lenders USING gin (risk_flags)
    WHERE risk_flags <> '[]'::jsonb;

-- Lineage query: find all entities merged into a specific bank
CREATE INDEX IF NOT EXISTS idx_lenders_parent_bank
    ON lenders (parent_bank)
    WHERE parent_bank <> '';

-- ── 4. Record migration ────────────────────────────────────────────────────────
