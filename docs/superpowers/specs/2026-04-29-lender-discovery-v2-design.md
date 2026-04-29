# Lender Discovery v2 — Design Spec
**Date:** 2026-04-29  
**Status:** Approved  

## Problem

1. Lenders that exist in the RBI registry but were never scraped/enriched return zero results — platform looks broken.
2. Search only matches exact/ILIKE names — a slight spelling difference returns nothing.
3. No mechanism for users to flag missing lenders.

## Goals

- Surface RBI-registered NBFCs as stub cards when a search returns 0 curated results.
- Add trigram fuzzy matching so near-name-matches are found.
- Let users request enrichment of a stub lender via a one-click button.
- Feed requests into an admin queue for follow-up scraping.

## Non-Goals

- Bulk-inserting shell NBFCs into the `lenders` table (pollutes curated data).
- Showing rates or policies on stub cards.
- Automated enrichment of requested lenders (manual queue only, for now).

---

## Architecture

### Search Flow (modified)

```
GET /v1/lenders/search?q=XYZ&...filters

Step 1: Exact + ILIKE search on lenders (current behaviour)
Step 2: If results < 3 AND q present → trigram similarity fallback on lenders
Step 3: If results still = 0 AND q present → query rbi_registry by similarity
Step 4: Return { results: LenderSummary[], stubs: RegistryStub[] }
```

Stubs are only returned when `results` is empty and a text query is present. Filters (state, type, etc.) do NOT apply to stubs — they are always shown unfiltered as a fallback.

---

## Database

### Migration 034 — pg_trgm + indexes

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_lenders_name_trgm
  ON lenders USING GiST (company_name gist_trgm_ops);
```

### Migration 035 — rbi_registry table

```sql
CREATE TABLE IF NOT EXISTS rbi_registry (
  id                    SERIAL PRIMARY KEY,
  company_name          TEXT NOT NULL,
  cin                   TEXT,
  rbi_registration_number TEXT,
  regulatory_tier       TEXT,   -- 'ND-UL' | 'ND-ML' | 'ND-BL'
  hq_state              TEXT,
  established_year      INTEGER,
  created_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rbi_registry_name_trgm
  ON rbi_registry USING GiST (company_name gist_trgm_ops);
```

### Migration 036 — lender_requests table

```sql
CREATE TABLE IF NOT EXISTS lender_requests (
  id            SERIAL PRIMARY KEY,
  company_name  TEXT NOT NULL,
  cin           TEXT,
  requested_by  TEXT,        -- email or 'anonymous'
  notes         TEXT,
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'in_progress', 'done', 'rejected'))
);
```

---

## Backend

### `routers/lenders.py` changes

**`search_lenders`** — modified return type:
```python
class LenderSearchResponse(BaseModel):
    total: int
    results: List[LenderSummary]
    stubs: List[RegistryStub] = []   # new field
```

New `RegistryStub` model:
```python
class RegistryStub(BaseModel):
    id: int
    company_name: str
    cin: Optional[str]
    rbi_registration_number: Optional[str]
    regulatory_tier: Optional[str]
    hq_state: Optional[str]
    established_year: Optional[int]
```

Fuzzy search logic (Step 2):
```sql
SELECT ... FROM lenders
WHERE approval_status = 'approved'
  AND similarity(company_name, $query) > 0.25
ORDER BY similarity(company_name, $query) DESC
LIMIT 10
```

Registry stub lookup (Step 3, only when results=0):
```sql
SELECT * FROM rbi_registry
WHERE similarity(company_name, $query) > 0.3
ORDER BY similarity(company_name, $query) DESC
LIMIT 5
```

**New endpoint:** `POST /v1/lenders/request`
```python
class LenderRequestBody(BaseModel):
    company_name: str
    cin: Optional[str] = None
    requested_by: Optional[str] = None  # email
    notes: Optional[str] = None
```
- Inserts into `lender_requests`
- Returns `{ "status": "submitted" }`
- No auth required (public endpoint)

### `routers/admin.py` changes

**New endpoint:** `GET /admin/lender-requests`
- Paginated list of `lender_requests` ordered by `created_at DESC`
- Returns `{ total, page, limit, results: LenderRequestRow[] }`

**New endpoint:** `POST /admin/lender-requests/{id}/update-status`
- Body: `{ status: 'in_progress' | 'done' | 'rejected' }`

---

## Data Seeding

**`backend/seed_rbi_registry.py`** — one-time script:
- Reads `data/input/rbi_nbfc_list.xlsx` (9,188 rows)
- Parses: NBFC Name, Classification, CIN, Layer
- Decodes CIN → hq_state, established_year (reuses logic from sync_nbfc_csv.py)
- Skips rows where `company_name` already exists in `lenders` table (no duplicates)
- Upserts into `rbi_registry` on `company_name` conflict
- Run once locally: `python backend/seed_rbi_registry.py`

---

## Frontend

### New `StubCard.tsx` component

Visual design:
- Grey background (`bg-gray-50`), dashed border (`border-dashed border-gray-300`)
- "Not yet enriched" badge (grey pill)
- Shows: company name, RBI tier badge, state, registration number
- "Request full profile" button → `POST /v1/lenders/request` → success toast

### `dashboard/page.tsx` changes

- `LenderSearchResponse` type gets `stubs: RegistryStub[]`
- After results grid, if `stubs.length > 0` and `results.length === 0`:
  ```
  ── Also registered with RBI (data not yet available) ──
  [StubCard] [StubCard] ...
  ```
- StubCards are not counted in the results total

### `admin/page.tsx` changes

- New `'requests'` tab alongside lenders / policies / pipeline
- Table: company name, CIN, requested by, date, status dropdown (pending/in_progress/done/rejected)

---

## Error Handling

- `POST /v1/lenders/request`: no auth, rate-limit by IP (10 requests/hour per IP) to prevent spam
- Stub lookup: if `rbi_registry` is empty (not seeded yet), return empty stubs silently — no error
- Trigram fallback: only runs when `q` param is present and `len(q) >= 3`

---

## Testing

- Search "HDFC" → returns curated results, no stubs
- Search "Hdfc" (case variation) → fuzzy match returns curated HDFC lender
- Search "Xyz Finserv" (not in lenders, in rbi_registry) → 0 curated results, 1-2 stubs shown
- Search "Xyz Finserv" (not in either table) → 0 results, 0 stubs, normal empty state
- Click "Request full profile" on stub → toast appears, row in lender_requests
- Admin requests tab → shows submitted request

---

## Rollout

1. Apply migrations 034, 035, 036 (Supabase)
2. Run `seed_rbi_registry.py` locally (one-time, ~60s)
3. Deploy backend (search + request endpoints)
4. Deploy frontend (StubCard + dashboard + admin)
