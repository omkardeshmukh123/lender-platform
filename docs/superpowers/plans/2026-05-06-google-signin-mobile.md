# Google Sign-In + First-Login Mobile Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Google OAuth sign-in to the Login and Signup pages, and show a required mobile-number modal on the dashboard the first time a Google user logs in.

**Architecture:** `AuthContext` gains `signInWithGoogle`, `phoneRequired`, `profileChecking`, and `savePhone`. On every Google sign-in (including page reload with existing session), the context queries `user_profiles` to detect whether a phone is on file; if not, it sets `phoneRequired: true`. The dashboard renders a `PhoneModal` overlay when that flag is true — no close button, submission required.

**Tech Stack:** Next.js App Router, React, Supabase JS v2, TypeScript, Tailwind CSS.

---

## Prerequisites (manual, one-time — do before running any task)

1. Open the Supabase dashboard → **Authentication → Providers → Google**.
2. Enable the Google provider and paste in your **Client ID** and **Client Secret** from Google Cloud Console (OAuth 2.0 credentials).
3. Under **Redirect URLs**, add:
   - `http://localhost:3000/dashboard` (local dev)
   - `https://<your-production-domain>/dashboard` (prod)
4. Save. No code changes needed in `backend/` — JWT verification via JWKS already handles OAuth tokens.

---

## Task 1: Extend AuthContext with Google auth + phone gating

**Files:**
- Modify: `frontend/app/components/AuthContext.tsx`

- [ ] **Step 1: Replace the full contents of `AuthContext.tsx`**

The new file adds `phoneRequired`, `profileChecking`, `signInWithGoogle`, and `savePhone` while keeping all existing behaviour unchanged for email/password users.

```tsx
'use client'

import { createContext, useContext, useEffect, useState } from 'react'
import { createClient } from '@supabase/supabase-js'

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  throw new Error(
    'Missing required environment variables: NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY must be set.'
  )
}

const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY)

type User = {
  id: string
  email: string
  access_token?: string
}

type AuthContextType = {
  user: User | null
  loading: boolean
  phoneRequired: boolean
  profileChecking: boolean
  signUp: (email: string, password: string) => Promise<{ data: any; error: any }>
  signIn: (email: string, password: string) => Promise<{ data: any; error: any }>
  signInWithGoogle: () => Promise<void>
  signOut: () => Promise<void>
  savePhone: (phone: string) => Promise<{ error: string | null }>
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  loading: true,
  phoneRequired: false,
  profileChecking: false,
  signUp: async () => ({ data: null, error: null }),
  signIn: async () => ({ data: null, error: null }),
  signInWithGoogle: async () => {},
  signOut: async () => {},
  savePhone: async () => ({ error: null }),
})

async function checkPhoneRequired(userId: string): Promise<boolean> {
  const { data } = await supabase
    .from('user_profiles')
    .select('phone')
    .eq('user_id', userId)
    .maybeSingle()
  return !data?.phone
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [phoneRequired, setPhoneRequired] = useState(false)
  const [profileChecking, setProfileChecking] = useState(false)

  useEffect(() => {
    let cancelled = false

    // Page load: restore existing session and check phone for Google users
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      if (cancelled) return
      if (session?.user) {
        setUser({
          id: session.user.id,
          email: session.user.email || '',
          access_token: session.access_token,
        })
        if (session.user.app_metadata?.provider === 'google') {
          setProfileChecking(true)
          const required = await checkPhoneRequired(session.user.id)
          if (!cancelled) {
            setPhoneRequired(required)
            setProfileChecking(false)
          }
        }
      } else {
        setUser(null)
      }
      if (!cancelled) setLoading(false)
    })

    // Auth state changes (login, logout, token refresh)
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      async (event, session) => {
        if (session?.user) {
          setUser({
            id: session.user.id,
            email: session.user.email || '',
            access_token: session.access_token,
          })
          // Check phone only on explicit sign-in, not on every token refresh
          if (event === 'SIGNED_IN' && session.user.app_metadata?.provider === 'google') {
            setProfileChecking(true)
            const required = await checkPhoneRequired(session.user.id)
            setPhoneRequired(required)
            setProfileChecking(false)
          }
        } else {
          setUser(null)
          setPhoneRequired(false)
          setProfileChecking(false)
        }
      }
    )

    return () => {
      cancelled = true
      subscription.unsubscribe()
    }
  }, [])

  const signUp = async (email: string, password: string) => {
    const result = await supabase.auth.signUp({ email, password })
    return { data: result.data, error: result.error }
  }

  const signIn = async (email: string, password: string) => {
    const result = await supabase.auth.signInWithPassword({ email, password })
    return { data: result.data, error: result.error }
  }

  const signInWithGoogle = async () => {
    await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: {
        redirectTo: `${window.location.origin}/dashboard`,
      },
    })
  }

  const signOut = async () => {
    await supabase.auth.signOut()
  }

  const savePhone = async (phone: string): Promise<{ error: string | null }> => {
    if (!user) return { error: 'Not authenticated' }
    const { error } = await supabase
      .from('user_profiles')
      .upsert({ user_id: user.id, email: user.email, phone }, { onConflict: 'user_id' })
    if (error) return { error: error.message }
    setPhoneRequired(false)
    return { error: null }
  }

  return (
    <AuthContext.Provider
      value={{ user, loading, phoneRequired, profileChecking, signUp, signIn, signInWithGoogle, signOut, savePhone }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend
npx tsc --noEmit
```

