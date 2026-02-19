# Lender Discovery Platform

**Goal:** Process 3,181 NBFCs and deploy filterable directory by end of day.

## Quick Start

### 1. Backend - Extract Data

```bash
cd backend

# Set API keys
export GEMINI_API_KEY="your_gemini_key_here"
export SERPAPI_KEY="your_serp_key_here"  # optional for re-search

# Install dependencies
pip install -r requirements.txt

# Run extraction (takes ~2-3 hours for 3181 companies)
python run_extraction.py
```

**Output:** `data/output/extracted_lenders.csv`

### 2. Frontend - Deploy on Vercel

```bash
cd frontend
npm install
npm run dev          # Test locally
vercel deploy        # Deploy to production
```

## Data Flow

```
Input: 19 batch CSVs (3,181 NBFCs with URLs)
  ↓
Validation: Quick check if URL is actually NBFC
  ↓
Gemini Flash: Extract 11 fields with Google Search grounding
  ↓
Output: extracted_lenders.csv
  ↓
Supabase: Import CSV
  ↓
Frontend: Filter UI on Vercel
```

## Timeline

- **Now:** Run extraction (2-3 hours)
- **Next:** Build frontend (2 hours)
- **Tonight:** Deploy live

## Tech Stack

- Extraction: Python + Gemini Flash API
- Database: Supabase (PostgreSQL)
- Frontend: Next.js 14 + Tailwind
- Deploy: Vercel

## Fields Extracted

1. company_name
2. company_type (NBFC)
3. website
4. aum_crores
5. products (JSON array)
6. hq_city, hq_state
7. established_year
8. employee_count
9. ticket_size (min/max in lakhs)
10. operating_states (JSON array)
11. phone, email

## Filters in UI

- Loan type (MSME / Home / Personal / Gold)
- State (Maharashtra / Gujarat / etc)
- Ticket size (< 5L / 5-50L / 50L-5Cr / > 5Cr)
- Company type (currently all NBFC, future: banks)
