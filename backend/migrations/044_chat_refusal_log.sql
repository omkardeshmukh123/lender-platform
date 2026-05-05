-- Migration 044: add refusal flag to chat_messages
-- Tracks whether an assistant response was an off-topic refusal.
-- Enables filtering refusals from analytics and measuring guardrail hit rate.

ALTER TABLE chat_messages
    ADD COLUMN IF NOT EXISTS refusal BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_chat_messages_refusal
    ON chat_messages (refusal)
    WHERE refusal = true;

COMMENT ON COLUMN chat_messages.refusal IS
    'true when assistant refused to answer because the question was out of scope (non-lending topic)';
