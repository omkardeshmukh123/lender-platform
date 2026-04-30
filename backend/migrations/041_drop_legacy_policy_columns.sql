-- 041_drop_legacy_policy_columns.sql
-- DO NOT RUN until 2-week soak period after policies_enriched view is in production.
-- Removes financial columns from policies that are now served by policy_enrichments vault.

ALTER TABLE policies
    DROP COLUMN IF EXISTS interest_rate_min,
    DROP COLUMN IF EXISTS interest_rate_max,
    DROP COLUMN IF EXISTS loan_amount_min,
    DROP COLUMN IF EXISTS loan_amount_max,
    DROP COLUMN IF EXISTS tenure_min,
    DROP COLUMN IF EXISTS tenure_max,
    DROP COLUMN IF EXISTS credit_score_min,
    DROP COLUMN IF EXISTS credit_score_max,
    DROP COLUMN IF EXISTS processing_fee;

REFRESH MATERIALIZED VIEW CONCURRENTLY policies_enriched;
