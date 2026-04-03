# MITRAM360 — How the System Works
### Plain English explanation of every part

---

## What This Platform Does

A small business owner needs a loan. There are 1,000+ NBFCs and banks in India. Each has different rules — minimum credit score, how much they lend, which states they cover, what type of business they accept.

The borrower has no way to know who will actually approve them without applying one by one.

**MITRAM360 solves this:**
1. We collect data from all lenders and extract their real eligibility rules using AI
2. An admin checks and approves the data before it goes live
3. When a borrower submits their profile, we instantly show them only the lenders they actually qualify for — ranked best to worst

---

## Big Picture — 5 Layers

```
┌────────────────────────────────────────────────────────────────┐
│  LAYER 1 — SCHEDULER (Apache Airflow)                          │
│  "What runs, when, and in what order"                          │
│  Runs extraction jobs, refreshes stale data, applies updates   │
└──────────────────────────┬─────────────────────────────────────┘
                           │ triggers
                           ▼
┌────────────────────────────────────────────────────────────────┐
│  LAYER 2 — DATA COLLECTION (Backend Python)                    │
│  "Where does our lender data come from?"                       │
│  Scrape websites → Gemini AI → Validate → Dead-letter failures │
└──────────────────────────┬─────────────────────────────────────┘
                           │ stores to
                           ▼
┌────────────────────────────────────────────────────────────────┐
│  LAYER 3 — DATABASE (Supabase / PostgreSQL)                    │
│  "Where data lives, how it's matched, who approves it"         │
│  lenders + policies + audit trail + match engine               │
└───────────────┬──────────────────────────┬─────────────────────┘
                │                          │
                ▼                          ▼
┌──────────────────────────┐   ┌──────────────────────────────┐
│  LAYER 4 — API (FastAPI) │   │  LAYER 5 — ANALYTICS         │
│  "The data gateway"      │   │  (Grafana)                   │
│                          │   │                              │
│  Search · Match · Compare│   │  Interest rates by loan type │
│  Redis cache · Auth      │   │  Match volume over time      │
│  Rate limiting · Logs    │   │  Lender pipeline status      │
└──────────────┬───────────┘   └──────────────────────────────┘
               │
               ▼
┌────────────────────────────────────────────────────────────────┐
│  FRONTEND (Next.js)                                            │
│  "What the user sees"                                          │
│  Login · Search with filters · Lender cards · Apply           │
└────────────────────────────────────────────────────────────────┘
```

---

## Layer 1 — Scheduler (Apache Airflow)

### Why we use Airflow

Running 1,000+ lenders through AI extraction as one script means:
- One failure kills everything
- No visibility into what went wrong
- Can't retry a single failed lender
- Can't run multiple lenders at the same time

Airflow runs jobs as individual tasks. Each task can be retried on its own, monitored in a dashboard, and run in parallel with others.

### Jobs that run automatically

#### Weekly — NBFC Extraction (Sundays 2am)
Processes all 934 NBFCs from our input list.

```
Split into chunks of 100
         │
         ▼
┌────────────────────────────────────────┐
│  Run 10 chunks at the same time        │
│  Chunk 1 (1–100) · Chunk 2 (101–200)  │
│  Chunk 3 (201–300) · ... Chunk 10     │
└────────────────────┬───────────────────┘
                     │
                     ▼
         Merge all chunks → save to database
```

Each chunk scrapes websites, runs AI extraction, validates the data, and saves results. Failures go to the dead-letter queue (see Layer 2).

#### Monthly — RBI Bank Extraction (1st of month, 3am)
Processes 177 banks across 4 categories (PSU, private, foreign, cooperative) in parallel.

#### Daily — Refresh (1am every day)
```
1. Re-scrape stale lenders (those overdue for update)
2. Apply any new database migrations
3. Refresh Grafana analytics views
4. Create next month's data partition (on the 25th)
```

---

## Layer 2 — Data Collection

### The Problem With Just Scraping

A website might say "Business Loans available" but not say:
- Minimum loan: ₹5 Lakhs
- Minimum CIBIL score: 680
- Only for GST-registered businesses with 2+ years

Plain scraping gets the product name. We need the **actual rules**.

