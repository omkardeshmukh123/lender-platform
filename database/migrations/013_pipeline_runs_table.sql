-- Migration 013: pipeline_runs table
-- ============================================================
-- Stores one row per pipeline run (or per chunk for parallel DAGs).
-- Grafana queries this table for:
--   - Daily extraction health dashboard
--   - Gemini cost tracking (tokens × price)
--   - Success/failure rate trends
--   - Pipeline duration alerts
-- ============================================================

CREATE TABLE IF NOT EXISTS pipeline_runs (
  id                  BIGSERIAL PRIMARY KEY,
  started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at         TIMESTAMPTZ,

  -- Identity
  pipeline            TEXT        NOT NULL,  -- 'nbfc_extraction' | 'rbi_extraction' | 'policy_extraction' | 'scheduler'
  run_date            DATE        NOT NULL,
  dag_run_id          TEXT,                  -- Airflow run_id for traceability
  chunk_index         INTEGER     DEFAULT -1, -- -1 = not chunked

  -- Counters
  duration_seconds    NUMERIC(10,1),
  total_processed     INTEGER     DEFAULT 0,
  success_count       INTEGER     DEFAULT 0,
  failed_count        INTEGER     DEFAULT 0,
  skipped_count       INTEGER     DEFAULT 0,  -- hash-unchanged skips

  -- Gemini cost
  gemini_calls        INTEGER     DEFAULT 0,
  gemini_tokens_used  INTEGER     DEFAULT 0,
  estimated_cost_usd  NUMERIC(10,4) DEFAULT 0,

  -- Diagnostics
  error_sample        JSONB       DEFAULT '[]',   -- up to 5 error messages
  metadata            JSONB       DEFAULT '{}'    -- arbitrary per-run context
);

-- Indexes for Grafana time-series queries
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started_at
  ON pipeline_runs (started_at DESC);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_pipeline_date
  ON pipeline_runs (pipeline, run_date DESC);

-- Grafana view: daily cost roll-up
CREATE OR REPLACE VIEW v_pipeline_cost_daily AS
SELECT
  run_date,
  pipeline,
  COUNT(*)                                        AS run_count,
  SUM(total_processed)                            AS total_processed,
  SUM(success_count)                              AS success_count,
  SUM(failed_count)                               AS failed_count,
  SUM(skipped_count)                              AS skipped_count,
  SUM(gemini_calls)                               AS gemini_calls,
  SUM(gemini_tokens_used)                         AS tokens_used,
  ROUND(SUM(estimated_cost_usd)::NUMERIC, 4)      AS total_cost_usd,
  ROUND(AVG(duration_seconds)::NUMERIC, 1)        AS avg_duration_s,
  ROUND(
    100.0 * SUM(success_count)::NUMERIC /
    NULLIF(SUM(success_count) + SUM(failed_count), 0),
    1
  )                                               AS success_rate_pct
FROM pipeline_runs
GROUP BY run_date, pipeline
ORDER BY run_date DESC, pipeline;

-- Grafana view: rolling 30-day gemini spend
CREATE OR REPLACE VIEW v_gemini_spend_30d AS
SELECT
  SUM(estimated_cost_usd)                         AS total_cost_usd_30d,
  SUM(gemini_calls)                               AS total_calls_30d,
  SUM(gemini_tokens_used)                         AS total_tokens_30d,
  ROUND(AVG(estimated_cost_usd / NULLIF(gemini_calls,0))::NUMERIC, 6)
                                                  AS avg_cost_per_call
FROM pipeline_runs
WHERE started_at > NOW() - INTERVAL '30 days'
  AND gemini_calls > 0;

-- RLS: authenticated admins can read, pipeline inserts via service_role
ALTER TABLE pipeline_runs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_policies WHERE tablename = 'pipeline_runs' AND policyname = 'auth_read_pipeline_runs'
  ) THEN
    CREATE POLICY "auth_read_pipeline_runs"
      ON pipeline_runs FOR SELECT TO authenticated
      USING (true);
  END IF;
END $$;

-- Record migration
INSERT INTO schema_versions (version, name, checksum)
VALUES (13, 'pipeline_runs_table', md5('013_pipeline_runs_table'))
ON CONFLICT (version) DO NOTHING;
