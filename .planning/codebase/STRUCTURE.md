# Codebase Structure

**Analysis Date:** 2026-05-06

## Directory Layout

```
lender-platform/
├── backend/                     # All Python — API, pipeline, scrapers
│   ├── api/                     # FastAPI application (deployed to Render)
│   │   ├── main.py              # App factory, lifespan, middleware, router mounting
│   │   ├── dependencies.py      # FastAPI Depends: get_db, get_cache
│   │   ├── limiter.py           # slowapi limiter instance
│   │   ├── core/                # Shared cross-cutting modules
│   │   │   ├── auth.py          # JWT verification, role deps (AuthUser, AdminUser)
│   │   │   ├── cache.py         # RedisCache + NullCache abstraction
│   │   │   ├── config.py        # cfg singleton — all env var config
│   │   │   ├── constants.py     # VALID_LOAN_TYPES, VALID_COMPANY_TYPES, etc.
│   │   │   ├── exceptions.py    # Centralized exception handlers
│   │   │   ├── gemini.py        # Chatbot Gemini client (two-pass RAG)
│   │   │   └── metrics.py       # InMemoryMetrics singleton
│   │   ├── middleware/
│   │   │   ├── logging.py       # StructuredLoggingMiddleware (JSON logs)
│   │   │   └── security.py      # SecurityHeadersMiddleware
│   │   ├── models/              # Pydantic response/request schemas
│   │   │   ├── lender.py        # LenderSummary, LenderDetail, GrievanceOfficer
│   │   │   ├── loan.py          # BorrowerProfile, MatchedLoan, MatchResponse
│   │   │   └── policy.py        # Policy, PolicyFilterResponse
│   │   ├── routers/             # Route handlers (one file per resource)
│   │   │   ├── admin.py         # /v1/admin/* (AdminUser required)
│   │   │   ├── chat.py          # /v1/chat (AuthUser required)
│   │   │   ├── leads.py         # /v1/leads (public)
│   │   │   ├── lenders.py       # /v1/lenders/search, /{id}, /stats, /request
│   │   │   ├── loans.py         # /v1/loans/match, /compare
│   │   │   └── policies.py      # /v1/policies/filter
│   │   └── requirements.txt     # API-specific deps (fastapi, asyncpg, PyJWT, etc.)
│   ├── pipeline/                # Reusable pipeline components (shared by scripts + API)
│   │   ├── borrower_evaluator.py # Python-side match scoring + FOIR + explanations
│   │   ├── circuit_breaker.py   # GeminiCircuitBreaker (CLOSED/OPEN/HALF_OPEN)
│   │   ├── gemini_cache.py      # Caches Gemini extraction results
│   │   ├── metrics.py           # Pipeline-side metrics recording
│   │   ├── nbfc_validator.py    # 10-check regulatory validation layer
│   │   └── retry.py             # Exponential backoff retry decorator
│   ├── scraper/                 # Web scraping modules
│   │   ├── lender_scraper.py    # Primary lender scraper v3.4 (Scrapling)
│   │   ├── policy_scraper.py    # Policy/rate scraper v2.0 (requests + BS4)
│   │   ├── guardrails.py        # Production validation pipeline v4.1
│   │   ├── firecrawl_fallback.py # Firecrawl for JS-rendered fields only
│   │   ├── ab_test_firecrawl.py # A/B test framework (scraper vs Firecrawl)
│   │   └── requirements_scraper.txt # Scraper-specific deps
│   ├── enrichers/               # External data enrichers
│   │   ├── bankbazaar.py        # BankBazaar rate aggregator (Rank 2)
│   │   ├── bse_xbrl.py          # BSE XBRL financial filings (Rank 4, highest trust)
│   │   └── fpc_pdf.py           # FPC/IR PDF extraction via pdfplumber (Rank 3)
│   ├── migrations/              # Legacy migration folder (use database/migrations/)
│   ├── tests/                   # pytest test suite
│   │   ├── test_bank_manager.py
│   │   ├── test_chatbot_guardrails.py
│   │   └── test_enrichers.py
│   ├── *.py                     # CLI pipeline scripts (enrich_lenders.py, import_*.py, etc.)
│   ├── requirements.txt         # Pipeline deps (scrapling, gemini, supabase, pdfplumber, etc.)
│   └── railway.toml             # Render/Railway deploy config
├── frontend/                    # Next.js 14 App Router application
│   ├── app/                     # Next.js App Router root
│   │   ├── layout.tsx           # Root layout — AuthProvider + SaveProvider
│   │   ├── page.tsx             # Landing page (/)
│   │   ├── error.tsx            # Error boundary
│   │   ├── not-found.tsx        # 404 page
│   │   ├── sitemap.ts           # Dynamic sitemap generation
│   │   ├── components/          # Shared React components
│   │   │   ├── AuthContext.tsx  # Supabase auth context + session management
│   │   │   ├── SaveContext.tsx  # Saved/shortlisted lenders context
│   │   │   ├── Navbar.tsx       # Top navigation bar
│   │   │   ├── Hero.tsx         # Landing page hero section
│   │   │   ├── Footer.tsx       # Page footer
│   │   │   ├── LenderCard.tsx   # Lender summary card + IntentModal
│   │   │   ├── MatchResultCard.tsx # Borrower match result card
│   │   │   ├── SearchFilter.tsx # Filter sidebar (loan type, state, AUM, etc.)
│   │   │   ├── StatsSection.tsx # Platform stats display
│   │   │   ├── StubCard.tsx     # RBI registry stub card (unscraped lenders)
│   │   │   └── ChatPanel.tsx    # AI chatbot panel
│   │   ├── lib/
│   │   │   └── supabase.ts      # Supabase client singleton
│   │   ├── admin/               # Admin panel (approval workflow)
│   │   │   ├── page.tsx         # Admin dashboard page
│   │   │   └── EditLenderPanel.tsx # Inline lender edit form
│   │   ├── dashboard/
│   │   │   └── page.tsx         # Main search/browse/match page
│   │   ├── lender/[id]/
│   │   │   └── page.tsx         # Individual lender detail page (client component)
│   │   ├── lenders/[slug]/
│   │   │   └── page.tsx         # SEO landing pages by state/loan type (SSG)
│   │   ├── login/               # Auth pages (login, signup, verify, forgot/reset password)
│   │   ├── signup/
│   │   ├── verify/
│   │   ├── forgot-password/
│   │   └── reset-password/
│   ├── public/                  # Static assets (logo.png, etc.)
│   ├── next.config.js           # Next.js config (minimal)
│   ├── tailwind.config.js       # Tailwind CSS config
│   ├── tsconfig.json            # TypeScript config
│   └── package.json             # Frontend deps
├── database/                    # Schema + migration runner
│   ├── schema_v2.sql            # Canonical baseline schema (lenders, policies, match_lenders())
│   ├── migrate.py               # Migration runner — applies pending migrations sequentially
│   └── migrations/              # SQL migration files (001–045+)
│       ├── 001_initial_schema.sql
│       └── ...045_*.sql
├── airflow/                     # Airflow DAGs for scheduled pipeline
│   └── dags/
│       ├── daily_refresh_dag.py       # Daily: migrations → scrape stale lenders → refresh views
│       ├── nbfc_extraction_dag.py     # NBFC batch extraction
│       ├── policy_extraction_dag.py   # Policy data extraction
│       ├── policy_enrichment_dag.py   # Post-extraction enrichment
│       └── rbi_extraction_dag.py      # RBI registry import
├── docker/                      # Docker Compose stacks
│   ├── docker-compose.yml       # Full stack (API + Airflow + Redis + Grafana)
│   ├── docker-compose.mvp.yml   # Simplified (API only)
│   ├── Dockerfile.api           # API image
│   └── Dockerfile.airflow       # Airflow image
├── grafana/                     # Grafana dashboard provisioning
│   ├── dashboards/              # Dashboard JSON files
│   └── provisioning/            # Auto-provisioned datasources + dashboards
├── data/
│   ├── input/                   # Source data (RBI NBFC lists, etc.)
│   └── output/                  # Scraper/pipeline output CSVs
├── docs/                        # Architecture docs, specs, plans
├── .github/workflows/deploy.yml # CI/CD pipeline
└── .planning/codebase/          # GSD codebase map documents (this directory)
```

