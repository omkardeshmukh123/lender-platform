# Pivot 1 — Realistic Improvements Based on Current Data State

**Data reality (as of May 2026):**
- 784 lenders, 99% have website/phone/email ✅
- AUM: only 23% filled ❌
- Policies: 683 lenders have records, but only 4.2% have interest rates ❌
- Grievance officers: 20 records ❌

**Strategy: Stop pretending to be a rate comparison tool. Own the NBFC directory space.**

---

## Step 1 — Remove lender onboarding portal (mentor feedback)
- [x] Delete spec file and brainstorm artifacts
- [x] Keep `lender_requests` table (used by stub card request flow)
- [ ] No frontend/backend code existed — nothing else to remove

## Step 2 — Reposition landing page for DSA/Loan Officers
- [x] Update hero headline and subtext to speak to DSAs + loan officers, not just borrowers
- [x] Add "For Loan Agents & DSAs" section/badge on landing
- [x] Keep borrower messaging secondary, not primary

## Step 3 — State + Loan Type SEO landing pages
- [x] Create `/lenders/[state]` dynamic route with static generation (33 states)
- [x] Create `/lenders/[loan-type]` dynamic route (18 loan types)
- [x] Each page: filtered lender list + meta title/description for SEO
- [x] Add sitemap.xml covering all lender detail pages + state/loan-type pages

## Step 4 — "Is This Lender Legit?" verification page
- [x] Create `/verify` page: search NBFC by name
- [x] Show: RBI category, CIN, MCA21 company_status, established year, AUM if available
- [x] Uses existing DB data — zero new scraping needed
- [x] Unique feature: no competitor does this cleanly for free

## Step 5 — Show sparse policy data honestly
- [x] On lender detail page: show policy cards even without rates
- [x] Display what IS available: loan type, amount range, tenure, processing fee
- [x] Replace "—" with contextual labels: "Rate: Contact lender" instead of empty dash
- [x] Policy count badge already shows on cards — make it mean something

## Step 6 — Intent capture before "Visit Website"
- [x] On "Visit Website" click: show a small modal (loan type dropdown + phone field)
- [x] Store as lead in new `leads` table (migration 043, POST /v1/leads)
- [x] Then redirect to lender's website
- [x] Apply migration 043 in Supabase

---

**What was removed:**
- Lender onboarding portal design (mentor feedback: lenders won't fill profiles)
- No code to remove — portal was never implemented, only specced
