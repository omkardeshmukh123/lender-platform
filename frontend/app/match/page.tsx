'use client'

/**
 * /match — Loan Matching Wizard
 *
 * Step 1: Borrower fills in their profile (loan type, amount, score, etc.)
 * Step 2: Matched policies + lenders shown, sorted by completeness + rate
 *
 * Calls GET /v1/policies/filter — no auth required (public endpoint)
 */

import { useState } from 'react'
import Link from 'next/link'
import {
  ArrowLeft, ArrowRight, Search, IndianRupee, Percent,
  Clock, TrendingUp, Globe, ExternalLink, Building2,
  ChevronDown, ChevronUp, RefreshCw, BadgeCheck, AlertCircle,
} from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

// ─── constants ────────────────────────────────────────────────────────────────

const LOAN_TYPES = [
  'MSME Loan', 'Personal Loan', 'Home Loan', 'Business Loan',
  'Vehicle Loan', 'Gold Loan', 'Education Loan', 'Micro Loan',
  'Loan Against Property', 'Working Capital', 'Agriculture Loan',
  'EV Loan', 'Two Wheeler Loan', 'Rural Loan', 'Microfinance',
  'Supply Chain Finance', 'Consumer Durable Loan', 'Credit Card',
]

const EMPLOYMENT_TYPES = [
  { value: 'salaried',      label: 'Salaried (Private)' },
  { value: 'salaried_govt', label: 'Salaried (Govt / PSU)' },
  { value: 'business',      label: 'Business Owner' },
  { value: 'self_employed_professional', label: 'Self Employed Professional' },
  { value: 'self_employed_non_professional', label: 'Self Employed (Non-Professional)' },
  { value: 'agriculture',   label: 'Agriculture / Farmer' },
  { value: 'nri',           label: 'NRI' },
]

const INDIA_STATES = [
  'Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chhattisgarh',
  'Goa', 'Gujarat', 'Haryana', 'Himachal Pradesh', 'Jharkhand', 'Karnataka',
  'Kerala', 'Madhya Pradesh', 'Maharashtra', 'Manipur', 'Meghalaya', 'Mizoram',
  'Nagaland', 'Odisha', 'Punjab', 'Rajasthan', 'Sikkim', 'Tamil Nadu',
  'Telangana', 'Tripura', 'Uttar Pradesh', 'Uttarakhand', 'West Bengal',
  'Delhi', 'Jammu & Kashmir', 'Ladakh', 'Puducherry', 'Chandigarh',
]

// ─── types ────────────────────────────────────────────────────────────────────

interface BorrowerProfile {
  loanType:       string
  amountLakhs:    string
  creditScore:    string
  employmentType: string
  state:          string
  collateral:     string   // 'yes' | 'no' | ''
  age:            string
  monthlyIncome:  string
}

interface MatchedPolicy {
  id:                 number
  lender_id:          number
  lender_name:        string | null
  product_name:       string | null
  loan_type:          string | null
  loan_amount_min:    number | null
  loan_amount_max:    number | null
  interest_rate_min:  number | null
  interest_rate_max:  number | null
  tenure_min:         number | null
  tenure_max:         number | null
  processing_fee:     number | null
  credit_score_min:   number | null
  employment_types:   string[]
  collateral_required: boolean
  eligible_states:    string[]
  eligibility_notes:  string | null
  completeness_score: number | null
}

interface FilterResponse {
  total:   number
  results: MatchedPolicy[]
}

// ─── helpers ──────────────────────────────────────────────────────────────────

function fmtAmount(v: number | null): string {
  if (v === null) return '—'
  if (v >= 100) return `₹${(v / 100).toFixed(1)} Cr`
  return `₹${v.toLocaleString('en-IN')} L`
}

function fmtTenure(m: number | null): string {
  if (m === null) return '—'
  if (m >= 12) return `${Math.floor(m / 12)}y${m % 12 ? ` ${m % 12}m` : ''}`
  return `${m}m`
}

const EMPTY: BorrowerProfile = {
  loanType: '', amountLakhs: '', creditScore: '', employmentType: '',
  state: '', collateral: '', age: '', monthlyIncome: '',
}