## Directory Purposes

**`backend/api/`:**
- Purpose: The deployed FastAPI application. Everything inside is the production API server.
- Contains: Route handlers, Pydantic models, core utilities (auth, cache, config, metrics)
- Key files: `main.py` (entry point), `core/config.py` (all config), `core/auth.py` (auth deps)

**`backend/pipeline/`:**
- Purpose: Reusable Python modules shared between API routes and offline scripts
- Contains: Borrower evaluator, circuit breaker, retry logic, NBFC validator, Gemini cache
- Key files: `borrower_evaluator.py` (imported by `routers/loans.py`), `circuit_breaker.py`

**`backend/scraper/`:**
- Purpose: Web scraping and data validation for lender and policy data
- Contains: Scrapers, guardrails validation, Firecrawl fallback, A/B test framework
- Key files: `guardrails.py` (quality scoring), `lender_scraper.py`, `policy_scraper.py`

**`backend/enrichers/`:**
- Purpose: Ranked data enrichment from external sources
- Contains: BankBazaar (Rank 2), FPC PDF (Rank 3), BSE XBRL (Rank 4)
- Key files: `bankbazaar.py`, `fpc_pdf.py`, `bse_xbrl.py`

**`backend/*.py` (root-level scripts):**
- Purpose: One-off and scheduled CLI scripts for data ingestion, imports, exports
- Examples: `enrich_lenders.py`, `import_rbi_nbfc_list.py`, `sync_nbfc_csv.py`, `fetch_bse_aum.py`
- Run manually or via Airflow DAG tasks