### How We Extract Data (6 Phases)

```
For each lender:

PHASE 1 — Visit the website (scrapling library)
  ├── Check up to 5 pages: home, loans, about, contact
  ├── Pull out: phone, email, city, state, year founded
  ├── Count: employees, branches, loan products
  ├── Detect: is this actually a lender? (confidence score)
  └── Save: operating states, RBI registration number

PHASE 2 — Check if we already have this page cached
  └── If the website hasn't changed since last run → skip Gemini
      This saves money on AI calls (Gemini costs per token)

PHASE 3 — AI Enrichment (Google Gemini Flash)
  ├── We tell Gemini what scraping already found
  ├── Gemini fills in what scraping can't find:
  │     AUM (total loan book), RBI category, revenue,
  │     min/max loan amounts, eligibility rules
  └── Protected by a circuit breaker (see below)

PHASE 4 — Merge (scraper facts win over AI guesses)
  ├── High-confidence scraped data always overrides Gemini
  ├── Gemini fills only when scraping found nothing
  └── Result: best of both sources

PHASE 5 — Validate (17 rules)
  ├── AUM must be between ₹0.1 Cr and ₹10 million Cr
  ├── State names must be real Indian states
  ├── Phone and email formats checked
  ├── Loan product names normalized to 18 standard types
  ├── Duplicate detection (90% similarity threshold)
  ├── Quality score calculated (0% to 100%)
  └── Below 30% quality → rejected to dead-letter queue

PHASE 6 — Write to database
  └── approval_status = 'pending' (admin must review first)
```

### The Circuit Breaker — Protecting Against Gemini Failures

If Gemini starts failing (API errors, timeouts), we don't want to keep hammering it and wasting time.

```
Normal state (CLOSED):
  Every Gemini call goes through → works fine

After 5 failures in a row (OPEN):
  All Gemini calls are blocked immediately
  The error is logged → Grafana shows an alert
  Wait 5 minutes

After 5 minutes (HALF-OPEN):
  Try one call to see if Gemini recovered
  If it works → back to CLOSED (normal)
  If it fails → back to OPEN (wait another 5 min)
```

When the circuit is OPEN, the lender is skipped and sent to the dead-letter queue for retry later.

### The Dead-Letter Queue — Nothing Gets Lost

Every failure is recorded in the `extraction_failures` table with:
- Which lender failed and at which stage
- The exact error message
- The raw AI response (if any)
- Which Airflow run caused it
- A `resolved` flag so admins can track what's been fixed

Admins can see a summary by day and failure stage in Grafana.

### Non-Lender Detection

Some entries in our input list aren't lenders (holding companies, tech vendors, etc.).

- Gemini scores how confident it is that this is NOT a lender (0–100%)
- Above 90% confidence → **automatically rejected** (not sent to admin)
- Below 90% confidence → sent to admin for manual review with a note explaining why

### Is This a Lender? (Confidence Gating)

```
confidence ≥ 90%  →  auto-reject, go to dead-letter
confidence < 90%  →  flag for admin with explanation note
confidence = 0%   →  treat as lender (proceed normally)
```

---

## Layer 3 — Database

### Schema Versioning — "The Pipeline Never Breaks"

Every database change is a numbered file in `database/migrations/`. We currently have 17 migrations.

**The safety rules:**
1. New columns always have default values — old code can still insert rows
2. Columns are never deleted — old queries won't break
3. The migration runner **blocks dangerous changes** before running them:
   - `DROP COLUMN` → blocked
   - `DROP TABLE` → blocked
   - `ADD COLUMN NOT NULL` without a DEFAULT → blocked

```bash
python database/migrate.py          # apply all pending
python database/migrate.py status   # what version are we on?
python database/migrate.py migrate --dry-run   # see what would run
```

### Two Tables — Lender + Policies

**Old design:** One row per lender. Loan types stored as a list. No eligibility rules.

**New design:** Two tables. Lender info in one, loan product rules in the other.

