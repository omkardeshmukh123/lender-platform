# Google Sign-In + First-Login Mobile Number Collection

**Date:** 2026-05-06
**Status:** Approved

---

## Goal

Enable users to sign in with Google on the Login and Signup pages. After a Google user's first successful login, show a required modal overlay on the dashboard to collect their 10-digit Indian mobile number before they can access the app. The number is stored in the existing `user_profiles` table.

---

## Architecture

### Supabase (manual one-time setup)
- Enable Google OAuth provider in the Supabase dashboard → Authentication → Providers.
- Add the production and local callback URLs to the allowed redirect URIs.
- No changes required to `backend/api/core/auth.py` — JWKS-based JWT verification already handles OAuth tokens.

### AuthContext (`frontend/app/components/AuthContext.tsx`)
New additions to the existing context:

| Addition | Purpose |
|---|---|
| `signInWithGoogle()` | Calls `supabase.auth.signInWithOAuth({ provider: 'google', redirectTo: origin + '/dashboard' })` |
| `phoneRequired: boolean` | True when the signed-in Google user has no phone in `user_profiles` |
| `profileChecking: boolean` | True while the async `user_profiles` lookup is in flight; prevents dashboard flash |
| `savePhone(phone: string)` | Upserts `user_profiles` with `user_id`, `email`, `phone`; sets `phoneRequired: false` on success |

**First-login detection logic** (inside `onAuthStateChange`):
1. On `SIGNED_IN` event, check `session.user.app_metadata.provider === 'google'`.
2. Query `user_profiles` for the `user_id`.
3. If no row or `phone` is null/empty → set `phoneRequired: true`.
4. Otherwise → `phoneRequired: false`.
5. Set `profileChecking: false` when done.

Email/password users are unaffected — their phone is collected at signup and always present.

---

## Components

### Login page (`/login/page.tsx`)
- Add "Continue with Google" button **above** the email/password form.
- Separate with a centered "or" text divider.
- Button style: white background, Google logo icon, teal border, matching brand font.
- Calls `signInWithGoogle()` on click; no additional loading state needed (page navigates away).

### Signup page (`/signup/page.tsx`)
- Same "Continue with Google" button in the same position (above form, "or" divider).

### PhoneModal (`frontend/app/components/PhoneModal.tsx`)
New component. A full-screen overlay (z-50, semi-transparent backdrop) rendered from the dashboard when `phoneRequired && user`.

Contents:
- Heading: "One last step"
- Subtext: "Enter your mobile number to continue"
- Input: `+91` prefix label, 10-digit numeric field, same styling as existing phone input on signup page
- Validation: Indian mobile regex `/^[6-9]\d{9}$/`
- "Continue" primary button (teal `#1A7070`)
- Inline error display on save failure
- **No close/skip button** — submission is required

### Dashboard (`/dashboard/page.tsx`)
- Import and render `<PhoneModal />` conditionally:
  ```tsx
  {user && phoneRequired && !profileChecking && <PhoneModal />}
  ```
- No other changes to dashboard logic.

---

## Data

**Table:** `user_profiles` (existing)

| Column | Type | Notes |
|---|---|---|
| `user_id` | uuid | PK, references `auth.users` |
| `email` | text | |
| `phone` | text | Null for Google users until modal submitted |

`savePhone` uses an **upsert** (`INSERT ... ON CONFLICT (user_id) DO UPDATE`) to handle the case where a partial row already exists (e.g., email-linked account).

---

## Edge Cases

| Scenario | Behaviour |
|---|---|
| Returning Google user with phone already saved | `user_profiles` row found with phone → `phoneRequired: false` → no modal |
| Email user who later uses Google (same email) | Supabase links accounts; `user_profiles` already has phone → no modal |
| Phone upsert fails | Inline error in modal, `phoneRequired` stays `true`, user can retry |
| User navigates back from Google consent screen | No session change; lands on login page in original state |
| `onAuthStateChange` fires before profile check resolves | `profileChecking: true` blocks modal render until check completes |

---

## Out of Scope

- Phone OTP/SMS verification (not requested)
- Allowing users to update their phone number after submission
- Other OAuth providers (GitHub, Apple, etc.)