**`frontend/app/components/`:**
- Purpose: All shared React components used across multiple pages
- Key files: `AuthContext.tsx` (auth state), `LenderCard.tsx` (primary data display component), `SearchFilter.tsx` (filter UI), `ChatPanel.tsx` (AI chatbot)

**`database/migrations/`:**
- Purpose: Sequential SQL migrations — applied by `migrate.py` in order
- Naming: `NNN_description.sql` (zero-padded 3-digit number)
- Safety: CI validates against forbidden patterns (DROP COLUMN, DROP TABLE without IF EXISTS, NOT NULL without DEFAULT)

## Key File Locations

**Entry Points:**
- `backend/api/main.py`: FastAPI app, all router mounting, lifespan hooks
- `frontend/app/layout.tsx`: Next.js root layout, global providers
- `database/migrate.py`: Migration runner (run in CI and Airflow)

**Configuration:**
- `backend/api/core/config.py`: All backend config — import `from core.config import cfg`
- `backend/api/core/constants.py`: Domain constants — VALID_LOAN_TYPES, VALID_COMPANY_TYPES, etc.
- `frontend/app/lib/supabase.ts`: Supabase client singleton — import `{ supabase }`

**Core Logic:**
- `backend/api/routers/lenders.py`: Search, detail, stats, lender request endpoints
- `backend/api/routers/loans.py`: Borrower match (`POST /loans/match`) + compare
- `backend/api/routers/chat.py`: Gemini-powered chatbot with RAG
- `backend/api/routers/admin.py`: Admin approval workflow (AdminUser required)
- `backend/pipeline/borrower_evaluator.py`: Python-side scoring engine — `rank_policies()`
- `backend/scraper/guardrails.py`: Lender data quality validation — called before any DB write

