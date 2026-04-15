-- database/migrations/029_chat_sessions.sql
-- Chat history persistence for the AI chatbot

CREATE TABLE IF NOT EXISTS chat_sessions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_active  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id           BIGSERIAL PRIMARY KEY,
  session_id   UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role         TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content      TEXT NOT NULL,
  intent       TEXT CHECK (intent IN ('filter', 'compare', 'qa')),
  filters_used JSONB,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session
  ON chat_messages(session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_user
  ON chat_sessions(user_id, last_active DESC);

-- RLS: users can only read/write their own data
ALTER TABLE chat_sessions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_messages  ENABLE ROW LEVEL SECURITY;

CREATE POLICY chat_sessions_owner ON chat_sessions
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY chat_messages_owner ON chat_messages
  USING (
    session_id IN (
      SELECT id FROM chat_sessions WHERE user_id = auth.uid()
    )
  )
  WITH CHECK (
    session_id IN (
      SELECT id FROM chat_sessions WHERE user_id = auth.uid()
    )
  );
