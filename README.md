# MITRAM360 — Lender Intelligence Platform

We help borrowers find the right lender. Not just lenders that exist — lenders that will actually approve them.

---

## The Problem We Solve

A small business owner needs a loan. There are 1,000+ NBFCs and banks in India. Each one has different rules:
- Minimum credit score
- How much they lend (min and max)
- Which states they operate in
- What type of business or employment they accept

The borrower has no way to know who will approve them without applying one by one and getting rejected repeatedly.

**MITRAM360 fixes this.** We collect data from every lender, extract their real eligibility rules using AI, and when a borrower submits their profile — we instantly show them only the lenders they qualify for, ranked best to worst.

---

## How It Works (Simple Version)

```
1. We scrape 1,000+ lender websites + run AI to extract their loan rules
           │
           ▼
2. An admin reviews and approves the data before it goes live
           │
           ▼
3. Borrower submits: credit score, loan amount, state, employment type, age
           │
           ▼
4. System scores every active lender policy 0–100 for that borrower
           │
           ▼
5. Returns ranked list: "These 12 lenders will likely approve you"
```

---

## Tech Stack

| What | Technology |
|---|---|
| Website (frontend) | Next.js 15, TypeScript |
| API server | FastAPI (Python) |
| Speed cache | Redis (auto-skips if not running) |
| Database | Supabase (PostgreSQL 15) |
| Job scheduler | Apache Airflow |
| AI extraction | Google Gemini Flash |
| Web scraping | Scrapling (Python) |
| Analytics | Grafana |
| Error tracking | Sentry (optional) |
| Deployment | Docker Compose |

---

## Getting Started

### What You Need

- Docker and Docker Compose installed
- A Google Gemini API key
- A Supabase project (free tier works for development)

### Step 1 — Set up your secrets

```bash
cp .env.example .env
```

Open `.env` and fill in:
- `GEMINI_API_KEY` — your Google Gemini key
- `SUPABASE_URL` — your Supabase project URL
- `SUPABASE_SERVICE_ROLE_KEY` — from Supabase → Project Settings → API
- `SUPABASE_JWT_SECRET` — from Supabase → Project Settings → API → JWT Secret
- `DATABASE_URL` — your Supabase database connection string

For production, use the PgBouncer connection (port 6543, not 5432):
```
DATABASE_URL=postgresql://postgres:yourpassword@project.pooler.supabase.com:6543/postgres
```

### Step 2 — Set up the database

```bash
python database/migrate.py
```

This runs all 17 migrations in order. Safe to run multiple times — skips anything already applied.

### Step 3 — Start the services

**MVP (just the API — quickest way to start):**
```bash
docker-compose -f docker-compose.mvp.yml up
```

**Full stack (API + Airflow + Grafana + Redis):**
```bash
docker-compose up
```

| Service | Address |
|---|---|
| API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| Airflow | http://localhost:8080 |
| Grafana | http://localhost:3001 |
| Frontend | http://localhost:3000 |

### Step 4 — Load lender data

