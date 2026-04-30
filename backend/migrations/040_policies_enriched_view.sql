-- 040_policies_enriched_view.sql
-- Materialized view joining policies with best-ranked enrichment values per field.
-- p.* retains legacy financial columns (interest_rate_min, loan_amount_min, etc.)
-- for backwards compatibility; the _enriched suffix columns are the authoritative
-- source-ranked values. Phase 2 (Task 10+) will migrate the API to use _enriched.

CREATE MATERIALIZED VIEW IF NOT EXISTS policies_enriched AS
SELECT
    p.*,
    ir.value_min   AS interest_rate_min_enriched,
    ir.value_max   AS interest_rate_max_enriched,
    ir.source      AS interest_rate_source,
    ir.confidence  AS interest_rate_confidence,
    la.value_min   AS loan_amount_min_enriched,
    la.value_max   AS loan_amount_max_enriched,
    la.source      AS loan_amount_source,
    t.value_min::INTEGER  AS tenure_min_enriched,
    t.value_max::INTEGER  AS tenure_max_enriched,
    cs.value_min::INTEGER AS credit_score_min_enriched,
    cs.value_max::INTEGER AS credit_score_max_enriched,
    pf.value_min   AS processing_fee_enriched,
    pf.value_max   AS processing_fee_max_enriched
FROM policies p
LEFT JOIN LATERAL (
    SELECT value_min, value_max, source, confidence
    FROM policy_enrichments
    WHERE policy_id = p.id AND field = 'interest_rate' AND validated = true
    ORDER BY source_rank DESC LIMIT 1
) ir ON true
LEFT JOIN LATERAL (
    SELECT value_min, value_max, source
    FROM policy_enrichments
    WHERE policy_id = p.id AND field = 'loan_amount' AND validated = true
    ORDER BY source_rank DESC LIMIT 1
) la ON true
LEFT JOIN LATERAL (
    SELECT value_min, value_max
    FROM policy_enrichments
    WHERE policy_id = p.id AND field = 'tenure' AND validated = true
    ORDER BY source_rank DESC LIMIT 1
) t ON true
LEFT JOIN LATERAL (
    SELECT value_min, value_max
    FROM policy_enrichments
    WHERE policy_id = p.id AND field = 'credit_score' AND validated = true
    ORDER BY source_rank DESC LIMIT 1
) cs ON true
LEFT JOIN LATERAL (
    SELECT value_min, value_max
    FROM policy_enrichments
    WHERE policy_id = p.id AND field = 'processing_fee' AND validated = true
    ORDER BY source_rank DESC LIMIT 1
) pf ON true
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_policies_enriched_id ON policies_enriched (id);
CREATE INDEX IF NOT EXISTS idx_policies_enriched_lender ON policies_enriched (lender_id);
CREATE INDEX IF NOT EXISTS idx_policies_enriched_loan_type ON policies_enriched (loan_type);
