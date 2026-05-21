# Semantic Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace SQL filter-based lender search with pgvector semantic search so user queries are matched by meaning, not keywords.

**Architecture:** Embed every lender profile once as a 768-dim Gemini vector stored in the `lenders` table. At query time, embed the user message and run a cosine-similarity lookup. The existing intent classifier, compare/lender_detail paths, and answer generation are unchanged — only the filter/qa search path changes.

**Tech Stack:** pgvector PostgreSQL extension, Gemini `text-embedding-004` (via existing `google-genai` SDK), asyncpg (existing), pytest (existing).

---

## File Map

| Action | File | Responsibility |
|---|---|---|
| Create | `database/migrations/033_pgvector_embeddings.sql` | Enable pgvector, add column, HNSW index |
| Create | `backend/api/core/embeddings.py` | build_lender_text, embed_text, embed_query, embed_lender, EmbeddingUnavailableError |
| Create | `backend/tests/test_embeddings.py` | Unit tests for embeddings module |
| Create | `scripts/embed_lenders.py` | One-time batch script to embed all existing lenders |
| Modify | `backend/api/core/config.py` | Add embedding_model, embedding_top_k config values |
| Modify | `backend/api/routers/chat.py` | Add _search_lenders_semantic; replace filter/qa path in both endpoints |
| Modify | `backend/api/routers/admin.py` | Re-embed lender on approval |

---

## Task 1: Database Migration — pgvector

**Files:**
- Create: `database/migrations/033_pgvector_embeddings.sql`

- [ ] **Step 1: Create the migration file**

```sql
-- migration 033: pgvector extension + lender embeddings column + HNSW index
-- Run once. Safe to re-run (all statements use IF NOT EXISTS / IF NOT EXISTS guards).

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE lenders ADD COLUMN IF NOT EXISTS embedding vector(768);

CREATE INDEX IF NOT EXISTS idx_lenders_embedding_hnsw
  ON lenders USING hnsw (embedding vector_cosine_ops)
  WHERE approval_status = 'approved' AND embedding IS NOT NULL;

INSERT INTO schema_versions (version, name, checksum)
VALUES (33, 'pgvector_embeddings', md5('033_pgvector_embeddings'))
ON CONFLICT (version) DO NOTHING;
```

- [ ] **Step 2: Apply the migration against your database**

```bash
psql $DATABASE_URL -f database/migrations/033_pgvector_embeddings.sql
```

Expected output:
```
CREATE EXTENSION
ALTER TABLE
CREATE INDEX
INSERT 0 1
```

If pgvector is not installed on the database server, install it first:
```bash
# On Ubuntu/Debian:
apt-get install postgresql-16-pgvector
# On the DB server restart may be needed, then re-run the migration.
```

- [ ] **Step 3: Verify**

```bash
psql $DATABASE_URL -c "\d lenders" | grep embedding
```

Expected: `embedding | vector(768) | ...`

- [ ] **Step 4: Commit**

```bash
git add database/migrations/033_pgvector_embeddings.sql
git commit -m "feat(db): add pgvector extension and lender embedding column"
```

---

## Task 2: Config — Embedding Values

**Files:**
- Modify: `backend/api/core/config.py`

- [ ] **Step 1: Add two config values after the `gemini_chat_retries` line**

Find this line in `backend/api/core/config.py`:
```python
    gemini_chat_retries:      int   = _int("GEMINI_CHAT_RETRIES", 3)      # attempts per Gemini call
```

Add immediately after it:
```python
    # Semantic search — embeddings
    embedding_model:          str   = _str("GEMINI_EMBEDDING_MODEL", "text-embedding-004")
    embedding_top_k:          int   = _int("EMBEDDING_TOP_K", 20)
```

- [ ] **Step 2: Commit**

```bash
git add backend/api/core/config.py
git commit -m "feat(config): add embedding_model and embedding_top_k config values"
```

---

## Task 3: Embeddings Module (TDD)

**Files:**
- Create: `backend/api/core/embeddings.py`
- Create: `backend/tests/test_embeddings.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_embeddings.py`:

