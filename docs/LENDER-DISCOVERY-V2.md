# Lender Discovery v2 — Progress Tracker

**Task:** Fix blank search results — fuzzy matching + RBI stub cards + request flow
**Plan:** `docs/superpowers/plans/2026-04-29-lender-discovery-v2.md`
**Spec:** `docs/superpowers/specs/2026-04-29-lender-discovery-v2-design.md`
**Started:** 2026-04-29

## What this builds (plain English)
1. Fuzzy search — "HDFC Fincorp" finds "HDFC Finance Ltd"
2. RBI stub cards — when 0 results, show ghost cards from RBI's 9,188-NBFC registry
3. Request button — user clicks "Request full profile", lands in admin queue

## Tasks

| # | Task | Status |
|---|------|--------|
| 1 | DB migrations 034 (pg_trgm), 035 (rbi_registry), 036 (lender_requests) | ✅ Done |
| 2 | Seed rbi_registry from RBI Excel | ⬜ Pending |
| 3 | RegistryStub model + stubs field in LenderSearchResponse | ⬜ Pending |
| 4 | Fuzzy search fallback + stub lookup in search endpoint | ⬜ Pending |
| 5 | POST /v1/lenders/request endpoint | ⬜ Pending |
| 6 | Admin lender-requests list + status endpoints | ⬜ Pending |
| 7 | StubCard.tsx frontend component | ⬜ Pending |
| 8 | Dashboard renders stub cards | ⬜ Pending |
| 9 | Admin Requests tab | ⬜ Pending |
| 10 | Push + verify production | ⬜ Pending |

## Resume instructions (if context lost)
1. Read this file to see what's done
2. Read the plan at `docs/superpowers/plans/2026-04-29-lender-discovery-v2.md`
3. Pick up from the first ⬜ Pending task
4. Migrations are applied via Supabase MCP (project id: `rhyzqmujazmwwsweaddh`)
5. Seed script: `python backend/seed_rbi_registry.py` (run locally, needs DATABASE_URL)

## Key files
- `backend/migrations/034_pg_trgm.sql`
- `backend/migrations/035_rbi_registry.sql`
- `backend/migrations/036_lender_requests.sql`
- `backend/seed_rbi_registry.py`
- `backend/api/models/lender.py` — add RegistryStub
- `backend/api/routers/lenders.py` — fuzzy search + request endpoint
- `backend/api/routers/admin.py` — requests admin endpoints
- `frontend/app/components/StubCard.tsx` — new component
- `frontend/app/dashboard/page.tsx` — render stubs
- `frontend/app/admin/page.tsx` — requests tab