```
lenders (one row per institution)
  id: 42
  company_name: "ABC Finance"
  company_type: "NBFC"
  aum_crores: 1500
  hq_state: "Maharashtra"
  approval_status: "approved"    ← admin controls this
  next_scrape_at: "2026-04-28"   ← when to re-check this lender
       │
       │ one lender has many loan products
       │
       ├── policies (row 1)
       │     product: "MSME Term Loan – Unsecured"
       │     loan_type: "MSME Loan"
       │     min loan: ₹5 Lakhs, max: ₹500 Lakhs
       │     min credit score: 680
       │     employment types: business, self-employed
       │     interest rate: from 12.5%
       │     tenure: up to 60 months
       │     notes: "GSTIN mandatory, 2 years ITR"
       │
       └── policies (row 2)
             product: "Working Capital – Invoice Discounting"
             ...
```

Each policy row is unique by `(lender_id, product_name, loan_type)` — re-running extraction won't create duplicates.

### The Matching Engine

When a borrower submits their profile, we call one database function:

```sql
SELECT * FROM match_lenders(
  loan_type       => 'MSME Loan',
  loan_amount     => 25,      -- ₹25 Lakhs
  credit_score    => 700,
  employment_type => 'business',
  state           => 'Maharashtra',
  age             => 35,
  monthly_income  => 80000
);
```

**How scoring works (100 points total):**

| What we check | Points | Partial credit? |
|---|---|---|
| Credit score meets the minimum | 25 pts | Within 20 pts of minimum → 10 pts |
| Loan amount is in range | 20 pts | Within 15% of the range edge → 8 pts |
| State is covered by lender | 20 pts | No |
| Employment type is accepted | 15 pts | "Any employment" in policy → 12 pts |
| Age is within limits | 5 pts | No |
| Policy data completeness | 15 pts | Scaled by how complete the policy is |

A lender needs at least 15 points to appear in results. Returns lenders ranked highest to lowest.

### Admin Approval — Nothing Goes Live Automatically

```
Extraction runs → approval_status = 'pending' (hidden from public)
                           │
                    Admin reviews in /dashboard
                           │
              ┌────────────┴──────────────┐
              │                           │
           Approve                     Reject
              │                           │
     Goes live immediately          Stays hidden
     (policies also activated)
```

Every approval and rejection is permanently recorded in `lender_audit_log` with:
- Who approved (name + email from their login)
- When (timestamp)
- What IP address they used
- Which request triggered it

This is written by a database stored procedure (`approve_lender_audited`) — the audit record and the status change happen in the same database transaction. They can't get out of sync.

### Cache Invalidation via Database Notifications

When a policy is approved, the database immediately notifies the API:

```
Policy approved in DB
        │
        ▼ (pg_notify — built into PostgreSQL)
FastAPI receives notification
        │
        ▼
Redis cache cleared for:
  - /loans/match (all loan matching results)
  - /policies/filter (all filtered policy results)
```

Users see fresh data within milliseconds of an admin approval — no 5-minute wait for cache to expire.

### Row-Level Security

Supabase enforces access rules at the database level:
- Public users → only see `approval_status = 'approved'` records
- The API uses a service key → can see all records, but applies its own `WHERE approval_status = 'approved'` filters

---

## Layer 4 — API (FastAPI)

The API is the single door between the frontend and the database. The frontend uses Supabase only for login. All data comes through here.

### What the API Does

| Request | What happens |
|---|---|
| `GET /v1/lenders/search` | Search + filter lenders by name, type, state, AUM, loan type |
| `GET /v1/lenders/{id}` | Full details for one lender |
| `GET /v1/policies/filter` | Filter loan products by borrower criteria |
| `POST /v1/loans/match` | Full match — returns ranked lenders for a borrower profile |
| `GET /v1/loans/compare` | Side-by-side comparison of up to 5 loan products |
| `GET /v1/admin/pipeline` | Admin only — all lenders + their pipeline status |
| `POST /v1/admin/lenders/{id}/approve` | Admin only — approve a lender |
| `POST /v1/admin/lenders/{id}/reject` | Admin only — reject with a reason |
| `GET /v1/admin/audit` | Admin only — full approval history |
| `GET /health` | Is the system healthy? DB + cache + metrics |

### Redis Cache — Speed

Results are cached in Redis to avoid hitting the database on every request.