```python
"""Unit tests for the embeddings module."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# build_lender_text
# ---------------------------------------------------------------------------

def test_build_lender_text_full():
    from api.core.embeddings import build_lender_text
    lender = {
        "company_name": "HDFC Bank",
        "company_type": "Private Bank",
        "aum_crores": 2500000,
        "aum_category": "Large",
        "hq_location": "Mumbai",
        "hq_state": "Maharashtra",
        "pan_india": True,
        "primary_loan_segments": ["Home Loan", "Personal Loan", "Vehicle Loan"],
        "business_sector": "Housing",
        "is_listed": True,
        "established_year": 1994,
        "employee_count": 177000,
        "operating_intensity": "Pan India",
    }
    text = build_lender_text(lender)
    assert "HDFC Bank" in text
    assert "Private Bank" in text
    assert "₹25,00,000 Cr" in text
    assert "Large AUM" in text
    assert "HQ: Mumbai, Maharashtra" in text
    assert "Pan India operations" in text
    assert "Home Loan" in text
    assert "Housing" in text
    assert "Listed company" in text
    assert "Est: 1994" in text


def test_build_lender_text_null_fields_omitted():
    """Null fields must not emit 'None' or 'null' into the text."""
    from api.core.embeddings import build_lender_text
    lender = {"company_name": "Test MFI", "company_type": "NBFC-MFI"}
    text = build_lender_text(lender)
    assert "None" not in text
    assert "null" not in text.lower()
    assert "Test MFI" in text
    assert "NBFC-MFI" in text


def test_build_lender_text_regional_shows_states():
    """Regional lenders (pan_india=False) should list operating states."""
    from api.core.embeddings import build_lender_text
    lender = {
        "company_name": "Regional Bank",
        "company_type": "Cooperative Bank",
        "pan_india": False,
        "operating_states": ["Maharashtra", "Goa", "Karnataka"],
    }
    text = build_lender_text(lender)
    assert "Maharashtra" in text
    assert "Pan India" not in text


def test_build_lender_text_hq_state_only():
    """When hq_location is missing, use hq_state alone."""
    from api.core.embeddings import build_lender_text
    lender = {"company_name": "Rural Lender", "hq_state": "Bihar"}
    text = build_lender_text(lender)
    assert "HQ: Bihar" in text


# ---------------------------------------------------------------------------
# EmbeddingUnavailableError raised when API key missing
# ---------------------------------------------------------------------------

def test_embed_text_raises_when_no_api_key():
    from api.core.embeddings import embed_text, EmbeddingUnavailableError, reset_client
    reset_client()
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(EmbeddingUnavailableError, match="GEMINI_API_KEY"):
            embed_text("test")


# ---------------------------------------------------------------------------
# embed_text delegates to Gemini client
# ---------------------------------------------------------------------------

def test_embed_text_returns_vector():
    from api.core.embeddings import embed_text, reset_client
    reset_client()

    mock_values = [0.1] * 768
    mock_embedding = MagicMock()
    mock_embedding.values = mock_values
    mock_result = MagicMock()
    mock_result.embeddings = [mock_embedding]

    mock_client = MagicMock()
    mock_client.models.embed_content.return_value = mock_result

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
        with patch("api.core.embeddings.genai.Client", return_value=mock_client):
            reset_client()
            result = embed_text("find gold loan lenders")

    assert len(result) == 768
    assert result[0] == pytest.approx(0.1)


def test_embed_text_wraps_api_error_as_unavailable():
    from api.core.embeddings import embed_text, EmbeddingUnavailableError, reset_client
    reset_client()

    mock_client = MagicMock()
    mock_client.models.embed_content.side_effect = RuntimeError("connection refused")

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
        with patch("api.core.embeddings.genai.Client", return_value=mock_client):
            reset_client()
            with pytest.raises(EmbeddingUnavailableError, match="Embedding API error"):
                embed_text("test")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_embeddings.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'api.core.embeddings'`

- [ ] **Step 3: Implement the embeddings module**

Create `backend/api/core/embeddings.py`:

```python
# backend/api/core/embeddings.py
"""
Embedding generation for semantic lender search.
Converts lender profiles and user queries to 768-dim vectors via Gemini text-embedding-004.
"""
from __future__ import annotations

import os
import threading
from typing import Optional

from google import genai


class EmbeddingUnavailableError(Exception):
    """Raised when the embedding API is unavailable or not configured."""


def build_lender_text(lender: dict) -> str:
    """Convert a lender record into a rich text document for embedding."""
    parts: list[str] = []

    name = lender.get("company_name") or lender.get("name", "")
    if name:
        parts.append(name)

    if ct := lender.get("company_type"):
        parts.append(ct)

    aum = lender.get("aum_crores")
    if aum is not None:
        parts.append(f"AUM: ₹{int(aum):,} Cr")

    if ac := lender.get("aum_category"):
        parts.append(f"{ac} AUM")

    hq_loc   = lender.get("hq_location")
    hq_state = lender.get("hq_state")
    if hq_loc and hq_state:
        parts.append(f"HQ: {hq_loc}, {hq_state}")
    elif hq_state:
        parts.append(f"HQ: {hq_state}")

    if lender.get("pan_india"):
        parts.append("Pan India operations")
    else:
        states = lender.get("operating_states") or []
        if states:
            parts.append(f"Operating in: {', '.join(states[:5])}")

    segments = lender.get("primary_loan_segments") or []
    if segments:
        parts.append(f"Loan products: {', '.join(segments)}")

    if sector := lender.get("business_sector"):
        parts.append(f"Sector: {sector}")

    if lender.get("is_listed"):
        parts.append("Listed company")

    if year := lender.get("established_year"):
        parts.append(f"Est: {year}")

    if emp := lender.get("employee_count"):
        parts.append(f"Employees: {emp}")

    if intensity := lender.get("operating_intensity"):
        parts.append(intensity)

    return " | ".join(parts)


_client: Optional[genai.Client] = None
_lock = threading.Lock()


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                api_key = os.environ.get("GEMINI_API_KEY", "")
                if not api_key:
                    raise EmbeddingUnavailableError("GEMINI_API_KEY not set")
                _client = genai.Client(api_key=api_key)
    return _client


def embed_text(text: str) -> list[float]:
    """Embed a text string. Returns a 768-dim float vector."""
    model = os.environ.get("GEMINI_EMBEDDING_MODEL", "text-embedding-004")
    try:
        client = _get_client()
        result = client.models.embed_content(model=model, contents=text)
        return list(result.embeddings[0].values)
    except EmbeddingUnavailableError:
        raise
    except Exception as exc:
        raise EmbeddingUnavailableError(f"Embedding API error: {exc}") from exc


def embed_query(query: str) -> list[float]:
    """Embed a user query for semantic search."""
    return embed_text(query)


def embed_lender(lender: dict) -> list[float]:
    """Build text document from lender dict and embed it."""
    return embed_text(build_lender_text(lender))


def reset_client() -> None:
    """Force re-creation of the embedding client. Used in tests."""
    global _client
    with _lock:
        _client = None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_embeddings.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/api/core/embeddings.py backend/tests/test_embeddings.py
git commit -m "feat(embeddings): add Gemini embedding module with build_lender_text"
```

