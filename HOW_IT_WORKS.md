# How the Data Pipelines Work — Plain English

This document explains in plain language what each core script does, step by step.
No jargon, no code — just what's actually happening under the hood.

---

## The Big Picture

The platform needs a database of real Indian lenders (NBFCs and banks) with their
loan products and eligibility rules. There is no ready-made dataset for this —
it has to be gathered from scratch. These four pipelines do that gathering.

```
Raw CSV list of lender names
        ↓
run_nbfc_extraction.py     ← scrapes + enriches every NBFC
run_rbi_extraction.py      ← scrapes + enriches every RBI-registered bank
        ↓
Database of lenders (with AUM, states, loan types, etc.)
        ↓
run_policy_extraction.py   ← for each lender, find their actual loan policies
        ↓
Database of policies (interest rate ranges, min CIBIL, tenure, etc.)
        ↓
guardrails.py              ← quality check that runs INSIDE all three pipelines
```

---

## 1. `run_nbfc_extraction.py` — Builds the NBFC database

### What it starts with
A plain CSV file at `data/input/nbfc_names.csv` that contains:
- The NBFC's name
- Its website (if known)
- A short description of what they do

### What it does, step by step

**Step 1 — Pre-flight checks**
Before doing anything, it checks: Is the input CSV there? Is the Gemini API key set?
If either is missing, it stops immediately with a clear error message.

**Step 2 — Load the checkpoint**
It reads a file called `.checkpoint.json` that remembers which NBFCs were already
processed in a previous run. This means if the script crashes halfway through 900
companies, you can restart and it picks up from where it left off — not from scratch.

**Step 3 — For each NBFC, Phase 1: Scrape the website**
It visits the NBFC's actual website and reads the text. It looks for:
- Phone numbers and email addresses
- The year the company was founded (`established_year`)
- How many employees / branches they have
- Which states they operate in
- What loan products they mention (Gold Loan, MSME Loan, etc.)
- Whether the company is listed on NSE/BSE

This scraped data is marked as **HIGH or MEDIUM confidence** because it came
directly from the company's own website.

**Step 4 — For each NBFC, Phase 2: Ask Gemini AI**
It sends a prompt to Google's Gemini AI saying:
> "Here is the NBFC name and website. Fill in this JSON form: AUM (assets under
> management), RBI registration number, loan types, headquarters state, etc."

The AI looks up its training knowledge about this company and returns a JSON object.
The prompt also includes the scraped facts from Step 3 and tells Gemini:
> "These fields are already verified — don't change them. Focus on the rest."

**Step 5 — Phase 3: Merge scraper + Gemini results**
The two sources are combined with a clear priority rule:
- If the scraper got something with HIGH confidence → always use the scraper's value
- If the scraper got something with MEDIUM confidence → use it only if Gemini had nothing
- If the scraper only got LOW confidence → trust Gemini's knowledge instead
- For loan types and operating states → combine both lists (union)

**Step 6 — Compute the AUM category automatically**
Once AUM (Assets Under Management) is known, the category is computed:
```
AUM < ₹500 Cr          → "Micro"
₹500 Cr – ₹5,000 Cr   → "Small"
₹5,000 Cr – ₹50,000 Cr → "Mid"
> ₹50,000 Cr            → "Large"
```
This is used for the "AUM Size" filter on the dashboard.

**Step 7 — Run Guardrails (quality check)**
The merged data is sent through `guardrails.py` (see section 4 below).
If guardrails rejects the record, it is not saved to the database.

**Step 8 — Save to CSV + Supabase**
Records that pass guardrails are written to `data/output/nbfc_extracted_verified.csv`
and uploaded to the Supabase database.

**Step 9 — Mark checkpoint**
The NBFC's ID is saved to the checkpoint file as "done". If it failed, it goes into
the "failed" list so `--retry-failed` can re-run just those ones later.

---

## 2. `run_rbi_extraction.py` — Builds the Bank database

### What it starts with
CSV files in `data/input/rbi_banks_output/` — one per bank category
(e.g. "Private Sector Banks", "Nationalised Banks", "Foreign Banks").
These CSVs come from the RBI website's official bank list.

### What's different from NBFC extraction

The flow is almost identical (scrape → Gemini → merge → guardrails → save),
but there are a few important differences:

