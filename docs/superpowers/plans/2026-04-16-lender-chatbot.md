# Lender Chatbot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Gemini-powered chat side panel to the dashboard that translates natural language into lender filters, answers domain questions, and compares lenders — with results synced to the main grid and history persisted in Supabase.

**Architecture:** `POST /v1/chat` FastAPI endpoint calls Gemini gemini-2.0-flash in JSON schema mode to classify intent and extract structured filters. Backend executes the right DB query, saves the turn to Supabase, and returns `{answer, lenders[], applied_filters}`. Frontend `ChatPanel.tsx` renders the thread and calls `onFiltersApplied` to sync the lender grid.

**Tech Stack:** FastAPI, asyncpg, google-generativeai, Next.js 14, Tailwind CSS, Supabase (auth + chat history), lucide-react

---

## Phase 1 — Backend Foundation

---

### Task 1: DB Migration — chat_sessions + chat_messages

**Files:**
- Create: `database/migrations/029_chat_sessions.sql`

- [ ] **Step 1: Write the migration**

```sql
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
```

- [ ] **Step 2: Apply migration via Supabase SQL editor or migrate.py**

Run in Supabase SQL editor or:
```bash
cd database && python migrate.py
```

Expected: tables `chat_sessions` and `chat_messages` created with RLS enabled.

- [ ] **Step 3: Verify tables exist**

```bash
# In Supabase SQL editor:
SELECT table_name FROM information_schema.tables
WHERE table_name IN ('chat_sessions', 'chat_messages');
```

Expected: 2 rows returned.

- [ ] **Step 4: Commit**

```bash
git add database/migrations/029_chat_sessions.sql
git commit -m "feat(db): add chat_sessions and chat_messages tables with RLS"
```

---

### Task 2: Add GEMINI_API_KEY to config + requirements

**Files:**
- Modify: `backend/api/core/config.py`
- Modify: `backend/api/requirements.txt`

- [ ] **Step 1: Add gemini_api_key to Config class**

In `backend/api/core/config.py`, add inside the `Config` class after the Gemini pipeline block:

```python
    # Chat
    gemini_api_key:     str   = _str("GEMINI_API_KEY", "")
    chat_history_limit: int   = _int("CHAT_HISTORY_LIMIT", 20)
    chat_context_turns: int   = _int("CHAT_CONTEXT_TURNS", 6)
```

- [ ] **Step 2: Add google-generativeai to requirements**

In `backend/api/requirements.txt`, add:

```
google-generativeai>=0.8.0
```

- [ ] **Step 3: Add GEMINI_API_KEY to .env**

In `.env`, add:
```
GEMINI_API_KEY=your_gemini_api_key_here
```

Get the key from: https://aistudio.google.com/app/apikey

- [ ] **Step 4: Install the package**

```bash
cd backend/api && pip install google-generativeai>=0.8.0
```

Expected: package installs without error.

- [ ] **Step 5: Commit**

```bash
git add backend/api/core/config.py backend/api/requirements.txt
git commit -m "feat(config): add GEMINI_API_KEY and chat config settings"
```

---

### Task 3: Gemini client — backend/api/core/gemini.py

**Files:**
- Create: `backend/api/core/gemini.py`

- [ ] **Step 1: Create the Gemini client module**

```python
# backend/api/core/gemini.py
"""
Gemini chat client for the lender chatbot.
Uses gemini-2.0-flash with JSON schema mode for structured intent extraction.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import google.generativeai as genai

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """You are a helpful AI assistant for MITRAM360, an Indian lender discovery platform.
You help users find NBFCs and banks based on their financing requirements.

Classify every user message into one of three intents:
- "filter"  — user wants to search/list lenders with specific criteria
- "compare" — user wants to compare 2–3 specific named lenders side-by-side
- "qa"      — general question about lending, finance, NBFCs, or the platform

VALID FILTER VALUES (use only these exact strings):
loan_type: MSME Loan, Personal Loan, Home Loan, Business Loan, Vehicle Loan,
           Gold Loan, Education Loan, Micro Loan, Loan Against Property,
           Working Capital, Agriculture Loan, EV Loan, Two Wheeler Loan,
           Rural Loan, Microfinance, Supply Chain Finance,
           Consumer Durable Loan, Credit Card

company_type: NBFC, Private Bank, PSU Bank, Foreign Bank,
              Cooperative Bank, NBFC-MFI, Small Finance Bank

aum_category: Micro, Small, Mid, Large
sort_by: aum_crores, established_year, employee_count, quality_score, company_name
sort_dir: asc, desc

RULES:
- Always populate "answer" with a friendly, concise reply (1–3 sentences).
- For "filter": extract all criteria mentioned. Omit fields that are not mentioned.
- For "compare": list company names in compare_names exactly as the user said them.
- For "qa": answer the question directly; leave filters and compare_names empty.
- If the user's language is Hindi or Hinglish, reply in Hinglish.
"""

# ---------------------------------------------------------------------------
# JSON response schema
# ---------------------------------------------------------------------------
_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["filter", "compare", "qa"],
        },
        "answer": {"type": "string"},
        "filters": {
            "type": "object",
            "properties": {
                "q":            {"type": "string"},
                "loan_type":    {"type": "array", "items": {"type": "string"}},
                "state":        {"type": "string"},
                "company_type": {"type": "array", "items": {"type": "string"}},
                "aum_category": {"type": "array", "items": {"type": "string"}},
                "aum_min":      {"type": "number"},
                "aum_max":      {"type": "number"},
                "pan_india":    {"type": "boolean"},
                "is_listed":    {"type": "boolean"},
                "sort_by":      {"type": "string"},
                "sort_dir":     {"type": "string", "enum": ["asc", "desc"]},
            },
        },
        "compare_names": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["intent", "answer"],
}


class GeminiChatClient:
    """Wraps google-generativeai for structured lender chat responses."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for the chat feature")
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(
            model_name="gemini-2.0-flash",
            system_instruction=_SYSTEM_PROMPT,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
                temperature=0.3,
                max_output_tokens=1024,
            ),
        )
        logger.info("GeminiChatClient initialized (model=gemini-2.0-flash)")

    def parse_response(self, message: str, history: list[dict]) -> dict:
        """
        Send a user message + conversation history to Gemini.
        Returns a parsed dict with keys: intent, answer, filters, compare_names.

        history: list of {role: "user"|"model", parts: [str]} dicts
                 (last cfg.chat_context_turns turns, already formatted)
        """
        # Build contents: history turns + current message
        contents = list(history) + [{"role": "user", "parts": [message]}]

        try:
            response = self._model.generate_content(contents)
            raw = response.text
        except Exception as exc:
            logger.error("Gemini API error: %s", exc)
            raise

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("Gemini returned non-JSON: %s | raw=%s", exc, raw[:200])
            # Fallback: treat as qa with raw text as answer
            return {"intent": "qa", "answer": raw, "filters": {}, "compare_names": []}

        # Ensure required fields exist
        data.setdefault("filters", {})
        data.setdefault("compare_names", [])
        return data


# ---------------------------------------------------------------------------
# Module-level singleton (lazy init — requires GEMINI_API_KEY in env)
# ---------------------------------------------------------------------------
_client: Optional[GeminiChatClient] = None


def get_gemini_client() -> GeminiChatClient:
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY", "")
        _client = GeminiChatClient(api_key)
    return _client
```