---

## Task 4: Semantic Search Function (TDD)

**Files:**
- Modify: `backend/api/routers/chat.py` (add `_search_lenders_semantic`)
- Modify: `backend/tests/test_chatbot_guardrails.py` (add fallback test)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_chatbot_guardrails.py`:

```python
# ---------------------------------------------------------------------------
# _search_lenders_semantic — embedding fallback
# ---------------------------------------------------------------------------

import asyncio
from unittest.mock import AsyncMock, patch as _patch


def test_semantic_search_falls_back_to_keyword_on_embedding_error():
    """When embedding API fails, semantic search falls back to _search_lenders_for_qa."""
    # Import here to avoid top-level import errors if module not yet implemented
    from api.routers.chat import _search_lenders_semantic
    from api.core.embeddings import EmbeddingUnavailableError

    mock_db = MagicMock()

    async def run():
        with _patch("api.routers.chat.embed_query", side_effect=EmbeddingUnavailableError("down")):
            with _patch("api.routers.chat._search_lenders_for_qa", new_callable=AsyncMock) as mock_qa:
                mock_qa.return_value = []
                result = await _search_lenders_semantic(mock_db, "gold loan lenders")
                mock_qa.assert_called_once_with(mock_db, "gold loan lenders")
                return result

    assert asyncio.run(run()) == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_chatbot_guardrails.py::test_semantic_search_falls_back_to_keyword_on_embedding_error -v