**Database Schema:**
- `database/schema_v2.sql`: Canonical schema baseline including `match_lenders()` SQL function
- `database/migrations/`: All schema changes post-baseline

**Testing:**
- `backend/tests/`: pytest tests — `test_bank_manager.py`, `test_enrichers.py`, `test_chatbot_guardrails.py`

## Naming Conventions

**Python Files:**
- `snake_case.py` for all Python files
- Router files named after the resource: `lenders.py`, `loans.py`, `policies.py`, `admin.py`
- Script files named after action: `enrich_lenders.py`, `import_rbi_nbfc_list.py`, `fetch_bse_aum.py`

**TypeScript/TSX Files:**
- `PascalCase.tsx` for React components: `LenderCard.tsx`, `AuthContext.tsx`, `ChatPanel.tsx`
- `camelCase.ts` for non-component modules: `supabase.ts`
- Next.js reserved names: `page.tsx`, `layout.tsx`, `error.tsx`, `not-found.tsx`

**SQL Migration Files:**
- `NNN_description.sql` — 3-digit zero-padded sequential number + snake_case description
- Example: `025_policy_composite_index_and_schema_fields.sql`

**Directories:**
- `kebab-case` not used — all directories are `snake_case` or short lowercase names

## Where to Add New Code

**New API Endpoint:**
- Route handler: `backend/api/routers/{resource}.py`
- Pydantic models: `backend/api/models/{resource}.py`
- Register router in: `backend/api/main.py` — `app.include_router(..., prefix="/v1/{resource}")`

**New Frontend Page:**
- Create directory: `frontend/app/{page-name}/page.tsx`
- Shared components: `frontend/app/components/{ComponentName}.tsx`

**New Database Table or Column:**
- Create: `database/migrations/NNN_{description}.sql` (next sequential number)
- Do NOT modify `schema_v2.sql` — that is the baseline only
- Migration rules: Use `IF NOT EXISTS`, no `DROP COLUMN`, no `NOT NULL` without `DEFAULT`

**New Scraper or Enricher:**
- External data enricher: `backend/enrichers/{source_name}.py`
- Export an `EnrichmentPayload` dataclass (follow pattern in `enrichers/__init__.py`)
- Add to enricher ranking in `backend/enrich_policies_db.py`

**New Airflow DAG:**
- Add to: `airflow/dags/{name}_dag.py`

**New Domain Constant (loan types, company types, etc.):**
- Backend: `backend/api/core/constants.py`
- Frontend: Duplicate in the relevant page component (e.g. `frontend/app/dashboard/page.tsx` `LOAN_TYPES` array) — constants are not currently shared via API

**New Pipeline Utility:**
- If shared between API and offline scripts: `backend/pipeline/{module}.py`
- If API-only: `backend/api/core/{module}.py`

## Special Directories

**`.planning/codebase/`:**
- Purpose: GSD codebase map documents (this directory)
- Generated: Yes (by `/gsd-map-codebase`)
- Committed: Yes

**`.github/workflows/`:**
- Purpose: GitHub Actions CI/CD pipeline definition
- Key file: `deploy.yml` — test → lint → migrate check → deploy to Render → run migrations

**`data/input/`:**
- Purpose: Source data files (RBI NBFC registry CSVs, bank lists, etc.)
- Generated: No — manually downloaded from RBI/MCA21
- Committed: Yes (reference data, not secrets)

**`data/output/`:**
- Purpose: Pipeline output — scraped lender JSON, enriched CSV exports
- Generated: Yes (by scraper/pipeline scripts)
- Committed: Partial (reference outputs may be committed)

**`.next/`:**
- Purpose: Next.js build cache and output
- Generated: Yes
- Committed: No (`.gitignore`)

**`backend/__pycache__/`, `backend/**/__pycache__/`:**
- Purpose: Python bytecode cache
- Generated: Yes
- Committed: No (`.gitignore`)

---

*Structure analysis: 2026-05-06*