- [ ] **Step 2: Smoke-test the client locally**

```bash
cd backend/api
python -c "
from core.gemini import get_gemini_client
client = get_gemini_client()
result = client.parse_response('Show me NBFC lenders in Maharashtra for MSME loans', [])
print(result)
"
```

Expected output (approximately):
```python
{
  'intent': 'filter',
  'answer': 'Here are NBFC lenders in Maharashtra offering MSME Loans...',
  'filters': {'company_type': ['NBFC'], 'state': 'Maharashtra', 'loan_type': ['MSME Loan']},
  'compare_names': []
}
```

- [ ] **Step 3: Commit**

```bash
git add backend/api/core/gemini.py
git commit -m "feat(chat): add GeminiChatClient with JSON schema mode"
```

---

### Task 4: Chat router — POST /v1/chat

**Files:**
- Create: `backend/api/routers/chat.py`

- [ ] **Step 1: Create the chat router**

```python
# backend/api/routers/chat.py
"""
POST /v1/chat      — Send a message, get AI response + lender results
GET  /v1/chat/history — Load recent messages for a session
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.auth import get_current_user, AuthUser
from core.config import cfg
from core.gemini import get_gemini_client
from core.constants import VALID_LOAN_TYPES, VALID_COMPANY_TYPES, VALID_AUM_CATEGORIES
from dependencies import get_db
from limiter import limiter

router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_SORT_COLS = {
    "aum_crores", "established_year", "employee_count",
    "branch_count", "quality_score", "company_name",
}


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class HistoryMessage(BaseModel):
    role:    str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    message:    str       = Field(..., min_length=1, max_length=1000)
    session_id: str       = Field(..., description="UUID generated by frontend")
    history:    list[HistoryMessage] = Field(default_factory=list)


class LenderResult(BaseModel):
    id:                    int
    company_name:          str
    company_type:          str
    rbi_category:          Optional[str] = None
    aum_crores:            Optional[float] = None
    aum_category:          Optional[str] = None
    hq_state:              Optional[str] = None
    hq_location:           Optional[str] = None
    pan_india:             bool = False
    primary_loan_segments: list[str] = Field(default_factory=list)
    operating_states:      list[str] = Field(default_factory=list)
    website:               Optional[str] = None
    quality_score:         Optional[float] = None
    employee_count:        Optional[int] = None
    established_year:      Optional[int] = None
    is_listed:             bool = False
    phone:                 Optional[str] = None
    email:                 Optional[str] = None


class ChatResponse(BaseModel):
    answer:          str
    intent:          str
    lenders:         list[LenderResult] = Field(default_factory=list)
    applied_filters: Optional[dict] = None
    session_id:      str


class HistoryResponse(BaseModel):
    session_id: str
    messages:   list[dict]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_arr(val: Any) -> list:
    import json as _json
    if val is None:
        return []
    if isinstance(val, list):
        return val
    try:
        return _json.loads(val)
    except Exception:
        return []


def _row_to_lender(row: Any) -> LenderResult:
    d = dict(row)
    return LenderResult(
        id=d["id"],
        company_name=d["company_name"],
        company_type=d["company_type"],
        rbi_category=d.get("rbi_category"),
        aum_crores=d.get("aum_crores"),
        aum_category=d.get("aum_category"),
        hq_state=d.get("hq_state"),
        hq_location=d.get("hq_location"),
        pan_india=bool(d.get("pan_india", False)),
        primary_loan_segments=_parse_arr(d.get("primary_loan_segments")),
        operating_states=_parse_arr(d.get("operating_states")),
        website=d.get("website"),
        quality_score=d.get("quality_score"),
        employee_count=d.get("employee_count"),
        established_year=d.get("established_year"),
        is_listed=bool(d.get("is_listed", False)),
        phone=d.get("phone"),
        email=d.get("email"),
    )


async def _search_lenders(db: asyncpg.Pool, filters: dict) -> list[LenderResult]:
    """Execute a lender search using filters extracted by Gemini."""
    conditions = ["approval_status = 'approved'"]
    params: list = []
    idx = 1

    q = (filters.get("q") or "").strip()
    if q:
        q_esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(f"company_name ILIKE ${idx}")
        params.append(f"%{q_esc}%")
        idx += 1

    company_type = [t for t in (filters.get("company_type") or []) if t in VALID_COMPANY_TYPES]
    if company_type:
        conditions.append(f"company_type = ANY(${idx}::text[])")
        params.append(company_type)
        idx += 1

    state = filters.get("state")
    if state:
        conditions.append(f"(pan_india = true OR ${idx} = ANY(operating_states))")
        params.append(state)
        idx += 1

    loan_type = [t for t in (filters.get("loan_type") or []) if t in VALID_LOAN_TYPES]
    if loan_type:
        lt_conds = []
        for lt in loan_type:
            lt_conds.append(f"${idx} = ANY(primary_loan_segments)")
            params.append(lt)
            idx += 1
        conditions.append(f"({' OR '.join(lt_conds)})")

    aum_category = [t for t in (filters.get("aum_category") or []) if t in VALID_AUM_CATEGORIES]
    if aum_category:
        conditions.append(f"aum_category = ANY(${idx}::text[])")
        params.append(aum_category)
        idx += 1

    if filters.get("aum_min") is not None:
        conditions.append(f"aum_crores >= ${idx}")
        params.append(float(filters["aum_min"]))
        idx += 1

    if filters.get("aum_max") is not None:
        conditions.append(f"aum_crores <= ${idx}")
        params.append(float(filters["aum_max"]))
        idx += 1

    if filters.get("pan_india") is not None:
        conditions.append(f"pan_india = ${idx}")
        params.append(bool(filters["pan_india"]))
        idx += 1

    if filters.get("is_listed") is not None:
        conditions.append(f"is_listed = ${idx}")
        params.append(bool(filters["is_listed"]))
        idx += 1

    sort_by = filters.get("sort_by", "aum_crores")
    if sort_by not in ALLOWED_SORT_COLS:
        sort_by = "aum_crores"
    sort_dir = "DESC" if filters.get("sort_dir", "desc") == "desc" else "ASC"

    where = " AND ".join(conditions)
    async with db.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, company_name, company_type, rbi_category,
                   aum_crores, aum_category, hq_state, hq_location,
                   pan_india, primary_loan_segments, operating_states,
                   website, quality_score, employee_count,
                   established_year, is_listed, phone, email
            FROM lenders
            WHERE {where}
            ORDER BY {sort_by} {sort_dir} NULLS LAST
            LIMIT 20
            """,
            *params,
        )
    return [_row_to_lender(r) for r in rows]


async def _fetch_lenders_by_name(db: asyncpg.Pool, names: list[str]) -> list[LenderResult]:
    """Fetch lenders by approximate name match for comparison intent."""
    results: list[LenderResult] = []
    async with db.acquire() as conn:
        for name in names[:3]:  # max 3 lenders for comparison
            name_esc = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            row = await conn.fetchrow(
                """
                SELECT id, company_name, company_type, rbi_category,
                       aum_crores, aum_category, hq_state, hq_location,
                       pan_india, primary_loan_segments, operating_states,
                       website, quality_score, employee_count,
                       established_year, is_listed, phone, email
                FROM lenders
                WHERE company_name ILIKE $1 AND approval_status = 'approved'
                ORDER BY quality_score DESC NULLS LAST
                LIMIT 1
                """,
                f"%{name_esc}%",
            )
            if row:
                results.append(_row_to_lender(row))
    return results


async def _ensure_session(db: asyncpg.Pool, session_id: str, user_id: str) -> None:
    """Create session row if it doesn't exist; update last_active."""
    async with db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO chat_sessions (id, user_id)
            VALUES ($1::uuid, $2::uuid)
            ON CONFLICT (id) DO UPDATE SET last_active = now()
            """,
            session_id, user_id,
        )


async def _save_turn(
    db: asyncpg.Pool,
    session_id: str,
    user_msg: str,
    assistant_msg: str,
    intent: str,
    filters_used: Optional[dict],
) -> None:
    """Save user + assistant turn to chat_messages."""
    import json as _json
    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO chat_messages (session_id, role, content) VALUES ($1::uuid, 'user', $2)",
            session_id, user_msg,
        )
        await conn.execute(
            """
            INSERT INTO chat_messages (session_id, role, content, intent, filters_used)
            VALUES ($1::uuid, 'assistant', $2, $3, $4::jsonb)
            """,
            session_id, assistant_msg, intent,
            _json.dumps(filters_used) if filters_used else None,
        )


def _format_history_for_gemini(history: list[HistoryMessage], max_turns: int) -> list[dict]:
    """Convert last N turns to Gemini content format."""
    # Take last max_turns messages (each turn = 1 user + 1 assistant = 2 messages)
    recent = history[-(max_turns * 2):]
    return [
        {
            "role": "user" if m.role == "user" else "model",
            "parts": [m.content],
        }
        for m in recent
    ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", response_model=ChatResponse)
@limiter.limit("10/minute")
async def chat(
    request: Request,
    body: ChatRequest,
    db: asyncpg.Pool = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    user_id = user.get("sub", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid user token")

    # Validate session_id is a valid UUID
    try:
        uuid.UUID(body.session_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="session_id must be a valid UUID")

    # 1. Ensure session exists in DB
    try:
        await _ensure_session(db, body.session_id, user_id)
    except Exception as exc:
        logger.error("chat: session upsert failed: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    # 2. Call Gemini
    gemini_history = _format_history_for_gemini(body.history, cfg.chat_context_turns)
    try:
        client = get_gemini_client()
        parsed = client.parse_response(body.message, gemini_history)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.error("chat: Gemini error: %s", exc)
        raise HTTPException(status_code=503, detail="AI service temporarily unavailable")

    intent  = parsed.get("intent", "qa")
    answer  = parsed.get("answer", "")
    filters = parsed.get("filters") or {}
    compare_names = parsed.get("compare_names") or []

    # 3. Execute intent
    lenders: list[LenderResult] = []
    applied_filters: Optional[dict] = None

    try:
        if intent == "filter" and filters:
            lenders = await _search_lenders(db, filters)
            applied_filters = filters
        elif intent == "compare" and compare_names:
            lenders = await _fetch_lenders_by_name(db, compare_names)
    except Exception as exc:
        logger.error("chat: DB query failed: %s", exc)
        raise HTTPException(status_code=503, detail="Search temporarily unavailable")

    # 4. Save turn to DB (non-blocking best-effort)
    try:
        await _save_turn(db, body.session_id, body.message, answer, intent, applied_filters)
    except Exception as exc:
        logger.warning("chat: failed to save turn: %s", exc)  # non-fatal

    return ChatResponse(
        answer=answer,
        intent=intent,
        lenders=lenders,
        applied_filters=applied_filters,
        session_id=body.session_id,
    )


@router.get("/history", response_model=HistoryResponse)
@limiter.limit("30/minute")
async def get_history(
    request: Request,
    session_id: Optional[str] = Query(None, description="UUID of session; omit for most recent"),
    db: asyncpg.Pool = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    user_id = user.get("sub", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid user token")

    try:
        async with db.acquire() as conn:
            if session_id:
                try:
                    uuid.UUID(session_id)
                except ValueError:
                    raise HTTPException(status_code=422, detail="session_id must be a valid UUID")
                # Verify the session belongs to this user
                owner = await conn.fetchval(
                    "SELECT user_id FROM chat_sessions WHERE id = $1::uuid",
                    session_id,
                )
                if str(owner) != user_id:
                    raise HTTPException(status_code=404, detail="Session not found")
                sid = session_id
            else:
                # Most recent session for this user
                sid = await conn.fetchval(
                    "SELECT id FROM chat_sessions WHERE user_id = $1::uuid ORDER BY last_active DESC LIMIT 1",
                    user_id,
                )
                if not sid:
                    return HistoryResponse(session_id="", messages=[])

            rows = await conn.fetch(
                """
                SELECT role, content, intent, filters_used, created_at
                FROM chat_messages
                WHERE session_id = $1::uuid
                ORDER BY created_at ASC
                LIMIT $2
                """,
                str(sid), cfg.chat_history_limit,
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_history: DB error: %s", exc)
        raise HTTPException(status_code=503, detail="History service temporarily unavailable")

    messages = [
        {
            "role":         r["role"],
            "content":      r["content"],
            "intent":       r["intent"],
            "filters_used": r["filters_used"],
            "created_at":   r["created_at"].isoformat(),
        }
        for r in rows
    ]
    return HistoryResponse(session_id=str(sid), messages=messages)
```

