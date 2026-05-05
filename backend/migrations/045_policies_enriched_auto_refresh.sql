-- Migration 045: auto-refresh policies_enriched materialized view
-- Trigger fires AFTER INSERT/UPDATE/DELETE on policies so the mat view
-- stays in sync without manual REFRESH calls.

CREATE OR REPLACE FUNCTION refresh_policies_enriched_mv()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  REFRESH MATERIALIZED VIEW policies_enriched;
  RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_refresh_policies_enriched ON policies;

CREATE TRIGGER trg_refresh_policies_enriched
AFTER INSERT OR UPDATE OR DELETE ON policies
FOR EACH STATEMENT
EXECUTE FUNCTION refresh_policies_enriched_mv();