// ─── policy result card ───────────────────────────────────────────────────────

function MatchCard({ policy }: { policy: MatchedPolicy }) {
  const [expanded, setExpanded] = useState(false)
  const hasRate   = policy.interest_rate_min !== null || policy.interest_rate_max !== null
  const hasAmount = policy.loan_amount_min !== null || policy.loan_amount_max !== null
  const hasTenure = policy.tenure_min !== null || policy.tenure_max !== null

  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden hover:border-[#3B5CCC]/30 hover:shadow-sm transition-all">
      <div className="p-5">
        {/* lender + product header */}
        <div className="flex items-start justify-between gap-3 mb-4">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap mb-1">
              <span className="px-2 py-0.5 rounded-md text-xs font-semibold bg-blue-50 text-[#3B5CCC] border border-blue-100">
                {policy.loan_type}
              </span>
              {policy.completeness_score !== null && policy.completeness_score < 0.3 && (
                <span className="px-2 py-0.5 rounded-md text-xs font-medium bg-amber-50 text-amber-700 border border-amber-100">
                  Limited data
                </span>
              )}
            </div>
            <p className="text-base font-bold text-gray-900 truncate">
              {policy.lender_name ?? 'Lender'}
            </p>
            {policy.product_name && policy.product_name !== policy.loan_type && (
              <p className="text-xs text-gray-500 mt-0.5">{policy.product_name}</p>
            )}
          </div>
          <Link
            href={`/lender/${policy.lender_id}`}
            className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5
                       border border-gray-200 text-gray-600 text-xs font-medium rounded-lg
                       hover:border-[#3B5CCC]/40 hover:text-[#3B5CCC] transition-colors"
          >
            Details
            <ArrowRight className="w-3 h-3" />
          </Link>
        </div>

        {/* key metrics */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="bg-gray-50 rounded-lg p-3">
            <div className="flex items-center gap-1 text-xs text-gray-400 mb-1">
              <Percent className="w-3 h-3" /> Interest
            </div>
            <div className="text-sm font-bold text-gray-900">
              {hasRate
                ? policy.interest_rate_min !== null && policy.interest_rate_max !== null
                  ? `${policy.interest_rate_min}–${policy.interest_rate_max}%`
                  : `${policy.interest_rate_min ?? policy.interest_rate_max}%`
                : <span className="text-gray-400 font-normal text-xs">N/A</span>}
            </div>
          </div>

          <div className="bg-gray-50 rounded-lg p-3">
            <div className="flex items-center gap-1 text-xs text-gray-400 mb-1">
              <IndianRupee className="w-3 h-3" /> Amount
            </div>
            <div className="text-sm font-bold text-gray-900">
              {hasAmount
                ? `${fmtAmount(policy.loan_amount_min)}${policy.loan_amount_max ? ` – ${fmtAmount(policy.loan_amount_max)}` : ''}`
                : <span className="text-gray-400 font-normal text-xs">N/A</span>}
            </div>
          </div>

          <div className="bg-gray-50 rounded-lg p-3">
            <div className="flex items-center gap-1 text-xs text-gray-400 mb-1">
              <Clock className="w-3 h-3" /> Tenure
            </div>
            <div className="text-sm font-bold text-gray-900">
              {hasTenure
                ? `${fmtTenure(policy.tenure_min)}${policy.tenure_max ? ` – ${fmtTenure(policy.tenure_max)}` : ''}`
                : <span className="text-gray-400 font-normal text-xs">N/A</span>}
            </div>
          </div>

          <div className="bg-gray-50 rounded-lg p-3">
            <div className="flex items-center gap-1 text-xs text-gray-400 mb-1">
              <TrendingUp className="w-3 h-3" /> Min CIBIL
            </div>
            <div className="text-sm font-bold text-gray-900">
              {policy.credit_score_min
                ? policy.credit_score_min
                : <span className="text-gray-400 font-normal text-xs">Not specified</span>}
            </div>
          </div>
        </div>
      </div>

      {/* expandable details */}
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full px-5 py-2.5 flex items-center justify-between text-xs text-gray-500
                   bg-gray-50 border-t border-gray-100 hover:bg-gray-100 transition-colors"
      >
        <span>{expanded ? 'Hide details' : 'More details'}</span>
        {expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
      </button>

      {expanded && (
        <div className="px-5 py-4 border-t border-gray-100 grid sm:grid-cols-2 gap-4 text-sm">
          {policy.processing_fee !== null && (
            <div>
              <p className="text-xs text-gray-400 mb-1">Processing Fee</p>
              <p className="text-gray-700">{policy.processing_fee}%</p>
            </div>
          )}
          <div>
            <p className="text-xs text-gray-400 mb-1">Collateral</p>
            <p className="text-gray-700">{policy.collateral_required ? 'Required' : 'Not required'}</p>
          </div>
          {policy.eligible_states.length > 0 && (
            <div className="sm:col-span-2">
              <p className="text-xs text-gray-400 mb-1">Available in</p>
              <div className="flex flex-wrap gap-1.5">
                {policy.eligible_states.slice(0, 10).map(s => (
                  <span key={s} className="px-2 py-0.5 text-xs bg-gray-100 text-gray-600 rounded-md">{s}</span>
                ))}
                {policy.eligible_states.length > 10 && (
                  <span className="px-2 py-0.5 text-xs bg-gray-100 text-gray-500 rounded-md">+{policy.eligible_states.length - 10} more</span>
                )}
              </div>
            </div>
          )}
          {policy.eligibility_notes && (
            <div className="sm:col-span-2">
              <p className="text-xs text-gray-400 mb-1">Notes</p>
              <p className="text-xs text-gray-600 leading-relaxed">{policy.eligibility_notes}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─── main wizard ──────────────────────────────────────────────────────────────

export default function MatchPage() {
  const [step,    setStep]    = useState<1 | 2>(1)
  const [profile, setProfile] = useState<BorrowerProfile>(EMPTY)
  const [results, setResults] = useState<MatchedPolicy[]>([])
  const [total,   setTotal]   = useState(0)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  const set = (k: keyof BorrowerProfile, v: string) =>
    setProfile(p => ({ ...p, [k]: v }))

  const canSubmit = !!profile.loanType

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit) return
    setLoading(true)
    setError(null)

    const params = new URLSearchParams()
    params.set('loan_type', profile.loanType)
    params.set('limit', '30')
    if (profile.amountLakhs)    params.set('loan_amount',      profile.amountLakhs)
    if (profile.creditScore)    params.set('credit_score',     profile.creditScore)
    if (profile.employmentType) params.set('employment_type',  profile.employmentType)
    if (profile.state)          params.set('state',            profile.state)
    if (profile.age)            params.set('age',              profile.age)
    if (profile.monthlyIncome)  params.set('monthly_income',   profile.monthlyIncome)
    if (profile.collateral === 'no') params.set('collateral', 'false')

    try {
      const res = await fetch(`${API_URL}/v1/policies/filter?${params}`)
      if (!res.ok) throw new Error(`Error ${res.status}`)
      const data: FilterResponse = await res.json()
      setResults(data.results ?? [])
      setTotal(data.total ?? 0)
      setStep(2)
    } catch (err: any) {
      setError('Could not fetch results. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  // ── Step 1: borrower profile form ─────────────────────────────

  if (step === 1) {
    return (
      <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white">
        {/* Header */}
        <nav className="bg-white border-b border-gray-200">
          <div className="max-w-2xl mx-auto px-4 sm:px-6 py-4 flex items-center gap-3">
            <Link href="/dashboard" className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-500 transition-colors">
              <ArrowLeft className="w-4 h-4" />
            </Link>
            <div>
              <h1 className="text-base font-bold text-gray-900">Find Your Match</h1>
              <p className="text-xs text-gray-400">Tell us about your loan requirement</p>
            </div>
          </div>
        </nav>

        <div className="max-w-2xl mx-auto px-4 sm:px-6 py-10">
          <form onSubmit={handleSubmit} className="space-y-6">

            {/* Loan type — required */}
            <div className="bg-white rounded-2xl border border-gray-200 p-6">
              <label className="block text-sm font-semibold text-gray-800 mb-3">
                What kind of loan do you need? <span className="text-red-500">*</span>
              </label>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {LOAN_TYPES.map(lt => (
                  <button
                    key={lt}
                    type="button"
                    onClick={() => set('loanType', lt)}
                    className={[
                      'px-3 py-2 rounded-xl text-sm font-medium border text-left transition-all',
                      profile.loanType === lt
                        ? 'bg-[#3B5CCC] text-white border-[#3B5CCC]'
                        : 'bg-gray-50 text-gray-700 border-gray-200 hover:border-[#3B5CCC]/40',
                    ].join(' ')}
                  >
                    {lt}
                  </button>
                ))}
              </div>
            </div>

            {/* Amount + credit score */}
            <div className="bg-white rounded-2xl border border-gray-200 p-6 grid sm:grid-cols-2 gap-5">
              <div>
                <label className="block text-sm font-semibold text-gray-800 mb-2">
                  Loan Amount Needed <span className="text-gray-400 font-normal">(₹ Lakhs)</span>
                </label>
                <input
                  type="number"
                  min={0}
                  placeholder="e.g. 25"
                  value={profile.amountLakhs}
                  onChange={e => set('amountLakhs', e.target.value)}
                  className="w-full px-4 py-3 rounded-xl border border-gray-300
                             focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC]
                             text-sm placeholder:text-gray-400"
                />
              </div>
              <div>
                <label className="block text-sm font-semibold text-gray-800 mb-2">
                  Your CIBIL / Credit Score
                </label>
                <input
                  type="number"
                  min={300}
                  max={900}
                  placeholder="e.g. 720"
                  value={profile.creditScore}
                  onChange={e => set('creditScore', e.target.value)}
                  className="w-full px-4 py-3 rounded-xl border border-gray-300
                             focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC]
                             text-sm placeholder:text-gray-400"
                />
                <p className="text-xs text-gray-400 mt-1.5">Range: 300 – 900</p>
              </div>
            </div>

            {/* Employment + state */}
            <div className="bg-white rounded-2xl border border-gray-200 p-6 grid sm:grid-cols-2 gap-5">
              <div>
                <label className="block text-sm font-semibold text-gray-800 mb-2">
                  Your Employment Type
                </label>
                <select
                  value={profile.employmentType}
                  onChange={e => set('employmentType', e.target.value)}
                  className="w-full px-4 py-3 rounded-xl border border-gray-300
                             focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC]
                             text-sm text-gray-700 bg-white"
                >
                  <option value="">Any / Not sure</option>
                  {EMPLOYMENT_TYPES.map(et => (
                    <option key={et.value} value={et.value}>{et.label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-semibold text-gray-800 mb-2">
                  Your State
                </label>
                <select
                  value={profile.state}
                  onChange={e => set('state', e.target.value)}
                  className="w-full px-4 py-3 rounded-xl border border-gray-300
                             focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC]
                             text-sm text-gray-700 bg-white"
                >
                  <option value="">Any State</option>
                  {INDIA_STATES.map(s => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* Age + income + collateral */}
            <div className="bg-white rounded-2xl border border-gray-200 p-6 grid sm:grid-cols-3 gap-5">
              <div>
                <label className="block text-sm font-semibold text-gray-800 mb-2">Age (years)</label>
                <input
                  type="number"
                  min={18} max={80}
                  placeholder="e.g. 35"
                  value={profile.age}
                  onChange={e => set('age', e.target.value)}
                  className="w-full px-4 py-3 rounded-xl border border-gray-300
                             focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC]
                             text-sm placeholder:text-gray-400"
                />
              </div>
              <div>
                <label className="block text-sm font-semibold text-gray-800 mb-2">
                  Monthly Income <span className="text-gray-400 font-normal">(₹k)</span>
                </label>
                <input
                  type="number"
                  min={0}
                  placeholder="e.g. 50"
                  value={profile.monthlyIncome}
                  onChange={e => set('monthlyIncome', e.target.value)}
                  className="w-full px-4 py-3 rounded-xl border border-gray-300
                             focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC]
                             text-sm placeholder:text-gray-400"
                />
              </div>
              <div>
                <label className="block text-sm font-semibold text-gray-800 mb-2">
                  Collateral Available?
                </label>
                <div className="flex gap-2 mt-1">
                  {[['yes', 'Yes'], ['no', 'No'], ['', 'Either']].map(([v, label]) => (
                    <button
                      key={v}
                      type="button"
                      onClick={() => set('collateral', v)}
                      className={[
                        'flex-1 py-2.5 rounded-xl text-sm font-medium border transition-all',
                        profile.collateral === v
                          ? 'bg-[#3B5CCC] text-white border-[#3B5CCC]'
                          : 'bg-gray-50 text-gray-600 border-gray-200 hover:border-gray-300',
                      ].join(' ')}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {error && (
              <div className="flex items-center gap-3 p-4 bg-red-50 border border-red-200 rounded-xl text-sm text-red-700">
                <AlertCircle className="w-4 h-4 flex-shrink-0" />
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={!canSubmit || loading}
              className="w-full py-4 bg-[#3B5CCC] text-white rounded-2xl font-semibold text-base
                         hover:bg-[#2d4aa8] transition-all hover:shadow-lg hover:shadow-[#3B5CCC]/25
                         disabled:opacity-50 disabled:cursor-not-allowed
                         flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <span className="animate-spin inline-block w-4 h-4 border-b-2 border-white rounded-full" />
                  Finding matches…
                </>
              ) : (
                <>
                  <Search className="w-4 h-4" />
                  Find Matching Lenders
                </>
              )}
            </button>

            <p className="text-center text-xs text-gray-400">
              Only the loan type is required — more details = more precise results
            </p>
          </form>
        </div>
      </div>
    )
  }

  // ── Step 2: results ───────────────────────────────────────────

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white">
      <nav className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-3xl mx-auto px-4 sm:px-6 py-4 flex items-center gap-3">
          <button
            onClick={() => setStep(1)}
            className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-500 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div className="flex-1 min-w-0">
            <h1 className="text-base font-bold text-gray-900">
              {total > 0 ? `${total} matching option${total !== 1 ? 's' : ''}` : 'No matches found'}
            </h1>
            <p className="text-xs text-gray-400 truncate">
              {profile.loanType}
              {profile.amountLakhs ? ` · ₹${profile.amountLakhs}L` : ''}
              {profile.state ? ` · ${profile.state}` : ''}
              {profile.creditScore ? ` · CIBIL ${profile.creditScore}` : ''}
            </p>
          </div>
          <button
            onClick={() => setStep(1)}
            className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5
                       border border-gray-200 text-gray-600 text-xs font-medium rounded-lg
                       hover:border-gray-300 transition-colors"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Edit
          </button>
        </div>
      </nav>

      <div className="max-w-3xl mx-auto px-4 sm:px-6 py-8">
        {results.length === 0 ? (
          <div className="text-center py-16 bg-white rounded-2xl border border-gray-200">
            <Building2 className="w-12 h-12 text-gray-200 mx-auto mb-4" />
            <p className="font-semibold text-gray-700 mb-1">No matching policies found</p>
            <p className="text-sm text-gray-400 mb-6">
              Try relaxing some criteria — remove state, credit score, or collateral filter.
            </p>
            <button
              onClick={() => setStep(1)}
              className="px-6 py-2.5 bg-[#3B5CCC] text-white rounded-xl font-medium
                         hover:bg-[#2d4aa8] transition-colors"
            >
              Adjust Criteria
            </button>
          </div>
        ) : (
          <>
            {total > results.length && (
              <p className="text-xs text-gray-400 mb-4 text-center">
                Showing top {results.length} of {total} matches · sorted by best data quality and lowest rate
              </p>
            )}
            <div className="space-y-3">
              {results.map(p => (
                <MatchCard key={p.id} policy={p} />
              ))}
            </div>
            <div className="mt-8 text-center">
              <Link
                href="/dashboard"
                className="inline-flex items-center gap-2 px-6 py-3 border border-gray-200
                           text-sm font-medium text-gray-600 rounded-xl hover:border-[#3B5CCC]/40
                           hover:text-[#3B5CCC] transition-colors"
              >
                Browse all lenders
                <ArrowRight className="w-4 h-4" />
              </Link>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
