-- migration 033: pgvector extension + lender embeddings column + HNSW index
-- Run once. Safe to re-run (all statements use IF NOT EXISTS guards).

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE lenders ADD COLUMN IF NOT EXISTS embedding vector(768);

CREATE INDEX IF NOT EXISTS idx_lenders_embedding_hnsw
  ON lenders USING hnsw (embedding vector_cosine_ops)
  WHERE approval_status = 'approved' AND embedding IS NOT NULL;

INSERT INTO schema_versions (version, name, checksum)
VALUES (33, 'pgvector_embeddings', md5('033_pgvector_embeddings'))
ON CONFLICT (version) DO NOTHING;
