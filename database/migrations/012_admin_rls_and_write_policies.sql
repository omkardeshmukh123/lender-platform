-- Migration 012: Admin Write RLS + Approval Helper Function
-- ============================================================
-- Problems fixed:
--   1. lenders/policies only have SELECT RLS policies for authenticated users.
--      The admin panel needs INSERT, UPDATE, DELETE too — but only service_role
--      should bypass RLS entirely; regular authenticated users (admins) need
--      explicit policies or they get 403 on every write.
--
--   2. No safe approve/reject function — admin panel calls raw PATCH via
--      PostgREST which can accidentally overwrite approval_status without
--      setting approved_by or approved_at.
--
--   3. No function to bulk-approve policies for a lender in one call.
-- ============================================================


-- ── 1. Admin write RLS on lenders ─────────────────────────────────────────────
-- authenticated = any logged-in Supabase user (admins use service_role key
-- in practice, but this covers direct Supabase dashboard / PostgREST usage)

DO $$
BEGIN
  -- INSERT: authenticated users can create lender records
  IF NOT EXISTS (
    SELECT FROM pg_policies
    WHERE tablename = 'lenders' AND policyname = 'auth_insert_lenders'
  ) THEN
    CREATE POLICY "auth_insert_lenders"
      ON lenders FOR INSERT TO authenticated
      WITH CHECK (true);
  END IF;

  -- UPDATE: authenticated users can update any lender
  IF NOT EXISTS (
    SELECT FROM pg_policies
    WHERE tablename = 'lenders' AND policyname = 'auth_update_lenders'
  ) THEN
    CREATE POLICY "auth_update_lenders"
      ON lenders FOR UPDATE TO authenticated
      USING (true);
  END IF;

  -- DELETE: only service_role (not regular authenticated) should delete
  -- Implemented by omitting a DELETE policy for authenticated role.
  -- service_role bypasses RLS entirely — no policy needed.
END $$;


-- ── 2. Admin write RLS on policies ────────────────────────────────────────────

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_policies
    WHERE tablename = 'policies' AND policyname = 'auth_insert_policies'
  ) THEN
    CREATE POLICY "auth_insert_policies"
      ON policies FOR INSERT TO authenticated
      WITH CHECK (true);
  END IF;

  IF NOT EXISTS (
    SELECT FROM pg_policies
    WHERE tablename = 'policies' AND policyname = 'auth_update_policies'
  ) THEN
    CREATE POLICY "auth_update_policies"
      ON policies FOR UPDATE TO authenticated
      USING (true);
  END IF;
END $$;


-- ── 3. approve_lender() — safe single-lender approval ─────────────────────────
-- Guarantees approved_by + approved_at are always set on approve.
-- Guarantees admin_notes is set on reject.
-- Returns the updated row so the caller can confirm the change.
--
-- Usage (PostgREST):
--   POST /rpc/approve_lender
--   { "p_lender_id": 42, "p_action": "approved", "p_actor": "admin@firm.com" }

CREATE OR REPLACE FUNCTION approve_lender(
  p_lender_id   BIGINT,
  p_action      TEXT,          -- 'approved' | 'rejected' | 'needs_update'
  p_actor       TEXT,          -- admin email or identifier
  p_notes       TEXT DEFAULT NULL
)
RETURNS SETOF lenders AS $$
BEGIN
  -- Validate action
  IF p_action NOT IN ('approved', 'rejected', 'needs_update') THEN
    RAISE EXCEPTION 'Invalid action: %. Must be approved|rejected|needs_update', p_action;
  END IF;

  -- rejected requires a reason
  IF p_action = 'rejected' AND (p_notes IS NULL OR trim(p_notes) = '') THEN
    RAISE EXCEPTION 'Rejection reason (p_notes) is required when rejecting a lender';
  END IF;

  RETURN QUERY
  UPDATE lenders SET
    approval_status = p_action,
    approved_by     = CASE WHEN p_action = 'approved' THEN p_actor    ELSE approved_by  END,
    approved_at     = CASE WHEN p_action = 'approved' THEN NOW()      ELSE approved_at  END,
    admin_notes     = COALESCE(p_notes, admin_notes)
  WHERE id = p_lender_id
  RETURNING *;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Lender % not found', p_lender_id;
  END IF;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- ── 4. approve_policy() — safe single-policy approval ─────────────────────────

