'use client'

/**
 * app/match/page.tsx
 * ====================
 * Borrower-first loan matching.
 *
 * Flow:
 *   1. Borrower fills the eligibility form (loan type, amount, credit score, etc.)
 *   2. On submit → POST /v1/loans/match
 *   3. Ranked MatchResultCard list (eligible first, ineligible after)
 *   4. Unauthenticated users can run 1 free match then are gated to sign up.
 */

import { useState, useCallback, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ArrowLeft,
  Search,
  AlertCircle,
  ChevronRight,
  IndianRupee,
  Percent,
  MapPin,
  Users,
  BadgeCheck,
  AlertTriangle,
} from 'lucide-react'
import { useAuth } from '../components/AuthContext'
import { MatchResultCard, MatchedLoanItem } from '../components/MatchResultCard'
import { SaveProvider } from '../components/SaveContext'

// ─────────────────────────────────────────────────────────────
// CONSTANTS
// ─────────────────────────────────────────────────────────────

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

const LOAN_TYPES = [
  'MSME Loan', 'Personal Loan', 'Home Loan', 'Business Loan',
  'Vehicle Loan', 'Gold Loan', 'Education Loan', 'Micro Loan',
  'Loan Against Property', 'Working Capital', 'Agriculture Loan',
  'EV Loan', 'Two Wheeler Loan', 'Rural Loan', 'Microfinance',
  'Supply Chain Finance', 'Consumer Durable Loan', 'Credit Card',
]

const EMPLOYMENT_TYPES = [
  { value: 'salaried',              label: 'Salaried (Private)' },
  { value: 'salaried_govt',         label: 'Salaried (Government)' },
  { value: 'salaried_psu',          label: 'Salaried (PSU)' },
  { value: 'self-employed',         label: 'Self Employed' },
  { value: 'self_employed_professional', label: 'Self Employed Professional' },
  { value: 'business',              label: 'Business Owner' },
  { value: 'agriculture',           label: 'Agriculture' },
]

const INDIA_STATES = [
  'Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chhattisgarh',
  'Goa', 'Gujarat', 'Haryana', 'Himachal Pradesh', 'Jharkhand', 'Karnataka',
  'Kerala', 'Madhya Pradesh', 'Maharashtra', 'Manipur', 'Meghalaya', 'Mizoram',
  'Nagaland', 'Odisha', 'Punjab', 'Rajasthan', 'Sikkim', 'Tamil Nadu',
  'Telangana', 'Tripura', 'Uttar Pradesh', 'Uttarakhand', 'West Bengal',
  'Delhi', 'Jammu & Kashmir', 'Ladakh', 'Puducherry', 'Chandigarh',
]

// ─────────────────────────────────────────────────────────────
// TYPES
// ─────────────────────────────────────────────────────────────

interface MatchFormState {
  loan_type:            string
  loan_amount:          string   // in Lakhs, user inputs number
  credit_score:         string
  employment_type:      string
  state:                string
  monthly_income:       string   // in ₹ thousands (optional)
  existing_emi_monthly: string   // ₹ per month (optional) — for total FOIR
  age:                  string   // optional
  business_vintage:     string   // years (optional)
  tenure_months:        string   // optional
}

interface MatchAPIResponse {
  profile:        object
  total_matches:  number
  eligible_count: number
  results:        MatchedLoanItem[]
}

const EMPTY_FORM: MatchFormState = {
  loan_type:            '',
  loan_amount:          '',
  credit_score:         '',
  employment_type:      '',
  state:                '',
  monthly_income:       '',
  existing_emi_monthly: '',
  age:                  '',
  business_vintage:     '',
  tenure_months:        '',
}

// ─────────────────────────────────────────────────────────────
// FIELD COMPONENTS
// ─────────────────────────────────────────────────────────────

function FieldLabel({ children, required }: { children: React.ReactNode; required?: boolean }) {
  return (
    <label className="block text-sm font-medium text-gray-700 mb-1.5">
      {children}
      {required && <span className="text-red-500 ml-0.5">*</span>}
    </label>
  )
}

