# Lender Discovery v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fuzzy name search, RBI registry stub cards, and a "request full profile" flow so no lender search ever returns a blank page.

**Architecture:** Three DB migrations add pg_trgm, a read-only `rbi_registry` table, and a `lender_requests` queue. The search endpoint gains a two-phase fallback: trigram similarity on `lenders` first, then `rbi_registry` stubs when results are still empty. A new `POST /v1/lenders/request` endpoint feeds the admin queue. The frontend renders stub cards below real results and the admin panel gets a Requests tab.

**Tech Stack:** PostgreSQL pg_trgm, asyncpg, FastAPI, Pydantic v2, Next.js 14, React, Tailwind CSS, openpyxl (already installed)

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `backend/migrations/034_pg_trgm.sql` | Create | Enable pg_trgm extension + index on lenders.company_name |
| `backend/migrations/035_rbi_registry.sql` | Create | rbi_registry table + trgm index |
| `backend/migrations/036_lender_requests.sql` | Create | lender_requests queue table |
| `backend/seed_rbi_registry.py` | Create | One-time seed of rbi_registry from RBI Excel |
| `backend/api/models/lender.py` | Modify | Add RegistryStub model, stubs field to LenderSearchResponse |
| `backend/api/routers/lenders.py` | Modify | Fuzzy fallback + stub lookup + POST /request endpoint |
| `backend/api/routers/admin.py` | Modify | GET/POST lender-requests admin endpoints |
| `frontend/app/components/StubCard.tsx` | Create | Grey stub card UI component |
| `frontend/app/dashboard/page.tsx` | Modify | RegistryStub type, stubs in response, render StubCards |
| `frontend/app/admin/page.tsx` | Modify | Requests tab (list + status update) |

---

## Task 1: DB Migrations 034, 035, 036

**Files:**
- Create: `backend/migrations/034_pg_trgm.sql`
- Create: `backend/migrations/035_rbi_registry.sql`
- Create: `backend/migrations/036_lender_requests.sql`

- [ ] **Step 1: Write migration 034**

```sql
-- backend/migrations/034_pg_trgm.sql
-- Enable trigram extension for fuzzy name search
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_lenders_name_trgm
  ON lenders USING GiST (company_name gist_trgm_ops);
```

- [ ] **Step 2: Write migration 035**

```sql
-- backend/migrations/035_rbi_registry.sql
-- Read-only RBI reference table — never shown as a real lender
CREATE TABLE IF NOT EXISTS rbi_registry (
  id                      SERIAL PRIMARY KEY,
  company_name            TEXT NOT NULL,
  cin                     TEXT,
  rbi_registration_number TEXT,
  regulatory_tier         TEXT,
  hq_state                TEXT,
  established_year        INTEGER,
  created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rbi_registry_name_trgm
  ON rbi_registry USING GiST (company_name gist_trgm_ops);

CREATE UNIQUE INDEX IF NOT EXISTS idx_rbi_registry_name_unique
  ON rbi_registry (lower(company_name));
```

- [ ] **Step 3: Write migration 036**

```sql
-- backend/migrations/036_lender_requests.sql
-- User-submitted requests to enrich a missing lender
CREATE TABLE IF NOT EXISTS lender_requests (
  id            SERIAL PRIMARY KEY,
  company_name  TEXT NOT NULL,
  cin           TEXT,
  requested_by  TEXT,
  notes         TEXT,
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'in_progress', 'done', 'rejected'))
);

CREATE INDEX IF NOT EXISTS idx_lender_requests_status
  ON lender_requests (status, created_at DESC);
```

- [ ] **Step 4: Apply all three migrations via Supabase MCP**

