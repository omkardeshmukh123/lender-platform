-- database/migrations/031_chat_feedback.sql
-- Streaming feedback tracking + fix intent constraint to include all intent values

-- Fix intent check constraint (migration 029 only had filter/compare/qa)
ALTER TABLE chat_messages DROP CONSTRAINT IF EXISTS chat_messages_intent_check;
ALTER TABLE chat_messages ADD CONSTRAINT chat_messages_intent_check
  CHECK (intent IN ('filter', 'compare', 'qa', 'lender_detail', 'concept', 'greeting', 'out_of_scope'));

-- Feedback table: one rating per user per message (upsert-safe)
CREATE TABLE IF NOT EXISTS chat_feedback (
  id         BIGSERIAL PRIMARY KEY,
  session_id UUID    NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  message_id BIGINT  REFERENCES chat_messages(id) ON DELETE SET NULL,
  user_id    UUID    REFERENCES auth.users(id) ON DELETE SET NULL,
  rating     TEXT    NOT NULL CHECK (rating IN ('up', 'down')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_feedback_user_message
  ON chat_feedback(user_id, message_id);

CREATE INDEX IF NOT EXISTS idx_chat_feedback_session
  ON chat_feedback(session_id);

CREATE INDEX IF NOT EXISTS idx_chat_feedback_rating
  ON chat_feedback(rating, created_at DESC);

ALTER TABLE chat_feedback ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS chat_feedback_owner ON chat_feedback;
CREATE POLICY chat_feedback_owner ON chat_feedback
  USING (session_id IN (SELECT id FROM chat_sessions WHERE user_id = auth.uid()))
  WITH CHECK (session_id IN (SELECT id FROM chat_sessions WHERE user_id = auth.uid()));