| Endpoint | Cached for |
|---|---|
| Loan match results | 5 minutes |
| Lender search | 2 minutes |
| Lender detail | 10 minutes |

Cache times have ±10% randomness added. This prevents 100 users getting cached results that all expire at the exact same second, causing a sudden flood of database queries.

When a policy is approved, the relevant cache entries are wiped immediately (see pg_notify above).

### Rate Limiting

Limits per minute to prevent abuse:

| Endpoint | Limit |
|---|---|
| Search + detail | 100 requests/min |
| Policy filter | 60 requests/min |
| Match + compare | 30 requests/min |

For logged-in users, the limit is per their user ID. For anonymous users, the limit is per IP address. A logged-in user can't abuse the system by rotating IPs.

### Authentication

All admin routes require a valid login token (JWT) issued by Supabase. The token is checked on every request:

```
Request arrives with Authorization: Bearer <token>
              │
              ▼
API verifies token signature (HS256)
Checks: not expired, has required fields
              │
          If token valid:
              │
              ▼
     Is user admin?
     Checks app_metadata.role = 'admin'
     (set in Supabase dashboard or SQL)
              │
      ┌───────┴────────┐
      │                │
   Yes (admin)     No (regular user)
      │                │
   Allow admin     Allow user routes
   routes only     only
```

### Request Logging

Every single API request produces one structured log line:

```json
{
  "ts": "2026-03-31T14:22:01Z",
  "request_id": "a1b2c3d4-...",
  "method": "POST",
  "path": "/v1/loans/match",
  "status": 200,
  "duration_ms": 43.2,
  "user_id": "uuid-of-the-user",
  "client_ip": "1.2.3.4"
}
```

Authorization headers and cookies are never logged (stripped before writing).

Health check requests are logged at a lower level so they don't clutter the logs.

### In-Process Health Metrics

The API tracks these counters in memory and shows them on `/health`:

```json
{
  "requests_total": 14820,
  "requests_5xx": 0,
  "requests_4xx": 23,
  "cache_hits": 11204,
  "cache_misses": 3616,
  "cache_hit_rate": 0.756,
  "cache_invalidations": 12,
  "db_errors": 0,
  "loans_matched": 892
}
```

### Error Responses

All errors return the same structure — no surprises for the frontend:

```json
{
  "error": {
    "code": "HTTP_404",
    "message": "Lender not found",
    "request_id": "a1b2c3d4-..."
  }
}
```

Validation errors include which field was wrong and why:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": [
      {"field": "credit_score", "message": "must be between 300 and 900", "type": "less_than_equal"}
    ]
  }
}
```

### Startup Safety Check

The API refuses to start if critical secrets are missing:
- `SUPABASE_JWT_SECRET` — required, must be at least 32 characters
- `DATABASE_URL` — required
- `SUPABASE_URL` — required

This prevents a misconfigured deployment from starting and silently serving broken responses.

---

## Layer 5 — Analytics (Grafana)

Grafana connects directly to the database and reads from SQL views.

### Dashboards

**Interest Rate Comparison**
- Average rate per loan type
- Best rate per lender per product
- PSU bank vs private bank vs NBFC comparison
- Daily match search volume

**Best Loans by Credit Score**
- Best available rate for each credit bracket (300–549 / 550–649 / 650–699 / 700–749 / 750+)
- How many lenders accept each score bracket
- Match volume by credit score — last 30 days

**Pipeline Status**
- Daily extraction success rate
- Gemini AI cost per run
- Circuit breaker open events
- Dead-letter queue failures by stage
- Lender approval pipeline counts (pending / approved / rejected)

---

## Continuous Updates (Scheduler)

Lenders change their rates, launch new products, get acquired. Data goes stale.

`scheduler.py` runs every night via Airflow and re-checks lenders that are overdue:

```
1. Ask DB: "which lenders haven't been checked recently?"
           │
           ▼
2. For each stale lender:
   Scrape → Gemini → Merge → Validate (same 6 phases as initial extraction)
   (protected by circuit breaker and retry logic)
           │
           ▼
