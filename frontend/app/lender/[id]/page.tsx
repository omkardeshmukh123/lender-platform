'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  MapPin, Globe, Phone, Mail, Building2, Users, GitBranch,
  Calendar, TrendingUp, BadgeCheck, ArrowLeft, ExternalLink,
  IndianRupee, Percent, Clock, Shield, ChevronDown, ChevronUp,
  FileText, AlertCircle,
} from 'lucide-react'
import { IntentModal } from '../../components/LenderCard'

// ─────────────────────────────────────────────────────────────
// TYPES
// ─────────────────────────────────────────────────────────────

interface GrievanceOfficer {
  name: string | null
  designation: string | null
  email: string | null
  phone: string | null
  source_url: string | null
  last_verified_at: string | null
}

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
  // MCA21
  cin: string | null
  company_status: string | null
  authorized_capital_lakhs: number | null
  paid_up_capital_lakhs: number | null
  mca21_status: string | null
  // Financial
  last_year_revenue: number | null
  recent_funding: string | null
  recent_funding_amount: number | null
  recent_funding_year: number | null
  financial_source: string | null
  // Grievance officer
  grievance_officer: GrievanceOfficer | null
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
    <div className="bg-white rounded-xl overflow-hidden transition-all"
         style={{ border: '1px solid #E6F4F4' }}
         onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.borderColor = '#A8DADA'; (e.currentTarget as HTMLDivElement).style.boxShadow = '0 2px 10px rgba(26,112,112,0.08)' }}
         onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.borderColor = '#E6F4F4'; (e.currentTarget as HTMLDivElement).style.boxShadow = 'none' }}
    >

      {/* Card header */}
      <div className="p-5">
        <div className="flex items-start justify-between gap-3 mb-4">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap mb-1">
              <span className="px-2 py-0.5 rounded-md text-xs font-semibold"
                    style={{ background: '#E6F4F4', color: '#1A7070', border: '1px solid #A8DADA' }}>
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
                         text-white text-xs font-semibold rounded-lg
                         transition-all hover:-translate-y-0.5 hover:shadow-md"
              style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)' }}
            >
              <Globe className="w-3.5 h-3.5" />
              Apply
              <ExternalLink className="w-3 h-3" />
            </a>
          )}
        </div>

        {/* Key metrics — only render boxes that have real data */}
        {(hasRate || hasAmount || hasTenure || hasScore) ? (
          <div className="flex flex-wrap gap-3">
            {hasRate && (
              <div className="bg-gray-50 rounded-lg p-3 min-w-[120px]">
                <div className="flex items-center gap-1 text-xs text-gray-400 mb-1">
                  <Percent className="w-3 h-3" />
                  Interest Rate
                </div>
                <div className="text-sm font-bold text-gray-900">
                  {policy.interest_rate_min !== null && policy.interest_rate_max !== null
                    ? `${policy.interest_rate_min}–${policy.interest_rate_max}%`
                    : `${policy.interest_rate_min ?? policy.interest_rate_max}% p.a.`}
                </div>
              </div>
            )}
            {hasAmount && (
              <div className="bg-gray-50 rounded-lg p-3 min-w-[140px]">
                <div className="flex items-center gap-1 text-xs text-gray-400 mb-1">
                  <IndianRupee className="w-3 h-3" />
                  Loan Amount
                </div>
                <div className="text-sm font-bold text-gray-900">
                  {policy.loan_amount_min !== null && policy.loan_amount_max !== null
                    ? `${formatAmount(policy.loan_amount_min)} – ${formatAmount(policy.loan_amount_max)}`
                    : formatAmount(policy.loan_amount_min ?? policy.loan_amount_max)}
                </div>
              </div>
            )}
            {hasTenure && (
              <div className="bg-gray-50 rounded-lg p-3 min-w-[120px]">
                <div className="flex items-center gap-1 text-xs text-gray-400 mb-1">
                  <Clock className="w-3 h-3" />
                  Tenure
                </div>
                <div className="text-sm font-bold text-gray-900">
                  {policy.tenure_min !== null && policy.tenure_max !== null
                    ? `${formatTenure(policy.tenure_min)} – ${formatTenure(policy.tenure_max)}`
                    : formatTenure(policy.tenure_min ?? policy.tenure_max)}
                </div>
              </div>
            )}
            {hasScore && (
              <div className="bg-gray-50 rounded-lg p-3 min-w-[100px]">
                <div className="flex items-center gap-1 text-xs text-gray-400 mb-1">
                  <TrendingUp className="w-3 h-3" />
                  Min CIBIL
                </div>
                <div className="text-sm font-bold text-gray-900">
                  {policy.credit_score_min}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="flex items-center gap-3 p-3 rounded-lg" style={{ background: '#F7FAFA', border: '1px solid #E6F4F4' }}>
            <AlertCircle className="w-4 h-4 flex-shrink-0" style={{ color: '#7A9E9E' }} />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium" style={{ color: '#3D6363' }}>
                Detailed rates not published online
              </p>
              <p className="text-[11px] mt-0.5" style={{ color: '#7A9E9E' }}>
                Contact lender directly for current terms
              </p>
            </div>
            {website && (
              <a href={website} target="_blank" rel="noopener noreferrer"
                 className="text-xs font-semibold px-2.5 py-1 rounded-lg flex-shrink-0"
                 style={{ background: '#E6F4F4', color: '#1A7070' }}>
                Visit Site
              </a>
            )}
          </div>
        )}
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
  const [intentOpen,      setIntentOpen]      = useState(false)

  // Fetch lender
  useEffect(() => {
    if (!id) return
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 30_000)
    setLoading(true)
    fetch(`${API_URL}/v1/lenders/${id}`, { signal: controller.signal })
      .then(r => {
        if (r.status === 404) throw new Error('Lender not found')
        if (!r.ok) throw new Error('Failed to load lender')
        return r.json()
      })
      .then((d: LenderDetail) => setLender(d))
      .catch(e => { if (e.name !== 'AbortError') setError(e.message) })
      .finally(() => { clearTimeout(timer); setLoading(false) })
    return () => { controller.abort(); clearTimeout(timer) }
  }, [id])

  // Fetch policies (all for this lender, paginated up to 100)
  useEffect(() => {
    if (!id) return
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 30_000)
    setPoliciesLoading(true)
    fetch(`${API_URL}/v1/policies/filter?lender_id=${id}&limit=100`, { signal: controller.signal })
      .then(r => r.ok ? r.json() : { total: 0, results: [] })
      .then((d: PolicyResponse) => {
        setPolicies(d.results ?? [])
        setPolicyTotal(d.total ?? 0)
      })
      .catch(() => { setPolicies([]); setPolicyTotal(0) })
      .finally(() => { clearTimeout(timer); setPoliciesLoading(false) })
    return () => { controller.abort(); clearTimeout(timer) }
  }, [id])

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: '#F7FAFA' }}>
        <div className="animate-spin rounded-full h-10 w-10 border-2 border-t-transparent"
             style={{ borderColor: '#1A7070', borderTopColor: 'transparent' }} />
      </div>
    )
  }

  if (error || !lender) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4" style={{ background: '#F7FAFA' }}>
        <Building2 className="w-12 h-12" style={{ color: '#A8DADA' }} />
        <p className="font-medium" style={{ color: '#3D6363' }}>{error ?? 'Lender not found'}</p>
        <Link href="/dashboard" className="text-sm hover:underline" style={{ color: '#1A7070' }}>
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
    <div className="min-h-screen" style={{ background: '#F7FAFA' }}>

      {/* Nav */}
      <nav className="bg-white sticky top-0 z-30" style={{ borderBottom: '1px solid #D8EBEB' }}>
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-3.5 flex items-center gap-3">
          <button
            onClick={() => router.back()}
            className="p-1.5 rounded-lg transition-colors"
            style={{ color: '#7A9E9E' }}
            onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.background = '#E6F4F4' }}
            onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent' }}
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <span className="text-sm truncate flex-1" style={{ color: '#3D6363' }}>{lender.company_name}</span>
          {lender.website && (
            <button
              onClick={() => setIntentOpen(true)}
              className="flex-shrink-0 inline-flex items-center gap-1.5 px-4 py-1.5
                         text-white text-sm font-semibold rounded-lg
                         transition-all hover:-translate-y-0.5 hover:shadow-lg"
              style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)', boxShadow: '0 2px 8px rgba(26,112,112,0.25)' }}
            >
              <Globe className="w-3.5 h-3.5" />
              Visit Website
              <ExternalLink className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </nav>

      {/* MCA21 company status warning */}
      {lender.company_status && ['struck_off', 'dormant', 'dissolved', 'converted'].includes(lender.company_status) && (
        <div className={`border-b px-4 py-3 text-sm font-medium flex items-center gap-2 ${
          lender.company_status === 'struck_off' || lender.company_status === 'dissolved'
            ? 'bg-red-50 border-red-200 text-red-800'
            : 'bg-amber-50 border-amber-200 text-amber-800'
        }`}>
          <Shield className="w-4 h-4 flex-shrink-0" />
          {lender.company_status === 'struck_off' && 'MCA21 registry shows this company as struck off. Exercise caution before engaging.'}
          {lender.company_status === 'dissolved' && 'MCA21 registry shows this company as dissolved.'}
          {lender.company_status === 'dormant' && 'MCA21 registry shows this company as dormant.'}
          {lender.company_status === 'converted' && 'MCA21 registry shows this company has been converted to another entity.'}
        </div>
      )}

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
                  <BadgeCheck className="w-4 h-4" style={{ color: '#1A7070' }} />
                  <span className="text-sm font-medium" style={{ color: '#1A7070' }}>{lender.rbi_category}</span>
                </div>
              )}
              {lender.hq_location && (
                <div className="flex items-center gap-1.5 mt-2 text-gray-500">
                  <MapPin className="w-3.5 h-3.5 flex-shrink-0" />
                  <span className="text-sm">{lender.hq_location}</span>
                </div>
              )}
            </div>

            <div className="rounded-xl px-5 py-3 text-center flex-shrink-0"
                 style={{ background: '#E6F4F4' }}>
              <div className="text-2xl font-bold" style={{ color: '#1A7070' }}>
                {formatAUM(lender.aum_crores)}
              </div>
              <div className="text-xs mt-0.5" style={{ color: '#7A9E9E' }}>
                AUM{lender.aum_category ? ` · ${lender.aum_category}` : ''}
              </div>
            </div>
          </div>

          {/* Contact row */}
          {(lender.website || lender.phone || lender.email) && (
            <div className="flex flex-wrap gap-4 mt-5 pt-5 border-t" style={{ borderColor: '#E6F4F4' }}>
              {lender.website && (
                <a href={lender.website} target="_blank" rel="noopener noreferrer"
                   className="inline-flex items-center gap-1.5 text-sm hover:underline"
                   style={{ color: '#1A7070' }}>
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

        {/* ── FINANCIAL OVERVIEW ───────────────────────────────── */}
        {(lender.last_year_revenue != null || lender.recent_funding_amount != null ||
          lender.authorized_capital_lakhs != null || lender.paid_up_capital_lakhs != null) && (
          <div className="bg-white rounded-2xl border border-gray-200 p-6">
            <h2 className="text-base font-semibold text-gray-800 mb-4 flex items-center gap-2">
              <TrendingUp className="w-4 h-4" style={{ color: '#1A7070' }} />
              Financial Overview
            </h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
              {lender.last_year_revenue != null && (
                <div className="rounded-xl p-4" style={{ background: '#F7FAFA' }}>
                  <p className="text-xs text-gray-400 mb-1">Last Year Revenue</p>
                  <p className="text-lg font-bold" style={{ color: '#0D3333' }}>
                    {lender.last_year_revenue >= 1_000
                      ? `₹${(lender.last_year_revenue / 1_000).toFixed(1)}K Cr`
                      : `₹${lender.last_year_revenue.toLocaleString('en-IN')} Cr`}
                  </p>
                  {lender.aum_crores != null && lender.last_year_revenue > 0 && (
                    <p className="text-[11px] text-gray-400 mt-0.5">
                      {((lender.last_year_revenue / lender.aum_crores) * 100).toFixed(1)}% of AUM
                    </p>
                  )}
                </div>
              )}
              {lender.aum_crores != null && (
                <div className="rounded-xl p-4" style={{ background: '#F7FAFA' }}>
                  <p className="text-xs text-gray-400 mb-1">Total AUM</p>
                  <p className="text-lg font-bold" style={{ color: '#0D3333' }}>
                    {formatAUM(lender.aum_crores)}
                  </p>
                  {lender.aum_category && (
                    <p className="text-[11px] text-gray-400 mt-0.5">{lender.aum_category} tier</p>
                  )}
                </div>
              )}
              {lender.authorized_capital_lakhs != null && (
                <div className="rounded-xl p-4" style={{ background: '#F7FAFA' }}>
                  <p className="text-xs text-gray-400 mb-1">Authorized Capital</p>
                  <p className="text-lg font-bold" style={{ color: '#0D3333' }}>
                    {formatAmount(lender.authorized_capital_lakhs)}
                  </p>
                </div>
              )}
              {lender.paid_up_capital_lakhs != null && (
                <div className="rounded-xl p-4" style={{ background: '#F7FAFA' }}>
                  <p className="text-xs text-gray-400 mb-1">Paid-up Capital</p>
                  <p className="text-lg font-bold" style={{ color: '#0D3333' }}>
                    {formatAmount(lender.paid_up_capital_lakhs)}
                  </p>
                </div>
              )}
              {lender.recent_funding_amount != null && (
                <div className="rounded-xl p-4" style={{ background: '#FDF8E4' }}>
                  <p className="text-xs text-gray-400 mb-1">Recent Funding</p>
                  <p className="text-lg font-bold" style={{ color: '#A07E1A' }}>
                    ₹{lender.recent_funding_amount.toLocaleString('en-IN')} Cr
                  </p>
                  {lender.recent_funding && (
                    <p className="text-[11px] text-gray-500 mt-0.5 truncate" title={lender.recent_funding}>
                      {lender.recent_funding}
                    </p>
                  )}
                  {lender.recent_funding_year && (
                    <p className="text-[11px] text-gray-400 mt-0.5">{lender.recent_funding_year}</p>
                  )}
                </div>
              )}
            </div>
            {lender.financial_source && (
              <p className="text-[11px] text-gray-400 mt-3">
                Source: {lender.financial_source}
              </p>
            )}
          </div>
        )}

        {/* ── GRIEVANCE OFFICER ────────────────────────────────── */}
        {lender.grievance_officer && (
          <div className="bg-white rounded-2xl border border-gray-200 p-6">
            <h2 className="text-base font-semibold text-gray-800 mb-4 flex items-center gap-2">
              <AlertCircle className="w-4 h-4" style={{ color: '#1A7070' }} />
              Grievance Redressal Officer
            </h2>
            <div className="flex flex-col sm:flex-row sm:items-start gap-4">
              <div className="flex-1 space-y-2">
                {lender.grievance_officer.name && (
                  <p className="text-sm font-semibold text-gray-900">{lender.grievance_officer.name}</p>
                )}
                {lender.grievance_officer.designation && (
                  <p className="text-xs text-gray-500">{lender.grievance_officer.designation}</p>
                )}
                <div className="flex flex-wrap gap-4 pt-1">
                  {lender.grievance_officer.email && (
                    <a href={`mailto:${lender.grievance_officer.email}`}
                       className="inline-flex items-center gap-1.5 text-sm hover:underline"
                       style={{ color: '#1A7070' }}>
                      <Mail className="w-3.5 h-3.5" />
                      {lender.grievance_officer.email}
                    </a>
                  )}
                  {lender.grievance_officer.phone && (
                    <a href={`tel:${lender.grievance_officer.phone}`}
                       className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:text-gray-900">
                      <Phone className="w-3.5 h-3.5" />
                      {lender.grievance_officer.phone}
                    </a>
                  )}
                </div>
              </div>
              {lender.grievance_officer.source_url && (
                <a href={lender.grievance_officer.source_url}
                   target="_blank"
                   rel="noopener noreferrer"
                   className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5
                              text-xs font-medium rounded-lg border transition-colors hover:bg-gray-50"
                   style={{ color: '#3D6363', borderColor: '#A8DADA' }}>
                  <ExternalLink className="w-3 h-3" />
                  Source
                </a>
              )}
            </div>
            {lender.grievance_officer.last_verified_at && (
              <p className="text-[11px] text-gray-400 mt-3">
                Verified {new Date(lender.grievance_officer.last_verified_at).toLocaleDateString('en-IN', {
                  day: 'numeric', month: 'long', year: 'numeric',
                })}
              </p>
            )}
          </div>
        )}

        {/* ── POLICIES SECTION ─────────────────────────────────── */}
        <div>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-bold flex items-center gap-2" style={{ color: '#0D3333' }}>
              <FileText className="w-5 h-5" style={{ color: '#1A7070' }} />
              Loan Policies
              {policyTotal > 0 && (
                <span className="text-sm font-normal text-gray-400">({policyTotal})</span>
              )}
            </h2>
            {lender.website && (
              <button
                onClick={() => setIntentOpen(true)}
                className="inline-flex items-center gap-1.5 px-4 py-2
                           text-white text-sm font-semibold rounded-xl
                           transition-all hover:-translate-y-0.5 hover:shadow-lg"
                style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)', boxShadow: '0 2px 8px rgba(26,112,112,0.2)' }}
              >
                <Globe className="w-4 h-4" />
                Apply on Website
                <ExternalLink className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          {/* Loan type filter tabs */}
          {loanTypes.length > 1 && (
            <div className="flex overflow-x-auto gap-2 mb-4 pb-1 -mx-1 px-1">
              {loanTypes.map(lt => (
                <button
                  key={lt}
                  onClick={() => setSelectedLoanType(lt)}
                  className={[
                    'flex-shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium border transition-all',
                    selectedLoanType === lt
                      ? 'text-white'
                      : 'bg-white border-gray-200 hover:border-gray-300',
                  ].join(' ')}
                  style={selectedLoanType === lt
                    ? { background: 'linear-gradient(135deg,#0F4848,#1A7070)', borderColor: 'transparent', color: 'white' }
                    : { color: '#3D6363' }}
                >
                  {lt}
                </button>
              ))}
            </div>
          )}

          {/* Policy cards */}
          {policiesLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="animate-spin rounded-full h-8 w-8 border-2 border-t-transparent"
                   style={{ borderColor: '#1A7070', borderTopColor: 'transparent' }} />
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
                             text-white text-sm font-semibold rounded-xl
                             transition-all hover:-translate-y-0.5 hover:shadow-lg"
                  style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)' }}
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
            Data last updated: {new Date(lender.last_scraped_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })}
          </p>
        )}
      </div>

      {intentOpen && lender.website && (
        <IntentModal
          lenderName={lender.company_name}
          website={lender.website}
          onClose={() => setIntentOpen(false)}
        />
      )}
    </div>
  )
}
