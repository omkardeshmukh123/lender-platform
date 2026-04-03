-- Migration 011: Indexes, Materialized View, Auto-Partition Maintenance
-- ============================================================
-- Problems fixed:
--   1. No trigram (pg_trgm) index on company_name — ILIKE is a full table scan
--   2. No index on data_hash — duplicate detection is O(n)
--   3. No composite index on matching_requests(user_id, created_at) — per-user
--      history queries scan the whole partition
--   4. No materialized view for dashboard summary stats — every page load hits
--      COUNT(*) aggregates on the full lenders/policies tables
--   5. No auto-partition function — matching_requests monthly partitions were
--      pre-created for ~8 months ahead (migration 002). When those expire,
--      new rows silently fall into the _default partition and lose partition
--      pruning benefits. This migration adds a function + scheduled call.
-- ============================================================


-- ── 1. pg_trgm extension (required for ILIKE / similarity indexes) ────────────
-- Safe: CREATE EXTENSION IF NOT EXISTS never fails on re-run.
CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- ── 2. Trigram index on lenders.company_name ──────────────────────────────────
-- Accelerates ILIKE '%query%' searches used by /lenders/search?q=
-- Without this: full table scan for every keystroke in the search box.
-- With this: GIN trgm index prunes to <10% of rows typically.

CREATE INDEX IF NOT EXISTS idx_lenders_name_trgm
  ON lenders USING GIN (company_name gin_trgm_ops);


-- ── 3. Index on data_hash for deduplication checks ────────────────────────────
-- The pipeline computes a hash per lender to detect changes.
-- Without an index, SELECT WHERE data_hash = $1 is a sequential scan.

CREATE INDEX IF NOT EXISTS idx_lenders_data_hash
  ON lenders (data_hash)
  WHERE data_hash IS NOT NULL;


-- ── 4. Composite index on matching_requests(user_id, created_at) ──────────────
-- Powers "show my search history" queries: WHERE user_id = $1 ORDER BY created_at DESC

CREATE INDEX IF NOT EXISTS idx_mr_user_history
  ON matching_requests (user_id, created_at DESC)
  WHERE user_id IS NOT NULL;


-- ── 5. Composite index on matching_requests(loan_type, created_at) ────────────
-- Powers Grafana time-series: GROUP BY loan_type, DATE_TRUNC('day', created_at)

CREATE INDEX IF NOT EXISTS idx_mr_loan_type_time
  ON matching_requests (loan_type, created_at DESC);


-- ── 6. Index on policies(lender_id, loan_type) for admin lender-detail page ───
-- "Show all policies for this lender, by loan type" — used in admin + lender detail

CREATE INDEX IF NOT EXISTS idx_policies_lender_loan_type
  ON policies (lender_id, loan_type)
  WHERE is_active = TRUE;


-- ── 7. Materialized view: dashboard summary stats ─────────────────────────────
-- Replaces live COUNT(*) + AVG() aggregates on every dashboard load.
-- Refreshed by the scheduler (or a cron job) every N minutes.
--
-- Usage: SELECT * FROM mv_dashboard_stats;
-- Refresh: SELECT refresh_dashboard_stats();

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_dashboard_stats AS
SELECT
  -- Lender counts
  COUNT(*)                                                          AS total_lenders,
  COUNT(*) FILTER (WHERE approval_status = 'approved')             AS approved_lenders,
  COUNT(*) FILTER (WHERE approval_status = 'pending')              AS pending_lenders,
  COUNT(*) FILTER (WHERE approval_status = 'needs_update')         AS stale_lenders,

  -- Company type breakdown
  COUNT(*) FILTER (WHERE company_type = 'NBFC')                    AS nbfc_count,
  COUNT(*) FILTER (WHERE company_type = 'Private Bank')            AS private_bank_count,
  COUNT(*) FILTER (WHERE company_type = 'PSU Bank')                AS psu_bank_count,
  COUNT(*) FILTER (WHERE company_type = 'Small Finance Bank')      AS sfb_count,
  COUNT(*) FILTER (WHERE company_type = 'NBFC-MFI')                AS mfi_count,

  -- AUM stats (approved only)
  ROUND(SUM(aum_crores) FILTER (WHERE approval_status = 'approved')::NUMERIC, 0)
                                                                    AS total_aum_crores,
  ROUND(AVG(aum_crores) FILTER (WHERE approval_status = 'approved')::NUMERIC, 0)
                                                                    AS avg_aum_crores,

  -- Data quality (approved only)
  ROUND(AVG(quality_score) FILTER (
    WHERE approval_status = 'approved' AND quality_score IS NOT NULL
  )::NUMERIC, 3)                                                    AS avg_quality_score,

  -- Geographic
  COUNT(*) FILTER (WHERE pan_india = TRUE AND approval_status = 'approved')
                                                                    AS pan_india_count,

  -- Freshness
  COUNT(*) FILTER (
    WHERE approval_status = 'approved'
      AND last_scraped_at > NOW() - INTERVAL '30 days'
  )                                                                 AS fresh_last_30d,

  NOW()                                                             AS computed_at
