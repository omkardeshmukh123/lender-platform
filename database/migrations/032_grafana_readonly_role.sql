-- Migration 032: Grafana read-only role
-- Creates a restricted postgres role for Grafana dashboard queries.
-- Grafana only needs SELECT — never write access to production data.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_ro') THEN
    CREATE ROLE grafana_ro WITH LOGIN PASSWORD 'CHANGE_ME_IN_SUPABASE';
  END IF;
END
$$;

GRANT CONNECT ON DATABASE postgres TO grafana_ro;
GRANT USAGE ON SCHEMA public TO grafana_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana_ro;