```

Expected: `ImportError` or `AttributeError` — `_search_lenders_semantic` does not exist yet.

- [ ] **Step 3: Add `_search_lenders_semantic` to `chat.py`**

Add this import near the top of `backend/api/routers/chat.py` (after existing imports):

```python
from core.embeddings import embed_query, EmbeddingUnavailableError
```

Add this function just before `_search_with_broadening` in `backend/api/routers/chat.py`:

```python
async def _search_lenders_semantic(
    db: asyncpg.Pool,
    query: str,
    limit: int = 20,
) -> list[LenderResult]:
    """Find lenders by vector similarity to query. Falls back to keyword search on error."""
    try:
        vector = await asyncio.get_running_loop().run_in_executor(None, embed_query, query)
    except EmbeddingUnavailableError:
        logger.warning("semantic search: embedding unavailable, falling back to keyword search")
        return await _search_lenders_for_qa(db, query)

    vec_literal = "[" + ",".join(f"{v:.8f}" for v in vector) + "]"
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, company_name, company_type, rbi_category,
                   aum_crores, aum_category, hq_state, hq_location,
                   pan_india, primary_loan_segments, operating_states,
                   website, quality_score, employee_count,
                   established_year, is_listed, phone, email,
                   operating_intensity, business_sector
            FROM lenders
            WHERE approval_status = 'approved'
              AND embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            vec_literal, limit,
        )
    return [_row_to_lender(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend && python -m pytest tests/test_chatbot_guardrails.py::test_semantic_search_falls_back_to_keyword_on_embedding_error -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/api/routers/chat.py backend/tests/test_chatbot_guardrails.py
git commit -m "feat(chat): add _search_lenders_semantic with keyword fallback"
```

---

## Task 5: Update /chat Endpoint — filter/qa → semantic

**Files:**
- Modify: `backend/api/routers/chat.py`

This task replaces the SQL filter path and the broadening logic with semantic search for `filter` and `qa` intents. **Compare and lender_detail paths are untouched.**

- [ ] **Step 1: Remove the multi-turn filter merge block**

Find and delete these lines in the `chat` endpoint function (just after `detail_names = parsed.get(...)`) :

```python
    # Multi-turn filter refinement: merge last applied filters with new ones
    if intent == "filter" and body.last_filters:
        filters = _merge_filters(body.last_filters, filters)
```

- [ ] **Step 2: Replace the lender DB lookup section**

Find this entire block (from `lenders: list[LenderResult] = []` to `if _lender_cache_key and lenders:`):

```python
    lenders: list[LenderResult] = []
    applied_filters: Optional[dict] = None
    broadening_note: str = ""
    unmatched_names: list[str] = []
    db_total: int = 0

    _lender_cache_key = None
    _cached_lender_payload = None
    _is_similarity = _is_similarity_query(body.message)

    if intent in ("filter", "compare", "lender_detail") and not _is_similarity:
        _lender_cache_params: dict = {"intent": intent}
        if intent == "filter":
            _lender_cache_params["filters"] = filters
        elif intent == "compare":
            _lender_cache_params["names"] = sorted(compare_names)
        elif intent == "lender_detail":
            _lender_cache_params["names"] = detail_names[:1]
        _lender_cache_key = make_key("chat:lenders", _lender_cache_params)
        _cached_lender_payload = await cache.get(_lender_cache_key)

    if _cached_lender_payload is not None:
        logger.debug("chat: lender cache HIT")
        lenders = [LenderResult(**d) for d in _cached_lender_payload["lenders"]]
        applied_filters = _cached_lender_payload.get("applied_filters")
        broadening_note = _cached_lender_payload.get("broadening_note", "")
        unmatched_names = _cached_lender_payload.get("unmatched_names", [])
        db_total        = _cached_lender_payload.get("db_total", len(lenders))
    else:
        try:
            ref_name_for_sim = (detail_names or compare_names or [None])[0]
            if _is_similarity and ref_name_for_sim:
                ref_lenders = await _fetch_lenders_by_name(db, [ref_name_for_sim])
                if ref_lenders:
                    ref = ref_lenders[0]
                    sim_filters: dict = {"sort_by": "aum_crores", "sort_dir": "desc"}
                    if ref.company_type:
                        sim_filters["company_type"] = [ref.company_type]
                    if ref.business_sector:
                        sim_filters["business_sector"] = [ref.business_sector]
                    lenders, applied_filters, broadening_note, db_total = await _search_with_broadening(db, sim_filters)
                    lenders = [l for l in lenders if l.id != ref.id]
                    db_total = max(0, db_total - 1)
                    intent = "filter"
                    sim_note = (
                        f"Showing lenders similar to {ref.company_name} "
                        f"({ref.company_type}{', ' + ref.business_sector if ref.business_sector else ''})."
                    )
                    broadening_note = (sim_note + " " + broadening_note).strip()
                else:
                    lenders = await _search_lenders_for_qa(db, body.message)
                    intent = "qa"

            elif intent == "filter":
                had_explicit_filters = bool(filters)
                search_filters = filters if filters else {"sort_by": "aum_crores", "sort_dir": "desc"}
                lenders, applied_filters, broadening_note, db_total = await _search_with_broadening(db, search_filters)
                if not had_explicit_filters:
                    applied_filters = None

            elif intent == "compare" and compare_names:
                lenders = _dedup_lenders(await _fetch_lenders_by_name(db, compare_names))
                unmatched_names = _compute_unmatched_names(compare_names, lenders)
                if len(lenders) == 1 and "another" in body.message.lower():
                    async with db.acquire() as conn:
                        alt = await conn.fetchrow(
                            """
                            SELECT id, company_name, company_type, rbi_category,
                                   aum_crores, aum_category, hq_state, hq_location,
                                   pan_india, primary_loan_segments, operating_states,
                                   website, quality_score, employee_count,
                                   established_year, is_listed, phone, email,
                                   operating_intensity, business_sector
                            FROM lenders
                            WHERE approval_status = 'approved'
                              AND company_type = $1 AND id != $2
                            ORDER BY quality_score DESC NULLS LAST, aum_crores DESC NULLS LAST
                            LIMIT 1
                            """,
                            lenders[0].company_type, lenders[0].id,
                        )
                    if alt:
                        lenders.append(_row_to_lender(alt))
                        unmatched_names = []

            elif intent == "lender_detail" and detail_names:
                lenders = _dedup_lenders(await _fetch_lenders_by_name(db, detail_names[:1]))

            elif intent == "qa":
                lenders = await _search_lenders_for_qa(db, body.message)

        except Exception as exc:
            logger.error("chat: DB query failed: %s", exc)
            raise HTTPException(status_code=503, detail="Search temporarily unavailable")

        if _lender_cache_key and lenders:
            await cache.set(_lender_cache_key, {
                "lenders": [l.model_dump() for l in lenders],
                "applied_filters": applied_filters,
                "broadening_note": broadening_note,
                "unmatched_names": unmatched_names,
                "db_total": db_total,
            }, ttl=CacheTTL.MATCH)

    # Append count note when filter returns a capped result set
    if intent == "filter" and db_total > len(lenders) > 0:
        count_note = f"Total matching: {db_total}, showing top {len(lenders)}."
        broadening_note = (count_note + " " + broadening_note).strip() if broadening_note else count_note
```

Replace with:

```python
    lenders: list[LenderResult] = []
    applied_filters: Optional[dict] = None
    unmatched_names: list[str] = []

    _lender_cache_key = None
    _cached_lender_payload = None

    if intent in ("filter", "qa", "compare", "lender_detail"):
        _lender_cache_params: dict = {"intent": intent}
        if intent in ("filter", "qa"):
            _lender_cache_params["query"] = body.message.strip().lower()
        elif intent == "compare":
            _lender_cache_params["names"] = sorted(compare_names)
        elif intent == "lender_detail":
            _lender_cache_params["names"] = detail_names[:1]
        _lender_cache_key = make_key("chat:lenders", _lender_cache_params)
        _cached_lender_payload = await cache.get(_lender_cache_key)

    if _cached_lender_payload is not None:
        logger.debug("chat: lender cache HIT")
        lenders = [LenderResult(**d) for d in _cached_lender_payload["lenders"]]
        applied_filters = _cached_lender_payload.get("applied_filters")
        unmatched_names = _cached_lender_payload.get("unmatched_names", [])
    else:
        try:
            if intent in ("filter", "qa"):
                lenders = _dedup_lenders(
                    await _search_lenders_semantic(db, body.message, cfg.embedding_top_k)
                )

            elif intent == "compare" and compare_names:
                lenders = _dedup_lenders(await _fetch_lenders_by_name(db, compare_names))
                unmatched_names = _compute_unmatched_names(compare_names, lenders)
                if len(lenders) == 1 and "another" in body.message.lower():
                    async with db.acquire() as conn:
                        alt = await conn.fetchrow(
                            """
                            SELECT id, company_name, company_type, rbi_category,
                                   aum_crores, aum_category, hq_state, hq_location,
                                   pan_india, primary_loan_segments, operating_states,
                                   website, quality_score, employee_count,
                                   established_year, is_listed, phone, email,
                                   operating_intensity, business_sector
                            FROM lenders
                            WHERE approval_status = 'approved'
                              AND company_type = $1 AND id != $2
                            ORDER BY quality_score DESC NULLS LAST, aum_crores DESC NULLS LAST
                            LIMIT 1
                            """,
                            lenders[0].company_type, lenders[0].id,
                        )
                    if alt:
                        lenders.append(_row_to_lender(alt))
                        unmatched_names = []

            elif intent == "lender_detail" and detail_names:
                lenders = _dedup_lenders(await _fetch_lenders_by_name(db, detail_names[:1]))

        except Exception as exc:
            logger.error("chat: DB query failed: %s", exc)
            raise HTTPException(status_code=503, detail="Search temporarily unavailable")

        if _lender_cache_key and lenders:
            await cache.set(_lender_cache_key, {
                "lenders":         [l.model_dump() for l in lenders],
                "applied_filters": applied_filters,
                "unmatched_names": unmatched_names,
            }, ttl=CacheTTL.MATCH)
```

- [ ] **Step 3: Remove `broadening_note` from the grounded answer call**

Find:
```python
                lambda: client.generate_grounded_answer(
                    body.message, intent, lender_dicts, gemini_history,
                    note=broadening_note,
                ),
```

Replace with:
```python
                lambda: client.generate_grounded_answer(
                    body.message, intent, lender_dicts, gemini_history,
                    note="",
                ),
```

- [ ] **Step 4: Run the existing tests to verify nothing broke**

```bash
cd backend && python -m pytest tests/test_chatbot_guardrails.py -v
```

Expected: all existing tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/api/routers/chat.py
git commit -m "feat(chat): replace filter/qa SQL search with semantic vector search"
```

---

## Task 6: Update /chat/stream Endpoint — mirror same changes

**Files:**
- Modify: `backend/api/routers/chat.py` (stream endpoint)

The `/chat/stream` endpoint has the same DB lookup section duplicated. Apply identical changes.

- [ ] **Step 1: Remove multi-turn filter merge in stream endpoint**

Find (inside `chat_stream`):
```python
    if intent == "filter" and body.last_filters:
        filters = _merge_filters(body.last_filters, filters)
```

Delete those two lines.

- [ ] **Step 2: Replace the lender DB lookup section in chat_stream**

Find this block (inside `chat_stream`, starting at `lenders: list[LenderResult] = []`):

```python
    lenders: list[LenderResult] = []
    applied_filters: Optional[dict] = None
    broadening_note = ""
    unmatched_names: list[str] = []
    db_total: int = 0
    _is_similarity = _is_similarity_query(body.message)

    # Lender results cache
    _lender_cache_key = None
    _cached_lender_payload = None
    if intent in ("filter", "compare", "lender_detail") and not _is_similarity:
        _lender_cache_params: dict = {"intent": intent}
        if intent == "filter":
            _lender_cache_params["filters"] = filters
        elif intent == "compare":
            _lender_cache_params["names"] = sorted(compare_names)
        elif intent == "lender_detail":
            _lender_cache_params["names"] = detail_names[:1]
        _lender_cache_key = make_key("chat:lenders", _lender_cache_params)
        _cached_lender_payload = await cache.get(_lender_cache_key)

    if _cached_lender_payload is not None:
        logger.debug("chat_stream: lender cache HIT")
        lenders = [LenderResult(**d) for d in _cached_lender_payload["lenders"]]
        applied_filters = _cached_lender_payload.get("applied_filters")
        broadening_note = _cached_lender_payload.get("broadening_note", "")
        unmatched_names = _cached_lender_payload.get("unmatched_names", [])
        db_total        = _cached_lender_payload.get("db_total", len(lenders))
    elif intent not in ("greeting", "out_of_scope", "concept"):
        try:
            ref_name_for_sim = (detail_names or compare_names or [None])[0]
            if _is_similarity and ref_name_for_sim:
                ref_lenders = await _fetch_lenders_by_name(db, [ref_name_for_sim])
                if ref_lenders:
                    ref = ref_lenders[0]
                    sim_filters: dict = {"sort_by": "aum_crores", "sort_dir": "desc"}
                    if ref.company_type:
                        sim_filters["company_type"] = [ref.company_type]
                    if ref.business_sector:
                        sim_filters["business_sector"] = [ref.business_sector]
                    lenders, applied_filters, broadening_note, db_total = await _search_with_broadening(db, sim_filters)
                    lenders = [l for l in lenders if l.id != ref.id]
                    db_total = max(0, db_total - 1)
                    intent = "filter"
                    sim_note = (
                        f"Showing lenders similar to {ref.company_name} "
                        f"({ref.company_type}{', ' + ref.business_sector if ref.business_sector else ''})."
                    )
                    broadening_note = (sim_note + " " + broadening_note).strip()
                else:
                    lenders = await _search_lenders_for_qa(db, body.message)
                    intent = "qa"
            elif intent == "filter":
                had_explicit_filters = bool(filters)
                search_filters = filters if filters else {"sort_by": "aum_crores", "sort_dir": "desc"}
                lenders, applied_filters, broadening_note, db_total = await _search_with_broadening(db, search_filters)
                if not had_explicit_filters:
                    applied_filters = None
            elif intent == "compare" and compare_names:
                lenders = _dedup_lenders(await _fetch_lenders_by_name(db, compare_names))
                unmatched_names = _compute_unmatched_names(compare_names, lenders)
                if len(lenders) == 1 and "another" in body.message.lower():
                    async with db.acquire() as conn:
                        alt = await conn.fetchrow(
                            """
                            SELECT id, company_name, company_type, rbi_category,
                                   aum_crores, aum_category, hq_state, hq_location,
                                   pan_india, primary_loan_segments, operating_states,
                                   website, quality_score, employee_count,
                                   established_year, is_listed, phone, email,
                                   operating_intensity, business_sector
                            FROM lenders
                            WHERE approval_status = 'approved'
                              AND company_type = $1 AND id != $2
                            ORDER BY quality_score DESC NULLS LAST, aum_crores DESC NULLS LAST
                            LIMIT 1
                            """,
                            lenders[0].company_type, lenders[0].id,
                        )
                    if alt:
                        lenders.append(_row_to_lender(alt))
                        unmatched_names = []
            elif intent == "lender_detail" and detail_names:
                lenders = _dedup_lenders(await _fetch_lenders_by_name(db, detail_names[:1]))
            elif intent == "qa":
                lenders = await _search_lenders_for_qa(db, body.message)
        except Exception as exc:
            logger.error("chat_stream: DB query failed: %s", exc)
            raise HTTPException(status_code=503, detail="Search temporarily unavailable")

        if _lender_cache_key and lenders:
            await cache.set(_lender_cache_key, {
                "lenders": [l.model_dump() for l in lenders],
                "applied_filters": applied_filters,
                "broadening_note": broadening_note,
                "unmatched_names": unmatched_names,
                "db_total": db_total,
            }, ttl=CacheTTL.MATCH)

    if intent == "filter" and db_total > len(lenders) > 0:
        count_note = f"Total matching: {db_total}, showing top {len(lenders)}."
        broadening_note = (count_note + " " + broadening_note).strip() if broadening_note else count_note
```

Replace with:

```python
    lenders: list[LenderResult] = []
    applied_filters: Optional[dict] = None
    unmatched_names: list[str] = []

    _lender_cache_key = None
    _cached_lender_payload = None

    if intent in ("filter", "qa", "compare", "lender_detail"):
        _lender_cache_params: dict = {"intent": intent}
        if intent in ("filter", "qa"):
            _lender_cache_params["query"] = body.message.strip().lower()
        elif intent == "compare":
            _lender_cache_params["names"] = sorted(compare_names)
        elif intent == "lender_detail":
            _lender_cache_params["names"] = detail_names[:1]
        _lender_cache_key = make_key("chat:lenders", _lender_cache_params)
        _cached_lender_payload = await cache.get(_lender_cache_key)

    if _cached_lender_payload is not None:
        logger.debug("chat_stream: lender cache HIT")
        lenders = [LenderResult(**d) for d in _cached_lender_payload["lenders"]]
        applied_filters = _cached_lender_payload.get("applied_filters")
        unmatched_names = _cached_lender_payload.get("unmatched_names", [])
    elif intent not in ("greeting", "out_of_scope", "concept"):
        try:
            if intent in ("filter", "qa"):
                lenders = _dedup_lenders(
                    await _search_lenders_semantic(db, body.message, cfg.embedding_top_k)
                )

            elif intent == "compare" and compare_names:
                lenders = _dedup_lenders(await _fetch_lenders_by_name(db, compare_names))
                unmatched_names = _compute_unmatched_names(compare_names, lenders)
                if len(lenders) == 1 and "another" in body.message.lower():
                    async with db.acquire() as conn:
                        alt = await conn.fetchrow(
                            """
                            SELECT id, company_name, company_type, rbi_category,
                                   aum_crores, aum_category, hq_state, hq_location,
                                   pan_india, primary_loan_segments, operating_states,
                                   website, quality_score, employee_count,
                                   established_year, is_listed, phone, email,
                                   operating_intensity, business_sector
                            FROM lenders
                            WHERE approval_status = 'approved'
                              AND company_type = $1 AND id != $2
                            ORDER BY quality_score DESC NULLS LAST, aum_crores DESC NULLS LAST
                            LIMIT 1
                            """,
                            lenders[0].company_type, lenders[0].id,
                        )
                    if alt:
                        lenders.append(_row_to_lender(alt))
                        unmatched_names = []

            elif intent == "lender_detail" and detail_names:
                lenders = _dedup_lenders(await _fetch_lenders_by_name(db, detail_names[:1]))

        except Exception as exc:
            logger.error("chat_stream: DB query failed: %s", exc)
            raise HTTPException(status_code=503, detail="Search temporarily unavailable")

        if _lender_cache_key and lenders:
            await cache.set(_lender_cache_key, {
                "lenders":         [l.model_dump() for l in lenders],
                "applied_filters": applied_filters,
                "unmatched_names": unmatched_names,
            }, ttl=CacheTTL.MATCH)
```

- [ ] **Step 3: Remove `broadening_note` from the stream worker**

Find inside `event_gen()`:
```python
                for token in client.generate_grounded_answer_stream(
                    body.message, intent, lender_dicts, gemini_history,
                    note=broadening_note,
                ):
```

Replace with:
```python
                for token in client.generate_grounded_answer_stream(
                    body.message, intent, lender_dicts, gemini_history,
                    note="",
                ):
```

- [ ] **Step 4: Run all tests**

```bash
cd backend && python -m pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/api/routers/chat.py
git commit -m "feat(chat): update streaming endpoint to use semantic search"
```

---

## Task 7: Re-embed on Lender Approval

**Files:**
- Modify: `backend/api/routers/admin.py`

When a lender is approved, generate and store their embedding immediately so they appear in semantic search results right away.

- [ ] **Step 1: Add the re-embed helper call in `approve_lender`**

In `backend/api/routers/admin.py`, find the `approve_lender` function. After the `logger.info(...)` line (just before `return`):

```python
    logger.info(
        "ADMIN_APPROVE lender=%d actor=%s policies_activated=%s request_id=%s",
        lender_id, actor_email, result.get("policies_activated"), request_id,
    )
    return {"lender_id": lender_id, "action": "approved", **result}
```

Replace with:

```python
    logger.info(
        "ADMIN_APPROVE lender=%d actor=%s policies_activated=%s request_id=%s",
        lender_id, actor_email, result.get("policies_activated"), request_id,
    )

    # Best-effort: generate embedding for the newly approved lender.
    # Failure is logged but does not block the approval response.
    try:
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, company_name, company_type, rbi_category,
                       aum_crores, aum_category, hq_state, hq_location,
                       pan_india, primary_loan_segments, operating_states,
                       is_listed, established_year, employee_count,
                       operating_intensity, business_sector
                FROM lenders WHERE id = $1
                """,
                lender_id,
            )
        if row:
            from core.embeddings import embed_lender, EmbeddingUnavailableError
            import json as _json

            lender_dict = dict(row)
            for arr_col in ("primary_loan_segments", "operating_states"):
                val = lender_dict.get(arr_col)
                if isinstance(val, str):
                    try:
                        lender_dict[arr_col] = _json.loads(val)
                    except Exception:
                        lender_dict[arr_col] = []

            vector = await asyncio.get_running_loop().run_in_executor(
                None, embed_lender, lender_dict
            )
            vec_literal = "[" + ",".join(f"{v:.8f}" for v in vector) + "]"
            async with db.acquire() as conn:
                await conn.execute(
                    "UPDATE lenders SET embedding = $1::vector WHERE id = $2",
                    vec_literal, lender_id,
                )
            logger.info("ADMIN_APPROVE lender=%d embedding updated", lender_id)
    except Exception as exc:
        logger.warning("ADMIN_APPROVE lender=%d embedding failed (non-fatal): %s", lender_id, exc)

    return {"lender_id": lender_id, "action": "approved", **result}
```

Also add `import asyncio` near the top of `admin.py` if it's not already imported:
```python
import asyncio
```

- [ ] **Step 2: Verify asyncio is already imported**

```bash
grep "import asyncio" backend/api/routers/admin.py
```

If no output, add `import asyncio` to the imports section of `admin.py`.

- [ ] **Step 3: Commit**

```bash
git add backend/api/routers/admin.py
git commit -m "feat(admin): re-embed lender on approval for immediate semantic search visibility"
```

---

## Task 8: Batch Embedding Script

**Files:**
- Create: `scripts/embed_lenders.py`

One-time script to embed all existing approved lenders. Safe to re-run.

- [ ] **Step 1: Create the script**

Create `scripts/embed_lenders.py`:

```python
#!/usr/bin/env python3
"""
Batch embed all approved lenders into the vector column.

Usage:
  python scripts/embed_lenders.py              # embed lenders with missing embeddings
  python scripts/embed_lenders.py --all        # re-embed everything (force refresh)
  python scripts/embed_lenders.py --dry-run    # print text docs without calling API
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Allow imports from backend/api
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "backend" / ".env")

from api.core.embeddings import build_lender_text, embed_lender, EmbeddingUnavailableError


_COLS = """
    id, company_name, company_type, rbi_category,
    aum_crores, aum_category, hq_state, hq_location,
    pan_india, primary_loan_segments, operating_states,
    is_listed, established_year, employee_count,
    operating_intensity, business_sector
"""

BATCH_SIZE = 20
BATCH_DELAY = 1.0  # seconds between batches — stay within Gemini free-tier rate limits


def _parse_row(row: asyncpg.Record) -> dict:
    d = dict(row)
    for arr_col in ("primary_loan_segments", "operating_states"):
        val = d.get(arr_col)
        if isinstance(val, str):
            try:
                d[arr_col] = json.loads(val)
            except Exception:
                d[arr_col] = []
        elif val is None:
            d[arr_col] = []
    return d


async def main(force_all: bool, dry_run: bool) -> None:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("ERROR: DATABASE_URL not set in environment or .env file", file=sys.stderr)
        sys.exit(1)

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key and not dry_run:
        print("ERROR: GEMINI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)

    try:
        where = "approval_status = 'approved'"
        if not force_all:
            where += " AND embedding IS NULL"

        async with pool.acquire() as conn:
            total = await conn.fetchval(f"SELECT COUNT(*) FROM lenders WHERE {where}")
            rows = await conn.fetch(f"SELECT {_COLS} FROM lenders WHERE {where} ORDER BY id")

        print(f"Lenders to embed: {total}")
        if total == 0:
            print("Nothing to do.")
            return

        embedded = 0
        failed = 0

        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]

            for row in batch:
                lender = _parse_row(row)
                text = build_lender_text(lender)

                if dry_run:
                    print(f"\n--- ID {lender['id']}: {lender.get('company_name')} ---")
                    print(text)
                    embedded += 1
                    continue

                try:
                    vector = embed_lender(lender)
                    vec_literal = "[" + ",".join(f"{v:.8f}" for v in vector) + "]"
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE lenders SET embedding = $1::vector WHERE id = $2",
                            vec_literal, lender["id"],
                        )
                    embedded += 1
                    print(f"  [{embedded}/{total}] Embedded: {lender.get('company_name')}")
                except EmbeddingUnavailableError as exc:
                    failed += 1
                    print(f"  [{embedded+failed}/{total}] FAILED: {lender.get('company_name')} — {exc}")
                except Exception as exc:
                    failed += 1
                    print(f"  [{embedded+failed}/{total}] ERROR: {lender.get('company_name')} — {exc}")

            if not dry_run and i + BATCH_SIZE < len(rows):
                print(f"  Batch done. Waiting {BATCH_DELAY}s...")
                await asyncio.sleep(BATCH_DELAY)

        print(f"\nDone. Embedded: {embedded}, Failed: {failed}")
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch embed lenders for semantic search")
    parser.add_argument("--all",     action="store_true", help="Re-embed all lenders, not just missing ones")
    parser.add_argument("--dry-run", action="store_true", help="Print text docs only, no API calls")
    args = parser.parse_args()
    asyncio.run(main(force_all=args.all, dry_run=args.dry_run))
```

- [ ] **Step 2: Test dry-run**

```bash
python scripts/embed_lenders.py --dry-run 2>&1 | head -40
```

Expected: prints lender text documents without calling any API. Check that "None" does not appear in any output line.

- [ ] **Step 3: Commit**

```bash
git add scripts/embed_lenders.py
git commit -m "feat(scripts): add embed_lenders.py batch embedding script"
```

---

## Task 9: Run the Batch Embedding Script

- [ ] **Step 1: Run the script against your database**

```bash
python scripts/embed_lenders.py
```

Expected output:
```
Lenders to embed: 312
  [1/312] Embedded: HDFC Bank
  [2/312] Embedded: State Bank of India
  ...
  Batch done. Waiting 1.0s...
  ...
Done. Embedded: 312, Failed: 0
```

- [ ] **Step 2: Verify embeddings are stored**

```bash
psql $DATABASE_URL -c "SELECT COUNT(*) FROM lenders WHERE embedding IS NOT NULL AND approval_status = 'approved';"
```

Expected: count matches total approved lenders.

- [ ] **Step 3: Smoke test the vector search**

```bash
psql $DATABASE_URL -c "
SELECT company_name, company_type
FROM lenders
WHERE approval_status = 'approved' AND embedding IS NOT NULL
ORDER BY embedding <=> (SELECT embedding FROM lenders WHERE company_name ILIKE '%HDFC%' LIMIT 1)
LIMIT 5;
"
```

Expected: HDFC Bank appears first, followed by similar private banks.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: semantic search complete — all lenders embedded"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ pgvector migration → Task 1
- ✅ `build_lender_text` + embeddings module → Task 3
- ✅ `embed_query`, `embed_lender`, `EmbeddingUnavailableError` → Task 3
- ✅ `_search_lenders_semantic` with fallback → Task 4
- ✅ filter/qa → semantic in `/chat` → Task 5
- ✅ filter/qa → semantic in `/chat/stream` → Task 6
- ✅ Re-embed on approval → Task 7
- ✅ Batch script with `--dry-run` and `--all` → Task 8
- ✅ Config values → Task 2
- ✅ Tests for `build_lender_text` null/full cases → Task 3
- ✅ Test for embedding fallback → Task 4

**Placeholder scan:** No TBDs or vague steps. Every code block is complete.

**Type consistency:** `_search_lenders_semantic` returns `list[LenderResult]` in Task 4, consumed as `list[LenderResult]` in Tasks 5 and 6. `embed_query` imported from `core.embeddings` in Task 4's function body and also at module level — both paths consistent.