- [ ] **Step 2: Commit**

```bash
git add backend/api/routers/chat.py
git commit -m "feat(chat): add POST /v1/chat and GET /v1/chat/history endpoints"
```

---

### Task 5: Wire chat router into main.py + add CORS method

**Files:**
- Modify: `backend/api/main.py`

- [ ] **Step 1: Read current main.py imports section (lines 48–50)**

Current:
```python
from routers import lenders
from routers import admin as admin_router
```

Add after:
```python
from routers import lenders
from routers import admin as admin_router
from routers import chat as chat_router
```

- [ ] **Step 2: Add router include after existing routers (lines 198–199)**

Current:
```python
app.include_router(lenders.router,      prefix=f"{_V1}/lenders",  tags=["Lenders"])
app.include_router(admin_router.router, prefix=f"{_V1}/admin",    tags=["Admin"])
```

Add:
```python
app.include_router(lenders.router,      prefix=f"{_V1}/lenders",  tags=["Lenders"])
app.include_router(admin_router.router, prefix=f"{_V1}/admin",    tags=["Admin"])
app.include_router(chat_router.router,  prefix=f"{_V1}/chat",     tags=["Chat"])
```

- [ ] **Step 3: Update CORS to allow POST (already present — verify)**

In `main.py` around line 189, confirm:
```python
allow_methods=["GET", "POST", "OPTIONS"],
```

