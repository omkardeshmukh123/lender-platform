# Lender Onboarding Portal — Design Spec

**Date:** 2026-05-01  
**Status:** Approved

---

## Problem

Only 4.2% of loan products (111/2,657) have verified interest rates. The fastest path to fixing this is letting lenders fill in their own data — they know their rates. Even 50 lenders completing their profiles would double coverage.

---

## Approach

**Invite existing lenders (C) + Full Supabase Auth (A) + public request form**

- Lenders already exist in the DB (added via scraper/RBI). Admin invites them to claim and complete their profile.
- Lenders get a real Supabase account — they can return anytime to update rates or check submission status.
- A public "Request Access" form lets new lenders (not yet in DB) apply.

---

## Architecture

### Auth

- Supabase Auth with `app_metadata.role = 'lender'` and `app_metadata.lender_id = <lenders.id>`
- Same JWT pattern as admin role (already implemented in `backend/api/core/auth.py`)
- New `require_lender()` FastAPI dependency: validates JWT, reads `lender_id`, scopes all queries to that lender
- Lenders cannot read or write any other lender's data

### Backend

New FastAPI router: `backend/api/routers/lender_portal.py`

All routes under `/lender-portal/` prefix. Protected by `require_lender()` except `POST /lender-portal/request-access` (public).

### Frontend

New route group: `frontend/app/lender-portal/`  
Own layout with lender nav (Dashboard, Policies, Profile, Analytics).  
Auth guard: redirect unauthenticated users to `/lender-portal/login`.

### Database additions

```sql
-- Links Supabase user accounts to lender records
CREATE TABLE lender_users (
  id          SERIAL PRIMARY KEY,
  user_id     UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  lender_id   BIGINT NOT NULL REFERENCES lenders(id) ON DELETE CASCADE,
  invited_by  TEXT,
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id),
  UNIQUE(lender_id)
);

-- Tracks pending invite links
CREATE TABLE lender_invites (
  id          SERIAL PRIMARY KEY,
  lender_id   BIGINT NOT NULL REFERENCES lenders(id) ON DELETE CASCADE,
  email       TEXT NOT NULL,
  token       TEXT NOT NULL UNIQUE,
  created_by  TEXT NOT NULL,
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  expires_at  TIMESTAMPTZ NOT NULL,
  accepted_at TIMESTAMPTZ
);
```

---

## Invite Flow (Admin → Lender)

1. Admin opens `/admin` → finds lender → clicks "Invite Lender" → enters email
2. Backend: creates `lender_invites` row with 7-day token + calls Supabase Admin API to send magic-link email
3. Lender clicks email link → Supabase creates account → webhook / redirect calls backend
4. Backend: sets `app_metadata = {role: 'lender', lender_id: X}` + inserts `lender_users` row
5. Lender lands on `/lender-portal/dashboard`

---

## Portal Pages

### `/lender-portal/login`
Standard Supabase email/password login. Redirect to dashboard on success.

### `/lender-portal/dashboard`
- Profile completion percentage (based on filled fields across lenders + policies)
- Missing data callouts (e.g., "6 loan products missing interest rates")
- Pending submission count (edits awaiting admin approval)
- 30-day stats: profile views, shortlists

### `/lender-portal/policies`
- Table of all loan products for this lender
- Inline editing: interest_rate_min/max, loan_amount_min/max, tenure_min/max, processing_fee
- "Add Product" button → modal form for new loan type
- Each save creates a pending revision visible to admin
- Status badge per row: Live / Pending Review / Rejected

### `/lender-portal/profile`
- Edit: company description, HQ location, contact email, phone, website
- Upload logo (stored in Supabase Storage)
- Upload FPC PDF (stored in Supabase Storage, linked to lender record)
- All changes go pending on save

### `/lender-portal/analytics`
- Read-only. Sourced from existing shortlists table + page view counters.
- 30-day profile views, total shortlists, top loan types viewed.

### `/lender-portal/request-access` (public, no auth)
- Fields: company name, CIN (optional), contact name, email, phone, notes
- On submit: inserts into `lender_requests` table (already exists, migration 036)
- Admin sees it in `/admin` requests queue
- On approval: admin clicks "Invite" and the normal flow begins

---

## Backend API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/lender-portal/request-access` | Public | Submit access request |
| `GET` | `/lender-portal/me` | Lender | Own lender profile + completion score |
| `PATCH` | `/lender-portal/profile` | Lender | Update profile fields (pending) |
| `POST` | `/lender-portal/profile/upload` | Lender | Upload logo or FPC PDF |
| `GET` | `/lender-portal/policies` | Lender | List own policies with status |
| `POST` | `/lender-portal/policies` | Lender | Add new loan product |
| `PATCH` | `/lender-portal/policies/{id}` | Lender | Edit policy (pending) |
| `GET` | `/lender-portal/analytics` | Lender | Views + shortlists summary |
| `POST` | `/admin/lenders/{id}/invite` | Admin | Send invite email to lender |

---

## Approval Flow

Lender edits do **not** go live immediately. They create a pending state:

- **Profile edits**: stored in a `pending_edits` JSONB column on `lenders` (simpler than a separate table; no schema migration needed for new editable fields).
- **Policy edits**: set `approval_status = 'pending'` on the policy row (field already exists).
- **New policies**: inserted with `approval_status = 'pending'`.

Admin sees all pending lender-submitted items in the existing `/admin` approval queue. On approval:
- Profile edits: merged into main `lenders` row, `pending_edits` cleared
- Policy edits: `approval_status` set to `'approved'`

---

## Data Model Changes Summary

| Change | Type |
|--------|------|
| `lender_users` table | New (migration 042) |
| `lender_invites` table | New (migration 042) |
| `lenders.pending_edits` JSONB column | New (migration 042) |
| `lenders.logo_url` TEXT column | New (migration 042) |
| `lenders.fpc_pdf_url` TEXT column | New (migration 042) |

---

## Out of Scope (Phase 2)

- Email notifications when admin approves/rejects a lender submission
- Lender-to-borrower messaging
- Multiple users per lender account
- Lender subscription/billing

---

## Success Criteria

- Admin can invite a lender in < 1 minute from `/admin`
- Lender can log in and fill all missing rates in < 10 minutes
- Lender-submitted rates appear in admin queue within seconds
- Public request form submits successfully for new lenders
