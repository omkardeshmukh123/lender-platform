-- migration 014: extraction_failures dead-letter table
-- ========================================================
-- Purpose:
--   Every time a lender extraction fails (scrape error, Gemini error,
--   guardrails rejection, DB upsert error), a row is written here.
--   This makes data gaps visible and auditable — instead of silent skips.
--
-- Benefits:
--   - Admin can see which lenders are consistently failing
--   - Retry logic can target only failed lenders (not all 1000+)
--   - Grafana can alert when failure_rate > threshold
--   - Post-incident: find exactly which lenders lost data and when
--
-- Used by:
--   airflow/dags/nbfc_extraction_dag.py (merge_and_upload task)
--   backend/scheduler.py (re_extract_lender)

CREATE TABLE IF NOT EXISTS extraction_failures (
    id              BIGSERIAL   PRIMARY KEY,

    -- Which lender failed
    lender_name     TEXT        NOT NULL,
    source_url      TEXT,
    lender_id       BIGINT      REFERENCES lenders(id) ON DELETE SET NULL,

    -- What went wrong
    failure_stage   TEXT        NOT NULL CHECK (failure_stage IN (
                        'scrape', 'gemini', 'guardrails', 'db_upsert', 'validation', 'unknown'
                    )),
    failure_reason  TEXT        NOT NULL,
    error_detail    TEXT,           -- full exception message / stack trace excerpt

    -- Context for retry decisions
    raw_response    JSONB,          -- Gemini or scraper raw output (if available)
    guardrail_issues JSONB,         -- guardrails issue list (if stage = 'guardrails')

    -- Pipeline context
    dag_run_id      TEXT,
    chunk_index     INT,
    run_date        DATE        NOT NULL DEFAULT CURRENT_DATE,

    -- Retry tracking
    retry_count     INT         NOT NULL DEFAULT 0,
    last_retried_at TIMESTAMPTZ,
    resolved        BOOLEAN     NOT NULL DEFAULT FALSE,
    resolved_at     TIMESTAMPTZ,
    resolution_note TEXT,

    failed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Lookup by lender name (most common query: "why is NBFC X not appearing?")
CREATE INDEX IF NOT EXISTS idx_extraction_failures_name
    ON extraction_failures (lender_name, failed_at DESC);

-- Lookup unresolved failures for retry job
CREATE INDEX IF NOT EXISTS idx_extraction_failures_unresolved
    ON extraction_failures (resolved, failed_at DESC)
    WHERE resolved = FALSE;

-- Lookup by pipeline run (post-run diagnostics)
CREATE INDEX IF NOT EXISTS idx_extraction_failures_run
    ON extraction_failures (run_date, dag_run_id);

-- RLS: only authenticated users (admins) can read failures
ALTER TABLE extraction_failures ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Authenticated users can read extraction_failures"
    ON extraction_failures FOR SELECT
    TO authenticated
    USING (true);

-- Service role writes (Airflow uses service role key)
CREATE POLICY "Service role can insert extraction_failures"
    ON extraction_failures FOR INSERT
    TO service_role
    WITH CHECK (true);

CREATE POLICY "Service role can update extraction_failures"
    ON extraction_failures FOR UPDATE
    TO service_role
    USING (true);

-- Grafana view: failure summary by stage and date (for alerts)
CREATE OR REPLACE VIEW v_extraction_failure_summary AS
SELECT
    run_date,
    failure_stage,
    COUNT(*)                                        AS failure_count,
    COUNT(*) FILTER (WHERE resolved = TRUE)         AS resolved_count,
    COUNT(*) FILTER (WHERE retry_count > 0)         AS retried_count,
    ROUND(AVG(retry_count), 1)                      AS avg_retries,
    MIN(failed_at)                                  AS first_failure,
    MAX(failed_at)                                  AS last_failure
FROM extraction_failures
GROUP BY run_date, failure_stage
ORDER BY run_date DESC, failure_count DESC;