This already includes POST, so no change needed.

- [ ] **Step 4: Start the server and verify the route is registered**

```bash
cd backend && uvicorn api.main:app --reload --port 8000
```

Open: `http://localhost:8000/docs`

Expected: `/v1/chat` POST and `/v1/chat/history` GET appear in the Swagger UI.

- [ ] **Step 5: Commit**

```bash
git add backend/api/main.py
git commit -m "feat(chat): wire chat router into FastAPI app at /v1/chat"
```

---

## Phase 2 — Frontend Chat Panel

---

### Task 6: ChatPanel.tsx component

**Files:**
- Create: `frontend/app/components/ChatPanel.tsx`

- [ ] **Step 1: Create ChatPanel.tsx**

```tsx
'use client'

/**
 * ChatPanel.tsx
 * ==============
 * Slide-in chat panel (right side, w-80).
 * - Sends messages to POST /v1/chat
 * - Renders thread: user bubbles (right/blue), bot bubbles (left/gray)
 * - On intent=filter: calls onFiltersApplied to sync the lender grid
 * - On intent=compare: renders a comparison table inside the bot bubble
 * - Loads history from GET /v1/chat/history on open
 * - Generates a session UUID per browser session (resets on "New chat")
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { X, Send, RotateCcw, Bot } from 'lucide-react'
import { MultiFilters, DEFAULT_FILTERS } from './SearchFilter'

// ─── Types ────────────────────────────────────────────────────

interface LenderResult {
  id:                    number
  company_name:          string
  company_type:          string
  aum_crores:            number | null
  aum_category:          string | null
  hq_state:              string | null
  hq_location:           string | null
  pan_india:             boolean
  primary_loan_segments: string[]
  operating_states:      string[]
  website:               string | null
  quality_score:         number | null
  employee_count:        number | null
  established_year:      number | null
  is_listed:             boolean
  phone:                 string | null
  email:                 string | null
}

interface ApiFilters {
  q?:            string
  loan_type?:    string[]
  state?:        string
  company_type?: string[]
  aum_category?: string[]
  aum_min?:      number
  aum_max?:      number
  pan_india?:    boolean
  is_listed?:    boolean
  sort_by?:      string
  sort_dir?:     'asc' | 'desc'
}

interface ChatMessage {
  role:    'user' | 'assistant'
  content: string
  intent?: 'filter' | 'compare' | 'qa'
  lenders?: LenderResult[]
}

interface ChatPanelProps {
  open:             boolean
  onClose:          () => void
  onFiltersApplied: (filters: MultiFilters) => void
  apiUrl:           string
  user:             { access_token?: string } | null
}

// ─── Helpers ──────────────────────────────────────────────────

function generateUUID(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

/** Convert API filter shape → MultiFilters for the dashboard */
function apiFiltersToMultiFilters(f: ApiFilters): MultiFilters {
  let listingStatus = 'All'
  if (f.is_listed === true)  listingStatus = 'Listed Only'
  if (f.is_listed === false) listingStatus = 'Unlisted Only'

  return {
    search:               f.q ?? '',
    loanType:             f.loan_type ?? [],
    state:                f.state ?? 'All States',
    ticketSize:           f.aum_category ?? [],
    companyType:          f.company_type ?? [],
    listingStatus,
    establishedYearRange: 'All Years',
    sortField:            (f.sort_by as MultiFilters['sortField']) ?? '',
    sortDirection:        f.sort_dir ?? 'desc',
  }
}

// ─── Sub-components ───────────────────────────────────────────

function CompareTable({ lenders }: { lenders: LenderResult[] }) {
  if (lenders.length < 2) return null
  const fields: { label: string; key: keyof LenderResult }[] = [
    { label: 'Type',        key: 'company_type' },
    { label: 'AUM (Cr)',    key: 'aum_crores' },
    { label: 'HQ',          key: 'hq_state' },
    { label: 'Est. Year',   key: 'established_year' },
    { label: 'Employees',   key: 'employee_count' },
    { label: 'Pan India',   key: 'pan_india' },
    { label: 'Listed',      key: 'is_listed' },
  ]
  return (
    <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200 text-xs">
      <table className="min-w-full">
        <thead>
          <tr className="bg-gray-50">
            <th className="px-2 py-1.5 text-left font-semibold text-gray-500">Field</th>
            {lenders.map(l => (
              <th key={l.id} className="px-2 py-1.5 text-left font-semibold text-gray-800 max-w-[100px] truncate">
                {l.company_name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {fields.map(f => (
            <tr key={f.key}>
              <td className="px-2 py-1 text-gray-500">{f.label}</td>
              {lenders.map(l => {
                const val = l[f.key]
                const display = val === null || val === undefined
                  ? '—'
                  : typeof val === 'boolean'
                  ? (val ? 'Yes' : 'No')
                  : typeof val === 'number' && f.key === 'aum_crores'
                  ? `₹${val.toLocaleString('en-IN')}`
                  : String(val)
                return (
                  <td key={l.id} className="px-2 py-1 text-gray-700">{display}</td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function BotBubble({ msg }: { msg: ChatMessage }) {
  return (
    <div className="flex gap-2 items-start">
      <div className="w-7 h-7 rounded-full bg-[#3B5CCC] flex items-center justify-center flex-shrink-0 mt-0.5">
        <Bot className="w-4 h-4 text-white" />
      </div>
      <div className="max-w-[85%]">
        <div className="bg-gray-100 rounded-2xl rounded-tl-none px-3 py-2 text-sm text-gray-800 whitespace-pre-wrap">
          {msg.content}
        </div>
        {msg.intent === 'compare' && msg.lenders && msg.lenders.length >= 2 && (
          <CompareTable lenders={msg.lenders} />
        )}
        {msg.intent === 'filter' && msg.lenders !== undefined && (
          <div className="mt-1.5 text-xs text-[#3B5CCC] font-medium">
            Showing {msg.lenders.length} lender{msg.lenders.length !== 1 ? 's' : ''} in grid
          </div>
        )}
      </div>
    </div>
  )
}

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] bg-[#3B5CCC] text-white rounded-2xl rounded-tr-none px-3 py-2 text-sm whitespace-pre-wrap">
        {content}
      </div>
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className="flex gap-2 items-start">
      <div className="w-7 h-7 rounded-full bg-[#3B5CCC] flex items-center justify-center flex-shrink-0">
        <Bot className="w-4 h-4 text-white" />
      </div>
      <div className="bg-gray-100 rounded-2xl rounded-tl-none px-3 py-2">
        <span className="flex gap-1">
          {[0, 1, 2].map(i => (
            <span
              key={i}
              className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
              style={{ animationDelay: `${i * 0.15}s` }}
            />
          ))}
        </span>
      </div>
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────

export function ChatPanel({ open, onClose, onFiltersApplied, apiUrl, user }: ChatPanelProps) {
  const [messages,   setMessages]   = useState<ChatMessage[]>([])
  const [input,      setInput]      = useState('')
  const [loading,    setLoading]    = useState(false)
  const [sessionId,  setSessionId]  = useState<string>(() => generateUUID())
  const [historyLoaded, setHistoryLoaded] = useState(false)
  const bottomRef  = useRef<HTMLDivElement>(null)
  const inputRef   = useRef<HTMLTextAreaElement>(null)

  // Load history when panel opens for the first time
  useEffect(() => {
    if (!open || historyLoaded || !user?.access_token) return
    loadHistory()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  // Focus input when panel opens
  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  const loadHistory = useCallback(async () => {
    if (!user?.access_token) return
    try {
      const res = await fetch(`${apiUrl}/v1/chat/history`, {
        headers: { Authorization: `Bearer ${user.access_token}` },
      })
      if (!res.ok) return
      const data = await res.json()
      if (data.session_id) setSessionId(data.session_id)
      if (data.messages?.length) {
        setMessages(
          data.messages.map((m: { role: string; content: string; intent?: string }) => ({
            role:    m.role as 'user' | 'assistant',
            content: m.content,
            intent:  m.intent as ChatMessage['intent'],
          }))
        )
      }
      setHistoryLoaded(true)
    } catch {
      // Non-fatal — panel still works without history
    }
  }, [apiUrl, user?.access_token])

  const sendMessage = useCallback(async () => {
    const text = input.trim()
    if (!text || loading || !user?.access_token) return

    const userMsg: ChatMessage = { role: 'user', content: text }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)

    // Build history payload from current messages (last 12 = 6 turns)
    const historyPayload = messages.slice(-12).map(m => ({
      role:    m.role,
      content: m.content,
    }))

    try {
      const res = await fetch(`${apiUrl}/v1/chat`, {
        method:  'POST',
        headers: {
          'Content-Type':  'application/json',
          'Authorization': `Bearer ${user.access_token}`,
        },
        body: JSON.stringify({
          message:    text,
          session_id: sessionId,
          history:    historyPayload,
        }),
      })

      if (!res.ok) {
        const err = await res.text()
        throw new Error(`API ${res.status}: ${err}`)
      }

      const data = await res.json()
      const botMsg: ChatMessage = {
        role:    'assistant',
        content: data.answer,
        intent:  data.intent,
        lenders: data.lenders,
      }
      setMessages(prev => [...prev, botMsg])

      // Sync grid if filter intent returned results
      if (data.intent === 'filter' && data.applied_filters) {
        onFiltersApplied(apiFiltersToMultiFilters(data.applied_filters))
      }
    } catch (err) {
      const errorMsg: ChatMessage = {
        role:    'assistant',
        content: 'Sorry, I ran into an error. Please try again.',
        intent:  'qa',
      }
      setMessages(prev => [...prev, errorMsg])
    } finally {
      setLoading(false)
    }
  }, [input, loading, user?.access_token, messages, sessionId, apiUrl, onFiltersApplied])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const startNewChat = () => {
    setMessages([])
    setSessionId(generateUUID())
    setHistoryLoaded(false)
    inputRef.current?.focus()
  }

  if (!open) return null

  return (
    <>
      {/* Backdrop for mobile */}
      <div
        className="fixed inset-0 bg-black/20 z-20 md:hidden"
        onClick={onClose}
      />

      {/* Panel */}
      <aside className="
        fixed right-0 top-0 h-full w-80 z-30
        bg-white border-l border-gray-200 shadow-xl
        flex flex-col
        md:relative md:shadow-none md:z-auto
      ">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 flex-shrink-0">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-full bg-[#3B5CCC] flex items-center justify-center">
              <Bot className="w-4 h-4 text-white" />
            </div>
            <span className="font-semibold text-gray-800 text-sm">Ask AI</span>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={startNewChat}
              title="New chat"
              className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
            >
              <RotateCcw className="w-4 h-4" />
            </button>
            <button
              onClick={onClose}
              title="Close"
              className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Message thread */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.length === 0 && !loading && (
            <div className="text-center py-8">
              <p className="text-gray-400 text-sm">Ask me about lenders, loan types, or compare specific lenders.</p>
              <div className="mt-4 space-y-2">
                {[
                  'Show NBFCs in Maharashtra for MSME loans',
                  'Compare Bajaj Finance vs Muthoot Finance',
                  'What is an NBFC-MFI?',
                ].map(s => (
                  <button
                    key={s}
                    onClick={() => { setInput(s); inputRef.current?.focus() }}
                    className="block w-full text-left text-xs text-[#3B5CCC] bg-blue-50 hover:bg-blue-100 px-3 py-2 rounded-lg transition-colors"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, i) =>
            msg.role === 'user'
              ? <UserBubble key={i} content={msg.content} />
              : <BotBubble key={i} msg={msg} />
          )}

          {loading && <TypingIndicator />}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="border-t border-gray-200 p-3 flex-shrink-0">
          <div className="flex gap-2 items-end">
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about lenders…"
              rows={1}
              className="
                flex-1 resize-none rounded-xl border border-gray-200
                px-3 py-2 text-sm text-gray-800 placeholder-gray-400
                focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/30 focus:border-[#3B5CCC]
                max-h-28 overflow-y-auto
              "
              style={{ lineHeight: '1.4' }}
            />
            <button
              onClick={sendMessage}
              disabled={!input.trim() || loading}
              className="
                p-2 rounded-xl bg-[#3B5CCC] text-white
                hover:bg-[#2d4aa8] transition-colors
                disabled:opacity-40 disabled:cursor-not-allowed flex-shrink-0
              "
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
          <p className="text-[10px] text-gray-400 mt-1.5 text-center">
            Enter to send · Shift+Enter for new line
          </p>
        </div>
      </aside>
    </>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/app/components/ChatPanel.tsx
git commit -m "feat(chat): add ChatPanel component with thread, compare table, history loading"
```

