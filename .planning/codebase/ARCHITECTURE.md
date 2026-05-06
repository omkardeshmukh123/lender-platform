# Architecture

**Analysis Date:** 2026-05-06

## Pattern Overview

**Overall:** Three-tier application with a separate data pipeline subsystem.

**Key Characteristics:**
- Strict separation between the live API (`backend/api/`) and the offline data pipeline (`backend/scraper/`, `backend/enrichers/`, `backend/pipeline/`, Airflow DAGs)
- No ORM — raw SQL via `asyncpg` for all database access in the API
- Two-stage admin approval workflow: all scraped data enters as `pending` before becoming visible to users
- Stateless API server — all state in PostgreSQL + Redis; allows horizontal scaling (though currently single worker)
- The frontend calls the FastAPI backend directly; Supabase is used only for auth (not as a data API)

## Layers

**API Layer:**
- Purpose: Serves HTTP requests, validates input, checks cache, queries DB, serializes responses
- Location: `backend/api/`
- Contains: `main.py` (app factory + lifespan), `routers/` (route handlers), `models/` (Pydantic schemas), `core/` (auth, cache, config, exceptions, metrics), `middleware/` (logging, security), `dependencies.py` (FastAPI `Depends`)
- Depends on: PostgreSQL pool (`app.state.db`), Redis cache (`app.state.cache`), Supabase JWKS endpoint
- Used by: Next.js frontend, Airflow DAGs (indirectly via direct DB writes)

**Frontend Layer:**
- Purpose: User-facing React SPA — search, filter, compare lenders; borrower matching; AI chatbot; admin panel
- Location: `frontend/app/`
- Contains: Next.js App Router pages (`page.tsx` files), shared components (`components/`), auth context (`components/AuthContext.tsx`), Supabase client singleton (`lib/supabase.ts`)
- Depends on: FastAPI (`NEXT_PUBLIC_API_URL`), Supabase Auth (`NEXT_PUBLIC_SUPABASE_*`)
- Used by: End users (borrowers, DSAs, admins)

**Data Pipeline Layer:**
- Purpose: Offline data acquisition, validation, enrichment, and DB population
- Location: `backend/scraper/`, `backend/enrichers/`, `backend/pipeline/`, `backend/*.py` scripts
- Contains: `lender_scraper.py`, `policy_scraper.py`, `guardrails.py`, enrichers (BankBazaar, BSE XBRL, FPC PDF), `borrower_evaluator.py`, `circuit_breaker.py`, `retry.py`, `gemini_cache.py`, `nbfc_validator.py`
- Depends on: External sites (scrapling/requests/Firecrawl), Gemini API, Supabase (direct writes)
- Used by: Airflow DAGs (scheduled triggers), manual CLI scripts

**Orchestration Layer:**
- Purpose: Schedules and executes pipeline tasks — daily refresh, NBFC/policy extraction, RBI import
- Location: `airflow/dags/`
- Contains: `daily_refresh_dag.py`, `nbfc_extraction_dag.py`, `policy_extraction_dag.py`, `policy_enrichment_dag.py`, `rbi_extraction_dag.py`
- Depends on: Backend pipeline scripts, `database/migrate.py`
- Used by: Airflow scheduler (cron: daily 01:00 UTC)

**Database Layer:**
- Purpose: Persistent storage, matching engine (SQL function), materialized views for dashboard stats
- Location: `database/schema_v2.sql`, `database/migrations/001–045+`, `database/migrate.py`
- Contains: `lenders`, `policies`, `policies_enriched`, `matching_requests`, `rbi_registry`, `grievance_officers`, `pipeline_runs`, `chat_sessions`, `leads`, `approval_audit`, `schema_versions` tables
- Core function: `match_lenders()` — PostgreSQL stored function that performs hard eligibility filtering and initial scoring entirely in SQL

## Data Flow

**Borrower Match Flow:**

1. User submits borrower profile (loan type, amount, credit score, employment, state, income) on `/dashboard`
2. `POST /v1/loans/match` — `BorrowerProfile` validated by Pydantic model
3. Router queries `match_lenders()` SQL function — hard eligibility filter + initial 100-pt scoring
4. `pipeline/borrower_evaluator.py::rank_policies()` — second-pass Python scoring: FOIR calculation, employment compatibility, 6-dimension scoring, human-readable explanations
5. Results cached in Redis with TTL=300s + jitter
6. Match request logged to `matching_requests` table (async background task)
7. Frontend renders ranked `MatchedLoan` objects with scores, warnings, disqualifiers

**Lender Discovery Flow:**

1. User searches on `/dashboard` with filters (state, loan type, AUM, company type, etc.)
2. `GET /v1/lenders/search` — query parameters validated, allowlisted against `VALID_*` constants
3. Parameterized SQL query against `lenders` table with dynamic `WHERE` clause
4. Phase 2: If < 3 ILIKE results, trigram fallback (`similarity() > 0.25`) is attempted
5. Phase 3: If 0 exact results, `rbi_registry` stubs are returned alongside fuzzy results
6. `policies_enriched` joined inline to surface `min_interest_rate` per lender
7. Results cached (TTL=120s), returned as `LenderSearchResponse`

**Data Ingestion Flow (Pipeline):**

1. Airflow `daily_refresh_dag.py` triggers at 01:00 UTC
2. `run_migrations()` — applies any pending SQL migrations first
3. `run_scheduler()` — finds lenders with `next_scrape_at` due, batches 50 at a time
4. For each lender: `lender_scraper.py` (Scrapling) → Firecrawl fallback (if JS-rendered) → `guardrails.py` validation → `nbfc_validator.py` regulatory checks → Gemini extraction → write to `lenders` table as `pending`
5. Policy scraping: own site → Firecrawl → BankBazaar → PaisaBazaar → FPC PDF enrichment — all regex only, no LLM
6. Admin reviews pending records via `GET /v1/admin/pipeline` panel
7. Admin approves → `approval_status = 'approved'` → record becomes public