**Bank type is determined from the RBI category name:**
```
"Private Sector Banks – Indian Banks"  → company_type = "Private Bank"
"Private Sector Banks – Foreign Banks" → company_type = "Foreign Bank"
"Nationalised Banks"                   → company_type = "PSU Bank"
"State Co-operative Banks"             → company_type = "Cooperative Bank"
"Regional Rural Banks (RRBs)"          → SKIP (not relevant to our platform)
"Payments Banks (PBs)"                 → SKIP (not lending institutions)
```

**Merger tracking:**
The script knows which banks have been merged into others (e.g. Dena Bank merged
into Bank of Baroda in 2019). Merged banks are skipped automatically so they don't
appear as active lenders on the platform.

**Product restrictions by bank type:**
Foreign Banks are blocked from being tagged with Agriculture Loan, Micro Loan,
Microfinance, Rural Loan — because foreign banks don't legally operate in those
segments in India. Cooperative Banks can't be tagged with Credit Card or EV Loan.
These rules are applied during the guardrails step.

**Pan-India logic:**
PSU Banks (nationalised banks like SBI, PNB, Canara) are automatically marked
pan-India. For all other banks, pan-India status requires evidence: either
≥20 valid states listed, or the scraper found a pan-India claim AND ≥10 states.

---

## 3. `run_policy_extraction.py` — Finds each lender's actual loan products

### What it starts with
The database of lenders that was built by the two scripts above.
Each lender already has a website URL.

### What is a "policy"?
A policy is one loan product with its specific terms. For example:
- Lender: HDFC Bank
- Product: Home Loan
- Interest rate: 8.5% – 9.5% p.a.
- Loan amount: ₹5L – ₹10 Cr
- Tenure: 12 – 360 months
- Min CIBIL score: 750
- Employment: Salaried or Self-Employed

One lender can have many policies — one per loan type, sometimes multiple per type
(e.g. a salaried version and a self-employed version of the same loan).

### Step by step

**Step 1 — Fetch all approved lenders from Supabase**
Gets the list of lenders from the database (those with `approval_status = 'approved'`).

**Step 2 — Check checkpoint**
Same resume logic as NBFC extraction — skips already-processed lenders.

**Step 3 — Phase 1: Scrape the lender's website**
Visits their website, specifically looking for loan product pages, rate cards, and
eligibility pages. Collects any numbers it finds (interest rates, credit scores, etc.).

**Step 4 — Phase 2: Ask Gemini to extract structured policies**
Sends a prompt to Gemini saying:
> "Here is the website content for [Lender Name]. Extract all their loan policies
> in this exact JSON format: loan_type, interest_rate_min, interest_rate_max,
> loan_amount_min, loan_amount_max, credit_score_min, tenure_min, tenure_max,
> employment_types, collateral_required, etc."

Gemini returns a list of policies, one per loan product.

**Step 5 — Heuristic fallback (if Gemini fails)**
If Gemini can't extract policies (e.g. the website has no readable content), the
script creates minimal "stub" policies based on the loan tags already discovered
during lender extraction. These stubs only have `loan_type` filled in — everything
else is null. They're saved with `approval_status = 'needs_review'` so an admin
can verify them manually.

**Step 6 — Phase 3: Validate each policy through 5 gates**

- **Gate 1 — Math check:** Is interest rate min ≤ max? Is loan amount min ≤ max?
  Is any number impossibly large (e.g. 500% interest rate)?
  Failed policies are logged and discarded.

- **Gate 2 — Company type rules:** MFI (Microfinance Institutions) have an RBI
  cap of 26% interest and ₹3L maximum loan amount. If a policy for an MFI claims
  35% interest or ₹50L loan, it's rejected.

- **Gate 3 — Always passes now.** We used to require a minimum amount of data
  here, but Indian NBFCs often don't publish their terms publicly. So we accept
  anything — even if only the loan_type is known.

- **Gate 4 — Completeness tagging:** Policies with very little data (heuristic
  stubs or very incomplete Gemini results) are tagged `needs_review` and given
  high review priority. They go to the database but aren't shown to users until
  an admin approves them.