---

### Task 7: Dashboard integration

**Files:**
- Modify: `frontend/app/dashboard/page.tsx`

- [ ] **Step 1: Add ChatPanel import at top of dashboard/page.tsx**

After the existing imports, add:
```tsx
import { ChatPanel }      from '../components/ChatPanel'
```

- [ ] **Step 2: Add chatOpen state inside the Dashboard component**

After the existing state declarations (around line 165):
```tsx
const [chatOpen, setChatOpen] = useState(false)
```

- [ ] **Step 3: Add "Ask AI" button to the mobile top bar**

Find the existing mobile top bar button block (around line 274):
```tsx
<div className="flex items-center justify-between mb-6 md:hidden">
  <button
    type="button"
    onClick={() => setSidebarOpen(true)}
    ...
  >
```

Replace with:
```tsx
<div className="flex items-center justify-between mb-6 md:hidden">
  <button
    type="button"
    onClick={() => setSidebarOpen(true)}
    className="inline-flex items-center gap-2 px-4 py-2
               bg-white border border-gray-200 rounded-xl
               text-sm font-medium text-gray-700
               hover:border-gray-300 transition-colors shadow-sm"
  >
    <SlidersHorizontal className="w-4 h-4 text-[#3B5CCC]" />
    Filters
  </button>
  <button
    type="button"
    onClick={() => setChatOpen(p => !p)}
    className="inline-flex items-center gap-2 px-4 py-2
               bg-[#3B5CCC] text-white rounded-xl
               text-sm font-medium
               hover:bg-[#2d4aa8] transition-colors shadow-sm"
  >
    Ask AI
  </button>
  <span className="text-sm text-gray-600">
    <span className="font-bold text-[#3B5CCC]">
      {totalCount.toLocaleString('en-IN')}
    </span>
    {' '}lender{totalCount !== 1 ? 's' : ''}
  </span>
</div>
```