CREATE OR REPLACE FUNCTION approve_policy(
  p_policy_id   BIGINT,
  p_action      TEXT,          -- 'approved' | 'rejected' | 'needs_update'
  p_actor       TEXT,
  p_notes       TEXT DEFAULT NULL
)
RETURNS SETOF policies AS $$
BEGIN
  IF p_action NOT IN ('approved', 'rejected', 'needs_update') THEN
    RAISE EXCEPTION 'Invalid action: %', p_action;
  END IF;

  RETURN QUERY
  UPDATE policies SET
    approval_status = p_action,
    is_active       = CASE WHEN p_action = 'rejected' THEN FALSE ELSE is_active END
  WHERE id = p_policy_id
  RETURNING *;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Policy % not found', p_policy_id;
  END IF;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- ── 5. approve_lender_with_policies() — approve lender + all its pending policies
-- One-click approve used by the admin dashboard.
-- Returns count of policies approved.

CREATE OR REPLACE FUNCTION approve_lender_with_policies(
  p_lender_id   BIGINT,
  p_actor       TEXT,
  p_notes       TEXT DEFAULT NULL
)
RETURNS TABLE (lender_updated BOOLEAN, policies_approved INTEGER) AS $$
DECLARE
  v_pol_count INTEGER;
BEGIN
  -- Approve the lender
  UPDATE lenders SET
    approval_status = 'approved',
    approved_by     = p_actor,
    approved_at     = NOW(),
    admin_notes     = COALESCE(p_notes, admin_notes)
  WHERE id = p_lender_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Lender % not found', p_lender_id;
  END IF;

  -- Approve all pending/needs_update policies for this lender
  UPDATE policies SET
    approval_status = 'approved',
    is_active       = TRUE
  WHERE lender_id = p_lender_id
    AND approval_status IN ('pending', 'needs_update');

  GET DIAGNOSTICS v_pol_count = ROW_COUNT;

  RETURN QUERY SELECT TRUE, v_pol_count;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- ── 6. Data lifecycle view — see all lenders + their pipeline stage ────────────
-- admin_panel query: "show me everything pending with its policy count"

CREATE OR REPLACE VIEW v_admin_pipeline AS
SELECT
  l.id,
  l.company_name,
  l.company_type,
  l.approval_status                                    AS lender_status,
  l.quality_score,
  l.data_source,
  l.last_scraped_at,
  l.next_scrape_at,
  l.approved_by,
  l.approved_at,
  l.admin_notes,

  -- Policy summary
  COUNT(p.id)                                          AS total_policies,
  COUNT(p.id) FILTER (WHERE p.approval_status = 'approved')   AS approved_policies,
  COUNT(p.id) FILTER (WHERE p.approval_status = 'pending')    AS pending_policies,
  COUNT(p.id) FILTER (WHERE p.approval_status = 'rejected')   AS rejected_policies,
  ROUND(AVG(p.completeness_score)::NUMERIC, 2)         AS avg_policy_completeness

FROM lenders l
LEFT JOIN policies p ON p.lender_id = l.id
GROUP BY l.id
ORDER BY
  CASE l.approval_status
    WHEN 'pending'      THEN 1
    WHEN 'needs_update' THEN 2
    WHEN 'approved'     THEN 3
    WHEN 'rejected'     THEN 4
    ELSE 5
  END,
  l.quality_score DESC NULLS LAST;


-- Record migration
INSERT INTO schema_versions (version, name, checksum)
VALUES (12, 'admin_rls_and_write_policies', md5('012_admin_rls_and_write_policies'))
ON CONFLICT (version) DO NOTHING;
