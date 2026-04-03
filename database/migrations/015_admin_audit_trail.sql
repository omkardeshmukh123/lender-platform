-- migration 015: enforce admin audit trail on lenders table
-- ===========================================================
-- Problem:
--   The approval workflow (approve/reject/flag) records actions in the
--   lenders table (approved_by, approved_at) but:
--     1. approved_by stores the admin's email string — not a FK to auth.users
--     2. No rejection reason is stored on the lenders row itself
--     3. No history: if an admin approves then another rejects, only the last
--        action is visible
--
-- Fix 1: Add approval_note column to lenders for inline rejection reason
-- Fix 2: Create lender_audit_log table for full history with FK to auth.users
-- Fix 3: Add trigger to auto-populate lender_audit_log on status changes

-- ── Fix 1: Inline note column (safe: nullable, no default needed) ─────────────
-- Check if column exists before adding (idempotent)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'lenders' AND column_name = 'approval_note'
    ) THEN
        ALTER TABLE lenders ADD COLUMN approval_note TEXT;
    END IF;
END $$;

-- ── Fix 2: Full audit history table ───────────────────────────────────────────
-- Table already created in migration 010 with generic schema; add missing columns.
CREATE TABLE IF NOT EXISTS lender_audit_log (
    id              BIGSERIAL   PRIMARY KEY,
    lender_id       BIGINT      REFERENCES lenders(id) ON DELETE CASCADE,
    lender_name     TEXT,
    action          TEXT,
    previous_status TEXT,
    new_status      TEXT,
    note            TEXT,
    actor_id        UUID,
    actor_email     TEXT        NOT NULL DEFAULT 'system',
    ip_address      INET,
    request_id      TEXT,
    acted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add columns that may be missing (table may exist from migration 010)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='lender_audit_log' AND column_name='lender_id') THEN
        ALTER TABLE lender_audit_log ADD COLUMN lender_id BIGINT REFERENCES lenders(id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='lender_audit_log' AND column_name='lender_name') THEN
        ALTER TABLE lender_audit_log ADD COLUMN lender_name TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='lender_audit_log' AND column_name='previous_status') THEN
        ALTER TABLE lender_audit_log ADD COLUMN previous_status TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='lender_audit_log' AND column_name='note') THEN
        ALTER TABLE lender_audit_log ADD COLUMN note TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='lender_audit_log' AND column_name='actor_id') THEN
        ALTER TABLE lender_audit_log ADD COLUMN actor_id UUID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='lender_audit_log' AND column_name='actor_email') THEN
        ALTER TABLE lender_audit_log ADD COLUMN actor_email TEXT NOT NULL DEFAULT 'system';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='lender_audit_log' AND column_name='ip_address') THEN
        ALTER TABLE lender_audit_log ADD COLUMN ip_address INET;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='lender_audit_log' AND column_name='request_id') THEN
        ALTER TABLE lender_audit_log ADD COLUMN request_id TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='lender_audit_log' AND column_name='acted_at') THEN
        ALTER TABLE lender_audit_log ADD COLUMN acted_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
    END IF;
END $$;

-- Fast lookup by lender (most common: "show history for this lender")
CREATE INDEX IF NOT EXISTS idx_lender_audit_lender_id
    ON lender_audit_log (lender_id, acted_at DESC);

-- Fast lookup by actor (admin accountability: "what did this admin approve today?")
CREATE INDEX IF NOT EXISTS idx_lender_audit_actor
    ON lender_audit_log (actor_id, acted_at DESC);

-- Fast lookup by date range (daily audit reports)
CREATE INDEX IF NOT EXISTS idx_lender_audit_date
    ON lender_audit_log (acted_at DESC);

-- RLS: authenticated users (admins) can read audit log
ALTER TABLE lender_audit_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Authenticated users can read lender_audit_log"
    ON lender_audit_log FOR SELECT
    TO authenticated
    USING (true);

CREATE POLICY "Service role can insert lender_audit_log"
    ON lender_audit_log FOR INSERT
    TO service_role
    WITH CHECK (true);

-- ── Fix 3: Helper function for API to call after each approval action ──────────
-- The FastAPI admin router calls this instead of raw UPDATE.
-- This ensures audit log is always populated — no way to approve without logging.

CREATE OR REPLACE FUNCTION approve_lender_audited(
    p_lender_id     BIGINT,
    p_new_status    TEXT,       -- 'approved', 'rejected', 'needs_update'
    p_note          TEXT,       -- rejection reason or flag reason
    p_actor_id      UUID,       -- admin's user ID from JWT sub
    p_actor_email   TEXT,       -- admin's email from JWT
    p_request_id    TEXT DEFAULT NULL,
    p_ip_address    TEXT DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_prev_status TEXT;
    v_name        TEXT;
    v_policy_count INT;
BEGIN
    -- Get current state (for audit log)
    SELECT approval_status, company_name
    INTO   v_prev_status, v_name
    FROM   lenders
    WHERE  id = p_lender_id
    FOR UPDATE;  -- lock row during approval

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Lender % not found', p_lender_id;
    END IF;

    -- Update lender status
    UPDATE lenders
    SET
        approval_status = p_new_status,
        approved_by     = p_actor_email,
        approved_at     = CASE WHEN p_new_status = 'approved' THEN NOW() ELSE approved_at END,
        approval_note   = p_note,
        admin_notes     = COALESCE(p_note, admin_notes)
    WHERE id = p_lender_id;

    -- Approve all pending policies when lender is approved
    IF p_new_status = 'approved' THEN
        UPDATE policies
        SET    approval_status = 'approved', is_active = TRUE
        WHERE  lender_id = p_lender_id
          AND  approval_status = 'pending';

        GET DIAGNOSTICS v_policy_count = ROW_COUNT;
    ELSE
        v_policy_count := 0;
    END IF;

    -- Write audit log entry
    INSERT INTO lender_audit_log
        (lender_id, lender_name, action, previous_status, new_status,
         note, actor_id, actor_email, ip_address, request_id)
    VALUES
        (p_lender_id, v_name, p_new_status, v_prev_status, p_new_status,
         p_note, p_actor_id, p_actor_email,
         p_ip_address::INET, p_request_id);

    RETURN jsonb_build_object(
        'lender_id',      p_lender_id,
        'lender_name',    v_name,
        'new_status',     p_new_status,
        'previous_status', v_prev_status,
        'policies_activated', v_policy_count
    );
END;
$$;

-- Record migration
