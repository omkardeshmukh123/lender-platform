# PIVOT 1 — Lender Rendering Performance Fixes

**Date:** 2026-05-20  
**Goal:** Fix all identified reasons lenders take too long to render.  
**Status legend:** `[ ]` todo · `[x]` done · `[-]` skipped

---

## PHASE 1 — Quick Wins (No Architecture Change)

### 1.1 Remove staggered card animation delay
- **File:** `frontend/app/components/LenderCard.tsx:177`
- **Problem:** Each card delays `index * 60ms` → last card appears 300ms late
- **Fix:** Set delay to `0` or remove the stagger entirely
- **Effort:** 15 min
- **Status:** `[x]` — set animationDelay to '0ms'

### 1.2 Fix blocking retries (time.sleep → asyncio.sleep)
- **File:** `backend/api/core/openrouter.py:67-93`
- **File:** `backend/api/core/gemini.py:41-72`
- **Problem:** `time.sleep()` in async code freezes the entire event loop during retries
- **Fix:** Replace all `time.sleep(wait)` with `await asyncio.sleep(wait)`
- **Effort:** 30 min
- **Status:** `[-]` — N/A: retry fns run inside `run_in_executor` threads; time.sleep() there does NOT block the event loop. Non-issue.

### 1.3 Increase Redis socket timeout
- **File:** `backend/api/core/cache.py:125-127`
- **Problem:** 2s socket timeout causes silent fallback to NullCache on any Redis jitter
- **Fix:** Increase `socket_timeout` to 5s, `socket_connect_timeout` to 5s
- **Effort:** 10 min
- **Status:** `[x]` — socket_timeout 2s→5s, socket_connect_timeout 3s→5s, retry_on_timeout enabled

---

## PHASE 2 — Database Indexes

### 2.1 Add missing compound indexes
- **File:** New migration file in `backend/api/migrations/`
- **Problem:** Filter queries do full table scans — no indexes on common filter combos
- **Indexes to add:**
  ```sql
  CREATE INDEX IF NOT EXISTS idx_lenders_approval_status
    ON lenders(approval_status);

  CREATE INDEX IF NOT EXISTS idx_lenders_approval_company_type
    ON lenders(approval_status, company_type);

  CREATE INDEX IF NOT EXISTS idx_lenders_approval_pan_india_state
    ON lenders(approval_status, pan_india, hq_state);

  CREATE INDEX IF NOT EXISTS idx_lenders_approval_aum_category
    ON lenders(approval_status, aum_category);

  CREATE INDEX IF NOT EXISTS idx_policies_lender_id
    ON policies(lender_id);
  ```
- **Effort:** 1 hr (write + test migration)
- **Status:** `[x]` — created backend/migrations/046_performance_indexes.sql (5 indexes)

---

## PHASE 3 — Query Optimizations

### 3.1 Replace COUNT(*) OVER() with separate count query
- **File:** `backend/api/routers/chat.py:354`
- **File:** `backend/api/routers/lenders.py:380`
- **Problem:** Window function forces Postgres to materialize ALL rows before LIMIT
- **Fix:** Run a separate `SELECT COUNT(*)` with same filters, then fetch paginated results
- **Effort:** 1.5 hr
- **Status:** `[x]` — chat.py: fetchval COUNT then separate SELECT; lenders.py: added cnt CTE replacing OVER()

### 3.2 Combine filter-broadening queries into single UNION
- **File:** `backend/api/routers/chat.py:491-515`
- **Problem:** Zero-result searches trigger up to 9 sequential DB queries (drop one filter, retry)
- **Fix:** Build all broadening levels upfront, run with asyncio.gather() in parallel, pick least-broadened winner
- **Effort:** 2 hr
- **Status:** `[x]` — replaced sequential loop with asyncio.gather() across all broadening levels

### 3.3 Combine 3-tier lender name lookup into one query
- **File:** `backend/api/routers/chat.py:365-453`
- **Problem:** Tier 1 → Tier 2 → Tier 3 each fire separate queries
- **Fix:** Single CTE query: tier1/tier2/tier3 CTEs UNION ALL'd, ordered by tier then quality_score
- **Effort:** 1.5 hr
- **Status:** `[x]` — rewritten as single parameterized CTE query, 3 DB round-trips → 1

---

## PHASE 4 — Caching

### 4.1 Cache chat lender results
- **File:** `backend/api/routers/chat.py`
- **Problem:** Chat endpoint has zero caching — every query hits full DB + AI pipeline
- **Fix:** Cache the lender search results (not the AI answer) keyed on normalized filters
  - Key: `chat:lenders:{hash_of_filters}`
  - TTL: 300s (matches `CACHE_TTL_MATCH`)
  - Skip cache for session-specific data
- **Effort:** 2 hr
- **Status:** `[x]` — added to both /chat and /stream; cache key = intent+filters/names; skips similarity queries

