# External Integrations

**Analysis Date:** 2026-05-06

## APIs & External Services

**AI / LLM:**
- Google Gemini API — Two distinct usages:
  1. Chatbot (API server): Two-pass RAG pipeline in `backend/api/core/gemini.py` — Pass 1 classifies intent/extracts entities, Pass 2 generates grounded answers from DB records. Client: `google-genai>=1.0.0`.
  2. Data extraction pipeline: NBFC/policy data extraction in `backend/enrich_lenders.py`, `backend/run_policy_extraction.py`. Client: `google-generativeai>=0.7.0`.
  - Auth: `GEMINI_API_KEY` env var
  - Circuit breaker: `backend/pipeline/circuit_breaker.py` — wraps all pipeline Gemini calls, opens after 5 consecutive failures, resets after 300s

**Web Scraping — Firecrawl:**
- Firecrawl API — JS-rendered page scraping for lenders whose sites require JavaScript (SBI, HDFC, ICICI class sites)
  - Endpoint: `https://api.firecrawl.dev/v1/scrape`
  - Used in: `backend/scraper/policy_scraper.py`, `backend/scraper/firecrawl_fallback.py`
  - Fallback scope: fills `employee_count`, `branch_count`, `established_year` only — scraper wins on all other fields (per A/B test April 2026)
  - Auth: `FIRECRAWL_API_KEY` env var

**Aggregator Enrichment:**
- BankBazaar — loan rate aggregator, Rank 2 enricher
  - Scraped via requests+BeautifulSoup in `backend/enrichers/bankbazaar.py`
  - URLs hardcoded by loan type (e.g. `bankbazaar.com/business-loan.html`)
  - No API key — public HTML scraping
- BSE (Bombay Stock Exchange) — XBRL financial filings for listed NBFCs, Rank 4 (highest trust)
  - `backend/enrichers/bse_xbrl.py` — fetches annual XBRL from `bseindia.com/xml-data/corpfiling/`
  - CIN → scrip code lookup via BSE list API
  - No API key — public data

**PDF Data Extraction:**
- Fair Practice Code PDFs — Rank 3 enricher
  - `backend/enrichers/fpc_pdf.py` — downloads annual report PDFs from lender IR pages, parses interest rate tables with `pdfplumber`
  - No external API — direct PDF download + deterministic table extraction

## Data Storage

**Databases:**
- Supabase PostgreSQL 15
  - Connection: `DATABASE_URL` env var (direct asyncpg pool in API; psycopg2 in migration runner)
  - Client: `asyncpg` (async pool) in FastAPI; `supabase-py` in pipeline scripts; `psycopg2-binary` in `database/migrate.py`
  - Extensions required: `pg_trgm` (trigram fuzzy search on company names), `pgcrypto` (uuid functions)
  - RLS: Enabled — admin routes bypass RLS via service-role connection; public routes use anon role
  - 45+ migrations in `database/migrations/` — tracked in `schema_versions` table

- Airflow Metadata DB (local PostgreSQL 15-alpine in docker-compose)
  - Separate from main DB — stores only Airflow state, DAG runs, XCom values
  - Connection: `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN` env var

**File Storage:**
- Local filesystem only — scraped data in `data/input/`, `data/output/`
- No cloud blob storage (S3/GCS) integrated

**Caching:**
- Redis 7 — response cache for FastAPI
  - Connection: `REDIS_URL` env var
  - Client: `redis.asyncio` (async)
  - Graceful degradation: `NullCache` used when Redis unavailable — `backend/api/core/cache.py`
  - TTLs: MATCH=300s, SEARCH=120s, DETAIL=600s, STATS=300s (with ±10% jitter)
  - Key namespace: `lp:{endpoint}:{sha256_digest[:32]}`

## Authentication & Identity

