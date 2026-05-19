'use client'

import { Suspense } from 'react'
import { useState, useEffect, useCallback, useRef } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { SlidersHorizontal } from 'lucide-react'
import { useAuth } from '../components/AuthContext'
import { useSaved, SavedLender } from '../components/SaveContext'
import StubCard, { RegistryStub } from '../components/StubCard'
import { Navbar }        from '../components/Navbar'
import { Hero }          from '../components/Hero'
import {
  SearchFilter,
  MultiFilters,
  DEFAULT_FILTERS,
  YEAR_RANGE_OPTIONS,
  SortField,
  SortDirection,
} from '../components/SearchFilter'
import { LenderCard }    from '../components/LenderCard'
import { StatsSection }  from '../components/StatsSection'
import { Footer }        from '../components/Footer'
import { ChatPanel }      from '../components/ChatPanel'
import { ErrorBoundary }  from '../components/ErrorBoundary'
import { PhoneModal }      from '../components/PhoneModal'

// ─────────────────────────────────────────────────────────────
// CONSTANTS
// ─────────────────────────────────────────────────────────────

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const PAGE_SIZE = 50

const LOAN_TYPES: string[] = [
  'MSME Loan', 'Personal Loan', 'Home Loan', 'Business Loan',
  'Vehicle Loan', 'Gold Loan', 'Education Loan', 'Micro Loan',
  'Loan Against Property', 'Working Capital', 'Agriculture Loan',
  'EV Loan', 'Two Wheeler Loan', 'Rural Loan', 'Microfinance',
  'Supply Chain Finance', 'Consumer Durable Loan', 'Credit Card',
]

const TICKET_SIZES: string[] = ['Micro', 'Small', 'Mid', 'Large']

const COMPANY_TYPES: string[] = [
  'NBFC', 'Private Bank', 'PSU Bank', 'Foreign Bank',
  'Cooperative Bank', 'NBFC-MFI', 'Small Finance Bank',
]

const LISTING_OPTIONS: string[] = ['All', 'Listed Only', 'Unlisted Only']

const OPERATING_INTENSITIES: string[] = ['Pan India', 'Regional', 'Single State']

// Static list of Indian states — doesn't change, no need for an API call
const INDIA_STATES: string[] = [
  'All States',
  'Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chhattisgarh',
  'Goa', 'Gujarat', 'Haryana', 'Himachal Pradesh', 'Jharkhand', 'Karnataka',
  'Kerala', 'Madhya Pradesh', 'Maharashtra', 'Manipur', 'Meghalaya', 'Mizoram',
  'Nagaland', 'Odisha', 'Punjab', 'Rajasthan', 'Sikkim', 'Tamil Nadu',
  'Telangana', 'Tripura', 'Uttar Pradesh', 'Uttarakhand', 'West Bengal',
  'Delhi', 'Jammu & Kashmir', 'Ladakh', 'Puducherry', 'Chandigarh',
]

// ─────────────────────────────────────────────────────────────
// TYPES  — shape returned by FastAPI /lenders/search
// ─────────────────────────────────────────────────────────────

interface LenderSummary {
  id:                    number
  company_name:          string
  company_type:          string
  rbi_category:          string | null
  aum_crores:            number | null
  aum_category:          string | null
  hq_state:              string | null
  hq_location:           string | null
  operating_intensity:   string | null
  business_sector:       string | null
  pan_india:             boolean
  primary_loan_segments: string[]
  operating_states:      string[]
  website:               string | null
  quality_score:         number | null
  employee_count:        number | null
  established_year:      number | null
  is_listed:             boolean
  phone:                 string | null
  email:                 string | null
  policy_count:          number | null
  last_year_revenue:     number | null
  min_interest_rate:     number | null
}

interface LenderSearchResponse {
  total:   number
  page:    number
  limit:   number
  results: LenderSummary[]
  stubs:   RegistryStub[]
}

// ─────────────────────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────────────────────

function fmtAum(val: number | null | undefined): string {
  if (!val) return 'N/A'
  if (val >= 100_000) return `₹${(val / 100_000).toFixed(1)}L Cr`
  if (val >= 1_000)   return `₹${(val / 1_000).toFixed(1)}K Cr`
  return `₹${val.toLocaleString('en-IN')} Cr`
}

function fmtNum(val: number | null | undefined): string | null {
  if (!val && val !== 0) return null
  return val.toLocaleString('en-IN')
}

// ─────────────────────────────────────────────────────────────
// API FETCH
// ─────────────────────────────────────────────────────────────

function yearRangeToParams(range: string): { min?: number; max?: number } {
  switch (range) {
    case 'Before 2000':   return { max: 1999 }
    case '2000–2009':     return { min: 2000, max: 2009 }
    case '2010–2019':     return { min: 2010, max: 2019 }
    case '2020 & after':  return { min: 2020 }
    default:              return {}
  }
}