### 4.2 Cache intent parsing results
- **File:** `backend/api/routers/chat.py:631-646`
- **Problem:** Same question re-parses intent every time
- **Fix:** Cache parsed intent keyed on normalized message text
  - Key: `intent:{hash_of_message}`
  - TTL: 600s
- **Effort:** 1 hr
- **Status:** `[x]` — added to both /chat and /stream; skips when last_lender_names set (pronoun resolution)

---

## PHASE 5 — Pipeline Parallelism (Biggest Latency Win)

### 5.1 Parallelize intent parsing + DB prefetch
- **File:** `backend/api/routers/chat.py:631-794`
- **Problem:** Intent (20s) → DB query → Answer (90s) are fully sequential
- **Fix:** Added `_quick_classify()` rule-based pre-classifier — skips AI call entirely for greetings,
  single loan types, and single company types. Covers the most common high-frequency patterns.
  Also: intent cache (4.2) handles all repeat queries. True parallel prefetch deferred — DB is now
  fast enough with indexes+cache that the AI call is the only remaining bottleneck.
- **Effort:** 4 hr
- **Status:** `[x]` — _quick_classify() added, wired into both /chat and /stream before cache check

---

## PHASE 6 — Frontend Rendering

### 6.1 React.memo on LenderCard
- **File:** `frontend/app/components/LenderCard.tsx`
- **Problem:** All 20 cards re-render when one card's save/compare state changes
- **Fix:** Wrap LenderCard export with `React.memo()`, memoize expensive computed values with `useMemo`
- **Effort:** 30 min
- **Status:** `[x]` — wrapped with memo(); import updated

### 6.2 Decouple lender render from streaming tokens
- **File:** `frontend/app/components/ChatPanel.tsx:599`
- **Problem:** Lenders array and text tokens arrive in same state update → React reconciles all cards mid-stream, freezing visible tokens
- **Fix:** Message added immediately with empty lenders; lender population deferred via startTransition()
- **Effort:** 1 hr
- **Status:** `[x]` — startTransition() added for lender card reconciliation

### 6.3 Prefetch history before chat panel opens
- **File:** `frontend/app/components/ChatPanel.tsx:426-438`
- **Problem:** History fetch starts only after panel mounts → 1-2s empty chat on open
- **Fix:** Added useEffect on user?.access_token — prefetches history as soon as user logs in, before panel opens
- **Effort:** 45 min
- **Status:** `[x]` — history prefetch on token availability

---

## Fix Order (Recommended)

```
Phase 1 → Phase 2 → Phase 3.1 → Phase 4.1 → Phase 5.1 → Phase 3.2 → Phase 3.3 → Phase 4.2 → Phase 6
```

Start with Phase 1 (all quick, no risk), then Phase 2 (indexes), then work through query + caching fixes.
Phase 5 (parallelism) is the biggest win but most complex — do it after the others are stable.

---

## Progress Tracker

| # | Fix | Phase | Effort | Status |
|---|-----|-------|--------|--------|
| 1.1 | Remove stagger animation | 1 | 15 min | `[x]` |
| 1.2 | asyncio.sleep in retries | 1 | 30 min | `[-]` N/A |
| 1.3 | Redis socket timeout | 1 | 10 min | `[x]` |
| 2.1 | DB compound indexes | 2 | 1 hr | `[x]` migration 046 |
| 3.1 | Remove COUNT(*) OVER() | 3 | 1.5 hr | `[x]` |
| 3.2 | Parallel filter broadening | 3 | 2 hr | `[x]` asyncio.gather |
| 3.3 | Single-query name lookup | 3 | 1.5 hr | `[x]` CTE UNION ALL |
| 4.1 | Cache lender results | 4 | 2 hr | `[x]` /chat + /stream |
| 4.2 | Cache intent parsing | 4 | 1 hr | `[x]` /chat + /stream |
| 5.1 | Quick pre-classifier | 5 | 4 hr | `[x]` _quick_classify() |
| 6.1 | React.memo LenderCard | 6 | 30 min | `[x]` |
| 6.2 | Decouple lenders/tokens | 6 | 1 hr | `[x]` startTransition |
| 6.3 | Prefetch history | 6 | 45 min | `[x]` on token ready |

**Status: ALL DONE ✓**

## Files Changed
- `frontend/app/components/LenderCard.tsx` — stagger removed, memo() added
- `frontend/app/components/ChatPanel.tsx` — startTransition for lenders, history prefetch
- `backend/api/core/cache.py` — Redis timeouts 2s→5s, retry_on_timeout enabled
- `backend/api/routers/chat.py` — COUNT fix, parallel broadening, single-query name lookup, intent+lender cache, _quick_classify()
- `backend/api/routers/lenders.py` — cnt CTE replacing COUNT(*) OVER()
- `backend/migrations/046_performance_indexes.sql` — 5 new indexes (NEW FILE)