3. Compare new data vs what we already have

   No changes found          Changes found
        │                         │
   Bump next_scrape_at        Update fields
   forward in time            Set status to 'needs_update'
                              Admin is notified
           │
           ▼
4. Schedule next check:
   NBFCs → every 30 days   (change rates often)
   Banks → every 45–60 days (more stable)
```

### Retry Logic

Every Gemini call is tried up to 3 times with increasing wait times:
- Try 1: fails → wait 10 seconds
- Try 2: fails → wait 20 seconds
- Try 3: fails → write to dead-letter queue

---

## How Data Flows End-to-End

```
START: Input data
  data/input/nbfc_names.csv   (934 NBFCs)
  data/input/rbi_banks/       (177 banks)
         │
         ▼ Airflow runs extraction in parallel chunks
  Chunk files: data/output/nbfc_chunks/YYYY-MM-DD/chunk_NNN.csv
  (date-stamped so every extraction run is reproducible)
         │
         ▼ Merge task — all-or-nothing database transaction
  Database: lenders table   (approval_status = 'pending')
         │
         ▼ Policy extraction runs separately
  Database: policies table  (approval_status = 'pending')
         │
         ▼ Admin logs in to /dashboard
  Admin approves or rejects each lender
         │
         ▼ Immediately on approval:
  lender.approval_status = 'approved'
  All its policies activated
  pg_notify fires → Redis cache cleared
  lender_audit_log entry written (permanent record)
         │
         ┌──────────────┬──────────────────┐
         │              │                  │
         ▼              ▼                  ▼
     Frontend        FastAPI           Grafana
   (searches        (serves           (reads SQL
    /dashboard)      matching)         views)
         │
         ▼ Nightly
  Scheduler checks for stale lenders → re-extracts
  Airflow applies any pending migrations
  Partitions created for next month
```

---

## Security Summary

| What | How |
|---|---|
| Login tokens | Supabase HS256 JWT, verified on every admin/user request |
| Admin role | Set in Supabase database, checked from JWT payload |
| Rate limiting | Per user ID (logged in) or per IP (anonymous) |
| SQL injection | All queries use parameters — never string concatenation |
| Sensitive headers | Authorization, Cookie, API keys stripped from all logs |
| CORS | Explicit origin whitelist — never wildcard in production |
| Security headers | CSP `default-src 'none'; frame-ancestors 'none'` on every response |
| Audit trail | Every admin action permanently logged (who, when, what, from where) |
| Data visibility | RLS enforced at DB level — public only sees approved records |
| Secret validation | API refuses to start without required secrets configured |

---

## Deployment Options

### MVP (API only — fastest to run)
```bash
cp .env.example .env
# Fill in: DATABASE_URL, SUPABASE_JWT_SECRET, SUPABASE_URL, CORS_ORIGINS

python database/migrate.py   # set up database

docker compose -f docker-compose.mvp.yml up
# API → http://localhost:8000
# Docs → http://localhost:8000/docs
```

### Full Stack (everything)
```bash
cp .env.example .env
# Fill in all variables (see .env.example for descriptions)

python database/migrate.py

