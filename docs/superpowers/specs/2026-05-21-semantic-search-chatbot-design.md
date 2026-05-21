# Semantic Search Chatbot — Design Spec
**Date:** 2026-05-21
**Status:** Approved

## Problem

The current two-pass chatbot pipeline extracts rigid SQL filter values (loan_type, state, company_type) from user queries via LLM intent classification. If the query is phrased unusually or uses vocabulary not in the synonym list, the filter extraction fails → SQL returns nothing → bad answer. Free-form queries like "who gives loans to kirana stores in villages" return no results even though relevant lenders exist.

## Goal

Replace the SQL filter-based lender search (for `filter` and `qa` intents) with vector similarity search so queries are matched by **meaning**, not keywords. Keep the rest of the pipeline unchanged.

---

## Architecture

```
User message
     │
     ▼
_quick_classify()          ← greetings, single loan/company type (free, instant)
     │ (if None)
     ▼
parse_intent()             ← DeepSeek via OpenRouter
     │
     ├── compare          → _fetch_lenders_by_name()     (unchanged)
     ├── lender_detail    → _fetch_lenders_by_name()     (unchanged)
     ├── concept          → LLM answer, no DB            (unchanged)
     ├── greeting         → hardcoded response            (unchanged)
     ├── out_of_scope     → refusal response              (unchanged)
     │
     └── filter / qa ──────────→ _search_lenders_semantic()   ← NEW
                                       │
                                       ├── embed query (Gemini text-embedding-004)
                                       ├── pgvector cosine similarity (top 20)
                                       └── results → LLM answer generation (unchanged)
```

---

## What Changes vs What Stays

| Component | Change |
|---|---|
| `_quick_classify()` | Unchanged |
| `parse_intent()` | Unchanged — still called for all intents |
| Filter param extraction from intent | **Removed** for filter/qa — no SQL filters needed |
| `_search_lenders()` | Replaced by `_search_lenders_semantic()` for filter/qa |
| `_search_with_broadening()` | Removed — vector search handles low-match cases naturally |
| `_search_lenders_for_qa()` | Kept as fallback when embedding API is unavailable |
| `_fetch_lenders_by_name()` | Unchanged — still used for compare/lender_detail |
| Answer generation | Unchanged |
| Streaming endpoint | Same changes mirrored from `/chat` to `/chat/stream` |

---

## Embedding Model

- **Provider:** Gemini `text-embedding-004`
- **Dimensions:** 768
- **Cost:** Free tier covers all existing lenders + ongoing query volume at this scale
- **Why Gemini:** Already configured via `GEMINI_API_KEY`, best Hindi/Hinglish support, no new API key needed

## Chat/Answer Model

- **Provider:** DeepSeek V3 via OpenRouter (already configured)
- **Used for:** Intent classification + answer generation (unchanged from current)

---

## Data Model

### pgvector setup

```sql
CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE lenders ADD COLUMN IF NOT EXISTS embedding vector(768);
CREATE INDEX IF NOT EXISTS idx_lenders_embedding_hnsw
  ON lenders USING hnsw (embedding vector_cosine_ops)
  WHERE approval_status = 'approved' AND embedding IS NOT NULL;
```

Migration file: `database/migrations/033_pgvector_embeddings.sql`

### Lender text document format

Each lender is serialised into a single text string before embedding:

```
{company_name} | {company_type} | AUM: ₹{aum_crores} Cr | {aum_category} AUM |
HQ: {hq_location}, {hq_state} | {pan_india_text} | Loan products: {loan_segments} |
Sector: {business_sector} | {listed_text} | Est: {established_year} |
Employees: {employee_count}
```

- Null fields are omitted gracefully (no "None" or "null" tokens in the vector)
- `pan_india_text` → "Pan India" or "Regional operations"
- `listed_text` → "Listed" or omitted if not listed
- Function: `build_lender_text(lender: dict) -> str` in `backend/api/core/embeddings.py`

---

## New Files

### `backend/api/core/embeddings.py`
Single responsibility: embedding generation and lender text building.

```python
build_lender_text(lender: dict) -> str
embed_text(text: str) -> list[float]          # calls Gemini embedding API
embed_query(query: str) -> list[float]         # alias for queries (same model)
embed_lender(lender: dict) -> list[float]      # build_lender_text + embed_text
```

- Uses `google.generativeai` (already installed)
- Raises `EmbeddingUnavailableError` on API failure (caught in router → fallback)

### `scripts/embed_lenders.py`
One-time batch script to embed all existing lenders.

```
python scripts/embed_lenders.py           # embed all approved lenders missing embeddings
python scripts/embed_lenders.py --all     # re-embed everything (force refresh)
python scripts/embed_lenders.py --dry-run # print text documents, no API calls
```

- Batches in groups of 20 with 1s delay between batches (rate limit safe)
- Prints progress: `Embedded 45/312 lenders...`
- Idempotent — safe to run multiple times

---

## Modified Files

### `backend/api/routers/chat.py`

**New function:**
```python
async def _search_lenders_semantic(
    db: asyncpg.Pool,
    query: str,
    limit: int = 20,
) -> list[LenderResult]
```
- Embeds `query` via `embed_query()`
- Runs: `SELECT ... FROM lenders WHERE approval_status='approved' AND embedding IS NOT NULL ORDER BY embedding <=> $1 LIMIT $2`
- Returns `list[LenderResult]`
- On `EmbeddingUnavailableError` → falls back to `_search_lenders_for_qa(db, query)`

**Removed for filter/qa path:**
- Filter merging (`_merge_filters`)
- `_search_with_broadening()`
- Broadening note logic
- `applied_filters` field (set to `None` for semantic results)

**Kept unchanged:**
- `_fetch_lenders_by_name()` for compare/lender_detail
- `_search_lenders_for_qa()` (now fallback only)
- All answer generation logic
- Streaming endpoint mirrors same changes

### `database/migrations/033_pgvector_embeddings.sql`
New migration — enables pgvector, adds column, creates HNSW index.

### `backend/api/core/config.py`
New config values:
```python
embedding_model: str = _str("GEMINI_EMBEDDING_MODEL", "text-embedding-004")
embedding_top_k: int = _int("EMBEDDING_TOP_K", 20)
```

---

## Re-embedding on Lender Update

When a lender is approved or updated, their embedding should be refreshed.

- Add a call to `embed_lender()` in the lender approval/update admin endpoint (best-effort)
- Failure is logged but does not block the save
- If embedding is stale/missing, the lender simply won't appear in semantic results until the next batch run

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Gemini embedding API down | Fall back to `_search_lenders_for_qa()` keyword search |
| Lender has no embedding | Excluded from vector results (`IS NOT NULL` filter) |
| pgvector extension missing | `embed_lenders.py` fails with clear error; router falls back to keyword search |
| Query embedding fails | Log error, fall back to keyword search, do not surface 503 to user |

---

## Testing

- **Unit:** `test_build_lender_text()` — null AUM, no loan segments, no states, Hindi name
- **Unit:** `test_search_lenders_semantic_fallback()` — mock embedding failure → keyword search triggered
- **Integration:** Extend `backend/tests/test_chatbot_guardrails.py` — embed a real query, verify top-K is non-empty and sensible
- **Script:** `embed_lenders.py --dry-run` shows document text for first 5 lenders without API calls

---

## Out of Scope

- Hybrid search (vector + hard SQL filters) — pure semantic only as decided
- Re-ranking with cross-encoders
- User query history personalisation
- Embedding caching per query (query volume too low to justify)
