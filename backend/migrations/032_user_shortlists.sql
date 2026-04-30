-- Migration 032: User shortlists table (persisted across devices for logged-in users)
CREATE TABLE IF NOT EXISTS user_shortlists (
  user_id   UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  lender_id BIGINT      NOT NULL REFERENCES lenders(id)   ON DELETE CASCADE,
  added_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, lender_id)
);

ALTER TABLE user_shortlists ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users_own_shortlists" ON user_shortlists
  FOR ALL
  USING     (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

CREATE INDEX IF NOT EXISTS idx_user_shortlists_user ON user_shortlists (user_id);
