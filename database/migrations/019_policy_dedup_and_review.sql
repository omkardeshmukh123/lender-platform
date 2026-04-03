-- migration 019: policy deduplication key + review workflow columns
-- ==================================================================
-- Problems addressed:
--   1. Cross-run duplicates: Gemini names same product differently across runs
--      ("MSME Loan" vs "MSME Term Loan" vs "MSME Loan - Unsecured") causing
--      fragmented catalog entries per lender.  Fix: add product_name_normalized
--      and key the ON CONFLICT on the normalized form.
--
--   2. No admin review prioritisation: all policies land in 'pending' with
--      no signal about which need urgent attention vs routine approval.
--      Fix: review_priority (high/medium/low) + anomaly_flags array.

-- ── 1. Normalised product name for deduplication ───────────────────────────

ALTER TABLE policies
    ADD COLUMN IF NOT EXISTS product_name_normalized TEXT NOT NULL DEFAULT '';

-- Backfill existing rows: lower, collapse hyphens + whitespace
UPDATE policies
SET product_name_normalized = regexp_replace(
        lower(regexp_replace(product_name, '-+', ' ', 'g')),
        '\s+', ' ', 'g'
    )
WHERE product_name_normalized = '';

-- Auto-maintain via trigger so future INSERT/UPDATE stays consistent
CREATE OR REPLACE FUNCTION trg_normalise_product_name()
RETURNS TRIGGER AS $$
BEGIN
    NEW.product_name_normalized := regexp_replace(
        lower(regexp_replace(NEW.product_name, '-+', ' ', 'g')),
        '\s+', ' ', 'g'
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_set_product_name_normalized ON policies;
CREATE TRIGGER trg_set_product_name_normalized
BEFORE INSERT OR UPDATE OF product_name ON policies
FOR EACH ROW EXECUTE FUNCTION trg_normalise_product_name();

-- New canonical unique constraint: resolves cross-run naming variance
CREATE UNIQUE INDEX IF NOT EXISTS uniq_policies_normalized
    ON policies (lender_id, product_name_normalized, loan_type);

-- ── 2. Review priority and anomaly flags ───────────────────────────────────

ALTER TABLE policies
    ADD COLUMN IF NOT EXISTS review_priority TEXT    NOT NULL DEFAULT 'low',
    ADD COLUMN IF NOT EXISTS anomaly_flags   TEXT[]  NOT NULL DEFAULT '{}';

ALTER TABLE policies
    ADD CONSTRAINT chk_review_priority
    CHECK (review_priority IN ('low', 'medium', 'high'));

-- Index for admin queue: high-priority unverified policies first
CREATE INDEX IF NOT EXISTS idx_policies_review_queue
    ON policies (review_priority, is_verified, approval_status)
    WHERE approval_status = 'pending';

-- ── 3. Record migration ────────────────────────────────────────────────────
