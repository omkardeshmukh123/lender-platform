-- 038_policy_enrichments.sql
CREATE TABLE IF NOT EXISTS policy_enrichments (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_id        BIGINT      NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    field            TEXT        NOT NULL
                                 CHECK (field IN ('interest_rate','loan_amount','tenure','credit_score','processing_fee')),
    value_min        NUMERIC,
    value_max        NUMERIC,
    source           TEXT        NOT NULL REFERENCES source_rank_rules(source),
    source_rank      SMALLINT    NOT NULL,
    validated        BOOLEAN     NOT NULL DEFAULT false,
    rejection_reason TEXT,
    raw_value        JSONB,
    confidence       NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (policy_id, field, source)
);

CREATE INDEX IF NOT EXISTS idx_pe_policy_field
    ON policy_enrichments (policy_id, field);

CREATE INDEX IF NOT EXISTS idx_pe_validated
    ON policy_enrichments (policy_id, field, source_rank DESC)
    WHERE validated = true;