function SelectField({
  value, onChange, options, placeholder, disabled,
}: {
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[] | string[]
  placeholder: string
  disabled?: boolean
}) {
  const normalized = typeof options[0] === 'string'
    ? (options as string[]).map(o => ({ value: o, label: o }))
    : (options as { value: string; label: string }[])

  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      disabled={disabled}
      className="w-full px-3.5 py-2.5 rounded-xl border border-gray-300 bg-white text-sm
                 focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC]
                 transition-all disabled:opacity-50 disabled:cursor-not-allowed
                 text-gray-800"
    >
      <option value="">{placeholder}</option>
      {normalized.map(o => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  )
}

function NumberField({
  value, onChange, placeholder, min, max, disabled,
}: {
  value: string
  onChange: (v: string) => void
  placeholder: string
  min?: number
  max?: number
  disabled?: boolean
}) {
  return (
    <input
      type="number"
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      min={min}
      max={max}
      disabled={disabled}
      className="w-full px-3.5 py-2.5 rounded-xl border border-gray-300 bg-white text-sm
                 focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC]
                 transition-all disabled:opacity-50 disabled:cursor-not-allowed text-gray-800
                 placeholder:text-gray-400"
    />
  )
}

// ─────────────────────────────────────────────────────────────
// MAIN COMPONENT
// ─────────────────────────────────────────────────────────────