- **Gate 5 — FOIR impossibility:** FOIR = Fixed Obligation to Income Ratio
  (how much of someone's monthly income can go toward loan payments). If a policy
  claims FOIR_min > FOIR_max, that's mathematically impossible and is rejected.

**Step 7 — Compute completeness score**
Each policy gets a score 0.0 – 1.0 based on how many important fields are filled:
- Has interest rate range? +0.20
- Has loan amount range? +0.20
- Has credit score requirement? +0.15
- Has tenure? +0.10
- Has employment types? +0.10
- Has eligibility notes? +0.10
- etc.

This score is used to rank policies — more complete data shown first on the lender
detail page.

**Step 8 — Flag possible hallucinations**
The script adds anomaly flags to policies that look like Gemini might have guessed:
- `rate_possibly_guessed`: both rate bounds are suspiciously round numbers (like
  exactly 12% and 18%) and came from Gemini (not scraped)
- `credit_score_possibly_guessed`: credit score min is exactly 600, 650, 700, or
  750 — the most common numbers AI models default to

These flags are stored as a note on the policy. They don't block it, but they warn
the admin to verify before approving.

**Step 9 — Upload to Supabase**
Approved policies are uploaded to the `policies` table.
Policies needing review go in too, but with `approval_status = 'needs_review'`.
The frontend only shows `approval_status = 'approved'` policies to users.

---

## 4. `guardrails.py` — The Quality Police

This file is not run directly — it's imported and used inside both extraction scripts.
Think of it as a checklist that every lender record must pass before being saved.

### What it checks, in order

**1. Company name**
Must exist and be at least 2 characters. If missing, the record is immediately
rejected — there's nothing we can do without a name.

**2. Company type**
Must be one of: NBFC, Private Bank, PSU Bank, Foreign Bank, Cooperative Bank,
NBFC-MFI, Small Finance Bank. If the AI returned something unknown (e.g. "Finance
Company"), it's defaulted to "NBFC" with a warning.

**3. Non-lender detection**
The website scraper sometimes flags a company as a non-lender (e.g. it's an
IT company named "XYZ Finance Solutions"). The guardrails checks:
- If the scraper flagged it as non-lender AND there are no lending signals
  (no loan product mentions, no RBI registration, no financial keywords) → reject
- If the scraper flagged it but there ARE lending signals → override the flag, keep it
- If confidence is < 90% → keep it but tag for admin review

**4. RBI registration number**
The format must match RBI's actual pattern (e.g. `N.13.02437`).
Common garbage values like "N/A", "REGISTERED", "ROW-73" are automatically rejected.
If a registry file is available (`rbi_cor_registry.csv`), it checks whether the
number is actually active in that registry.

**5. AUM validation**
- Must be between ₹10 Lakh and ₹1 Trillion Crores (sanity range)
- NBFC-MFIs: warned if AUM exceeds ₹50,000 Cr (unusually large for a microfinance institution)
- If AUM is null → OK, just compute category as empty

**6. Established year**
Must be between 1850 and today. If Gemini returns 3000 or 1750, it's nulled out.

**7. Website**
Must start with `http://` or `https://`. Domains like `blogspot.com`, `wordpress.com`,
`github.io`, or `vercel.app` are rejected — these are free hosting sites, not
real corporate websites.

**8. Phone and email format**
Basic format check. Indian mobile numbers (10 digits starting with 6-9) and
standard email format. Doesn't call the number — just checks if it looks real.

**9. Loan type / product list**
Only canonical product names from the approved list are kept. If Gemini returns
"Home Finance" instead of "Home Loan", guardrails normalizes it using a synonym
map. Unknown products are dropped.

**10. State list**
Only real Indian state names are kept. Invented states or country names are dropped.

**11. Duplicate detection**
Before accepting a record, guardrails checks:
- Has this exact company fingerprint (name + RBI number hash) been seen before?
- Does any previously saved company have a similar name (≥ 90% match)?
- Is the same RBI registration number already assigned to a different company?

If any of these match, the record is flagged as a duplicate and rejected.

**12. Quality score calculation**
Points are added for each important field that is present and valid:
```
Website present          → +0.15
AUM present              → +0.15
RBI registration         → +0.15
HQ state valid           → +0.10
Loan segments filled     → +0.10
Employee count present   → +0.05
Established year present → +0.05
Contact info present     → +0.05
... etc.
```
- Score < 0.20 → hard reject (not worth saving)
- Score 0.20 – 0.40 → saved but flagged for admin review
- Score > 0.40 → approved automatically

---

## 5. The Checkpoint System — How "Resume" Works

All three extraction scripts use the same checkpoint pattern.

A file like `.checkpoint.json` stores two lists:
- `processed_ids` — every NBFC/bank/lender that has been attempted
- `failed_ids` — ones that were attempted but failed (API error, bad data, etc.)

When you restart the script:
1. It loads the checkpoint file
2. For each item in the input, it checks: "Is this ID in `processed_ids`?" → skip
3. Only unprocessed items are sent to Gemini

When you run `--retry-failed`:
1. All IDs in `failed_ids` are removed from `processed_ids`
2. On the next run, those items look "unprocessed" and get retried

The checkpoint is saved every 20 records so even a hard crash loses at most 20
records of progress.

---

## 6. How a Single NBFC Goes From Name → Database

To make it concrete, here's what happens for a single company:

**Input:** `"Muthoot Finance Ltd"` in the CSV

1. Pre-flight passes ✓
2. Checkpoint: not processed before → proceed
3. **Scraper** visits `muthootfinance.com`:
   - Finds phone: `+91 1800 313 1212` (HIGH confidence)
   - Finds email: `customercare@muthootfinance.com` (HIGH confidence)
   - Finds established year: `1939` (MEDIUM confidence from "Est. 1939" text)
   - Finds loan tags: `["Gold Loan", "Personal Loan", "Home Loan"]` (any confidence)
   - Finds states: `["Kerala", "Tamil Nadu", "Maharashtra", ...]` (25 states)
4. **Gemini** is asked about Muthoot Finance:
   - Returns AUM: `₹75,000 Cr`
   - Returns is_listed: `true`
   - Returns stock_symbol: `MUTHOOTFIN`
   - Returns rbi_category: `NBFC-ND-SI`
   - Returns hq_state: `Kerala`
5. **Merge:** scraper's phone/email/year override Gemini. Loan tags unioned.
6. **AUM Category computed:** ₹75,000 Cr → `"Large"`
7. **Guardrails:**
   - Name: ✓
   - Company type NBFC: ✓
   - RBI number format: ✓
   - AUM ₹75,000 Cr: ✓ (in valid range)
   - Year 1939: ✓ (between 1850 and today)
   - 25 states found → pan_india = true
   - Quality score: 0.82 → approved ✓
   - No duplicates ✓
8. **Saved** to CSV + Supabase with `approval_status = 'approved'`
9. **Checkpoint** marks ID as done

Then `run_policy_extraction.py` picks up Muthoot Finance later and extracts:
- Gold Loan policy: rate 11%–26%, ₹1,500–₹1.5 Cr, tenure 3–36 months, no CIBIL required
- Personal Loan policy: rate 14%–24%, ₹50K–₹15L, CIBIL ≥ 700, salaried/self-employed
- Home Loan policy: limited data → saved with needs_review, completeness 0.35

---

## 7. What Each File Produces

| Script | Output file | Database table |
|--------|-------------|----------------|
| `run_nbfc_extraction.py` | `data/output/nbfc_extracted_verified.csv` | `lenders` |
| `run_rbi_extraction.py` | `data/output/rbi_banks_extracted_v8.csv` | `lenders` |
| `run_policy_extraction.py` | `data/output/policies_extracted.csv` | `policies` |
| `guardrails.py` | (no output — used internally) | — |

---

## 8. What the API Does With This Data

Once the data is in the database, the FastAPI backend serves it:

- **`GET /v1/lenders/search`** — the dashboard filter page.
  Filters by company_type (NBFC, Private Bank, Foreign Bank, etc.),
  state, loan_type, AUM size, listing status, established year range.
  Sorts by AUM or established year.

- **`GET /v1/lenders/{id}`** — single lender detail page.
  Returns all fields for one lender including website, phone, states, etc.

- **`GET /v1/policies/filter`** — used on the lender detail page.
  Filters policies for a given lender. Returns interest rates, tenure, CIBIL
  requirements, employment types, etc. — the actual loan terms.

The frontend uses these three endpoints to power everything users see.