Expected: No errors related to `AuthContext.tsx`. Fix any type errors before continuing.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/components/AuthContext.tsx
git commit -m "feat(auth): add Google sign-in, phoneRequired gate, savePhone to AuthContext"
```

---

## Task 2: Create PhoneModal component

**Files:**
- Create: `frontend/app/components/PhoneModal.tsx`

- [ ] **Step 1: Create the file with this exact content**

```tsx
'use client'

import { useState } from 'react'
import { Phone, AlertCircle } from 'lucide-react'
import { useAuth } from './AuthContext'

export function PhoneModal() {
  const { savePhone } = useAuth()
  const [phone, setPhone] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    const digits = phone.replace(/\D/g, '')
    if (!/^[6-9]\d{9}$/.test(digits)) {
      setError('Please enter a valid 10-digit Indian mobile number')
      return
    }

    setLoading(true)
    const { error: saveError } = await savePhone(digits)
    if (saveError) {
      setError("Couldn't save your number, please try again")
      setLoading(false)
    }
    // On success AuthContext sets phoneRequired=false — modal unmounts automatically
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
      style={{ background: 'rgba(13,51,51,0.6)', backdropFilter: 'blur(2px)' }}
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-8">
        <div className="text-center mb-6">
          <div
            className="w-14 h-14 rounded-full flex items-center justify-center mx-auto mb-4"
            style={{ background: '#E6F4F4' }}
          >
            <Phone className="w-6 h-6" style={{ color: '#1A7070' }} />
          </div>
          <h2 className="text-xl font-bold text-gray-900 mb-1">One last step</h2>
          <p className="text-sm text-gray-500">Enter your mobile number to continue</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div className="p-3 rounded-xl bg-red-50 border border-red-200 flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
              <p className="text-sm text-red-800">{error}</p>
            </div>
          )}

          <div>
            <label
              htmlFor="phone-modal"
              className="block text-sm font-medium text-gray-700 mb-2"
            >
              Mobile Number
            </label>
            <div className="flex">
              <span className="inline-flex items-center px-3 rounded-l-xl border border-r-0 border-gray-300 bg-gray-50 text-gray-500 text-sm select-none">
                +91
              </span>
              <input
                id="phone-modal"
                type="tel"
                value={phone}
                onChange={(e) =>
                  setPhone(e.target.value.replace(/\D/g, '').slice(0, 10))
                }
                className="flex-1 px-4 py-3.5 rounded-r-xl border border-gray-300 focus:outline-none focus:ring-2 focus:ring-[#1A7070]/20 focus:border-[#1A7070] transition-all placeholder:text-gray-400"
                placeholder="10-digit number"
                disabled={loading}
                inputMode="numeric"
              />
            </div>
            <p className="mt-1.5 text-xs text-gray-500">
              Indian mobile number starting with 6–9
            </p>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3.5 px-4 bg-[#1A7070] text-white rounded-xl font-medium hover:bg-[#0F4848] focus:outline-none focus:ring-2 focus:ring-[#1A7070]/20 transition-all disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-[#1A7070]/25"
          >
            {loading ? 'Saving...' : 'Continue'}
          </button>
        </form>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend
npx tsc --noEmit
```

Expected: No errors. Fix any before continuing.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/components/PhoneModal.tsx
git commit -m "feat(auth): add PhoneModal — required mobile collection for first-time Google users"
```

---

## Task 3: Add Google button to Login page

**Files:**
- Modify: `frontend/app/login/page.tsx`

- [ ] **Step 1: Add `signInWithGoogle` to the `useAuth` destructure**

In `LoginContent`, the existing destructure is:
```tsx
const { signIn, user } = useAuth()
```

Change it to:
```tsx
const { signIn, signInWithGoogle, user } = useAuth()
```

- [ ] **Step 2: Add the Google button above the form**

Inside the `<div className="bg-white rounded-2xl shadow-lg shadow-gray-200/50 p-8">` block, the current structure opens with the `<div className="mb-8">` header, then `<form onSubmit={handleSubmit} ...>`.

Insert the Google button block **between** the header div and the form:

```tsx
{/* Google Sign In */}
<button
  type="button"
  onClick={signInWithGoogle}
  className="w-full py-3.5 px-4 bg-white border border-gray-300 text-gray-700 rounded-xl font-medium hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-gray-200 transition-all flex items-center justify-center gap-3 mb-4"
>
  <svg className="w-5 h-5 flex-shrink-0" viewBox="0 0 24 24">
    <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
    <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
    <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
    <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
  </svg>
  Continue with Google
</button>

{/* Divider */}
<div className="relative mb-6">
  <div className="absolute inset-0 flex items-center">
    <div className="w-full border-t border-gray-200" />
  </div>
  <div className="relative flex justify-center text-xs uppercase">
    <span className="bg-white px-2 text-gray-400 font-medium tracking-wide">or</span>
  </div>
</div>
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd frontend
npx tsc --noEmit
```

Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/login/page.tsx
git commit -m "feat(auth): add Google sign-in button to Login page"
```

---

## Task 4: Add Google button to Signup page

**Files:**
- Modify: `frontend/app/signup/page.tsx`

- [ ] **Step 1: Add `signInWithGoogle` to the `useAuth` destructure**

The existing destructure in `SignUp` is:
```tsx
const { signUp } = useAuth()
```

Change it to:
```tsx
const { signUp, signInWithGoogle } = useAuth()
```

- [ ] **Step 2: Add the Google button above the form**

Inside the `<form onSubmit={handleSubmit} className="space-y-5">` block, the form opens with the error message block. Insert the Google button **before** the error block (i.e., as the first child of the form, before `{error && ...}`):

```tsx
{/* Google Sign In */}
<button
  type="button"
  onClick={signInWithGoogle}
  className="w-full py-3.5 px-4 bg-white border border-gray-300 text-gray-700 rounded-xl font-medium hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-gray-200 transition-all flex items-center justify-center gap-3"
>
  <svg className="w-5 h-5 flex-shrink-0" viewBox="0 0 24 24">
    <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
    <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
    <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
    <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
  </svg>
  Continue with Google
</button>

{/* Divider */}
<div className="relative">
  <div className="absolute inset-0 flex items-center">
    <div className="w-full border-t border-gray-200" />
  </div>
  <div className="relative flex justify-center text-xs uppercase">
    <span className="bg-white px-2 text-gray-400 font-medium tracking-wide">or</span>
  </div>
</div>
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd frontend
npx tsc --noEmit
```

Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/signup/page.tsx
git commit -m "feat(auth): add Google sign-in button to Signup page"
```

---

## Task 5: Wire PhoneModal into Dashboard

**Files:**
- Modify: `frontend/app/dashboard/page.tsx`

- [ ] **Step 1: Add imports at the top of `dashboard/page.tsx`**

After the existing imports, add:
```tsx
import { PhoneModal } from '../components/PhoneModal'
```

- [ ] **Step 2: Destructure `phoneRequired` and `profileChecking` from `useAuth`**

The existing destructure inside `DashboardContent` is:
```tsx
const { user, signOut, loading: authLoading } = useAuth()
```

Change it to:
```tsx
const { user, signOut, loading: authLoading, phoneRequired, profileChecking } = useAuth()
```

- [ ] **Step 3: Render the modal**

At the very end of the `DashboardContent` return, just before the closing `</div>` of the outermost `min-h-screen` div, add:

```tsx
{user && phoneRequired && !profileChecking && <PhoneModal />}
```

The full tail of the return should look like:

```tsx
      <StatsSection totalLenders={totalCount} />
      <Footer />

      {user && phoneRequired && !profileChecking && <PhoneModal />}
    </div>
  )
}
```

- [ ] **Step 4: Verify TypeScript compiles**

```bash
cd frontend
npx tsc --noEmit
```

Expected: No errors.

- [ ] **Step 5: Run a production build to catch any Next.js-specific issues**

```bash
cd frontend
npm run build
```

Expected: Build completes successfully with no errors. Warnings about `any` types are acceptable.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/dashboard/page.tsx
git commit -m "feat(auth): render PhoneModal on dashboard for first-time Google users"
```

---

## Task 6: Manual end-to-end verification

> **Prerequisite:** Supabase Google provider must be configured (see Prerequisites section above) before testing this task.

- [ ] **Step 1: Start the dev server**

```bash
cd frontend
npm run dev
```

Open `http://localhost:3000`.

- [ ] **Verify: Login page has Google button**

Navigate to `http://localhost:3000/login`.
Expected: "Continue with Google" button appears above the email form, with a Google logo and an "or" divider below it.

- [ ] **Verify: Signup page has Google button**

Navigate to `http://localhost:3000/signup`.
Expected: "Continue with Google" button appears above the sign-up form, with an "or" divider below it.

- [ ] **Verify: First Google login shows phone modal**

1. Click "Continue with Google" on the login page.
2. Complete Google OAuth consent.
3. Expected: Redirected to `/dashboard`, phone modal overlay appears immediately.
4. Try submitting an invalid number (e.g. `1234567890` — starts with 1, not 6–9).
5. Expected: Inline error "Please enter a valid 10-digit Indian mobile number".
6. Enter a valid number (e.g. `9876543210`).
7. Click Continue.
8. Expected: Modal disappears, dashboard is accessible.

- [ ] **Verify: Returning Google user has no modal**

1. Sign out (or open incognito, sign in with same Google account).
2. Expected: Redirected to `/dashboard` with no phone modal.

- [ ] **Verify: Email/password login is unaffected**

1. Sign in with an existing email/password account.
2. Expected: Redirected to `/dashboard`, no phone modal, all existing functionality works.

- [ ] **Commit verification note**

```bash
git commit --allow-empty -m "chore: manual e2e verification passed — Google sign-in + phone modal"
```