Apply 034, then 035, then 036 in order.

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/034_pg_trgm.sql backend/migrations/035_rbi_registry.sql backend/migrations/036_lender_requests.sql
git commit -m "feat(db): add pg_trgm, rbi_registry, lender_requests tables"
```

---

## Task 2: Seed rbi_registry from RBI Excel

**Files:**
- Create: `backend/seed_rbi_registry.py`

- [ ] **Step 1: Write the seed script**

```python
"""
seed_rbi_registry.py
====================
One-time script: load data/input/rbi_nbfc_list.xlsx into rbi_registry table.
Skips rows whose company_name already exists in the lenders table.
Run: python backend/seed_rbi_registry.py
     python backend/seed_rbi_registry.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# reuse Excel parsing + CIN decode from sync_nbfc_csv
sys.path.insert(0, str(Path(__file__).parent))
from sync_nbfc_csv import load_rbi_nbfc_excel, parse_cin, _MCA_STATE_CODES

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / '.env', override=False)
except ImportError:
    pass

import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s')
log = logging.getLogger(__name__)

ROOT          = Path(__file__).parent.parent
RBI_NBFC_XLSX = ROOT / 'data' / 'input' / 'rbi_nbfc_list.xlsx'

_RBI_LAYER_MAP = {'upper': 'ND-UL', 'middle': 'ND-ML', 'base': 'ND-BL'}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    db_url = os.environ.get('DATABASE_URL', '')
    if not db_url:
        log.error('DATABASE_URL not set')
        sys.exit(1)

    log.info('Loading RBI Excel from %s', RBI_NBFC_XLSX)
    entries = load_rbi_nbfc_excel(RBI_NBFC_XLSX)
    log.info('Loaded %d rows from Excel', len(entries))

    conn = psycopg2.connect(db_url)
    cur  = conn.cursor()

    # Fetch all existing lender names (lowercase) to skip duplicates
    cur.execute("SELECT lower(company_name) FROM lenders")
    existing_lender_names = {row[0] for row in cur.fetchall()}
    log.info('Found %d existing lenders in DB', len(existing_lender_names))

    # Fetch already-seeded registry names
    cur.execute("SELECT lower(company_name) FROM rbi_registry")
    existing_registry_names = {row[0] for row in cur.fetchall()}
    log.info('Found %d existing rbi_registry rows', len(existing_registry_names))

    rows_to_insert = []
    skipped_lenders = 0
    skipped_registry = 0

    for entry in entries:
        name = entry.get('name', '').strip()
        if not name:
            continue

        name_lower = name.lower()

        if name_lower in existing_lender_names:
            skipped_lenders += 1
            continue

        if name_lower in existing_registry_names:
            skipped_registry += 1
            continue

        cin  = entry.get('cin') or None
        tier = _RBI_LAYER_MAP.get((entry.get('layer') or '').lower())
        cor  = entry.get('cor') or entry.get('rbi_registration_number') or None

        hq_state = None
        established_year = None
        if cin:
            decoded = parse_cin(cin)
            hq_state = decoded.get('hq_state')
            established_year = decoded.get('established_year')

        rows_to_insert.append((name, cin, cor, tier, hq_state, established_year))
        existing_registry_names.add(name_lower)  # prevent intra-batch duplicates

    log.info(
        'Inserting %d rows | skipped (in lenders): %d | skipped (already in registry): %d',
        len(rows_to_insert), skipped_lenders, skipped_registry,
    )

    if args.dry_run:
        log.info('DRY RUN — no writes')
        conn.close()
        return

    if rows_to_insert:
        execute_values(
            cur,
            """
            INSERT INTO rbi_registry
              (company_name, cin, rbi_registration_number, regulatory_tier, hq_state, established_year)
            VALUES %s
            ON CONFLICT (lower(company_name)) DO NOTHING
            """,
            rows_to_insert,
        )
        conn.commit()
        log.info('Done. Inserted %d rows into rbi_registry.', len(rows_to_insert))
    else:
        log.info('Nothing to insert.')

    conn.close()


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Run the seed script (dry-run first)**

```bash
python backend/seed_rbi_registry.py --dry-run
```

Expected output: `Inserting N rows | skipped (in lenders): M | ...` — no DB writes.

- [ ] **Step 3: Run for real**

```bash
python backend/seed_rbi_registry.py
```

Expected: `Done. Inserted ~8000+ rows into rbi_registry.`

- [ ] **Step 4: Verify in DB**

```sql
SELECT COUNT(*) FROM rbi_registry;
SELECT * FROM rbi_registry LIMIT 5;
```

- [ ] **Step 5: Commit**

```bash
git add backend/seed_rbi_registry.py
git commit -m "feat(db): seed rbi_registry from RBI NBFC Excel (~9k rows)"
```

---

## Task 3: Add RegistryStub model + stubs to LenderSearchResponse

**Files:**
- Modify: `backend/api/models/lender.py`

- [ ] **Step 1: Add RegistryStub and update LenderSearchResponse**

Open `backend/api/models/lender.py`. The current `LenderSearchResponse` is at line 70. Replace the end of the file (from line 70) with:

```python
class RegistryStub(BaseModel):
    id: int
    company_name: str
    cin: Optional[str] = None
    rbi_registration_number: Optional[str] = None
    regulatory_tier: Optional[str] = None
    hq_state: Optional[str] = None
    established_year: Optional[int] = None


class LenderSearchResponse(BaseModel):
    total: int
    page: int
    limit: int
    results: List[LenderSummary]
    stubs: List[RegistryStub] = Field(default_factory=list)
```

- [ ] **Step 2: Update the import in lenders.py**

In `backend/api/routers/lenders.py` line 22, add `RegistryStub` to the import:

```python
from models.lender import LenderDetail, LenderSearchResponse, LenderSummary, RegistryStub
```

- [ ] **Step 3: Commit**

```bash
git add backend/api/models/lender.py backend/api/routers/lenders.py
git commit -m "feat(models): add RegistryStub model, stubs field to LenderSearchResponse"
```

---

## Task 4: Fuzzy search fallback + registry stub lookup

**Files:**
- Modify: `backend/api/routers/lenders.py` (lines 197–342)

- [ ] **Step 1: Replace the search body in `search_lenders`**

Find the block starting at `conditions = ["l.approval_status = 'approved'"]` (line 197) and replace everything from there through `return result` (line 342) with:

```python
    conditions = ["l.approval_status = 'approved'"]
    params: list = []
    idx = 1

    q_clean = q.strip() if q else None
    if q_clean:
        q_esc = q_clean.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(f"l.company_name ILIKE ${idx}")
        params.append(f"%{q_esc}%")
        idx += 1

    if company_type:
        conditions.append(f"l.company_type = ANY(${idx}::text[])")
        params.append(company_type)
        idx += 1

    if state:
        conditions.append(f"(l.pan_india = true OR ${idx} = ANY(l.operating_states))")
        params.append(state)
        idx += 1

    if loan_type:
        lt_conds = []
        for lt in loan_type:
            lt_conds.append(f"${idx} = ANY(l.primary_loan_segments)")
            params.append(lt)
            idx += 1
        conditions.append(f"({' OR '.join(lt_conds)})")

    if aum_category:
        conditions.append(f"l.aum_category = ANY(${idx}::text[])")
        params.append(aum_category)
        idx += 1

    if aum_min is not None:
        conditions.append(f"l.aum_crores >= ${idx}")
        params.append(aum_min)
        idx += 1

    if aum_max is not None:
        conditions.append(f"l.aum_crores <= ${idx}")
        params.append(aum_max)
        idx += 1

    if established_year_min is not None:
        conditions.append(f"l.established_year >= ${idx}")
        params.append(established_year_min)
        idx += 1

    if established_year_max is not None:
        conditions.append(f"l.established_year <= ${idx}")
        params.append(established_year_max)
        idx += 1

    if pan_india is not None:
        conditions.append(f"l.pan_india = ${idx}")
        params.append(pan_india)
        idx += 1

    if is_listed is not None:
        conditions.append(f"l.is_listed = ${idx}")
        params.append(is_listed)
        idx += 1

    if operating_intensity:
        conditions.append(f"l.operating_intensity = ANY(${idx}::text[])")
        params.append(operating_intensity)
        idx += 1

    if business_sector:
        conditions.append(f"l.business_sector = ANY(${idx}::text[])")
        params.append(business_sector)
        idx += 1

    if has_policies is True:
        conditions.append(
            "EXISTS (SELECT 1 FROM policies p WHERE p.lender_id = l.id "
            "AND p.is_active = true AND p.approval_status = 'approved')"
        )
    elif has_policies is False:
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM policies p WHERE p.lender_id = l.id "
            "AND p.is_active = true AND p.approval_status = 'approved')"
        )

    if has_revenue is True:
        conditions.append("l.last_year_revenue IS NOT NULL")
    elif has_revenue is False:
        conditions.append("l.last_year_revenue IS NULL")

    if revenue_min is not None:
        conditions.append(f"l.last_year_revenue >= ${idx}")
        params.append(revenue_min)
        idx += 1

    if revenue_max is not None:
        conditions.append(f"l.last_year_revenue <= ${idx}")
        params.append(revenue_max)
        idx += 1

    where    = " AND ".join(conditions)
    sort_sql = f"ORDER BY l.{sort_by} {sort_dir.upper()} NULLS LAST"
    offset   = (page - 1) * limit

    SELECT_COLS = """
        l.id, l.company_name, l.company_type, l.rbi_category,
        l.aum_crores, l.aum_category, l.hq_state, l.hq_location,
        l.operating_intensity, l.business_sector, l.pan_india,
        l.primary_loan_segments, l.operating_states, l.website,
        l.quality_score, l.employee_count, l.established_year,
        l.is_listed, l.phone, l.email, l.last_year_revenue,
        (
            SELECT COUNT(*)::int FROM policies p
            WHERE p.lender_id = l.id
              AND p.is_active = true
              AND p.approval_status = 'approved'
        ) AS policy_count
    """

    stubs: List[RegistryStub] = []

    try:
        async with db.acquire() as conn:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM lenders l WHERE {where}", *params
            )
            rows = await conn.fetch(
                f"""
                SELECT {SELECT_COLS}
                FROM lenders l
                WHERE {where}
                {sort_sql}
                LIMIT ${idx} OFFSET ${idx + 1}
                """,
                *params, limit, offset,
            )

            # Phase 2: trigram fallback when ILIKE returned < 3 results
            fuzzy_rows: list = []
            if q_clean and len(q_clean) >= 3 and (total or 0) < 3:
                existing_ids = {r["id"] for r in rows}
                fuzzy_rows = await conn.fetch(
                    f"""
                    SELECT {SELECT_COLS}
                    FROM lenders l
                    WHERE l.approval_status = 'approved'
                      AND similarity(l.company_name, $1) > 0.25
                      AND l.id <> ALL($2::bigint[])
                    ORDER BY similarity(l.company_name, $1) DESC
                    LIMIT 10
                    """,
                    q_clean, list(existing_ids),
                )

            # Phase 3: registry stubs when still no results
            if q_clean and len(q_clean) >= 3 and (total or 0) == 0 and not fuzzy_rows:
                stub_rows = await conn.fetch(
                    """
                    SELECT id, company_name, cin, rbi_registration_number,
                           regulatory_tier, hq_state, established_year
                    FROM rbi_registry
                    WHERE similarity(company_name, $1) > 0.3
                    ORDER BY similarity(company_name, $1) DESC
                    LIMIT 5
                    """,
                    q_clean,
                )
                stubs = [
                    RegistryStub(
                        id=r["id"],
                        company_name=r["company_name"],
                        cin=r["cin"],
                        rbi_registration_number=r["rbi_registration_number"],
                        regulatory_tier=r["regulatory_tier"],
                        hq_state=r["hq_state"],
                        established_year=r["established_year"],
                    )
                    for r in stub_rows
                ]

    except Exception as exc:
        logger.error("search_lenders DB error: %s | request_id=%s",
                     exc, getattr(request.state, "request_id", ""))
        metrics.inc("db.error_count")
        raise HTTPException(status_code=503, detail="Search service temporarily unavailable")

    all_results = [_row_to_summary(r) for r in rows] + [_row_to_summary(r) for r in fuzzy_rows]

    result = LenderSearchResponse(
        total=total,
        page=page,
        limit=limit,
        results=all_results,
        stubs=stubs,
    )

    await cache.set(cache_key, result.model_dump(), ttl=CacheTTL.SEARCH)
    return result
```

- [ ] **Step 2: Verify the file compiles**

```bash
cd backend && python -c "from api.routers.lenders import router; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/api/routers/lenders.py
git commit -m "feat(search): trigram fuzzy fallback + RBI registry stubs on zero results"
```

---

## Task 5: POST /v1/lenders/request endpoint

**Files:**
- Modify: `backend/api/routers/lenders.py` (append after `get_lender`)

- [ ] **Step 1: Add the request model and endpoint**

Append to the end of `backend/api/routers/lenders.py`:

```python
# ── Lender request ─────────────────────────────────────────────────────────────

class LenderRequestBody(BaseModel):
    company_name: str = Field(..., min_length=2, max_length=200)
    cin: Optional[str] = Field(None, max_length=30)
    requested_by: Optional[str] = Field(None, max_length=200)
    notes: Optional[str] = Field(None, max_length=500)


@router.post("/request", summary="Submit a request to enrich a missing lender")
@limiter.limit("10/hour")
async def request_lender(
    request: Request,
    body: LenderRequestBody,
    db: asyncpg.Pool = Depends(get_db),
):
    try:
        async with db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO lender_requests (company_name, cin, requested_by, notes)
                VALUES ($1, $2, $3, $4)
                """,
                body.company_name,
                body.cin,
                body.requested_by or "anonymous",
                body.notes,
            )
    except Exception as exc:
        logger.error("request_lender DB error: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    logger.info("LENDER_REQUEST company=%s by=%s", body.company_name, body.requested_by)
    return {"status": "submitted"}
```

Also add `BaseModel, Field` to the existing pydantic import at the top of the file. Check line 1-10 — if `BaseModel` isn't imported yet, add:

```python
from pydantic import BaseModel, Field
```

- [ ] **Step 2: Verify**

```bash
cd backend && python -c "from api.routers.lenders import router; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/api/routers/lenders.py
git commit -m "feat(api): POST /v1/lenders/request endpoint for missing lender submissions"
```

---

## Task 6: Admin lender-requests endpoints

**Files:**
- Modify: `backend/api/routers/admin.py`

- [ ] **Step 1: Add models and endpoints**

Find the end of the models section in `admin.py` (before `# ── Endpoints ──`). Add:

```python
class LenderRequestRow(BaseModel):
    id: int
    company_name: str
    cin: Optional[str] = None
    requested_by: Optional[str] = None
    notes: Optional[str] = None
    created_at: str
    status: str


class LenderRequestsResponse(BaseModel):
    total: int
    page: int
    limit: int
    results: List[LenderRequestRow]


class UpdateRequestStatus(BaseModel):
    status: str = Field(..., pattern="^(pending|in_progress|done|rejected)$")
```

Then append two endpoints before the final `@router.get("/pipeline"` block:

```python
@router.get(
    "/lender-requests",
    response_model=LenderRequestsResponse,
    summary="List lender enrichment requests (paginated)",
)
async def get_lender_requests(
    request: Request,
    admin: AdminUser,
    db: asyncpg.Pool = Depends(get_db),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    offset = (page - 1) * limit
    conditions = []
    params: list = []
    idx = 1

    if status:
        conditions.append(f"status = ${idx}")
        params.append(status)
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    try:
        async with db.acquire() as conn:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM lender_requests {where}", *params
            )
            rows = await conn.fetch(
                f"""
                SELECT id, company_name, cin, requested_by, notes, created_at, status
                FROM lender_requests
                {where}
                ORDER BY created_at DESC
                LIMIT ${idx} OFFSET ${idx + 1}
                """,
                *params, limit, offset,
            )
    except Exception as exc:
        logger.error("get_lender_requests DB error: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    return LenderRequestsResponse(
        total=total or 0,
        page=page,
        limit=limit,
        results=[
            LenderRequestRow(
                id=r["id"],
                company_name=r["company_name"],
                cin=r["cin"],
                requested_by=r["requested_by"],
                notes=r["notes"],
                created_at=r["created_at"].isoformat(),
                status=r["status"],
            )
            for r in rows
        ],
    )


@router.post(
    "/lender-requests/{request_id}/status",
    summary="Update status of a lender enrichment request",
)
async def update_lender_request_status(
    request: Request,
    request_id: int,
    body: UpdateRequestStatus,
    admin: AdminUser,
    db: asyncpg.Pool = Depends(get_db),
):
    try:
        async with db.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE lender_requests SET status = $1 WHERE id = $2 RETURNING id",
                body.status, request_id,
            )
    except Exception as exc:
        logger.error("update_lender_request_status DB error: %s", exc)
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    if not row:
        raise HTTPException(status_code=404, detail=f"Request {request_id} not found")

    return {"id": request_id, "status": body.status}
```

- [ ] **Step 2: Verify**

```bash
cd backend && python -c "from api.routers.admin import router; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/api/routers/admin.py
git commit -m "feat(admin): lender-requests list + status update endpoints"
```

---

## Task 7: StubCard frontend component

**Files:**
- Create: `frontend/app/components/StubCard.tsx`

- [ ] **Step 1: Create StubCard.tsx**

```tsx
'use client'

import { useState } from 'react'
import { Building2, MapPin, CheckCircle, AlertCircle } from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export interface RegistryStub {
  id: number
  company_name: string
  cin: string | null
  rbi_registration_number: string | null
  regulatory_tier: string | null
  hq_state: string | null
  established_year: number | null
}

const TIER_LABELS: Record<string, string> = {
  'ND-UL': 'Upper Layer',
  'ND-ML': 'Middle Layer',
  'ND-BL': 'Base Layer',
}

const TIER_COLORS: Record<string, string> = {
  'ND-UL': 'bg-purple-50 text-purple-700',
  'ND-ML': 'bg-blue-50 text-blue-700',
  'ND-BL': 'bg-gray-100 text-gray-600',
}

interface Props {
  stub: RegistryStub
  userEmail?: string | null
}

export default function StubCard({ stub, userEmail }: Props) {
  const [state, setState] = useState<'idle' | 'loading' | 'done' | 'error'>('idle')

  const handleRequest = async () => {
    setState('loading')
    try {
      const res = await fetch(`${API_URL}/v1/lenders/request`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          company_name: stub.company_name,
          cin: stub.cin,
          requested_by: userEmail ?? 'anonymous',
        }),
      })
      if (!res.ok) throw new Error('failed')
      setState('done')
    } catch {
      setState('error')
      setTimeout(() => setState('idle'), 3000)
    }
  }

  const tierLabel = stub.regulatory_tier ? TIER_LABELS[stub.regulatory_tier] ?? stub.regulatory_tier : null
  const tierColor = stub.regulatory_tier ? TIER_COLORS[stub.regulatory_tier] ?? 'bg-gray-100 text-gray-600' : ''

  return (
    <div className="relative bg-gray-50 border border-dashed border-gray-300 rounded-2xl p-5 flex flex-col gap-3 opacity-80">
      {/* "Not enriched" badge */}
      <div className="absolute top-3 right-3">
        <span className="text-xs font-medium bg-gray-200 text-gray-500 px-2 py-0.5 rounded-full">
          Data not yet available
        </span>
      </div>

      {/* Header */}
      <div className="flex items-start gap-3 pr-32">
        <div className="w-9 h-9 rounded-xl bg-gray-200 flex items-center justify-center flex-shrink-0">
          <Building2 className="w-4 h-4 text-gray-400" />
        </div>
        <div>
          <h3 className="font-semibold text-gray-700 leading-tight">{stub.company_name}</h3>
          {stub.rbi_registration_number && (
            <p className="text-xs text-gray-400 mt-0.5">RBI Reg: {stub.rbi_registration_number}</p>
          )}
        </div>
      </div>

      {/* Meta */}
      <div className="flex flex-wrap gap-2 text-xs">
        {tierLabel && (
          <span className={`px-2 py-0.5 rounded-full font-medium ${tierColor}`}>
            {tierLabel}
          </span>
        )}
        {stub.hq_state && (
          <span className="flex items-center gap-1 text-gray-500">
            <MapPin className="w-3 h-3" />{stub.hq_state}
          </span>
        )}
        {stub.established_year && (
          <span className="text-gray-400">Est. {stub.established_year}</span>
        )}
        {stub.cin && (
          <span className="text-gray-400 font-mono">{stub.cin}</span>
        )}
      </div>

      {/* Request button */}
      <div className="mt-1">
        {state === 'done' ? (
          <div className="flex items-center gap-1.5 text-green-600 text-xs font-medium">
            <CheckCircle className="w-3.5 h-3.5" />
            Request submitted — we'll enrich this soon
          </div>
        ) : state === 'error' ? (
          <div className="flex items-center gap-1.5 text-red-500 text-xs font-medium">
            <AlertCircle className="w-3.5 h-3.5" />
            Failed — please try again
          </div>
        ) : (
          <button
            onClick={handleRequest}
            disabled={state === 'loading'}
            className="text-xs font-medium text-[#3B5CCC] hover:underline disabled:opacity-50"
          >
            {state === 'loading' ? 'Submitting…' : 'Request full profile →'}
          </button>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/app/components/StubCard.tsx
git commit -m "feat(ui): StubCard component for RBI registry stubs"
```

---

## Task 8: Dashboard — handle stubs in API response

**Files:**
- Modify: `frontend/app/dashboard/page.tsx`

- [ ] **Step 1: Add RegistryStub interface and update LenderSearchResponse**

Find `interface LenderSearchResponse` (around line 90) and replace it:

```tsx
interface RegistryStub {
  id: number
  company_name: string
  cin: string | null
  rbi_registration_number: string | null
  regulatory_tier: string | null
  hq_state: string | null
  established_year: number | null
}

interface LenderSearchResponse {
  total:   number
  page:    number
  limit:   number
  results: LenderSummary[]
  stubs:   RegistryStub[]
}
```

- [ ] **Step 2: Add StubCard import at the top of the file**

After the existing imports (around line 1-10), add:

```tsx
import StubCard from '../components/StubCard'
```

- [ ] **Step 3: Add stubs state**

Find where `lenders` state is declared (search for `useState<LenderSummary[]>`). Add a stubs state right after it:

```tsx
const [stubs, setStubs] = useState<RegistryStub[]>([])
```

- [ ] **Step 4: Populate stubs from API response**

Find where `setLenders(data.results ?? [])` is called (inside the fetch handler). Add the line to also set stubs:

```tsx
setLenders(data.results ?? [])
setStubs(data.stubs ?? [])
```

Also clear stubs when loading starts — find `setLoading(true)` in the search effect and add:

```tsx
setStubs([])
```

- [ ] **Step 5: Render StubCards below the results grid**

Find the closing `</div>` after the lender cards grid (search for `resultsCount` or the grid container). After the results grid and pagination, add:

```tsx
{stubs.length > 0 && lenders.length === 0 && (
  <div className="mt-8">
    <div className="flex items-center gap-2 mb-4">
      <div className="h-px flex-1 bg-gray-200" />
      <span className="text-xs font-medium text-gray-400 whitespace-nowrap">
        Also registered with RBI — data not yet available
      </span>
      <div className="h-px flex-1 bg-gray-200" />
    </div>
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      {stubs.map(stub => (
        <StubCard key={stub.id} stub={stub} />
      ))}
    </div>
  </div>
)}
```

- [ ] **Step 6: Commit**

```bash
git add frontend/app/dashboard/page.tsx
git commit -m "feat(dashboard): render RBI registry stubs when search returns no results"
```

---

## Task 9: Admin — Requests tab

**Files:**
- Modify: `frontend/app/admin/page.tsx`

- [ ] **Step 1: Add LenderRequest interface**

After the existing interfaces (after `PipelineRun`), add:

```tsx
interface LenderRequest {
  id: number
  company_name: string
  cin: string | null
  requested_by: string | null
  notes: string | null
  created_at: string
  status: string
}
```

- [ ] **Step 2: Add requests state**

In the component, after the `runs` state declaration, add:

```tsx
const [requests, setRequests] = useState<LenderRequest[]>([])
const [requestTotal, setRequestTotal] = useState(0)
const [requestPage, setRequestPage] = useState(1)
```

Update the tab type to include `'requests'`:

```tsx
const [tab, setTab] = useState<'lenders' | 'policies' | 'pipeline' | 'requests'>('lenders')
```

- [ ] **Step 3: Add fetchRequests function**

After `fetchRuns`, add:

```tsx
const fetchRequests = useCallback(async () => {
  if (!token) return
  setLoading(true)
  try {
    const data = await apiGet(`/v1/admin/lender-requests?page=${requestPage}&limit=${PAGE_SIZE}`)
    setRequests(data.results ?? [])
    setRequestTotal(data.total ?? 0)
  } catch (e: any) {
    showToast(`Failed to load requests: ${e.message}`, false)
  } finally { setLoading(false) }
}, [token, requestPage, apiGet])
```

- [ ] **Step 4: Wire fetchRequests into the tab effect**

In the tab `useEffect` (the one that runs on tab/page changes), add the requests case:

```tsx
useEffect(() => {
  if (!authDone) return
  if (tab === 'lenders') fetchLenders()
  else if (tab === 'policies') fetchPolicies()
  else if (tab === 'requests') fetchRequests()
  else fetchRuns()
}, [tab, lenderPage, policyPage, requestPage, fetchLenders, fetchPolicies, fetchRequests, fetchRuns])
```

Also add `fetchRequests()` to the initial load `useEffect` alongside the other three fetches.

- [ ] **Step 5: Add status update handler**

After `handleApproveAllPolicies`, add:

```tsx
const handleUpdateRequestStatus = async (id: number, status: string) => {
  try {
    await apiPost(`/v1/admin/lender-requests/${id}/status`, { status })
    showToast(`Request marked ${status}`, true)
    fetchRequests()
  } catch (e: any) { showToast(`Update failed: ${e.message}`, false) }
}
```

- [ ] **Step 6: Add Requests tab button**

In the tabs row (the `(['lenders', 'policies', 'pipeline'] as const).map(...)` block), replace the array with:

```tsx
{(['lenders', 'policies', 'requests', 'pipeline'] as const).map(t => (
  <button
    key={t}
    onClick={() => { setTab(t); setLenderPage(1); setPolicyPage(1); setRequestPage(1) }}
    className={[
      'px-5 py-2 rounded-lg text-sm font-medium transition-all flex items-center gap-1.5',
      tab === t ? 'bg-white text-[#3B5CCC] shadow-sm' : 'text-gray-500 hover:text-gray-700',
    ].join(' ')}
  >
    {t === 'lenders' ? 'Pending Lenders'
      : t === 'policies' ? `Pending Policies${policyTotal > 0 ? ` (${policyTotal})` : ''}`
      : t === 'requests' ? `Requests${requestTotal > 0 ? ` (${requestTotal})` : ''}`
      : 'Pipeline Runs'}
  </button>
))}
```

- [ ] **Step 7: Add Requests tab panel**

After the `{/* ── Pipeline tab ── */}` block closing `</>`, add:

```tsx
{/* ── Requests tab ── */}
{tab === 'requests' && (
  <>
    {loading && requests.length === 0 ? (
      <div className="text-center py-16 text-gray-400">Loading…</div>
    ) : requests.length === 0 ? (
      <div className="bg-white rounded-2xl border border-gray-200 p-12 text-center">
        <CheckCircle className="w-10 h-10 text-green-400 mx-auto mb-3" />
        <p className="font-medium text-gray-700">No pending requests</p>
      </div>
    ) : (
      <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="text-left px-4 py-3 font-medium text-gray-600">Company</th>
              <th className="text-left px-4 py-3 font-medium text-gray-600 hidden md:table-cell">Requested By</th>
              <th className="text-left px-4 py-3 font-medium text-gray-600 hidden lg:table-cell">Date</th>
              <th className="text-right px-4 py-3 font-medium text-gray-600">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {requests.map(r => (
              <tr key={r.id} className="hover:bg-gray-50 transition-colors">
                <td className="px-4 py-3">
                  <div className="font-medium text-gray-900">{r.company_name}</div>
                  {r.cin && <div className="text-xs text-gray-400 font-mono">{r.cin}</div>}
                  {r.notes && <div className="text-xs text-gray-500 mt-0.5">{r.notes}</div>}
                </td>
                <td className="px-4 py-3 text-gray-500 text-xs hidden md:table-cell">
                  {r.requested_by ?? '—'}
                </td>
                <td className="px-4 py-3 text-xs text-gray-400 hidden lg:table-cell">
                  {new Date(r.created_at).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}
                </td>
                <td className="px-4 py-3 text-right">
                  <select
                    value={r.status}
                    onChange={e => handleUpdateRequestStatus(r.id, e.target.value)}
                    className="text-xs border border-gray-200 rounded-lg px-2 py-1 bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-[#3B5CCC]"
                  >
                    <option value="pending">Pending</option>
                    <option value="in_progress">In Progress</option>
                    <option value="done">Done</option>
                    <option value="rejected">Rejected</option>
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {Math.ceil(requestTotal / PAGE_SIZE) > 1 && (
          <div className="px-4 py-3 border-t border-gray-100 flex items-center justify-between">
            <span className="text-xs text-gray-500">
              {((requestPage - 1) * PAGE_SIZE) + 1}–{Math.min(requestPage * PAGE_SIZE, requestTotal)} of {requestTotal}
            </span>
            <div className="flex gap-2">
              <button onClick={() => setRequestPage(p => Math.max(1, p - 1))} disabled={requestPage === 1} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 disabled:opacity-30">
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="text-xs text-gray-500 flex items-center">{requestPage} / {Math.ceil(requestTotal / PAGE_SIZE)}</span>
              <button onClick={() => setRequestPage(p => Math.min(Math.ceil(requestTotal / PAGE_SIZE), p + 1))} disabled={requestPage === Math.ceil(requestTotal / PAGE_SIZE)} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 disabled:opacity-30">
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    )}
  </>
)}
```

- [ ] **Step 8: Commit**

```bash
git add frontend/app/admin/page.tsx
git commit -m "feat(admin): add Requests tab for lender enrichment queue"
```

---

## Task 10: Final push

- [ ] **Step 1: Push all commits**

```bash
git push origin main
```

- [ ] **Step 2: Verify production**

1. Search for an existing lender (e.g. "HDFC") → normal results, no stubs
2. Search for a slight misspelling (e.g. "Hdfc Fincorp") → fuzzy match returns near results  
3. Search for an NBFC not in lenders (e.g. try one from RBI list) → stub card appears with tier/state/reg number
4. Click "Request full profile" on stub → toast shows "Request submitted"
5. In admin panel → Requests tab → request appears, status dropdown works