docker compose up
# API      → http://localhost:8000
# Airflow  → http://localhost:8080
# Grafana  → http://localhost:3001
# Frontend → http://localhost:3000
```

---

## Project Structure

```
lender-platform/
│
├── backend/
│   ├── api/
│   │   ├── main.py              App startup, health check, pg_notify listener
│   │   ├── core/
│   │   │   ├── auth.py          Login token verification, admin role check
│   │   │   ├── cache.py         Redis wrapper, auto-fallback if Redis is down
│   │   │   ├── config.py        All settings from environment variables
│   │   │   ├── exceptions.py    Standard error responses for all failure types
│   │   │   └── metrics.py       In-memory counters exposed on /health
│   │   ├── middleware/
│   │   │   ├── logging.py       One JSON log line per request
│   │   │   └── security.py      Security headers + CSP on every response
│   │   └── routers/
│   │       ├── lenders.py       Search + detail endpoints
│   │       ├── policies.py      Policy filter endpoint
│   │       ├── loans.py         Match + compare endpoints
│   │       └── admin.py         Admin approve/reject/flag/audit
│   │
│   ├── pipeline/
│   │   ├── circuit_breaker.py   Stops hammering Gemini when it's failing
│   │   ├── retry.py             Try again with increasing wait times
│   │   ├── gemini_cache.py      Skip Gemini if page hasn't changed
│   │   └── metrics.py           Record Gemini cost + success rate per run
│   │
│   ├── scraper/
│   │   ├── lender_scraper.py    Visits lender websites, extracts facts
│   │   └── guardrails.py        17 validation rules, quality scoring
│   │
│   ├── run_nbfc_extraction.py   Run pipeline for all 934 NBFCs
│   ├── run_rbi_extraction.py    Run pipeline for 177 RBI banks
│   ├── run_policy_extraction.py Extract loan policy rules, upload to DB
│   └── scheduler.py             Re-check stale lenders nightly
│
├── airflow/
│   └── dags/
│       ├── nbfc_extraction_dag.py    Weekly parallel NBFC extraction
│       └── daily_refresh_dag.py      Daily: refresh + migrate + partitions
│
├── database/
│   ├── migrate.py               Safe migration runner (blocks destructive changes)
│   ├── schema_v2.sql            Base schema
│   └── migrations/
│       ├── 001–013              Schema evolution
│       ├── 014_extraction_failures.sql    Dead-letter queue table
│       ├── 015_admin_audit_trail.sql      Audit log + stored procedure
│       ├── 016_match_lenders_optimized.sql  Partial scoring + performance indexes
│       └── 017_policy_cache_invalidation.sql  pg_notify trigger
│
├── frontend/
│   └── app/
│       ├── dashboard/page.tsx   Main search interface
│       └── components/
│           ├── AuthContext.tsx  Supabase login session
│           ├── SearchFilter.tsx Sidebar filters
│           ├── LenderCard.tsx   One lender result card
│           └── MatchResultCard.tsx  Match result with score
│
├── grafana/                     Dashboard provisioning configs
├── docker-compose.yml           Full stack
├── docker-compose.mvp.yml       API only
└── .env.example                 All variables documented with descriptions
```

---

## Key Decisions — Why We Built It This Way

**Why Airflow and not a simple cron job?**
Cron runs things one at a time and silently swallows failures. Airflow runs 10 chunks in parallel, retries individual failures, and shows a visual dashboard of exactly what succeeded or failed.

**Why a circuit breaker for Gemini?**
Without it, if Gemini goes down, every lender extraction would wait for a timeout before failing. With the circuit breaker, after 5 consecutive failures the system stops trying immediately, waits 5 minutes, then tries again. The pipeline runs much faster during outages.

**Why a dead-letter queue?**
Without it, failed extractions are just lost — no trace of what failed or why. The `extraction_failures` table means every failure is visible, searchable, and can be retried once the underlying issue is fixed.

**Why admin approval before data goes live?**
AI can make mistakes. A wrong interest rate or incorrect eligibility rule would mislead borrowers. The pending → approved workflow means a human always verifies before data is public.

**Why the audit trail?**
Every admin action (approve, reject, flag) is permanently recorded. If something goes wrong — a lender was incorrectly approved, or a rejection was disputed — we have a complete history of who did what, when, and from where.

**Why pg_notify for cache invalidation?**
Without it, after an admin approves a lender, users would see stale results for up to 5 minutes (the cache TTL). With pg_notify, the database tells the API immediately when a policy is approved, the cache is cleared in milliseconds, and users see the new lender right away.

**Why separate lenders and policies tables?**
Two NBFCs both offer "MSME Loans" but one requires CIBIL 750 and the other accepts 650. Without separate policy rows, you can only show "both offer MSME Loans." With policy rows, you can show "lender A doesn't qualify, lender B does."

**Why partial credit scoring?**
A strict pass/fail match misses useful results. If a borrower has credit score 680 and a lender requires 700, they're close — that lender should still appear with a lower score, not disappear entirely. Partial credit within 20 points makes results more helpful.

**Why single API worker?**
The database connection pool lives inside the process. With multiple workers, each would create its own pool — 4 workers × 5 connections = 20 connections, which exceeds Supabase's free tier limit. One worker with multiple containers is the correct way to scale.