async function fetchFromAPI(f: MultiFilters, pg: number): Promise<LenderSearchResponse> {
  const params = new URLSearchParams()

  if (f.search.trim())             params.set('q', f.search.trim())
  if (f.state && f.state !== 'All States') params.set('state', f.state)
  if (f.listingStatus === 'Listed Only')   params.set('is_listed', 'true')
  if (f.listingStatus === 'Unlisted Only') params.set('is_listed', 'false')
  if (f.sortField)                 params.set('sort_by', f.sortField)
  if (f.sortDirection)             params.set('sort_dir', f.sortDirection)
  if (f.hasPolicies)               params.set('has_policies', 'true')
  if (f.hasRevenue)                params.set('has_revenue', 'true')

  const yr = yearRangeToParams(f.establishedYearRange ?? 'All Years')
  if (yr.min !== undefined) params.set('established_year_min', String(yr.min))
  if (yr.max !== undefined) params.set('established_year_max', String(yr.max))

  f.loanType.forEach(t            => params.append('loan_type',           t))
  f.companyType.forEach(t         => params.append('company_type',         t))
  f.ticketSize.forEach(t          => params.append('aum_category',         t))
  f.operatingIntensity.forEach(t  => params.append('operating_intensity',  t))
  f.businessSector.forEach(t      => params.append('business_sector',      t))

  params.set('page',  String(pg + 1))   // API is 1-indexed, our state is 0-indexed
  params.set('limit', String(PAGE_SIZE))

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 30_000)
  try {
    const res = await fetch(`${API_URL}/v1/lenders/search?${params}`, { signal: controller.signal })
    if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`)
    return res.json() as Promise<LenderSearchResponse>
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError')
      throw new Error('Server is waking up — please wait a moment and try again.')
    throw err
  } finally {
    clearTimeout(timer)
  }
}

// ─────────────────────────────────────────────────────────────
// URL ↔ FILTER HELPERS  (persist filters across navigation)
// ─────────────────────────────────────────────────────────────

function filtersFromParams(params: URLSearchParams): MultiFilters {
  return {
    search:               params.get('q')    ?? DEFAULT_FILTERS.search,
    loanType:             params.getAll('lt'),
    state:                params.get('state') ?? DEFAULT_FILTERS.state,
    ticketSize:           params.getAll('ts'),
    companyType:          params.getAll('ct'),
    operatingIntensity:   params.getAll('oi'),
    businessSector:       params.getAll('bs'),
    listingStatus:        params.get('ls')   ?? DEFAULT_FILTERS.listingStatus,
    establishedYearRange: params.get('yr')   ?? DEFAULT_FILTERS.establishedYearRange,
    sortField:            (params.get('sf')  ?? DEFAULT_FILTERS.sortField)     as SortField,
    sortDirection:        (params.get('sd')  ?? DEFAULT_FILTERS.sortDirection) as SortDirection,
    hasPolicies:          params.get('hp')   === 'true',
    hasRevenue:           params.get('hr')   === 'true',
  }
}

function filtersToParams(f: MultiFilters, pg: number): string {
  const p = new URLSearchParams()
  if (f.search.trim())                                      p.set('q',     f.search.trim())
  f.loanType.forEach(v           => p.append('lt', v))
  if (f.state && f.state !== DEFAULT_FILTERS.state)         p.set('state', f.state)
  f.ticketSize.forEach(v         => p.append('ts', v))
  f.companyType.forEach(v        => p.append('ct', v))
  f.operatingIntensity.forEach(v => p.append('oi', v))
  f.businessSector.forEach(v     => p.append('bs', v))
  if (f.listingStatus !== DEFAULT_FILTERS.listingStatus)    p.set('ls', f.listingStatus)
  if (f.establishedYearRange !== DEFAULT_FILTERS.establishedYearRange) p.set('yr', f.establishedYearRange)
  if (f.sortField)                                          p.set('sf', f.sortField)
  if (f.sortDirection !== DEFAULT_FILTERS.sortDirection)    p.set('sd', f.sortDirection)
  if (f.hasPolicies)                                        p.set('hp', 'true')
  if (f.hasRevenue)                                         p.set('hr', 'true')
  if (pg > 0)                                               p.set('pg', String(pg))
  const qs = p.toString()
  return qs ? `/dashboard?${qs}` : '/dashboard'
}

// ─────────────────────────────────────────────────────────────
// COMPARE MODAL
// ─────────────────────────────────────────────────────────────

type PolicySnap = {
  interest_rate_min: number | null
  interest_rate_max: number | null
  loan_amount_min:   number | null
  loan_amount_max:   number | null
  tenure_min:        number | null
  tenure_max:        number | null
}

function CompareModal({ lenders, onClose }: { lenders: LenderSummary[]; onClose: () => void }) {
  const [copied,   setCopied]   = useState(false)
  const [policies, setPolicies] = useState<Record<number, PolicySnap | null>>({})

  useEffect(() => {
    Promise.all(
      lenders.map(async l => {
        try {
          const res = await fetch(`${API_URL}/v1/policies/filter?lender_id=${l.id}&limit=1`)
          if (!res.ok) return [l.id, null] as const
          const data = await res.json()
          return [l.id, (data.results?.[0] ?? null) as PolicySnap | null] as const
        } catch {
          return [l.id, null] as const
        }
      })
    ).then(entries => setPolicies(Object.fromEntries(entries)))
  }, [lenders])

  function fmtRate(p: PolicySnap | null): string {
    if (!p || (p.interest_rate_min == null && p.interest_rate_max == null)) return '—'
    const lo = p.interest_rate_min, hi = p.interest_rate_max
    if (lo != null && hi != null && lo !== hi) return `${lo}% – ${hi}%`
    return `${lo ?? hi}%`
  }
  function fmtLoan(p: PolicySnap | null): string {
    if (!p || (p.loan_amount_min == null && p.loan_amount_max == null)) return '—'
    const fmt = (v: number) => v >= 100 ? `₹${(v / 100).toFixed(0)}Cr` : `₹${v}L`
    const lo = p.loan_amount_min, hi = p.loan_amount_max
    if (lo != null && hi != null) return `${fmt(lo)} – ${fmt(hi)}`
    return fmt((lo ?? hi)!)
  }
  function fmtTenure(p: PolicySnap | null): string {
    if (!p || (p.tenure_min == null && p.tenure_max == null)) return '—'
    const fmt = (m: number) => m >= 12 ? `${Math.floor(m / 12)}y${m % 12 ? `${m % 12}m` : ''}` : `${m}m`
    const lo = p.tenure_min, hi = p.tenure_max
    if (lo != null && hi != null && lo !== hi) return `${fmt(lo)} – ${fmt(hi)}`
    return fmt((lo ?? hi)!)
  }

  type FieldDef = { label: string; render: (l: LenderSummary) => string }
  const fields: FieldDef[] = [
    { label: 'Type',               render: l => l.company_type },
    { label: 'AUM',                render: l => fmtAum(l.aum_crores) },
    { label: 'AUM Band',           render: l => l.aum_category ?? '—' },
    { label: 'HQ',                 render: l => l.hq_location ?? l.hq_state ?? '—' },
    { label: 'Est. Year',          render: l => l.established_year != null ? String(l.established_year) : '—' },
    { label: 'Employees',          render: l => l.employee_count != null ? l.employee_count.toLocaleString('en-IN') : '—' },
    { label: 'Data Quality',       render: l => l.quality_score != null ? `${Math.round(l.quality_score * 100)}%` : '—' },
    { label: 'Loan Products',      render: l => l.primary_loan_segments.length ? l.primary_loan_segments.slice(0, 3).join(', ') : '—' },
    { label: 'Pan India',          render: l => l.pan_india ? 'Yes' : 'No' },
    { label: 'Listed',             render: l => l.is_listed ? 'Yes' : 'No' },
    { label: 'Op. Intensity',      render: l => l.operating_intensity ?? '—' },
  ]

  type PolicyFieldDef = { label: string; render: (p: PolicySnap | null) => string }
  const policyFields: PolicyFieldDef[] = [
    { label: 'Interest Rate', render: fmtRate },
    { label: 'Loan Amount',   render: fmtLoan },
    { label: 'Tenure',        render: fmtTenure },
  ]

  const copyCSV = () => {
    const header  = ['Field', ...lenders.map(l => l.company_name)].join('\t')
    const rows    = fields.map(f => [f.label, ...lenders.map(l => f.render(l))].join('\t'))
    const prows   = policyFields.map(f => [f.label, ...lenders.map(l => f.render(policies[l.id] ?? null))].join('\t'))
    navigator.clipboard.writeText([header, ...rows, ...prows].join('\n')).then(() => {
      setCopied(true); setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(13,51,51,0.5)', backdropFilter: 'blur(2px)' }}
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl w-full max-w-4xl max-h-[90vh] flex flex-col overflow-hidden"
        style={{ boxShadow: '0 20px 60px rgba(13,51,51,0.2)' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b flex-shrink-0"
             style={{ borderColor: '#E6F4F4', background: '#F7FAFA' }}>
          <h2 className="font-bold text-base" style={{ color: '#0D3333' }}>
            Side-by-Side Comparison
          </h2>
          <div className="flex items-center gap-2">
            <button
              onClick={copyCSV}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border transition-colors"
              style={{ borderColor: '#D8EBEB', color: '#3D6363' }}
            >
              {copied ? '✓ Copied!' : 'Copy CSV'}
            </button>
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg transition-colors"
              style={{ color: '#7A9E9E' }}
            >
              ✕
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-auto p-4">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr style={{ background: '#F7FAFA' }}>
                <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide w-36"
                    style={{ color: '#7A9E9E' }}>
                  Field
                </th>
                {lenders.map(l => (
                  <th key={l.id} className="px-3 py-2 text-left font-bold text-sm">
                    <a href={`/lender/${l.id}`} className="hover:underline" style={{ color: '#1A7070' }}>
                      {l.company_name}
                    </a>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {fields.map((f, i) => (
                <tr key={f.label} style={{ background: i % 2 === 0 ? '#F7FAFA' : 'white' }}>
                  <td className="px-3 py-2 text-xs font-semibold whitespace-nowrap" style={{ color: '#7A9E9E' }}>
                    {f.label}
                  </td>
                  {lenders.map(l => (
                    <td key={l.id} className="px-3 py-2 text-sm" style={{ color: '#3D6363' }}>
                      {f.render(l)}
                    </td>
                  ))}
                </tr>
              ))}
              <tr>
                <td colSpan={lenders.length + 1} className="px-3 pt-3 pb-1 text-[10px] font-bold uppercase tracking-widest" style={{ color: '#1A7070' }}>
                  Loan Terms (best policy)
                </td>
              </tr>
              {policyFields.map((f, i) => (
                <tr key={f.label} style={{ background: i % 2 === 0 ? '#F7FAFA' : 'white' }}>
                  <td className="px-3 py-2 text-xs font-semibold whitespace-nowrap" style={{ color: '#7A9E9E' }}>
                    {f.label}
                  </td>
                  {lenders.map(l => (
                    <td key={l.id} className="px-3 py-2 text-sm" style={{ color: '#3D6363' }}>
                      {f.render(policies[l.id] ?? null)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────
// CSV EXPORT HELPER
// ─────────────────────────────────────────────────────────────

function exportShortlistCSV(saved: SavedLender[]) {
  const header = ['Name', 'Type', 'AUM', 'Products', 'Website', 'Phone', 'Email']
  const rows = saved.map(l => [
    l.name, l.companyType, l.aum ?? '',
    (l.products ?? []).join('; '),
    l.website ?? '', l.phone ?? '', l.email ?? '',
  ])
  const csv = [header, ...rows]
    .map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(','))
    .join('\n')
  const blob = new Blob([csv], { type: 'text/csv' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = 'shortlist.csv'
  a.click()
  URL.revokeObjectURL(url)
}

// ─────────────────────────────────────────────────────────────
// COMPONENT
// ─────────────────────────────────────────────────────────────

function DashboardContent() {
  const { user, signOut, loading: authLoading, phoneRequired, profileChecking } = useAuth()
  const { saved, count: savedCount, isSaved, toggle: toggleSave } = useSaved()
  const router = useRouter()
  const searchParams = useSearchParams()

  const [lenders,       setLenders]       = useState<LenderSummary[]>([])
  const [stubs,         setStubs]         = useState<RegistryStub[]>([])
  const [totalCount,    setTotalCount]    = useState(0)
  const [loading,       setLoading]       = useState(true)
  const [filterLoading, setFilterLoading] = useState(false)
  const [apiError,      setApiError]      = useState<string | null>(null)
  const [page,          setPage]          = useState(() => Number(searchParams.get('pg') ?? 0))
  const [filters,       setFilters]       = useState<MultiFilters>(() => filtersFromParams(searchParams))
  const [sidebarOpen,    setSidebarOpen]   = useState(false)
  const [chatOpen,       setChatOpen]      = useState(false)
  const [savedOpen,      setSavedOpen]     = useState(false)
  const [compareLenders, setCompareLenders] = useState<LenderSummary[]>([])
  const [compareOpen,    setCompareOpen]   = useState(false)

  const toggleCompare = (lender: LenderSummary) => {
    setCompareLenders(prev => {
      const exists = prev.some(l => l.id === lender.id)
      if (exists) return prev.filter(l => l.id !== lender.id)
      if (prev.length >= 4) return prev
      return [...prev, lender]
    })
  }
  const isComparingId = (id: string) => compareLenders.some(l => String(l.id) === id)

  const isFirstLoad  = useRef(true)
  const requestIdRef = useRef(0)

  // ── Sync filters + page into URL (restores state on back-nav) ──
  useEffect(() => {
    router.replace(filtersToParams(filters, page), { scroll: false })
  }, [filters, page, router])

  // ── Main fetch ──────────────────────────────────────────────
  const fetchLenders = useCallback(async (f: MultiFilters, pg: number) => {
    const thisRequestId = ++requestIdRef.current

    if (isFirstLoad.current) setLoading(true)
    else                     setFilterLoading(true)

    try {
      const data = await fetchFromAPI(f, pg)

      if (thisRequestId !== requestIdRef.current) return

      setApiError(null)
      setLenders(data.results)
      setStubs(data.stubs ?? [])
      setTotalCount(data.total)
    } catch (err: unknown) {
      if (thisRequestId !== requestIdRef.current) return
      const msg = err instanceof Error ? err.message : 'Unknown error'
      console.error('[Dashboard] API error:', msg)
      const isWakeUp = msg.includes('waking up')
      setApiError(
        isWakeUp
          ? 'Server is waking up — this takes ~30s on first load. Click Retry in a moment.'
          : 'Unable to reach the server. Please check your connection and try again.'
      )
      setLenders([])
      setTotalCount(0)
    } finally {
      if (thisRequestId === requestIdRef.current) {
        setLoading(false)
        setFilterLoading(false)
        isFirstLoad.current = false
      }
    }
  }, [])

  useEffect(() => {
    fetchLenders(filters, page)
  }, [filters, page, fetchLenders])

  useEffect(() => {
    if (savedOpen) {
      document.body.style.overflow = 'hidden'
      return () => { document.body.style.overflow = '' }
    }
  }, [savedOpen])

  // ── Filter change handler ───────────────────────────────────
  const handleFilterChange = useCallback(
    <K extends keyof MultiFilters>(key: K, value: MultiFilters[K]) => {
      setPage(0)
      isFirstLoad.current = false
      setFilters(prev => ({ ...prev, [key]: value }))
    },
    []
  )

  // ── Transform API rows → LenderCard props ──────────────────
  const transformedLenders = lenders.map(l => ({
    id:              String(l.id),
    name:            l.company_name          || 'Unknown',
    city:            l.hq_location?.split(',')[0]?.trim() || l.hq_state || 'N/A',
    state:           l.hq_state              || 'N/A',
    companyType:     l.company_type          || 'N/A',
    aum:             fmtAum(l.aum_crores),
    established:     l.established_year ? String(l.established_year) : 'N/A',
    ticketSize:      l.aum_category          || 'N/A',
    businessSector:  l.business_sector       || null,
    products:        l.primary_loan_segments,
    operatingStates: l.operating_states,
    headquarters:    l.hq_location           || l.hq_state || 'N/A',
    employees:       fmtNum(l.employee_count),
    phone:           l.phone                 || null,
    email:           l.email                 || null,
    website:         l.website               || null,
    qualityScore:       l.quality_score         ?? null,
    policyCount:        l.policy_count          ?? null,
    minInterestRate:    l.min_interest_rate     ?? null,
  }))

  // ── Render ──────────────────────────────────────────────────
  return (
    <div className="min-h-screen flex flex-col" style={{ background: '#F7FAFA' }}>
      <Navbar
        authenticated={!!user}
        user={user}
        onSignOut={signOut}
        savedCount={savedCount}
        onSavedClick={user ? () => setSavedOpen(o => !o) : undefined}
      />
      <Hero />

      <div className="flex flex-1 min-h-0 relative">

        <SearchFilter
          filters={filters}
          onFilterChange={handleFilterChange}
          resultsCount={totalCount}
          loanTypes={LOAN_TYPES}
          states={INDIA_STATES}
          ticketSizes={TICKET_SIZES}
          companyTypes={COMPANY_TYPES}
          operatingIntensities={OPERATING_INTENSITIES}
          businessSectors={['MSME', 'Housing', 'Gold', 'Vehicle', 'Microfinance', 'Agriculture', 'Retail']}
          listingStatus={LISTING_OPTIONS}
          yearRanges={[...YEAR_RANGE_OPTIONS]}
          sidebar
          sidebarOpen={sidebarOpen}
          onClose={() => setSidebarOpen(false)}
        />

        <main className={`flex-1 min-w-0 py-8 px-4 sm:px-6 lg:px-8 bg-gradient-to-b from-gray-50 to-white${compareLenders.length >= 2 ? ' pb-28' : ''}`}>

          {/* Desktop Ask AI button — auth required */}
          {user && (
            <div className="hidden md:flex justify-end mb-4">
              <button
                onClick={() => setChatOpen(p => !p)}
                className="inline-flex items-center gap-2 px-4 py-2
                           text-white rounded-xl text-sm font-semibold
                           transition-all hover:-translate-y-0.5 hover:shadow-lg"
                style={{ background: chatOpen
                  ? 'linear-gradient(135deg,#C9A227,#A07E1A)'
                  : 'linear-gradient(135deg,#0F4848,#1A7070)',
                  boxShadow: '0 2px 8px rgba(26,112,112,0.25)' }}
              >
                {chatOpen ? 'Close AI' : '✦ Ask AI'}
              </button>
            </div>
          )}

          <div className="flex items-center justify-between mb-6 md:hidden">
            <button
              type="button"
              onClick={() => setSidebarOpen(true)}
              className="inline-flex items-center gap-2 px-4 py-2
                         bg-white border rounded-xl
                         text-sm font-medium transition-colors shadow-sm"
              style={{ borderColor: '#D8EBEB', color: '#3D6363' }}
            >
              <SlidersHorizontal className="w-4 h-4" style={{ color: '#1A7070' }} />
              Filters
            </button>
            {user && (
              <button
                type="button"
                onClick={() => setChatOpen(p => !p)}
                className="inline-flex items-center gap-2 px-4 py-2
                           text-white rounded-xl text-sm font-semibold
                           transition-all hover:shadow-lg"
                style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)', boxShadow: '0 2px 8px rgba(26,112,112,0.25)' }}
              >
                ✦ Ask AI
              </button>
            )}
            <span className="text-sm" style={{ color: '#3D6363' }}>
              <span className="font-bold" style={{ color: '#1A7070' }}>
                {totalCount.toLocaleString('en-IN')}
              </span>
              {' '}lender{totalCount !== 1 ? 's' : ''}
            </span>
          </div>

          {filterLoading && !loading && (
            <div className="flex justify-center mb-6">
              <span className="flex items-center gap-2 text-sm bg-white px-4 py-2
                               rounded-full border shadow-sm"
                    style={{ color: '#3D6363', borderColor: '#D8EBEB' }}>
                <span className="animate-spin inline-block w-4 h-4 rounded-full border-2"
                      style={{ borderColor: '#D8EBEB', borderTopColor: '#1A7070' }} />
                Updating results…
              </span>
            </div>
          )}

          {/* API error banner */}
          {apiError && !loading && (
            <div className="flex items-center justify-between mb-6
                            bg-red-50 border border-red-200 rounded-xl px-5 py-4">
              <div>
                <p className="text-red-700 font-semibold text-sm">Connection error</p>
                <p className="text-red-500 text-xs mt-0.5">{apiError}</p>
              </div>
              <button
                onClick={() => { setApiError(null); fetchLenders(filters, page) }}
                className="px-4 py-2 bg-red-600 text-white text-xs font-medium
                           rounded-lg hover:bg-red-700 transition-colors flex-shrink-0 ml-4"
              >
                Retry
              </button>
            </div>
          )}

          {/* Skeleton loaders */}
          {loading ? (
            <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-5">
              {Array.from({ length: 12 }).map((_, i) => (
                <div key={i} className="bg-white rounded-2xl p-5 animate-pulse"
                     style={{ border: '1px solid #E6F4F4', boxShadow: '0 2px 6px rgba(26,112,112,0.05)' }}>
                  <div className="h-5 rounded-lg w-3/4 mb-3 skeleton" />
                  <div className="h-4 rounded-lg w-1/2 mb-2 skeleton" />
                  <div className="h-4 rounded-lg w-2/3 mb-2 skeleton" />
                  <div className="h-4 rounded-lg w-1/3 mb-5 skeleton" />
                  <div className="flex gap-2">
                    <div className="h-6 skeleton rounded-lg w-20" />
                    <div className="h-6 skeleton rounded-lg w-16" />
                  </div>
                </div>
              ))}
            </div>

          ) : transformedLenders.length > 0 ? (
            <>
              <ErrorBoundary label="lender-grid">
              <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-5">
                {transformedLenders.map((lender, index) => (
                  <LenderCard
                    key={lender.id}
                    lender={lender}
                    index={index}
                    isSaved={user ? isSaved(lender.id) : false}
                    onSave={user ? () => toggleSave({
                      id:          lender.id,
                      name:        lender.name,
                      companyType: lender.companyType,
                      aum:         lender.aum,
                      products:    lender.products,
                      website:     lender.website,
                      phone:       lender.phone,
                      email:       lender.email,
                    }) : undefined}
                    isComparing={isComparingId(lender.id)}
                    onCompare={() => toggleCompare(lenders[index])}
                    onTagClick={tag => {
                      if (!filters.loanType.includes(tag)) {
                        handleFilterChange('loanType', [...filters.loanType, tag])
                      }
                    }}
                  />
                ))}
              </div>
              </ErrorBoundary>

              {totalCount > PAGE_SIZE && (
                <div className="flex items-center justify-center gap-4 mt-10">
                  <button
                    onClick={() => setPage(p => Math.max(0, p - 1))}
                    disabled={page === 0}
                    className="px-5 py-2 rounded-xl border text-sm font-medium
                               bg-white transition-colors
                               disabled:opacity-40 disabled:cursor-not-allowed"
                    style={{ borderColor: '#D8EBEB', color: '#3D6363' }}
                  >
                    ← Previous
                  </button>

                  <span className="text-sm" style={{ color: '#3D6363' }}>
                    <span className="font-semibold" style={{ color: '#0D3333' }}>
                      {(page * PAGE_SIZE + 1).toLocaleString('en-IN')}–
                      {Math.min((page + 1) * PAGE_SIZE, totalCount).toLocaleString('en-IN')}
                    </span>
                    {' '}of{' '}
                    <span className="font-semibold" style={{ color: '#1A7070' }}>
                      {totalCount.toLocaleString('en-IN')}
                    </span>
                  </span>

                  <button
                    onClick={() => setPage(p => p + 1)}
                    disabled={(page + 1) * PAGE_SIZE >= totalCount}
                    className="px-5 py-2 rounded-xl border text-sm font-medium
                               bg-white transition-colors
                               disabled:opacity-40 disabled:cursor-not-allowed"
                    style={{ borderColor: '#D8EBEB', color: '#3D6363' }}
                  >
                    Next →
                  </button>
                </div>
              )}

              {stubs.length > 0 && (
                <div className="mt-8">
                  <div className="flex items-center gap-2 mb-4">
                    <div className="h-px flex-1 bg-gray-200" />
                    <span className="text-xs font-medium text-gray-400 whitespace-nowrap">
                      Also registered with RBI — data not yet available
                    </span>
                    <div className="h-px flex-1 bg-gray-200" />
                  </div>
                  <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4">
                    {stubs.map(stub => (
                      <StubCard key={stub.id} stub={stub} userEmail={user?.email ?? null} />
                    ))}
                  </div>
                </div>
              )}
            </>

          ) : (
            <>
              <div className="text-center py-16 bg-white rounded-2xl shadow-sm"
                   style={{ border: '1px solid #E6F4F4' }}>
                <div className="text-5xl mb-4" aria-hidden="true">🔍</div>
                <p className="font-semibold text-lg mb-1" style={{ color: '#0D3333' }}>
                  No lenders found
                </p>
                <p className="text-sm mb-6" style={{ color: '#7A9E9E' }}>
                  Try removing some filters or searching with different terms
                </p>
                <button
                  onClick={() => { setFilters(DEFAULT_FILTERS); setPage(0) }}
                  className="px-6 py-2.5 text-white rounded-xl font-semibold
                             transition-all hover:-translate-y-0.5 hover:shadow-lg"
                  style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)' }}
                >
                  Reset All Filters
                </button>
              </div>

              {stubs.length > 0 && (
                <div className="mt-8">
                  <div className="flex items-center gap-2 mb-4">
                    <div className="h-px flex-1 bg-gray-200" />
                    <span className="text-xs font-medium text-gray-400 whitespace-nowrap">
                      Also registered with RBI — data not yet available
                    </span>
                    <div className="h-px flex-1 bg-gray-200" />
                  </div>
                  <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4">
                    {stubs.map(stub => (
                      <StubCard key={stub.id} stub={stub} userEmail={user?.email ?? null} />
                    ))}
                  </div>
                </div>
              )}
            </>
          )}

        </main>

        {user && (
          <ErrorBoundary label="chat-panel">
            <ChatPanel
              open={chatOpen}
              onClose={() => setChatOpen(false)}
              onFiltersApplied={(f) => {
                setFilters(f)
                setPage(0)
                isFirstLoad.current = false
              }}
              apiUrl={API_URL}
              user={user}
            />
          </ErrorBoundary>
        )}

        {/* Saved lenders drawer — auth required */}
        {user && savedOpen && (
          <>
            <div className="fixed inset-0 bg-black/30 z-[38]" onClick={() => setSavedOpen(false)} />
            <aside className="fixed inset-y-0 right-0 z-40 w-80 bg-white flex flex-col"
                 style={{ borderLeft: '1px solid #D8EBEB', boxShadow: '0 12px 32px rgba(26,112,112,0.14)' }}>
            <div className="flex items-center justify-between px-5 py-4 border-b"
                 style={{ background: '#F7FAFA', borderColor: '#E6F4F4' }}>
              <div>
                <h2 className="font-bold" style={{ color: '#0D3333' }}>Shortlist</h2>
                <p className="text-xs mt-0.5" style={{ color: '#7A9E9E' }}>
                  {savedCount} lender{savedCount !== 1 ? 's' : ''} saved
                </p>
              </div>
              <div className="flex items-center gap-2">
                {saved.length > 0 && (
                  <button
                    onClick={() => exportShortlistCSV(saved)}
                    className="px-2.5 py-1 text-xs font-semibold rounded-lg border transition-colors"
                    style={{ borderColor: '#D8EBEB', color: '#3D6363' }}
                    title="Download as CSV"
                  >
                    ↓ CSV
                  </button>
                )}
                <button
                  onClick={() => setSavedOpen(false)}
                  className="p-1.5 rounded-lg transition-colors"
                  style={{ color: '#7A9E9E' }}
                  onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.background = '#E6F4F4' }}
                  onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent' }}
                >
                  ✕
                </button>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto py-3 px-4 space-y-3">
              {saved.length === 0 ? (
                <div className="text-center py-12">
                  <p className="text-sm" style={{ color: '#7A9E9E' }}>No lenders saved yet.</p>
                  <p className="text-xs mt-1" style={{ color: '#A8CECE' }}>Click the bookmark icon on any card.</p>
                </div>
              ) : saved.map(l => (
                <div key={l.id} className="rounded-xl p-3"
                     style={{ background: '#F7FAFA', border: '1px solid #E6F4F4' }}>
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-semibold truncate" style={{ color: '#0D3333' }}>{l.name}</p>
                      <p className="text-xs" style={{ color: '#7A9E9E' }}>
                        {l.companyType}{l.aum ? ` · ${l.aum}` : ''}
                      </p>
                    </div>
                    <button
                      onClick={() => toggleSave(l)}
                      className="text-gray-300 hover:text-red-400 transition-colors flex-shrink-0 p-0.5"
                      title="Remove"
                    >
                      ✕
                    </button>
                  </div>
                  {l.products && l.products.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-2">
                      {l.products.slice(0, 3).map(p => (
                        <span key={p} className="px-1.5 py-0.5 text-[10px] rounded-md"
                              style={{ background: '#E6F4F4', color: '#1A7070' }}>
                          {p}
                        </span>
                      ))}
                      {l.products.length > 3 && (
                        <span className="px-1.5 py-0.5 text-[10px] rounded-md"
                              style={{ background: '#F7FAFA', color: '#7A9E9E' }}>
                          +{l.products.length - 3}
                        </span>
                      )}
                    </div>
                  )}
                  <div className="flex gap-2">
                    <a href={`/lender/${l.id}`}
                       className="flex-1 text-center py-1.5 text-xs font-semibold rounded-lg transition-colors"
                       style={{ background: '#E6F4F4', color: '#1A7070' }}>
                      Details
                    </a>
                    {l.website && (
                      <a href={l.website} target="_blank" rel="noopener noreferrer"
                         className="flex-1 text-center py-1.5 text-xs font-semibold text-white rounded-lg transition-colors"
                         style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)' }}>
                        Apply
                      </a>
                    )}
                  </div>
                </div>
              ))}
            </div>
            </aside>
          </>
        )}
      </div>

      {/* Compare sticky bar */}
      {compareLenders.length >= 2 && (
        <div className="fixed bottom-0 left-0 right-0 z-30 px-4 py-3 bg-white border-t"
             style={{ borderColor: '#D8EBEB', boxShadow: '0 -4px 16px rgba(26,112,112,0.1)' }}>
          <div className="max-w-5xl mx-auto flex items-center gap-3 flex-wrap">
            <span className="text-sm font-semibold flex-shrink-0" style={{ color: '#0D3333' }}>
              Compare ({compareLenders.length}/4)
            </span>
            <div className="flex flex-wrap gap-2 flex-1 min-w-0">
              {compareLenders.map(l => (
                <span key={l.id}
                      className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium"
                      style={{ background: '#FDF8E4', color: '#A07E1A', border: '1px solid #F5E8A8' }}>
                  {l.company_name}
                  <button
                    onClick={() => setCompareLenders(prev => prev.filter(x => x.id !== l.id))}
                    className="hover:text-red-500 transition-colors"
                  >✕</button>
                </span>
              ))}
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <button
                onClick={() => setCompareLenders([])}
                className="px-3 py-1.5 text-xs rounded-lg border transition-colors"
                style={{ borderColor: '#D8EBEB', color: '#7A9E9E' }}
              >
                Clear
              </button>
              <button
                onClick={() => setCompareOpen(true)}
                className="px-4 py-1.5 text-xs font-semibold text-white rounded-lg transition-all hover:-translate-y-0.5 hover:shadow-lg"
                style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)' }}
              >
                Compare →
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Compare modal */}
      {compareOpen && compareLenders.length >= 2 && (
        <CompareModal lenders={compareLenders} onClose={() => setCompareOpen(false)} />
      )}

      <StatsSection totalLenders={totalCount} />
      <Footer />

      {user && phoneRequired && !profileChecking && <PhoneModal />}
    </div>
  )
}

export default function Dashboard() {
  return (
    <Suspense fallback={
      <div className="min-h-screen flex items-center justify-center" style={{ background: '#F7FAFA' }}>
        <div className="animate-spin rounded-full h-10 w-10 border-2 border-t-transparent"
             style={{ borderColor: '#1A7070', borderTopColor: 'transparent' }} />
      </div>
    }>
      <DashboardContent />
    </Suspense>
  )
}