- [ ] **Step 4: Add desktop "Ask AI" button and ChatPanel to the flex layout**

Find the outer flex container (around line 254):
```tsx
<div className="flex flex-1 min-h-0">
  <SearchFilter ... />
  <main ...>
```

Replace with:
```tsx
<div className="flex flex-1 min-h-0">
  <SearchFilter
    filters={filters}
    onFilterChange={handleFilterChange}
    resultsCount={totalCount}
    loanTypes={LOAN_TYPES}
    states={INDIA_STATES}
    ticketSizes={TICKET_SIZES}
    companyTypes={COMPANY_TYPES}
    listingStatus={LISTING_OPTIONS}
    yearRanges={[...YEAR_RANGE_OPTIONS]}
    sidebar
    sidebarOpen={sidebarOpen}
    onClose={() => setSidebarOpen(false)}
  />

  <main className="flex-1 min-w-0 py-8 px-4 sm:px-6 lg:px-8
                   bg-gradient-to-b from-gray-50 to-white">

    {/* Desktop Ask AI button */}
    <div className="hidden md:flex justify-end mb-4">
      <button
        onClick={() => setChatOpen(p => !p)}
        className="inline-flex items-center gap-2 px-4 py-2
                   bg-[#3B5CCC] text-white rounded-xl text-sm font-medium
                   hover:bg-[#2d4aa8] transition-colors shadow-sm"
      >
        {chatOpen ? 'Close AI' : 'Ask AI'}
      </button>
    </div>
```

