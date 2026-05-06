# Technology Stack

**Analysis Date:** 2026-05-06

## Languages

**Primary:**
- Python 3.11 — Backend API, data pipeline, scrapers, enrichers, Airflow DAGs
- TypeScript 5.x — Next.js frontend (all `.tsx`/`.ts` files)

**Secondary:**
- SQL (PostgreSQL) — Migration scripts, stored functions (`match_lenders()`), materialized views

## Runtime

**Backend Environment:**
- Python 3.11 (pinned in GitHub Actions `setup-python`)
- Deployed on Render via nixpacks builder

**Frontend Environment:**
- Node.js 20 (pinned in GitHub Actions `setup-node`)

**Package Manager:**
- Backend: pip (lockfile: none — ranges in `requirements.txt`)
- Frontend: npm (lockfile: `frontend/package-lock.json` — present)

## Frameworks

**Backend Core:**
- FastAPI 0.115+ — REST API framework, async, ASGI
- Uvicorn (standard) — ASGI server, single worker in production on Render
- Pydantic v2 — Request/response validation, data models

**Frontend Core:**
- Next.js 14.2.0 — React framework, App Router, server components + client components
- React 18.2.0 — UI library

**Styling:**
- Tailwind CSS 3.4.x — Utility-first CSS
- Framer Motion 12.x — Animation library (used for transitions on landing page)
- Lucide React 0.575+ — Icon library

**Pipeline / Orchestration:**
- Apache Airflow (CeleryExecutor) — DAG scheduling for daily data refresh
- Celery — Airflow's task execution backend

**Testing:**
- pytest — Backend unit tests (`backend/tests/`)
- TypeScript compiler (`tsc --noEmit`) — Frontend type checking in CI

## Key Dependencies

**Backend API (`backend/api/requirements.txt`):**
- `asyncpg>=0.29.0` — Async PostgreSQL driver; direct pool (no ORM)
- `PyJWT>=2.8.0` — Supabase JWT verification (ES256 + HS256 fallback)
- `slowapi>=0.1.9` — Rate limiting middleware wrapping limits-per-minute
- `redis>=5.0.0` — Async Redis client for response caching
- `sentry-sdk[fastapi]>=2.0.0` — Error tracking and performance monitoring
- `google-genai>=1.0.0` — Gemini API client for the chatbot intent classifier + answer generation

**Backend Pipeline (`backend/requirements.txt`):**
- `scrapling>=0.2.0` — Primary web scraper for lender sites
- `google-generativeai>=0.7.0` — Gemini API client for data extraction pipeline
- `supabase>=2.0.0` — Supabase Python client for pipeline writes
- `pdfplumber>=0.11.0` — PDF extraction for Fair Practice Code documents
- `beautifulsoup4>=4.12.0` + `lxml>=5.0.0` — HTML parsing in scraper/enrichers
- `requests>=2.31.0` — Synchronous HTTP for enrichers (BankBazaar, BSE, FPC PDF)
- `python-Levenshtein>=0.21.0` — Fuzzy string matching for dedup/validation
- `openpyxl>=3.1.0` — Excel file handling (RBI NBFC list imports)

**Frontend (`frontend/package.json`):**
- `@supabase/supabase-js ^2.97.0` — Supabase Auth client (singleton in `app/lib/supabase.ts`)
- `next 14.2.0` — Framework
- `framer-motion ^12.34.3` — Animations
- `lucide-react ^0.575.0` — Icons

## Configuration

**Environment Variables (Backend API):**
- `DATABASE_URL` — PostgreSQL DSN (Supabase pooler URL, required at startup)
- `SUPABASE_URL` — Supabase project URL (required at startup, used for JWKS)
- `SUPABASE_JWT_SECRET` — HS256 fallback secret for JWT verification
- `REDIS_URL` — Redis connection URL (optional; disables cache if absent)
- `SENTRY_DSN` — Sentry error tracking DSN (optional)
- `GEMINI_API_KEY` — Google Gemini API key for chatbot
- `CORS_ORIGINS` — Comma-separated allowed origins (default: `http://localhost:3000`)
- `ENV` — `production` or `development` (controls docs exposure, log verbosity)
- `DB_POOL_MIN`, `DB_POOL_MAX` — asyncpg pool sizing (defaults: 2/5)

**Environment Variables (Frontend):**
- `NEXT_PUBLIC_SUPABASE_URL` — Supabase project URL
- `NEXT_PUBLIC_SUPABASE_ANON_KEY` — Supabase anon key
- `NEXT_PUBLIC_API_URL` — FastAPI base URL (default: `http://localhost:8000`)

**Config Source:** `backend/api/core/config.py` — `Config` class reads all env vars with safe defaults. Singleton `cfg` imported everywhere.

**Build Config:**
- Backend: `backend/railway.toml` — nixpacks builder, `pip install -r api/requirements.txt`, start command `uvicorn main:app`
- Frontend: `frontend/next.config.js` — minimal, no customizations
- Docker: `docker/docker-compose.yml` — full stack (API + Airflow + Redis + Grafana)

## Platform Requirements

**Development:**
- Python 3.11+
- Node.js 20+
- PostgreSQL 15+ (via Supabase, or local)
- Redis (optional — graceful NullCache fallback if absent)

**Production:**
- Backend: Render (single Uvicorn worker, nixpacks build)
- Frontend: Vercel (`https://lender-platform.vercel.app`)
- Database: Supabase (PostgreSQL 15 with RLS, PostGIS, pg_trgm extension)
- Cache: Redis (separate service in docker-compose; URL injected via env)
- Monitoring: Grafana (port 3001 in docker-compose), Sentry (optional)
- CI/CD: GitHub Actions (`.github/workflows/deploy.yml`) — test → lint → check migrations → deploy to Render → run migrations

---

*Stack analysis: 2026-05-06*
