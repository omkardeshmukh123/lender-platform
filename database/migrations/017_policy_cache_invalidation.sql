-- migration 017: notify channel on policy approval for API cache invalidation
-- ===========================================================================
-- Problem:
--   When an admin approves a new policy, the match cache (/loans/match) still
--   returns old results for up to 5 minutes. The admin router only invalidates
--   cache on lender approval, not policy approval.
--
-- Fix:
--   Add a DB trigger on policies that sends a pg_notify('policy_updated', ...)
--   event. The FastAPI app listens to this channel via asyncpg LISTEN and
--   invalidates the match+search cache keys immediately.
--
--   This is a NOTIFY-only approach — no polling, no cron. Postgres pushes.
--
-- Usage in FastAPI (backend/api/main.py lifespan):
--   conn = await pool.acquire()
--   await conn.add_listener("policy_updated", on_policy_updated)

CREATE OR REPLACE FUNCTION notify_policy_updated()
RETURNS TRIGGER AS $$
BEGIN
  PERFORM pg_notify(
    'policy_updated',
    json_build_object(
      'policy_id',  NEW.id,
      'lender_id',  NEW.lender_id,
      'loan_type',  NEW.loan_type,
      'action',     TG_OP
    )::text
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_notify_policy_updated ON policies;

CREATE TRIGGER trg_notify_policy_updated
AFTER INSERT OR UPDATE OF approval_status, is_active ON policies
FOR EACH ROW
WHEN (NEW.approval_status = 'approved' AND NEW.is_active = TRUE)
EXECUTE FUNCTION notify_policy_updated();

-- Add missing index for the admin pipeline view (approval_status + company_type)
CREATE INDEX IF NOT EXISTS idx_lenders_status_type
    ON lenders (approval_status, company_type);

-- Record migration
