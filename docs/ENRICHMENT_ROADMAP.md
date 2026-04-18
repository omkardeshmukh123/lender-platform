# Data Enrichment Roadmap

## Current DB State (as of April 2026)

### Lenders (770 approved)
| Field | Coverage | Notes |
|---|---|---|
| Website | 769 / 770 | ✅ |
| Operating coverage | 770 / 770 | ✅ |
| Business sector | 759 / 770 | ✅ |
| RBI category | 746 / 770 | ✅ |
| Loan segments | 759 / 770 | ✅ |
| Avg quality score | 0.83 | ✅ |
| AUM | 138 / 770 | ⚠️ biggest gap |
| Employee / Branch count | ~78 / ~68 | ⚠️ sparse |

### Policies (1876 total, covering 638 lenders)
| Field | Coverage | Notes |
|---|---|---|
| Loan type | 1876 / 1876 | ✅ |
| Interest rate | 38 / 1876 | ❌ critical gap |
| Loan amount | 180 / 1876 | ❌ critical gap |
| Tenure | 153 / 1876 | ❌ |
| CIBIL score | 34 / 1876 | ❌ |
| Rate + Amount both | 14 / 1876 | ❌ essentially empty |

---

## Enrichment Phase 1 — BSE/NSE XBRL Filings

**Script to build:** `backend/fetch_bse_financials.py`

**What it does:**
- 117 lenders are listed on BSE/NSE
- BSE mandates quarterly + annual results in machine-readable XBRL format
- Match lenders by CIN → get BSE scrip code → pull latest annual result → extract financials

**Fields it fills:**
- `aum_crores` — loan book size from balance sheet
- `employee_count` — from annual report
- `branch_count` — from annual report
- Net NPA % (new column to add)
- Capital adequacy ratio / CAR (new column to add)

**Why it matters:** Covers 71 listed lenders with missing AUM. Zero hallucination — data straight from BSE regulatory filing.

**Implementation notes:**
1. MCA21 gives CIN for each company (already have `cin` column from `mca21_enrich.py`)
2. BSE has a CIN → scrip code lookup: `https://api.bseindia.com/BseIndiaAPI/api/...`
3. Annual results XBRL: `https://www.bseindia.com/xml-data/corpfiling/`
4. Extract AUM from "Loans" or "Advances" line in balance sheet
5. Upsert with `data_source = 'bse_xbrl'`

**Run order:** First run `mca21_enrich.py` to populate CIN, then `fetch_bse_financials.py`

---

## Enrichment Phase 2 — Annual Report PDFs (Unlisted NBFCs)

**Script to build:** `backend/fetch_annual_report_pdf.py`

**What it does:**
- 653 lenders are unlisted — no BSE filing
- Most large NBFCs publish annual report PDFs on their IR/investor pages
- Download PDF from lender website → use Gemini to extract AUM table from MD&A section

**Fields it fills:**
- `aum_crores` — from MD&A loan book table
- `employee_count`, `branch_count` — from operational highlights section

**Why it matters:** Covers top 100–200 unlisted NBFCs. Annual reports are audited — reliable source.

**Implementation notes:**
- Look for `/investor-relations`, `/annual-report`, `/financials` paths on lender website
- Most annual reports are linked from the homepage footer
- PDF table extraction: Gemini `gemini-1.5-pro` handles multi-page financial tables well
- Store `data_source = 'annual_report_pdf'` and `last_scraped_at`
- Only process PDFs from the current FY (FY2025 = April 2024 – March 2025)

---

## Enrichment Phase 3 — Policy Data from Annual Reports

**What it does:**
- Every NBFC annual report has a "Fair Practice Code" / "Interest Rate Policy" section
- This is the most legally reliable source for interest rates per product
- Directly fills the critical gap in the `policies` table

**Fields it fills:**
- `interest_rate_min`, `interest_rate_max`
- `loan_amount_min`, `loan_amount_max`
- `tenure_min`, `tenure_max`
- `processing_fee`

**Why it matters:** Fair Practice Code rates are what NBFCs file with RBI — the most authoritative source. Much more reliable than scraping product pages.

**Implementation notes:**
- Same PDF download as Phase 2, different section extraction
- Prompt: "Extract the interest rate range, loan amount range, and tenure for each loan product listed in the Fair Practice Code or Interest Rate Policy section"
- Store with `data_source = 'annual_report_fpc'` and `completeness_score` recalculated

---

## Enrichment Phase 4 — BankBazaar / PaisaBazaar (Quick Win)

**Script already built:** `backend/enrich_policies_db.py`

**What it does:**
- Scrapes BankBazaar and PaisaBazaar for structured loan terms
- Covers ~150–200 major lenders (PSU banks, private banks, large NBFCs)
- Interest rate, loan amount, tenure, CIBIL, processing fee

**Run it:**
```bash
cd backend
python enrich_policies_db.py --dry-run   # preview changes
python enrich_policies_db.py             # write to DB
python enrich_policies_db.py --force     # overwrite existing values
```

---

## Scripts Already Built (run these now)

| Script | Purpose | Command |
|---|---|---|
| `compute_derived_fields.py` | Backfill operating_intensity, aum_category, business_sector, hq_state | `python compute_derived_fields.py` |
| `mca21_enrich.py` | Scrape zaubacorp for CIN, company_status | `python mca21_enrich.py --limit 100` |
| `enrich_policies_db.py` | BankBazaar policy scraping | `python enrich_policies_db.py` |
| `audit_hallucinations.py` | Null out-of-range / Gemini-only values | `python audit_hallucinations.py --dry-run` |

All scripts support `--dry-run`, `--limit N`, and `--force` flags.

---

## Go-Live Recommendation

**Ship now:** The lender directory is production-ready. 770 lenders, 0.83 avg quality, full search/filter with 8 dimensions.

**Post-launch:** Run Phase 4 (BankBazaar) first — quickest win on policy data. Then Phase 1 (BSE XBRL) for AUM on listed lenders. Phase 2 and 3 are medium-effort, high-value.