Close the `<main>` tag before adding ChatPanel, then add ChatPanel after `</main>`:

```tsx
  </main>

  <ChatPanel
    open={chatOpen}
    onClose={() => setChatOpen(false)}
    onFiltersApplied={(f) => {
      setFilters(f)
      setPage(0)
      isFirstLoad.current = false
    }}
    apiUrl={API_URL}
    user={user}
  />
</div>
```

- [ ] **Step 5: Verify TypeScript compiles**

```bash
cd frontend && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 6: Start dev server and test golden path**

```bash
cd frontend && npm run dev
```

1. Log in → Dashboard
2. Click "Ask AI" → panel slides in
3. Type: "Show NBFC lenders in Maharashtra for MSME loans"
4. Expected: bot replies, lender grid updates with filtered results
5. Type: "Compare Bajaj Finance vs Muthoot Finance"
6. Expected: comparison table appears in the chat bubble
7. Type: "What is an NBFC-MFI?"
8. Expected: plain text answer, grid unchanged

- [ ] **Step 7: Commit**

```bash
git add frontend/app/dashboard/page.tsx
git commit -m "feat(chat): integrate ChatPanel into dashboard with grid sync"
```

---

## Phase 3 — History Persistence + Polish

---

### Task 8: Verify end-to-end history persistence

**Files:**
- No new files — verifies Tasks 1–7 work together

- [ ] **Step 1: Send 3 messages in the chat panel**

With the dev server running, send:
1. "Show gold loan lenders"
2. "Filter by PSU Bank"
3. "What is AUM?"

- [ ] **Step 2: Verify messages are saved in Supabase**

```sql
-- Run in Supabase SQL editor
SELECT cm.role, cm.content, cm.intent, cm.created_at
FROM chat_messages cm
JOIN chat_sessions cs ON cm.session_id = cs.id
ORDER BY cm.created_at DESC
LIMIT 10;
```

Expected: 6 rows (3 user + 3 assistant), correct roles and intents.

- [ ] **Step 3: Refresh the page and reopen the chat panel**

Expected: previous messages load from history. The session_id from the API matches the last session.

- [ ] **Step 4: Test "New chat" button**

Click the reset icon in the chat panel header.
Expected: messages clear, new session UUID generated. Old history still in Supabase.

- [ ] **Step 5: Commit**

```bash
git commit --allow-empty -m "test(chat): verified end-to-end history persistence"
```

---

### Task 9: Environment variable wiring for production

**Files:**
- Modify: `frontend/.env.local` (already exists — verify NEXT_PUBLIC_API_URL is set)

- [ ] **Step 1: Verify frontend env**

```bash
cat frontend/.env.local | grep NEXT_PUBLIC_API_URL
```

Expected: `NEXT_PUBLIC_API_URL=https://your-railway-url.railway.app` (or localhost for dev).

