# Production Readiness — Pre-Testing Polish
**Date:** 2026-05-04  
**Approach:** Code + UI fixes now; scraper runs handed off to user

---

## Context

MITRAM360 is going into user testing. The backend is already solid: security headers, rate limiting, CORS, GZip, Sentry, Redis caching, health check, DB pool, startup validation. Auth flows (login/signup/forgot/reset) work. Dashboard error state already has a retry banner.

Two categories of fixes are needed before handing to testers:
1. Code that's done but not committed (GRO feature)
2. UI polish issues that would look unfinished to testers

Scraper runs (GRO + FPC) are handed off to the user to run interactively, not done in this session.

---

## Fix 1 — Commit pending GRO code

Three modified files in git status:
- `backend/api/models/lender.py` — `GrievanceOfficer` Pydantic model + `LenderDetail` field
- `backend/api/routers/lenders.py` — LEFT JOIN `grievance_officers`, mapping `gro_*` fields
- `frontend/app/lender/[id]/page.tsx` — GRO contact card between Financial Overview and Policies

Commit as: `feat(gro): wire grievance officer to API and lender detail page`

---

## Fix 2 — Brand color consistency (53 occurrences, 12 files)

The entire design system uses teal (`#1A7070` / `#0F4848`) but auth pages and several shared components use blue (`#3B5CCC` / `#2d4aa8`) — a leftover from an earlier design iteration.

**Files affected:**
| File | Occurrences |
|------|-------------|
| `app/signup/page.tsx` | 10 |
| `app/login/page.tsx` | 6 |
| `app/admin/page.tsx` | 6 |
| `app/components/ChatPanel.tsx` | 7 |
| `app/components/MatchResultCard.tsx` | 7 |
| `app/reset-password/page.tsx` | 7 |
| `app/forgot-password/page.tsx` | 5 |
| `app/error.tsx` | 1 |
| `app/components/Footer.tsx` | 1 |
| `app/components/StatsSection.tsx` | 1 |
| `app/components/SearchFilter.tsx` | 1 |
| `app/components/StubCard.tsx` | 1 |

**Replacement mapping:**
- `#3B5CCC` (primary blue) → `#1A7070` (brand teal)
- `#2d4aa8` (hover blue) → `#0F4848` (brand dark teal)
- `ring-[#3B5CCC]/20` → `ring-[#1A7070]/20`
- `focus:ring-[#3B5CCC]` → `focus:ring-[#1A7070]`
- `hover:shadow-[#3B5CCC]/25` → `hover:shadow-[#1A7070]/25`
- `text-[#3B5CCC]` → `text-[#1A7070]`
- `border-[#3B5CCC]` → `border-[#1A7070]`
- `bg-[#3B5CCC]` → background: use `linear-gradient(135deg,#0F4848,#1A7070)` on primary buttons, `#1A7070` on inline accents

**Commit:** `fix(ui): replace blue accent (#3B5CCC) with brand teal across all components`

---

## Fix 3 — Remove hardcoded hero stats

In `frontend/app/page.tsx`, the hero widget hardcodes:
```tsx
{ label: 'Banks', value: 177 }
{ label: 'Loan Types', value: 18 }
```

These are static and will drift from reality. The existing `stats` API call (`/v1/lenders/stats`) returns `total_lenders`, `total_policies`, `states_covered`, `company_types`. 

**Fix:** Replace the four-tile hero grid with live data from the stats API:
- `total_lenders` → "Verified Lenders"
- `states_covered` → "States Covered"  
- `total_policies` → "Loan Policies"
- Keep "100% Free" as the fourth static tile (it's a brand claim, not a stat)

Remove the `Math.floor(stats.total_lenders * 0.84)` NBFC approximation and the hardcoded 177.

**Commit:** `fix(landing): show live lender/policy stats instead of hardcoded numbers`

---

## Fix 4 — 404 not-found page

Next.js uses `app/not-found.tsx` for 404s. Currently missing — users hitting invalid URLs see a generic Next.js error page with no branding.

**Create:** `frontend/app/not-found.tsx`

Design: Match app theme — teal/dark background, MITRAM360 logo, "Page not found" message, link back to home and dashboard. Use the same design tokens as the rest of the app.

**Commit:** `feat(ui): add branded 404 not-found page`

---

## Scraper handoff (user runs after deploy)

Once code is committed and deployed, run in order:

```bash
# 1. Reset GRO checkpoint (dry run polluted it)
python backend/scrape_grievance_officers.py --reset-checkpoint

# 2. GRO full run (PSU banks need Firecrawl — key already in .env)
python backend/scrape_grievance_officers.py --limit 200

# 3. FPC full run (mid-size NBFCs with static sites will hit)
python backend/scrape_fpc_pages.py --limit 200
```

Monitor output for hit rate. If PSU banks still return 0 after Firecrawl, their GRO URLs may need hardcoding as a follow-up.

---

## What is already done (no action needed)

- Font loading — Inter imported in `globals.css`, applied to `body`
- CompareModal policy rates — already wired (`/v1/policies/filter` fetch, `fmtRate`/`fmtLoan`/`fmtTenure`)
- Dashboard error state — retry banner already exists
- Security, caching, rate limiting, Sentry — already production-grade
- Auth flows — login, signup, forgot, reset all functional