**Auth Provider:**
- Supabase Auth — handles signup, signin, email verification, password reset
  - Frontend: `@supabase/supabase-js` client in `frontend/app/components/AuthContext.tsx` and `frontend/app/lib/supabase.ts`
  - JWTs signed with ES256 (asymmetric). Backend verifies via JWKS endpoint: `{SUPABASE_URL}/auth/v1/.well-known/jwks.json` — `backend/api/core/auth.py`
  - HS256 fallback for anon/service-role tokens using `SUPABASE_JWT_SECRET`
  - Admin role: set via `raw_app_meta_data` → `{"role": "admin"}` in Supabase SQL. Checked in `_is_admin()` in `backend/api/core/auth.py`
  - FastAPI dependencies: `AuthUser`, `AdminUser`, `OptionalUser` type aliases in `backend/api/core/auth.py`

## Monitoring & Observability

**Error Tracking:**
- Sentry — optional, initialized at startup in `backend/api/main.py`
  - Integrations: `FastApiIntegration`, `AsyncPGIntegration`
  - Auth: `SENTRY_DSN` env var (disabled if not set)
  - 5xx responses also captured in `StructuredLoggingMiddleware` (`backend/api/middleware/logging.py`)
  - Traces sample rate: configurable via `SENTRY_TRACES_RATE` (default 10%)

**Metrics:**
- In-process `InMemoryMetrics` — thread-safe counters, exposed on `GET /health`
  - `backend/api/core/metrics.py` — tracks: requests.total, 5xx/4xx counts, cache hit/miss rates, DB errors
  - Not Prometheus — no external scraping

**Logs:**
- Structured JSON request logs from `StructuredLoggingMiddleware` — includes request_id, method, path, status, duration_ms, user_id, client_ip
- Standard Python `logging` module with INFO level, timestamped format

**Dashboards:**
- Grafana 10.4.2 — analytics dashboards on port 3001
  - Provisioned via `grafana/provisioning/` and `grafana/dashboards/`
  - Data source: direct PostgreSQL connection to main Supabase DB
  - Auth: `GRAFANA_PG_HOST`, `GRAFANA_PG_USER`, `GRAFANA_PG_PASSWORD` env vars

## CI/CD & Deployment

**Hosting:**
- Backend API: Render (auto-deploy via deploy hook)
- Frontend: Vercel (`https://lender-platform.vercel.app`)
- Vercel preview deployments: `lender-platform-*.vercel.app` (CORS regex allowed)

**CI Pipeline:**
- GitHub Actions — `.github/workflows/deploy.yml`
  - `test-api`: import check + Pydantic model validation
  - `lint-frontend`: `npm ci` + `tsc --noEmit`
  - `check-migrations`: validates SQL files against forbidden patterns (DROP COLUMN, DROP TABLE without IF EXISTS, NOT NULL without DEFAULT)
  - `deploy-api`: triggers Render deploy hook (main branch only)
  - `run-migrations`: runs `database/migrate.py` post-deploy

## Webhooks & Callbacks

**Incoming:**
- Render deploy hook (POST from GitHub Actions) — triggers API redeploy

**Outgoing:**
- None configured

## External Data Sources (Regulatory / Reference)

**RBI NBFC Registry:**
- `data/input/rbi_nbfc_registry.csv` — local snapshot used by `backend/pipeline/nbfc_validator.py` for CoR verification
- `backend/import_rbi_nbfc_list.py` — imports RBI's publicly available NBFC list (Excel)
- `backend/seed_rbi_registry.py` — seeds `rbi_registry` DB table

**MCA21 (Ministry of Corporate Affairs):**
- `backend/mca21_enrich.py` — enriches lenders with CIN, company status, authorized capital from MCA21 registry
- Writes to `cin`, `company_status`, `authorized_capital_lakhs`, `paid_up_capital_lakhs` columns

**BSE Financial Data:**
- `backend/fetch_bse_financials.py`, `backend/fetch_bse_aum.py` — fetches AUM and revenue from BSE filings for listed NBFCs

---

*Integration audit: 2026-05-06*
