# Lender Chatbot — Design Spec
**Date:** 2026-04-16  
**Status:** Approved  
**Stack:** FastAPI + Gemini + Next.js + Supabase

---

## 1. Overview

A conversational AI chatbot embedded as a slide-in side panel on the MITRAM360 dashboard. Users (borrowers and analysts) type natural language queries; the bot filters lenders, answers domain questions, and compares specific lenders. Chat results sync the main lender grid. Conversation history is persisted per user in Supabase.

---

## 2. Architecture

```
User types message
       ↓
ChatPanel.tsx (side panel, right side of dashboard)
       ↓  POST /v1/chat  {message, session_id, history[-6 turns]}
FastAPI routers/chat.py
       ↓
Gemini gemini-2.0-flash (JSON schema mode)
       ↓ {intent, answer, filters, compare_ids}
       ↓
 intent=filter  → reuse search query logic → return lenders[]
 intent=compare → fetch lender by ID for each compare_id
 intent=qa      → return answer only, no DB query
       ↓
Save turn to chat_messages (Supabase)
       ↓
Response: {answer, lenders[], applied_filters}
       ↓
Frontend: render answer + sync filter state + grid re-fetches
```

---

## 3. Database

Migration: `database/migrations/029_chat_sessions.sql`

```sql
CREATE TABLE chat_sessions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  created_at   TIMESTAMPTZ DEFAULT now(),
  last_active  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE chat_messages (
  id           BIGSERIAL PRIMARY KEY,
  session_id   UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role         TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content      TEXT NOT NULL,
  intent       TEXT,
  filters_used JSONB,
  created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ON chat_messages(session_id, created_at);
CREATE INDEX ON chat_sessions(user_id, last_active DESC);
```

**RLS:** Both tables locked to `auth.uid() = user_id`. Users can only read/write their own history.

**History loading:** On panel open, fetch last 20 messages for user's most recent session. Last 6 turns sent to Gemini as context.

---

## 4. Backend

### 4.1 New files
- `backend/api/routers/chat.py` — chat endpoint
- `backend/api/core/gemini.py` — Gemini client + system prompt

### 4.2 Endpoint

```
POST /v1/chat
Auth: Supabase JWT (required)
Rate limit: 10/minute per user

Request body:
{
  "message":    string,
  "session_id": uuid,
  "history":    [{role: "user"|"assistant", content: string}]  // last 6 turns
}

Response:
{
  "answer":          string,
  "intent":          "filter" | "compare" | "qa",
  "lenders":         LenderSummary[],   // empty for qa intent
  "applied_filters": FilterParams | null
}
```

### 4.3 Gemini integration

Model: `gemini-2.0-flash`  
Mode: `response_mime_type="application/json"` + `response_schema` (enforced JSON, no regex parsing)

Gemini output schema:
```json
{
  "intent":      "filter | compare | qa",
  "answer":      "natural language reply — always present",
  "filters": {
    "q": "string",
    "loan_type": ["string"],
    "state": "string",
    "company_type": ["string"],
    "aum_category": ["string"],
    "aum_min": "number",
    "aum_max": "number",
    "pan_india": "boolean",
    "is_listed": "boolean",
    "sort_by": "string",
    "sort_dir": "asc | desc"
  },
  "compare_ids": ["int"]
}
```

System prompt covers:
- Domain context: Indian NBFCs, banks, loan types, AUM categories
- Valid enum values for all filter fields (from `core/constants.py`)
- Intent classification rules
- Instruction to always produce a friendly `answer` in Hindi-English mix if appropriate

### 4.4 History endpoint

```
GET /v1/chat/history
Auth: Supabase JWT (required)
Query params: session_id (uuid, optional — if omitted, returns most recent session)

Response:
{
  "session_id": uuid,
  "messages": [{role, content, intent, filters_used, created_at}]  // last 20
}
```

### 4.5 Router wired in main.py
```python
app.include_router(chat_router.router, prefix="/v1/chat", tags=["Chat"])
```

---

## 5. Frontend

### 5.1 New files
- `frontend/app/components/ChatPanel.tsx`

### 5.2 Dashboard changes (`frontend/app/dashboard/page.tsx`)
- Add `chatOpen` state (default: false)
- Add "Ask AI" button to top bar (next to mobile Filters button)
- Render `<ChatPanel>` with props:
  - `open: boolean`
  - `onFiltersApplied: (filters: MultiFilters) => void`
  - `apiUrl: string`
  - `user: User`

### 5.3 ChatPanel layout

```
┌──────────────────────────────────────────────────────┐
│ [Filter Sidebar] │      [Lender Grid]       │ [Chat] │
│                  │                          │ panel  │
│  Loan Type ✓     │  Card  Card  Card        │ slides │
│  State ✓         │  Card  Card  Card        │  in    │
│  Company Type    │  Card  Card  Card        │        │
└──────────────────┴──────────────────────────┴────────┘
```

Panel width: `w-80` (320px), fixed right, full height, scrollable message thread.

### 5.4 Grid sync flow

```
ChatPanel receives response {applied_filters, lenders}
  → calls onFiltersApplied(applied_filters)
  → Dashboard.handleFilterChange() updates filters state
  → setPage(0)
  → fetchLenders() re-runs → grid updates normally
```

No duplicate data — chat response shows lenders inline in the chat thread; grid re-fetches via normal path so pagination and sort stay consistent.

### 5.5 Chat UX details
- Message thread: user bubbles (right, blue), bot bubbles (left, gray)
- Loading: animated dots while awaiting response
- On `intent=filter`: bot message shows answer + "Showing N lenders in grid" confirmation chip
- On `intent=compare`: bot renders a compact side-by-side comparison table inside the chat bubble
- On `intent=qa`: plain text answer, no grid change
- "New chat" button resets session_id (frontend generates new UUID), grid unchanged
- History loaded on panel open: last 20 messages from Supabase via `/v1/chat/history?session_id=`

---

## 6. Phases

### Phase 1 — Backend foundation
- Migration `029_chat_sessions.sql`
- `backend/api/core/gemini.py` (Gemini client + prompt)
- `backend/api/routers/chat.py` (POST /v1/chat + GET /v1/chat/history)
- Wire router into `main.py`
- Add `GEMINI_API_KEY` to env config

### Phase 2 — Frontend chat panel
- `ChatPanel.tsx` (message thread, input, loading state)
- Dashboard integration (chatOpen state, Ask AI button, onFiltersApplied wiring)
- Grid sync on filter intent

### Phase 3 — History persistence + compare
- Save/load turns from Supabase
- Lender comparison table inside chat bubble
- Session management (new chat button)

---

## 7. Out of Scope
- Voice input
- File/document upload
- Push notifications for saved searches
- Multi-language UI (Gemini may respond in Hinglish naturally)