**Chat Flow:**

1. User sends message via `ChatPanel` component → `POST /v1/chat`
2. Chat history loaded from `chat_sessions` table (last N turns, configurable)
3. Gemini Pass 1 (`parse_intent()`): classifies intent + extracts filter entities (no answer generated)
4. Router executes DB query matching extracted filters (same logic as search)
5. If zero results, filter broadening applied (`_BROADENING_DROP_ORDER`)
6. Gemini Pass 2 (`generate_grounded_answer()`): answers strictly from DB records — no hallucination
7. Response streamed back (`StreamingResponse`) with lender results attached

**State Management (Frontend):**
- `AuthContext` — React context wrapping Supabase auth session; provides `user`, `signIn`, `signOut`
- `SaveContext` — React context for client-side saved/shortlisted lenders (in-memory + `localStorage`)
- No global state library (no Redux/Zustand) — local `useState` + contexts

## Key Abstractions

**BorrowerProfile:**
- Purpose: Represents a borrower's loan application criteria
- Examples: `backend/api/models/loan.py`
- Pattern: Pydantic `BaseModel` with strict validators on `loan_type` and `employment_type` against allowlists in `backend/api/core/constants.py`

**Policy / policies_enriched:**
- Purpose: One row per lender × loan product variant; the atomic unit for eligibility matching
- Examples: `database/schema_v2.sql`, `backend/api/models/policy.py`
- Pattern: `policies` table stores raw scraped data; `policies_enriched` is a materialized view that auto-refreshes on policy writes (migration 045)

**Config Singleton (`cfg`):**
- Purpose: Single source of truth for all tunable configuration
- Examples: `backend/api/core/config.py`
- Pattern: Class-level attributes read from env vars at import time. Import as `from core.config import cfg`

**Cache Abstraction:**
- Purpose: Transparent Redis caching with NullCache fallback
- Examples: `backend/api/core/cache.py`
- Pattern: `RedisCache` wraps async Redis client; `NullCache` is a no-op subclass. Both expose identical interface. Injected via `Depends(get_cache)`.

**EvaluationResult:**
- Purpose: Carries per-policy match output from the borrower evaluator
- Examples: `backend/pipeline/borrower_evaluator.py`
- Pattern: Python `@dataclass` with `eligible`, `score`, `disqualifiers`, `warnings`, `explanation` fields

**GeminiCircuitBreaker:**
- Purpose: Prevents pipeline cascade failure when Gemini is rate-limited or down
- Examples: `backend/pipeline/circuit_breaker.py`
- Pattern: CLOSED → OPEN → HALF_OPEN state machine with threading.Lock

## Entry Points

**FastAPI Application:**
- Location: `backend/api/main.py`
- Triggers: Uvicorn `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Responsibilities: App factory, lifespan (DB pool + Redis init), middleware registration, router mounting, CORS, rate limiting

**Next.js Application:**
- Location: `frontend/app/layout.tsx`
- Triggers: `next start` (production) / `next dev` (development)
- Responsibilities: Root layout, wraps app in `AuthProvider` + `SaveProvider`, SEO metadata

**Database Migration Runner:**
- Location: `database/migrate.py`
- Triggers: GitHub Actions post-deploy job, Airflow `daily_refresh_dag.py` task 1
- Responsibilities: Sequential migration application with checksum verification against `schema_versions`

**Airflow DAGs:**
- Location: `airflow/dags/daily_refresh_dag.py` (and 4 others)
- Triggers: Cron schedule (daily 01:00 UTC for refresh DAG)
- Responsibilities: Orchestrate scraping, enrichment, migration, materialized view refresh, pipeline metrics recording

## Error Handling

**Strategy:** Centralized exception handlers registered at app startup; 503 for all DB/external service failures (never 500 from expected conditions)

**Patterns:**
- All DB errors in routers caught → `HTTPException(503, "Service temporarily unavailable")` + `metrics.inc("db.error_count")`
- Unhandled exceptions caught by `register_exception_handlers()` in `backend/api/core/exceptions.py` → JSON `{"error": {"code": "INTERNAL_ERROR", ...}}` with `request_id`
- Validation errors from Pydantic → 422 with field-level detail in `{"error": {"code": "VALIDATION_ERROR", "details": [...]}}`
- Pipeline errors: `GeminiCircuitBreaker` for Gemini; `retry.py` with exponential backoff for transient failures
- Redis failures: silently degrade to `NullCache` — API continues without caching

## Cross-Cutting Concerns

**Logging:** Structured JSON via `StructuredLoggingMiddleware` (`backend/api/middleware/logging.py`) — every request logged with `request_id`, duration_ms, user_id. Health endpoints at DEBUG level only. Sentry captures 5xx responses.

**Validation:** Two layers — Pydantic model validation (422 on failure) + business logic allowlists (`VALID_LOAN_TYPES`, `VALID_COMPANY_TYPES`, etc. in `backend/api/core/constants.py`)

**Authentication:** FastAPI `Depends` pattern — `AuthUser`, `AdminUser`, `OptionalUser` type aliases from `backend/api/core/auth.py`. Admin check: `app_metadata.role == "admin"` in Supabase JWT. Public routes use no auth dependency.

**Rate Limiting:** `slowapi` with per-endpoint limits: search 100/minute, stats 60/minute, detail 200/minute, lender request 10/hour

**Security Headers:** `SecurityHeadersMiddleware` (`backend/api/middleware/security.py`) — adds X-Frame-Options, CSP, HSTS (HTTPS only), cache no-store defaults

---

*Architecture analysis: 2026-05-06*