In the Airflow UI (http://localhost:8080), trigger the `nbfc_extraction` DAG manually. It will:
1. Split 934 NBFCs into batches of 100
2. Process up to 10 batches in parallel (scraping + AI + validation)
3. Save everything to the database as `pending`

### Step 5 — Approve lenders

Go to `/dashboard` (you need an admin account). Review the pending lenders and approve them. They go live immediately.

Every approval is permanently recorded — who approved it, when, and from which IP address.

---

## API Endpoints

All routes are under `/v1/`. Admin routes require a login token with the admin role.

### Anyone can use these (no login needed)

```
GET  /v1/lenders/search          Search and filter lenders
GET  /v1/lenders/{id}            Full details for one lender
GET  /v1/policies/filter         Filter loan products by borrower criteria
POST /v1/loans/match             Get ranked lenders for a borrower profile
GET  /v1/loans/compare           Compare up to 5 loan products side by side
```

### Admin only (login + admin role required)

```
GET  /v1/admin/pipeline              All lenders + where they are in the pipeline
POST /v1/admin/lenders/{id}/approve  Approve a lender (goes live immediately)
POST /v1/admin/lenders/{id}/reject   Reject with a reason
POST /v1/admin/lenders/{id}/flag     Flag for immediate re-scrape
GET  /v1/admin/audit                 Full approval history
GET  /v1/admin/pipeline-runs         AI cost and success rate per extraction run
GET  /v1/admin/stats                 Dashboard summary numbers
```

### System health

```
GET  /health                     Is everything working? DB + cache + metrics
GET  /schema-version             What database version are we on?
```

### Example — find lenders for a borrower

```bash
curl -X POST http://localhost:8000/v1/loans/match \
  -H "Content-Type: application/json" \
  -d '{
    "loan_type": "MSME Loan",
    "loan_amount": 25,
    "credit_score": 720,
    "employment_type": "business",
    "state": "Maharashtra",
    "monthly_income": 80000,
    "age": 35
  }'
```

Returns lenders scored 0–100, ordered by score then interest rate.

---

## How Scoring Works

When a borrower submits their profile, every active lender policy is scored out of 100:

| What we check | Max points | How |
|---|---|---|
| Credit score meets minimum | 25 pts | Full if eligible, 10 pts if within 20 of minimum |
| Loan amount is in range | 20 pts | Full if in range, 8 pts if within 15% of the edge |
| State is covered | 20 pts | Full if state matches or lender is pan-India |
| Employment type accepted | 15 pts | Full if accepted, 12 pts if policy says "any" |
| Age within limits | 5 pts | Full if within limits |
| Policy data completeness | 15 pts | How complete the policy data is |

Minimum score to appear in results: **15 points**.

---

## Data Pipeline

### How we extract data from each lender

```
For each lender website:

Phase 1 — Scrape the website
  Extract: phone, email, address, states, loan products, RBI number
  Detect: is this actually a lender? (confidence scored)

Phase 2 — Check if page changed
  If unchanged since last run → skip AI (saves cost)

Phase 3 — Run Google Gemini AI
  Fill in what scraping can't find:
  AUM, RBI category, loan amounts, eligibility rules
  Protected by circuit breaker — stops automatically if Gemini keeps failing

Phase 4 — Merge results
  Scraped facts always override AI guesses

Phase 5 — Validate (17 rules)
  Quality score 0–100%
  Below 30% → rejected to dead-letter queue
  Non-lender (>90% confidence) → auto-rejected

Phase 6 — Save to database
  approval_status = 'pending' until admin approves
```

### If something fails — the dead-letter queue

Every failure is saved to the `extraction_failures` table with the exact error, which stage failed, and the raw AI response. Nothing is silently dropped. Admins can see failures in Grafana and retry them.

### Keeping data fresh

`scheduler.py` runs every night and re-checks lenders that are overdue for an update:
- NBFCs: re-checked every 30 days
- Banks: re-checked every 45–60 days

If something changed, the lender is flagged for admin review. If nothing changed, it's quietly scheduled for the next check.

---

## Database

17 migrations, applied in order. The migration runner blocks destructive changes (`DROP COLUMN`, `DROP TABLE`, `NOT NULL` without a default).

### Main tables

| Table | What's in it |
|---|---|
| `lenders` | One row per lending institution (44 fields) |
| `policies` | One row per loan product per lender (amounts, rates, eligibility) |
| `matching_requests` | Every borrower search — partitioned by month for performance |
| `lender_audit_log` | Permanent record of every admin approval/rejection |
| `extraction_failures` | Dead-letter queue — every failed extraction logged here |
| `pipeline_runs` | AI cost, success rate, token counts per extraction run |
| `schema_versions` | Which migrations have been applied |

### Run migrations

```bash
python database/migrate.py                      # apply all pending
python database/migrate.py status               # check current version
python database/migrate.py migrate --dry-run    # see what would run
python database/migrate.py migrate --max-version 015  # apply up to version 015
```

---

## Security

| What | How |
|---|---|
| Startup | Server refuses to boot if `SUPABASE_JWT_SECRET`, `DATABASE_URL`, or `SUPABASE_URL` are missing |
| Login tokens | Supabase JWT verified on every admin/user request |
| Admin role | Checked from JWT, set in Supabase database |
| Rate limiting | Per user ID when logged in, per IP for anonymous |
| SQL injection | Every query uses parameters — never string concatenation |
| Sensitive data | Authorization headers and cookies stripped before logs or Sentry |
| CORS | Explicit origin whitelist — never wildcard in production |
| Security headers | `default-src 'none'; frame-ancestors 'none'` on every response |
| Audit trail | Every admin action permanently logged (who, when, from where) |
| Data visibility | Database-level rules — public only sees approved records |

### Make a user admin

```sql
UPDATE auth.users
SET raw_app_meta_data = raw_app_meta_data || '{"role": "admin"}'::jsonb
WHERE email = 'admin@yourcompany.com';
```

---

## Health Check

Hit `/health` to see the current system state:

```json
{
  "status": "ok",
  "db": { "ok": true, "version": "15.1" },
  "cache": { "ok": true },
  "pool": { "size": 3, "idle": 2, "max": 5 },
  "metrics": {
    "requests_total": 14820,
    "requests_5xx": 0,
    "requests_4xx": 23,
    "cache_hits": 11204,
    "cache_misses": 3616,
    "cache_hit_rate": 0.756,
    "db_errors": 0,
    "loans_matched": 892
  }
}
```

---

## Grafana Dashboards (port 3001)

Connects directly to the database. Shows:
- Daily extraction success rate and Gemini AI cost
- Lender pipeline counts (pending / approved / rejected)
- Match volume by loan type and state
- Cache hit rate over time
- Dead-letter queue failures by stage
- Circuit breaker open events

---

## Configuration

All settings are environment variables — no code changes needed to adjust thresholds.

```bash
# Database connection pool (Supabase free tier: ~20 connections total)
DB_POOL_MIN=2
DB_POOL_MAX=5

# How long to cache results (seconds)
CACHE_TTL_MATCH=300      # match results: 5 minutes
CACHE_TTL_SEARCH=120     # search results: 2 minutes
CACHE_TTL_DETAIL=600     # lender detail: 10 minutes

# Data quality
GUARDRAILS_MIN_QUALITY=0.30              # reject if below 30%
GUARDRAILS_NON_LENDER_CONFIDENCE=0.90   # auto-reject non-lenders above 90%

# Gemini circuit breaker
GEMINI_CIRCUIT_THRESHOLD=5     # open after 5 consecutive failures
GEMINI_CIRCUIT_RESET_S=300     # try again after 5 minutes

# Gemini retry
GEMINI_RETRY_ATTEMPTS=3
GEMINI_RETRY_DELAY_S=10.0      # doubles each attempt (10s → 20s → 40s)

# Error tracking
SENTRY_TRACES_RATE=0.10        # sample 10% of requests
```

See `.env.example` for the full list with descriptions.

---

## Project Structure

```
lender-platform/
├── backend/
│   ├── api/
│   │   ├── main.py               App entry point, health check, startup validation
│   │   ├── core/
│   │   │   ├── auth.py           JWT verification, admin role check
│   │   │   ├── cache.py          Redis wrapper, auto-fallback if Redis is down
│   │   │   ├── config.py         All settings from environment variables
│   │   │   ├── exceptions.py     Standard error format for all failure types
│   │   │   └── metrics.py        In-memory counters exposed on /health
│   │   ├── middleware/
│   │   │   ├── logging.py        One JSON log line per request
│   │   │   └── security.py       Security headers on every response
│   │   └── routers/
│   │       ├── lenders.py        Search + detail
│   │       ├── policies.py       Policy filter
│   │       ├── loans.py          Match + compare
│   │       └── admin.py          Approve / reject / flag / audit
│   ├── pipeline/
│   │   ├── circuit_breaker.py    Stops hammering Gemini when it's failing
│   │   ├── retry.py              Try again with increasing wait times
│   │   ├── gemini_cache.py       Skip Gemini if page hasn't changed
│   │   └── metrics.py            Record cost + success rate per run
│   ├── scraper/
│   │   ├── lender_scraper.py     Website scraper
│   │   └── guardrails.py         17 validation rules + quality scoring
│   └── scheduler.py              Nightly re-check of stale lenders
├── airflow/
│   └── dags/
│       ├── nbfc_extraction_dag.py    Weekly parallel extraction
│       └── daily_refresh_dag.py      Daily refresh + migrations + partitions
├── database/
│   ├── migrate.py                Safe migration runner
│   ├── schema_v2.sql             Base schema
│   └── migrations/               001 through 017
├── frontend/
│   └── app/
│       ├── components/           AuthContext, LenderCard, SearchFilter, MatchResultCard
│       └── dashboard/            Main app page
├── grafana/                      Dashboard configs
├── docker-compose.yml            Full stack
├── docker-compose.mvp.yml        API only
└── .env.example                  All variables documented
```