export default function MatchPage() {
  const { user } = useAuth()
  const router    = useRouter()

  const [form,        setForm]        = useState<MatchFormState>(EMPTY_FORM)
  const [results,     setResults]     = useState<MatchedLoanItem[] | null>(null)
  const [summary,     setSummary]     = useState<{ total: number; eligible: number } | null>(null)
  const [loading,     setLoading]     = useState(false)
  const [error,       setError]       = useState<string | null>(null)
  const [formErrors,  setFormErrors]  = useState<Partial<MatchFormState>>({})

  // Pre-fill loan_type from ?loan_type= query param (set by landing page quick-match)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const lt = params.get('loan_type')
    if (lt && LOAN_TYPES.includes(lt)) {
      setForm(prev => ({ ...prev, loan_type: lt }))
    }
  }, [])

  const set = useCallback(<K extends keyof MatchFormState>(key: K, val: string) => {
    setForm(prev => ({ ...prev, [key]: val }))
    setFormErrors(prev => ({ ...prev, [key]: '' }))
  }, [])

  const validate = (): boolean => {
    const errs: Partial<MatchFormState> = {}
    if (!form.loan_type)       errs.loan_type       = 'Required'
    if (!form.loan_amount)     errs.loan_amount     = 'Required'
    else if (Number(form.loan_amount) <= 0 || Number(form.loan_amount) > 50_000)
                               errs.loan_amount     = 'Must be 0–50,000 Lakhs'
    if (!form.credit_score)    errs.credit_score    = 'Required'
    else if (Number(form.credit_score) < 300 || Number(form.credit_score) > 900)
                               errs.credit_score    = 'Must be 300–900'
    if (!form.employment_type) errs.employment_type = 'Required'
    if (!form.state)           errs.state           = 'Required'
    setFormErrors(errs)
    return Object.keys(errs).length === 0
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!validate()) return

    // Gate unauthenticated users after they see the form is valid
    if (!user) {
      router.push(`/signup?redirect=/match`)
      return
    }

    setError(null)
    setLoading(true)

    const body: Record<string, unknown> = {
      loan_type:        form.loan_type,
      loan_amount:      Number(form.loan_amount),
      credit_score:     Number(form.credit_score),
      employment_type:  form.employment_type,
      state:            form.state,
    }
    if (form.monthly_income)       body.monthly_income       = Number(form.monthly_income)
    if (form.existing_emi_monthly) body.existing_emi_monthly = Number(form.existing_emi_monthly)
    if (form.age)                  body.age                  = Number(form.age)
    if (form.business_vintage)     body.business_vintage     = Number(form.business_vintage)
    if (form.tenure_months)        body.tenure_months        = Number(form.tenure_months)

    try {
      const res = await fetch(`${API_URL}/v1/loans/match?limit=30`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(body),
      })
      if (!res.ok) {
        const txt = await res.text()
        throw new Error(`API error ${res.status}: ${txt}`)
      }
      const data: MatchAPIResponse = await res.json()
      setResults(data.results)
      setSummary({ total: data.total_matches, eligible: data.eligible_count })
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Unknown error')
      setResults(null)
    } finally {
      setLoading(false)
    }
  }

  const resetForm = () => {
    setForm(EMPTY_FORM)
    setResults(null)
    setSummary(null)
    setError(null)
    setFormErrors({})
  }

  // ── Render ──────────────────────────────────────────────────────

  return (
    <SaveProvider>
      <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white">

        {/* Nav */}
        <nav className="bg-white border-b border-gray-200 sticky top-0 z-10">
          <div className="max-w-5xl mx-auto px-4 sm:px-6 py-3 flex items-center gap-4">
            <Link
              href="/"
              className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-900 transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
              Home
            </Link>
            <span className="text-gray-300">/</span>
            <span className="text-sm font-semibold text-gray-800">Find My Loan</span>
            <div className="ml-auto flex items-center gap-3">
              {!user ? (
                <>
                  <Link href="/login"  className="text-sm text-gray-600 hover:text-gray-900 transition-colors">Login</Link>
                  <Link href="/signup" className="text-sm bg-[#3B5CCC] text-white px-4 py-1.5 rounded-lg hover:bg-[#2d4aa8] transition-colors">Sign Up</Link>
                </>
              ) : (
                <Link href="/dashboard" className="text-sm text-gray-600 hover:text-gray-900 transition-colors">Browse All Lenders →</Link>
              )}
            </div>
          </div>
        </nav>

        <main className="max-w-5xl mx-auto px-4 sm:px-6 py-10">

          {/* Header */}
          <div className="mb-8 text-center">
            <h1 className="text-3xl font-bold text-gray-900 mb-2">Find Your Best Loan Match</h1>
            <p className="text-gray-500 text-base">
              Tell us your profile — we rank eligible lenders by match score, rate, and FOIR fit.
            </p>
          </div>

          {/* Form card */}
          <form onSubmit={handleSubmit} noValidate>
            <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-6 sm:p-8 mb-6">

              <h2 className="text-base font-semibold text-gray-900 mb-5 flex items-center gap-2">
                <Search className="w-4 h-4 text-[#3B5CCC]" />
                Loan Requirements
              </h2>

              {/* Row 1 — Loan type + amount */}
              <div className="grid sm:grid-cols-2 gap-5 mb-5">
                <div>
                  <FieldLabel required>Loan Type</FieldLabel>
                  <SelectField
                    value={form.loan_type}
                    onChange={v => set('loan_type', v)}
                    options={LOAN_TYPES}
                    placeholder="Select loan type"
                    disabled={loading}
                  />
                  {formErrors.loan_type && (
                    <p className="text-xs text-red-500 mt-1">{formErrors.loan_type}</p>
                  )}
                </div>

                <div>
                  <FieldLabel required>Loan Amount</FieldLabel>
                  <div className="relative">
                    <IndianRupee className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
                    <input
                      type="number"
                      value={form.loan_amount}
                      onChange={e => set('loan_amount', e.target.value)}
                      placeholder="e.g. 25"
                      min={0.1}
                      max={50000}
                      step={0.1}
                      disabled={loading}
                      className="w-full pl-9 pr-16 py-2.5 rounded-xl border border-gray-300 bg-white text-sm
                                 focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC]
                                 transition-all disabled:opacity-50 text-gray-800 placeholder:text-gray-400"
                    />
                    <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400 pointer-events-none">
                      Lakhs
                    </span>
                  </div>
                  {formErrors.loan_amount && (
                    <p className="text-xs text-red-500 mt-1">{formErrors.loan_amount}</p>
                  )}
                </div>
              </div>

              {/* Row 2 — Credit score + employment */}
              <div className="grid sm:grid-cols-2 gap-5 mb-5">
                <div>
                  <FieldLabel required>CIBIL / Credit Score</FieldLabel>
                  <div className="relative">
                    <Percent className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
                    <input
                      type="number"
                      value={form.credit_score}
                      onChange={e => set('credit_score', e.target.value)}
                      placeholder="e.g. 720"
                      min={300}
                      max={900}
                      disabled={loading}
                      className="w-full pl-9 pr-4 py-2.5 rounded-xl border border-gray-300 bg-white text-sm
                                 focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC]
                                 transition-all disabled:opacity-50 text-gray-800 placeholder:text-gray-400"
                    />
                  </div>
                  {formErrors.credit_score && (
                    <p className="text-xs text-red-500 mt-1">{formErrors.credit_score}</p>
                  )}
                </div>

                <div>
                  <FieldLabel required>Employment Type</FieldLabel>
                  <SelectField
                    value={form.employment_type}
                    onChange={v => set('employment_type', v)}
                    options={EMPLOYMENT_TYPES}
                    placeholder="Select employment type"
                    disabled={loading}
                  />
                  {formErrors.employment_type && (
                    <p className="text-xs text-red-500 mt-1">{formErrors.employment_type}</p>
                  )}
                </div>
              </div>

              {/* Row 3 — State + monthly income */}
              <div className="grid sm:grid-cols-2 gap-5 mb-5">
                <div>
                  <FieldLabel required>Your State</FieldLabel>
                  <div className="relative">
                    <MapPin className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
                    <select
                      value={form.state}
                      onChange={e => set('state', e.target.value)}
                      disabled={loading}
                      className="w-full pl-9 pr-3.5 py-2.5 rounded-xl border border-gray-300 bg-white text-sm
                                 focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC]
                                 transition-all disabled:opacity-50 text-gray-800"
                    >
                      <option value="">Select state</option>
                      {INDIA_STATES.map(s => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </div>
                  {formErrors.state && (
                    <p className="text-xs text-red-500 mt-1">{formErrors.state}</p>
                  )}
                </div>

                <div>
                  <FieldLabel>Monthly Income <span className="text-gray-400 font-normal">(optional)</span></FieldLabel>
                  <div className="relative">
                    <IndianRupee className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
                    <input
                      type="number"
                      value={form.monthly_income}
                      onChange={e => set('monthly_income', e.target.value)}
                      placeholder="e.g. 75"
                      min={1}
                      disabled={loading}
                      className="w-full pl-9 pr-20 py-2.5 rounded-xl border border-gray-300 bg-white text-sm
                                 focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC]
                                 transition-all disabled:opacity-50 text-gray-800 placeholder:text-gray-400"
                    />
                    <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400 pointer-events-none">
                      ₹ thousands
                    </span>
                  </div>
                  <p className="text-xs text-gray-400 mt-1">Used to calculate FOIR (EMI/income ratio)</p>
                </div>

                <div>
                  <FieldLabel>Existing EMIs <span className="text-gray-400 font-normal">(optional)</span></FieldLabel>
                  <div className="relative">
                    <IndianRupee className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
                    <input
                      type="number"
                      value={form.existing_emi_monthly}
                      onChange={e => set('existing_emi_monthly', e.target.value)}
                      placeholder="e.g. 8000"
                      min={0}
                      disabled={loading}
                      className="w-full pl-9 pr-12 py-2.5 rounded-xl border border-gray-300 bg-white text-sm
                                 focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC]
                                 transition-all disabled:opacity-50 text-gray-800 placeholder:text-gray-400"
                    />
                    <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400 pointer-events-none">
                      ₹/mo
                    </span>
                  </div>
                  <p className="text-xs text-gray-400 mt-1">Home loan, car, CC obligations — for total FOIR</p>
                </div>
              </div>

              {/* Row 4 — Optional fields */}
              <div className="grid sm:grid-cols-3 gap-5">
                <div>
                  <FieldLabel>Age <span className="text-gray-400 font-normal">(optional)</span></FieldLabel>
                  <NumberField
                    value={form.age}
                    onChange={v => set('age', v)}
                    placeholder="e.g. 35"
                    min={18} max={80}
                    disabled={loading}
                  />
                </div>
                <div>
                  <FieldLabel>Business Vintage <span className="text-gray-400 font-normal">(years)</span></FieldLabel>
                  <NumberField
                    value={form.business_vintage}
                    onChange={v => set('business_vintage', v)}
                    placeholder="e.g. 3"
                    min={0} max={100}
                    disabled={loading}
                  />
                </div>
                <div>
                  <FieldLabel>Tenure <span className="text-gray-400 font-normal">(months)</span></FieldLabel>
                  <NumberField
                    value={form.tenure_months}
                    onChange={v => set('tenure_months', v)}
                    placeholder="e.g. 60"
                    min={1} max={600}
                    disabled={loading}
                  />
                </div>
              </div>

            </div>

            {/* Auth notice for unauthenticated */}
            {!user && (
              <div className="flex items-start gap-3 bg-blue-50 border border-blue-200 rounded-xl px-4 py-3 mb-5 text-sm text-blue-800">
                <Users className="w-4 h-4 mt-0.5 flex-shrink-0 text-blue-500" />
                <span>
                  You&apos;ll need to{' '}
                  <Link href="/signup" className="font-semibold underline hover:no-underline">create a free account</Link>
                  {' '}to see your personalised matches.
                </span>
              </div>
            )}

            {/* Error */}
            {error && (
              <div className="flex items-start gap-3 bg-red-50 border border-red-200 rounded-xl px-4 py-3 mb-5">
                <AlertCircle className="w-4 h-4 text-red-500 mt-0.5 flex-shrink-0" />
                <p className="text-sm text-red-700">{error}</p>
              </div>
            )}

            {/* Submit */}
            <div className="flex gap-3">
              <button
                type="submit"
                disabled={loading}
                className="flex-1 sm:flex-none inline-flex items-center justify-center gap-2
                           px-8 py-3 bg-[#3B5CCC] text-white rounded-xl font-semibold text-sm
                           hover:bg-[#2d4aa8] transition-all disabled:opacity-50 disabled:cursor-not-allowed
                           hover:shadow-lg hover:shadow-[#3B5CCC]/25 hover:scale-[1.02] active:scale-[0.98]"
              >
                {loading ? (
                  <>
                    <span className="animate-spin inline-block w-4 h-4 border-b-2 border-white rounded-full" />
                    Matching…
                  </>
                ) : (
                  <>
                    <Search className="w-4 h-4" />
                    Find My Matches
                    <ChevronRight className="w-4 h-4" />
                  </>
                )}
              </button>

              {results !== null && (
                <button
                  type="button"
                  onClick={resetForm}
                  className="px-5 py-3 border border-gray-200 text-gray-600 text-sm font-medium
                             rounded-xl hover:bg-gray-50 transition-colors"
                >
                  Reset
                </button>
              )}
            </div>
          </form>

          {/* Results */}
          {results !== null && (
            <div className="mt-8">

              {/* Summary bar */}
              {summary && (
                <div className="flex flex-wrap items-center gap-4 mb-6 p-4 bg-white rounded-xl border border-gray-200 shadow-sm">
                  <div className="flex items-center gap-2 text-sm">
                    <BadgeCheck className="w-4 h-4 text-emerald-500" />
                    <span className="font-semibold text-gray-900">{summary.eligible}</span>
                    <span className="text-gray-500">eligible</span>
                  </div>
                  <div className="w-px h-4 bg-gray-200" />
                  <div className="flex items-center gap-2 text-sm text-gray-500">
                    <span className="font-semibold text-gray-700">{summary.total}</span>
                    total matches found
                  </div>
                  {summary.eligible === 0 && (
                    <>
                      <div className="w-px h-4 bg-gray-200" />
                      <div className="flex items-center gap-1.5 text-xs text-amber-700">
                        <AlertTriangle className="w-3.5 h-3.5" />
                        No eligible matches — try adjusting your credit score or loan amount
                      </div>
                    </>
                  )}
                </div>
              )}

              {results.length === 0 ? (
                <div className="text-center py-16 bg-white rounded-2xl border border-gray-200">
                  <div className="text-4xl mb-3" aria-hidden="true">🔍</div>
                  <p className="font-semibold text-gray-800 mb-1">No matches found</p>
                  <p className="text-sm text-gray-400 mb-4">
                    Try a different loan type, lower loan amount, or check if your state is covered.
                  </p>
                  <button
                    onClick={resetForm}
                    className="px-5 py-2 bg-[#3B5CCC] text-white rounded-xl text-sm font-medium hover:bg-[#2d4aa8] transition-colors"
                  >
                    Try Again
                  </button>
                </div>
              ) : (
                <div className="grid sm:grid-cols-2 gap-4">
                  {results.map((item, i) => (
                    <MatchResultCard key={item.policy_id} item={item} rank={i + 1} />
                  ))}
                </div>
              )}
            </div>
          )}

        </main>
      </div>
    </SaveProvider>
  )
}
