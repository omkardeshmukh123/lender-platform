# Pivot 2 — Accuracy, Editability, Real-User Validation

**Mentor feedback (May 2026):**
- Scraping is not the priority — use existing structured public datasets (RBI Excel/PDFs)
- Lenders won't share private contact info — design the product around incomplete but public data
- Build a system where data can be manually edited, not a system that depends on automated extraction
- Focus ONE feature to accuracy before expanding (GRO data)
- Test with real users (DSAs, loan agents) before ads or marketing
- AI chatbot must be domain-scoped (loan/lender only, not a general chatbot)

**Current reality:**
- 784 lenders, contact data mostly complete
- Grievance officers: 20 records — sparse and unverified
- Chatbot: no domain guardrails, answers anything
- Admin panel: approve/reject only, no editing capability
- Leads table live, capturing intent — not yet visible in admin

---

## Step 1 — Leads visibility in admin panel

Show captured leads (from the Visit Website modal) in the admin dashboard.

- [x] `GET /v1/admin/leads` — paginated, sortable by date; returns lender_name, loan_type, phone, created_at
- [x] Admin UI: "Leads" tab in admin page — table with date, lender, loan type, phone (masked: last 4 digits visible)
- [x] Running count in admin stats card

## Step 2 — Admin "Edit Lender" interface

Build UI + API so any lender's data can be corrected manually.

- [x] `PATCH /v1/admin/lenders/{id}` — editable fields: website, phone, email, hq_location, hq_state, rbi_category, aum_crores, pan_india, operating_states[], primary_loan_segments[]
- [x] Admin UI: "Edit" button per lender row → slide-out form with all editable fields
- [x] On save: write audit log entry (reuse lender_audit_log table, source='admin_manual')
- [x] Validation: website must start with http, phone max 20 chars

## Step 3 — Admin "Grievance Officer" editor

Make GRO data manually maintainable — the one feature we're betting accuracy on.

- [x] `PATCH /v1/admin/lenders/{id}/grievance-officer` — upsert: name, designation, email, phone, source_url
- [x] `DELETE /v1/admin/lenders/{id}/grievance-officer` — remove stale/wrong record
- [x] Admin UI: GRO sub-section in lender edit form — shows current record, inline edit, delete button
- [x] source_type field: dropdown — 'website', 'rbi_circular', 'annual_report', 'manual'
- [x] last_verified_at: auto-set to NOW() on every admin save

## Step 4 — RBI structured data import (no scraping)

Replace brute-force website scraping with structured public datasets.

- [x] Download RBI master NBFC list (Excel from rbi.org.in/Scripts/NBFC_List.aspx) → `backend/import_rbi_nbfc_list.py` → bulk-upsert rbi_category, cin, registration_number into lenders
- [x] Download RBI's NBFC ombudsman circulars (PDF) → manual extraction → CSV → import GRO contacts (`backend/import_gro_csv.py`)
- [x] For listed NBFCs: annual reports mandate GRO disclosure — check BSE filing portal for structured data
- [x] Script output: dry-run first (default), then `--apply`
- [x] Do NOT run new website scrapers — structured data only

## Step 5 — AI chatbot domain guardrails

Restrict the Gemini chatbot to loan/lender questions only.

- [x] Update system prompt: add explicit refusal instruction for non-loan topics ("I only answer questions about lenders, loan products, NBFCs, and interest rates. For other topics, please consult the relevant resource.")
- [x] Add 10 off-topic test queries to a `backend/tests/test_chatbot_guardrails.py` — each must return a refusal, not an answer
- [x] Run same on-topic query 10 times → check variance in output (aim: consistent lender name, rate format)
- [x] Log refusals: add `refusal: bool` field to chat_logs table (migration 044)

## Step 6 — Real-user testing (DSAs / loan agents)

Ship to 5 real users before any paid marketing.

- [ ] Identify 5 DSAs or loan agents from Omkar's existing network
- [ ] Give them the live URL with 3 specific tasks:
  1. "Find all NBFCs doing MSME loans in Maharashtra"
  2. "Check if [any lender] is RBI-registered and operational"
  3. "Find the grievance officer for [a lender they know]"
- [ ] Collect structured feedback: useful? what's missing? would you use daily?
- [ ] Fix top 3 friction points before Step 7
- [ ] Document findings in `docs/user-testing-round1.md`

## Step 7 — Run ads only after Steps 1–6 complete

- [ ] Google/Meta ad targeting: "loan agent", "DSA", "NBFC directory"
- [ ] Landing copy: "Find any NBFC's grievance officer in 10 seconds"
- [ ] Budget: small test ₹500–1000/day, measure lead quality

---

**What is explicitly NOT in Pivot 2:**
- New website scrapers (mentor: stop scraping everything)
- Rate comparison (4.2% coverage — not useful yet)
- Lender onboarding portal (mentor: lenders won't fill forms)
- Mobile number collection from lenders (mentor: they won't share it)
- Any feature expansion before user testing validates what exists