FROM lenders
WITH DATA;

-- Unique index required for CONCURRENTLY refresh (non-blocking)
CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_dashboard_stats_singleton
  ON mv_dashboard_stats ((1));

-- Function to refresh (call from scheduler or cron)
CREATE OR REPLACE FUNCTION refresh_dashboard_stats()
RETURNS VOID AS $$
BEGIN
  REFRESH MATERIALIZED VIEW CONCURRENTLY mv_dashboard_stats;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- ── 8. Materialized view: policy stats by loan type ───────────────────────────
-- Used by the "Market Overview" panel: average rates, counts by type.

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_policy_stats AS
SELECT
  p.loan_type,
  l.company_type,
  COUNT(*)                                                          AS policy_count,
  ROUND(MIN(p.interest_rate_min)::NUMERIC, 2)                      AS best_rate,
  ROUND(AVG(p.interest_rate_min)::NUMERIC, 2)                      AS avg_rate_min,
  ROUND(AVG(p.interest_rate_max)::NUMERIC, 2)                      AS avg_rate_max,
  ROUND(AVG(p.completeness_score)::NUMERIC, 2)                     AS avg_completeness,
  COUNT(*) FILTER (WHERE p.collateral_required = FALSE)            AS unsecured_count,
  NOW()                                                             AS computed_at
FROM policies p
JOIN lenders l ON l.id = p.lender_id
WHERE p.is_active = TRUE
  AND p.approval_status = 'approved'
  AND l.approval_status = 'approved'
GROUP BY p.loan_type, l.company_type
WITH DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_policy_stats_key
  ON mv_policy_stats (loan_type, company_type);

CREATE OR REPLACE FUNCTION refresh_policy_stats()
RETURNS VOID AS $$
BEGIN
  REFRESH MATERIALIZED VIEW CONCURRENTLY mv_policy_stats;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- ── 9. Auto-partition maintenance for matching_requests ───────────────────────
-- Migration 002 pre-created partitions for -3 to +8 months from its run date.
-- When those expire, rows fall into matching_requests_default (no pruning).
-- This function creates partitions for the next N months on demand.
-- Call it monthly from a pg_cron job or the scheduler's health check.

CREATE OR REPLACE FUNCTION ensure_matching_request_partitions(months_ahead INTEGER DEFAULT 3)
RETURNS INTEGER AS $$
DECLARE
  i       INTEGER;
  sd      DATE;
  ed      DATE;
  pname   TEXT;
  created INTEGER := 0;
BEGIN
  FOR i IN 0..months_ahead LOOP
    sd    := DATE_TRUNC('month', NOW() + (i || ' months')::INTERVAL)::DATE;
    ed    := (sd + INTERVAL '1 month')::DATE;
    pname := 'matching_requests_' || TO_CHAR(sd, 'YYYY_MM');

    IF NOT EXISTS (
      SELECT FROM pg_class c
      JOIN pg_inherits inh ON inh.inhrelid = c.oid
      JOIN pg_class p      ON p.oid = inh.inhparent
      WHERE p.relname = 'matching_requests'
        AND c.relname = pname
    ) THEN
      EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF matching_requests '
        'FOR VALUES FROM (%L) TO (%L)',
        pname, sd, ed
      );
      created := created + 1;
    END IF;
  END LOOP;

  RETURN created;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Create partitions for the next 6 months from now (safe to run repeatedly)
SELECT ensure_matching_request_partitions(6);


-- Record migration
INSERT INTO schema_versions (version, name, checksum)
VALUES (11, 'indexes_and_views', md5('011_indexes_and_views'))
ON CONFLICT (version) DO NOTHING;
