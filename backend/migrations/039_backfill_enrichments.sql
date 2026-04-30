-- 039_backfill_enrichments.sql
-- Backfill existing policy financial fields into the policy_enrichments vault.
-- policies.id is bigint and policy_enrichments.policy_id is bigint — no cast needed.

INSERT INTO policy_enrichments (policy_id, field, value_min, value_max, source, source_rank, validated, confidence, raw_value)
SELECT
    id,
    'interest_rate',
    interest_rate_min,
    interest_rate_max,
    'legacy', 1, true, 0.60,
    jsonb_build_object('migrated_from', 'policies.interest_rate_min/max', 'migrated_at', now())
FROM policies
WHERE interest_rate_min IS NOT NULL OR interest_rate_max IS NOT NULL
ON CONFLICT (policy_id, field, source) DO NOTHING;

INSERT INTO policy_enrichments (policy_id, field, value_min, value_max, source, source_rank, validated, confidence, raw_value)
SELECT
    id,
    'loan_amount',
    loan_amount_min,
    loan_amount_max,
    'legacy', 1, true, 0.60,
    jsonb_build_object('migrated_from', 'policies.loan_amount_min/max', 'migrated_at', now())
FROM policies
WHERE loan_amount_min IS NOT NULL OR loan_amount_max IS NOT NULL
ON CONFLICT (policy_id, field, source) DO NOTHING;

INSERT INTO policy_enrichments (policy_id, field, value_min, value_max, source, source_rank, validated, confidence, raw_value)
SELECT
    id,
    'tenure',
    tenure_min::NUMERIC,
    tenure_max::NUMERIC,
    'legacy', 1, true, 0.60,
    jsonb_build_object('migrated_from', 'policies.tenure_min/max', 'migrated_at', now())
FROM policies
WHERE tenure_min IS NOT NULL OR tenure_max IS NOT NULL
ON CONFLICT (policy_id, field, source) DO NOTHING;

INSERT INTO policy_enrichments (policy_id, field, value_min, value_max, source, source_rank, validated, confidence, raw_value)
SELECT
    id,
    'credit_score',
    credit_score_min::NUMERIC,
    credit_score_max::NUMERIC,
    'legacy', 1, true, 0.60,
    jsonb_build_object('migrated_from', 'policies.credit_score_min/max', 'migrated_at', now())
FROM policies
WHERE credit_score_min IS NOT NULL OR credit_score_max IS NOT NULL
ON CONFLICT (policy_id, field, source) DO NOTHING;

INSERT INTO policy_enrichments (policy_id, field, value_min, value_max, source, source_rank, validated, confidence, raw_value)
SELECT
    id,
    'processing_fee',
    processing_fee,
    processing_fee,
    'legacy', 1, true, 0.60,
    jsonb_build_object('migrated_from', 'policies.processing_fee', 'migrated_at', now())
FROM policies
WHERE processing_fee IS NOT NULL
ON CONFLICT (policy_id, field, source) DO NOTHING;
