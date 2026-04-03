'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  MapPin, Globe, Phone, Mail, Building2, Users, GitBranch,
  Calendar, TrendingUp, BadgeCheck, ArrowLeft, ExternalLink,
  IndianRupee, Percent, Clock, Shield, ChevronDown, ChevronUp,
  FileText,
} from 'lucide-react'

// ─────────────────────────────────────────────────────────────
// TYPES
// ─────────────────────────────────────────────────────────────

interface LenderDetail {
  id: number
  company_name: string
  company_type: string
  rbi_category: string | null
  aum_crores: number | null
  aum_category: string | null
  hq_location: string | null
  hq_state: string | null
  operating_intensity: string | null
  pan_india: boolean
  primary_loan_segments: string[]
  operating_states: string[]
  website: string | null
  phone: string | null
  email: string | null
  employee_count: number | null
  branch_count: number | null
  established_year: number | null
  is_listed: boolean
  stock_symbol: string | null
  quality_score: number | null
  last_scraped_at: string | null
  data_source: string | null
}

interface Policy {
  id: number
  lender_id: number
  lender_name: string | null
  product_name: string | null
  loan_type: string | null
  loan_amount_min: number | null
  loan_amount_max: number | null
  credit_score_min: number | null
  credit_score_max: number | null
  interest_rate_min: number | null
  interest_rate_max: number | null
  tenure_min: number | null
  tenure_max: number | null
  processing_fee: number | null
  employment_types: string[]
  collateral_required: boolean
  collateral_types: string[]
  eligible_states: string[]
  min_age: number | null
  max_age: number | null
  min_monthly_income: number | null
  prepayment_allowed: boolean
  eligibility_notes: string | null
  completeness_score: number | null
  data_source: string | null
}