- [ ] **Step 2: Add GEMINI_API_KEY to Railway environment**

In Railway dashboard → your backend service → Variables:
```
GEMINI_API_KEY=your_gemini_api_key_here
```

- [ ] **Step 3: Add GEMINI_API_KEY to .env.example**

In `.env.example`, add:
```
GEMINI_API_KEY=           # Get from https://aistudio.google.com/app/apikey
```

- [ ] **Step 4: Deploy backend**

```bash
git push origin main
```

Railway auto-deploys. Verify in Railway logs: `GeminiChatClient initialized`.

- [ ] **Step 5: Test production chat endpoint**

```bash
curl -X POST https://your-railway-url.railway.app/v1/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_JWT" \
  -d '{"message":"Show NBFC lenders in Maharashtra","session_id":"00000000-0000-4000-a000-000000000001","history":[]}'
```

Expected: JSON response with `intent`, `answer`, `lenders`, `applied_filters`.

- [ ] **Step 6: Commit**

```bash
git add .env.example
git commit -m "chore: add GEMINI_API_KEY to .env.example for production setup"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] POST /v1/chat endpoint — Task 4
- [x] GET /v1/chat/history endpoint — Task 4
- [x] Gemini JSON schema mode — Task 3
- [x] intent=filter → search lenders — Task 4 (`_search_lenders`)
- [x] intent=compare → fetch by name — Task 4 (`_fetch_lenders_by_name`)
- [x] intent=qa → answer only — Task 4
- [x] Grid sync on filter — Task 7 (`onFiltersApplied`)
- [x] DB migration with RLS — Task 1
- [x] ChatPanel slide-in panel — Task 6
- [x] Compare table in chat bubble — Task 6 (`CompareTable`)
- [x] History loading on panel open — Task 6 (`loadHistory`)
- [x] New chat button / session reset — Task 6 (`startNewChat`)
- [x] Rate limiting (10/min) — Task 4 (`@limiter.limit`)
- [x] Auth guard (JWT required) — Task 4 (`Depends(get_current_user)`)
- [x] GEMINI_API_KEY config — Task 2

**Type consistency:**
- `LenderResult` defined in `chat.py` — matches fields used in `ChatPanel.tsx` ✓
- `ChatResponse.applied_filters` is `Optional[dict]` — `apiFiltersToMultiFilters` accepts it with `??` defaults ✓
- `MultiFilters` imported from `SearchFilter.tsx` in both `ChatPanel.tsx` and `dashboard/page.tsx` ✓
- `_format_history_for_gemini` uses `"model"` for Gemini role (not `"assistant"`) — Gemini API requires `"model"` ✓