interface PolicyResponse {
  total: number
  page: number
  limit: number
  results: Policy[]
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

// ─────────────────────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────────────────────

function formatAUM(crores: number | null): string {
  if (crores === null) return 'N/A'
  if (crores >= 100_000) return `₹${(crores / 100_000).toFixed(1)}L Cr`
  if (crores >= 1_000)   return `₹${(crores / 1_000).toFixed(1)}K Cr`
  return `₹${crores.toLocaleString('en-IN')} Cr`
}

function formatAmount(val: number | null): string {
  if (val === null) return '—'
  if (val >= 100) return `₹${(val / 100).toFixed(1)} Cr`
  return `₹${val.toLocaleString('en-IN')} L`
}

function formatTenure(months: number | null): string {
  if (months === null) return '—'
  if (months >= 12) return `${Math.floor(months / 12)}y${months % 12 ? ` ${months % 12}m` : ''}`
  return `${months}m`
}

function formatEmp(types: string[]): string {
  if (!types || types.length === 0) return '—'
  const labels: Record<string, string> = {
    salaried: 'Salaried', salaried_govt: 'Govt Employee', salaried_psu: 'PSU',
    salaried_private: 'Private Salaried', self_employed_professional: 'Self Employed (Pro)',
    self_employed_non_professional: 'Self Employed', 'self-employed': 'Self Employed',
    business: 'Business', agriculture: 'Agriculture', student: 'Student', nri: 'NRI',
  }
  return types.map(t => labels[t] ?? t).join(', ')
}

// ─────────────────────────────────────────────────────────────
// POLICY CARD
// ─────────────────────────────────────────────────────────────

function PolicyCard({ policy, website }: { policy: Policy; website: string | null }) {
  const [expanded, setExpanded] = useState(false)

  const hasRate   = policy.interest_rate_min !== null || policy.interest_rate_max !== null
  const hasAmount = policy.loan_amount_min !== null || policy.loan_amount_max !== null
  const hasTenure = policy.tenure_min !== null || policy.tenure_max !== null
  const hasScore  = policy.credit_score_min !== null

  const isWeak = !hasRate && !hasScore && policy.completeness_score !== null && policy.completeness_score < 0.4

  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden hover:border-[#3B5CCC]/30 hover:shadow-sm transition-all">

      {/* Card header */}
      <div className="p-5">
        <div className="flex items-start justify-between gap-3 mb-4">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap mb-1">
              <span className="px-2 py-0.5 rounded-md text-xs font-semibold bg-blue-50 text-[#3B5CCC] border border-blue-100">
                {policy.loan_type}
              </span>
              {isWeak && (
                <span className="px-2 py-0.5 rounded-md text-xs font-medium bg-amber-50 text-amber-700 border border-amber-100">
                  Limited data
                </span>
              )}
            </div>
            <h3 className="text-sm font-semibold text-gray-900 leading-snug">
              {policy.product_name || policy.loan_type || 'Loan Product'}
            </h3>
          </div>
          {website && (
            <a
              href={website}
              target="_blank"
              rel="noopener noreferrer"
              className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5
                         bg-[#3B5CCC] text-white text-xs font-semibold rounded-lg
                         hover:bg-[#2d4aa8] transition-colors"
            >
              <Globe className="w-3.5 h-3.5" />
              Apply
              <ExternalLink className="w-3 h-3" />
            </a>
          )}
        </div>

        {/* Key metrics row */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">

          <div className="bg-gray-50 rounded-lg p-3">
            <div className="flex items-center gap-1 text-xs text-gray-400 mb-1">
              <Percent className="w-3 h-3" />
              Interest Rate
            </div>
            <div className="text-sm font-bold text-gray-900">
              {hasRate
                ? policy.interest_rate_min !== null && policy.interest_rate_max !== null
                  ? `${policy.interest_rate_min}–${policy.interest_rate_max}%`
                  : `${policy.interest_rate_min ?? policy.interest_rate_max}% p.a.`
                : <span className="text-gray-400 font-normal text-xs">Not disclosed</span>
              }
            </div>
          </div>

          <div className="bg-gray-50 rounded-lg p-3">
            <div className="flex items-center gap-1 text-xs text-gray-400 mb-1">
              <IndianRupee className="w-3 h-3" />
              Loan Amount
            </div>
            <div className="text-sm font-bold text-gray-900">
              {hasAmount
                ? policy.loan_amount_min !== null && policy.loan_amount_max !== null
                  ? `${formatAmount(policy.loan_amount_min)} – ${formatAmount(policy.loan_amount_max)}`
                  : formatAmount(policy.loan_amount_min ?? policy.loan_amount_max)
                : <span className="text-gray-400 font-normal text-xs">Not disclosed</span>
              }
            </div>
          </div>

          <div className="bg-gray-50 rounded-lg p-3">
            <div className="flex items-center gap-1 text-xs text-gray-400 mb-1">
              <Clock className="w-3 h-3" />
              Tenure
            </div>
            <div className="text-sm font-bold text-gray-900">
              {hasTenure
                ? policy.tenure_min !== null && policy.tenure_max !== null
                  ? `${formatTenure(policy.tenure_min)} – ${formatTenure(policy.tenure_max)}`
                  : formatTenure(policy.tenure_min ?? policy.tenure_max)
                : <span className="text-gray-400 font-normal text-xs">Not disclosed</span>
              }
            </div>
          </div>

          <div className="bg-gray-50 rounded-lg p-3">
            <div className="flex items-center gap-1 text-xs text-gray-400 mb-1">
              <TrendingUp className="w-3 h-3" />
              Min CIBIL
            </div>
            <div className="text-sm font-bold text-gray-900">
              {hasScore
                ? policy.credit_score_min
                : <span className="text-gray-400 font-normal text-xs">Not specified</span>
              }
            </div>
          </div>
        </div>
      </div>

      {/* Expandable details */}
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

          {policy.employment_types.length > 0 && (
            <div>
              <p className="text-xs text-gray-400 mb-1">Employment Types</p>
              <p className="text-gray-700">{formatEmp(policy.employment_types)}</p>
            </div>
          )}

          {policy.processing_fee !== null && (
            <div>
              <p className="text-xs text-gray-400 mb-1">Processing Fee</p>
              <p className="text-gray-700">{policy.processing_fee}%</p>
            </div>
          )}

          <div>
            <p className="text-xs text-gray-400 mb-1">Collateral</p>
            <p className="text-gray-700">
              {policy.collateral_required
                ? `Required${policy.collateral_types.length ? ` (${policy.collateral_types.join(', ')})` : ''}`
                : 'Not required'}
            </p>
          </div>

          <div>
            <p className="text-xs text-gray-400 mb-1">Prepayment</p>
            <p className="text-gray-700">{policy.prepayment_allowed ? 'Allowed' : 'Not allowed'}</p>
          </div>

          {(policy.min_age !== null || policy.max_age !== null) && (
            <div>
              <p className="text-xs text-gray-400 mb-1">Age Criteria</p>
              <p className="text-gray-700">
                {policy.min_age ?? '18'} – {policy.max_age ?? '65'} years
              </p>
            </div>
          )}

          {policy.min_monthly_income !== null && (
            <div>
              <p className="text-xs text-gray-400 mb-1">Min Monthly Income</p>
              <p className="text-gray-700">₹{policy.min_monthly_income.toLocaleString('en-IN')}k</p>
            </div>
          )}

          {policy.eligible_states.length > 0 && (
            <div className="sm:col-span-2">
              <p className="text-xs text-gray-400 mb-1">Eligible States</p>
              <div className="flex flex-wrap gap-1.5">
                {policy.eligible_states.map(s => (
                  <span key={s} className="px-2 py-0.5 text-xs bg-gray-100 text-gray-600 rounded-md">{s}</span>
                ))}
              </div>
            </div>
          )}

          {policy.eligibility_notes && (
            <div className="sm:col-span-2">
              <p className="text-xs text-gray-400 mb-1">Notes</p>
              <p className="text-gray-600 text-xs leading-relaxed">{policy.eligibility_notes}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────
// MAIN PAGE
// ─────────────────────────────────────────────────────────────

export default function LenderDetailPage() {
  const params = useParams()
  const router = useRouter()
  const id     = params?.id as string

  const [lender,          setLender]          = useState<LenderDetail | null>(null)
  const [policies,        setPolicies]        = useState<Policy[]>([])
  const [policyTotal,     setPolicyTotal]     = useState(0)
  const [selectedLoanType, setSelectedLoanType] = useState<string>('All')
  const [loading,         setLoading]         = useState(true)
  const [policiesLoading, setPoliciesLoading] = useState(true)
  const [error,           setError]           = useState<string | null>(null)

  // Fetch lender
  useEffect(() => {
    if (!id) return
    setLoading(true)
    fetch(`${API_URL}/v1/lenders/${id}`)
      .then(r => {
        if (r.status === 404) throw new Error('Lender not found')
        if (!r.ok) throw new Error('Failed to load lender')
        return r.json()
      })
      .then((d: LenderDetail) => setLender(d))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [id])

  // Fetch policies (all for this lender, paginated up to 100)
  useEffect(() => {
    if (!id) return
    setPoliciesLoading(true)
    fetch(`${API_URL}/v1/policies/filter?lender_id=${id}&limit=100`)
      .then(r => r.ok ? r.json() : { total: 0, results: [] })
      .then((d: PolicyResponse) => {
        setPolicies(d.results ?? [])
        setPolicyTotal(d.total ?? 0)
      })
      .catch(() => {
        setPolicies([])
        setPolicyTotal(0)
      })
      .finally(() => setPoliciesLoading(false))
  }, [id])

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-[#3B5CCC]" />
      </div>
    )
  }

  if (error || !lender) {
    return (
      <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center gap-4">
        <Building2 className="w-12 h-12 text-gray-300" />
        <p className="font-medium text-gray-700">{error ?? 'Lender not found'}</p>
        <Link href="/dashboard" className="text-sm text-[#3B5CCC] hover:underline">
          ← Back to lenders
        </Link>
      </div>
    )
  }

  const qualityPct   = lender.quality_score != null ? Math.round(lender.quality_score * 100) : null
  const qualityColor = qualityPct == null ? 'text-gray-400'
    : qualityPct >= 70 ? 'text-green-600'
    : qualityPct >= 40 ? 'text-yellow-600'
    : 'text-red-500'

  // Loan type filter tabs — derived from actual policies
  const loanTypes = ['All', ...Array.from(new Set(policies.map(p => p.loan_type).filter(Boolean) as string[]))]

  const filteredPolicies = selectedLoanType === 'All'
    ? policies
    : policies.filter(p => p.loan_type === selectedLoanType)

  return (
    <div className="min-h-screen bg-gray-50">

      {/* Nav */}
      <nav className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-3.5 flex items-center gap-3">
          <button
            onClick={() => router.back()}
            className="p-1.5 rounded-lg hover:bg-gray-100 transition-colors text-gray-500"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <span className="text-sm text-gray-500 truncate flex-1">{lender.company_name}</span>
          {lender.website && (
            <a
              href={lender.website}
              target="_blank"
              rel="noopener noreferrer"
              className="flex-shrink-0 inline-flex items-center gap-1.5 px-4 py-1.5
                         bg-[#3B5CCC] text-white text-sm font-semibold rounded-lg
                         hover:bg-[#2d4aa8] transition-colors"
            >
              <Globe className="w-3.5 h-3.5" />
              Visit Website
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          )}
        </div>
      </nav>

      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-8 space-y-6">

        {/* Header card */}
        <div className="bg-white rounded-2xl border border-gray-200 p-6">
          <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
            <div className="flex-1 min-w-0">
              <div className="flex flex-wrap items-center gap-2 mb-2">
                <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-50 text-blue-700">
                  {lender.company_type}
                </span>
                {lender.is_listed && (
                  <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-purple-50 text-purple-700">
                    Listed {lender.stock_symbol ? `· ${lender.stock_symbol}` : ''}
                  </span>
                )}
                {lender.pan_india && (
                  <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-green-50 text-green-700">
                    Pan India
                  </span>
                )}
              </div>
              <h1 className="text-2xl sm:text-3xl font-bold text-gray-900">{lender.company_name}</h1>
              {lender.rbi_category && (
                <div className="flex items-center gap-1.5 mt-1.5">
                  <BadgeCheck className="w-4 h-4 text-[#3B5CCC]" />
                  <span className="text-sm text-[#3B5CCC] font-medium">{lender.rbi_category}</span>
                </div>
              )}
              {lender.hq_location && (
                <div className="flex items-center gap-1.5 mt-2 text-gray-500">
                  <MapPin className="w-3.5 h-3.5 flex-shrink-0" />
                  <span className="text-sm">{lender.hq_location}</span>
                </div>
              )}
            </div>

            <div className="bg-blue-50 rounded-xl px-5 py-3 text-center flex-shrink-0">
              <div className="text-2xl font-bold text-[#3B5CCC]">{formatAUM(lender.aum_crores)}</div>
              <div className="text-xs text-blue-500 mt-0.5">
                AUM{lender.aum_category ? ` · ${lender.aum_category}` : ''}
              </div>
            </div>
          </div>

          {/* Contact row */}
          {(lender.website || lender.phone || lender.email) && (
            <div className="flex flex-wrap gap-4 mt-5 pt-5 border-t border-gray-100">
              {lender.website && (
                <a href={lender.website} target="_blank" rel="noopener noreferrer"
                   className="inline-flex items-center gap-1.5 text-sm text-[#3B5CCC] hover:underline">
                  <Globe className="w-4 h-4" />
                  {lender.website.replace(/^https?:\/\//, '')}
                  <ExternalLink className="w-3 h-3" />
                </a>
              )}
              {lender.phone && (
                <a href={`tel:${lender.phone}`}
                   className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:text-gray-900">
                  <Phone className="w-4 h-4" />
                  {lender.phone}
                </a>
              )}
              {lender.email && (
                <a href={`mailto:${lender.email}`}
                   className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:text-gray-900">
                  <Mail className="w-4 h-4" />
                  {lender.email}
                </a>
              )}
            </div>
          )}
        </div>

        {/* Stats grid */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {[
            { icon: Calendar,   label: 'Established',  value: lender.established_year ?? '—' },
            { icon: Users,      label: 'Employees',    value: lender.employee_count?.toLocaleString('en-IN') ?? '—' },
            { icon: GitBranch,  label: 'Branches',     value: lender.branch_count?.toLocaleString('en-IN') ?? '—' },
            { icon: TrendingUp, label: 'Data Quality', value: qualityPct != null ? `${qualityPct}%` : '—', color: qualityColor },
          ].map(({ icon: Icon, label, value, color }) => (
            <div key={label} className="bg-white rounded-xl border border-gray-200 p-4">
              <div className="flex items-center gap-2 text-gray-400 mb-1.5">
                <Icon className="w-4 h-4" />
                <span className="text-xs">{label}</span>
              </div>
              <div className={`text-xl font-bold ${color ?? 'text-gray-800'}`}>{value}</div>
            </div>
          ))}
        </div>

        {/* ── POLICIES SECTION ─────────────────────────────────── */}
        <div>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-bold text-gray-900 flex items-center gap-2">
              <FileText className="w-5 h-5 text-[#3B5CCC]" />
              Loan Policies
              {policyTotal > 0 && (
                <span className="text-sm font-normal text-gray-400">({policyTotal})</span>
              )}
            </h2>
            {lender.website && (
              <a
                href={lender.website}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 px-4 py-2
                           bg-[#3B5CCC] text-white text-sm font-semibold rounded-xl
                           hover:bg-[#2d4aa8] transition-all hover:shadow-md"
              >
                <Globe className="w-4 h-4" />
                Apply on Website
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
            )}
          </div>

          {/* Loan type filter tabs */}
          {loanTypes.length > 1 && (
            <div className="flex flex-wrap gap-2 mb-4">
              {loanTypes.map(lt => (
                <button
                  key={lt}
                  onClick={() => setSelectedLoanType(lt)}
                  className={[
                    'px-3 py-1.5 rounded-lg text-xs font-medium border transition-all',
                    selectedLoanType === lt
                      ? 'bg-[#3B5CCC] text-white border-[#3B5CCC]'
                      : 'bg-white text-gray-600 border-gray-200 hover:border-gray-300',
                  ].join(' ')}
                >
                  {lt}
                </button>
              ))}
            </div>
          )}

          {/* Policy cards */}
          {policiesLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[#3B5CCC]" />
            </div>
          ) : filteredPolicies.length === 0 ? (
            <div className="bg-white rounded-2xl border border-gray-200 py-12 text-center">
              <Shield className="w-10 h-10 text-gray-200 mx-auto mb-3" />
              <p className="text-sm font-medium text-gray-600">No policies available yet</p>
              <p className="text-xs text-gray-400 mt-1 mb-4">
                This lender hasn&apos;t published detailed loan terms online.
              </p>
              {lender.website && (
                <a
                  href={lender.website}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 px-5 py-2
                             bg-[#3B5CCC] text-white text-sm font-semibold rounded-xl
                             hover:bg-[#2d4aa8] transition-colors"
                >
                  <Globe className="w-4 h-4" />
                  Check their website directly
                  <ExternalLink className="w-3.5 h-3.5" />
                </a>
              )}
            </div>
          ) : (
            <div className="space-y-3">
              {filteredPolicies.map(p => (
                <PolicyCard key={p.id} policy={p} website={lender.website} />
              ))}
            </div>
          )}
        </div>

        {/* Operating states */}
        {lender.operating_states.length > 0 && !lender.pan_india && (
          <div className="bg-white rounded-2xl border border-gray-200 p-6">
            <h2 className="text-base font-semibold text-gray-800 mb-4">
              Operating States
              <span className="ml-2 text-sm font-normal text-gray-400">
                ({lender.operating_states.length} states)
              </span>
            </h2>
            <div className="flex flex-wrap gap-2">
              {lender.operating_states.map(state => (
                <span key={state}
                      className="px-2.5 py-1 rounded-lg text-xs font-medium bg-gray-50 text-gray-600 border border-gray-200">
                  {state}
                </span>
              ))}
            </div>
          </div>
        )}

        {lender.last_scraped_at && (
          <p className="text-xs text-gray-400 text-center pb-4">
            Data last updated {new Date(lender.last_scraped_at).toLocaleDateString('en-IN', {
              day: 'numeric', month: 'long', year: 'numeric',
            })}
            {lender.data_source ? ` · ${lender.data_source}` : ''}
          </p>
        )}
      </div>
    </div>
  )
}
