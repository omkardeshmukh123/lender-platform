'use client'

/**
 * app/dashboard/page.tsx
 * =======================
 * Data source: FastAPI  GET /lenders/search
 * Auth: Supabase (unchanged — only used for login/session, not data)
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { SlidersHorizontal } from 'lucide-react'
import { useAuth } from '../components/AuthContext'
import { useSaved, SavedLender } from '../components/SaveContext'
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
  primary_loan_segments: string[]   // already parsed array from API
  operating_states:      string[]   // already parsed array from API
  website:               string | null
  quality_score:         number | null
  employee_count:        number | null
  established_year:      number | null
  is_listed:             boolean
  phone:                 string | null
  email:                 string | null
}

interface LenderSearchResponse {
  total:   number
  page:    number
  limit:   number
  results: LenderSummary[]
}

// ─────────────────────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────────────────────

function fmtAum(val: number | null | undefined): string {
  if (!val) return 'N/A'
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

  const res = await fetch(`${API_URL}/v1/lenders/search?${params}`)
  if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`)
  return res.json() as Promise<LenderSearchResponse>
}

// ─────────────────────────────────────────────────────────────
// COMPONENT
// ─────────────────────────────────────────────────────────────

export default function Dashboard() {
  const { user, signOut, loading: authLoading } = useAuth()
  const { saved, count: savedCount, isSaved, toggle: toggleSave } = useSaved()
  const router = useRouter()

  const [lenders,       setLenders]       = useState<LenderSummary[]>([])
  const [totalCount,    setTotalCount]    = useState(0)
  const [loading,       setLoading]       = useState(true)
  const [filterLoading, setFilterLoading] = useState(false)
  const [apiError,      setApiError]      = useState<string | null>(null)
  const [page,          setPage]          = useState(0)
  const [filters,       setFilters]       = useState<MultiFilters>(DEFAULT_FILTERS)
  const [sidebarOpen,   setSidebarOpen]   = useState(false)
  const [chatOpen,      setChatOpen]      = useState(false)
  const [savedOpen,     setSavedOpen]     = useState(false)

  const isFirstLoad  = useRef(true)
  const requestIdRef = useRef(0)

  // ── Auth guard ──────────────────────────────────────────────
  useEffect(() => {
    if (!authLoading && !user) router.push('/')
  }, [user, authLoading, router])

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
      setTotalCount(data.total)
    } catch (err: unknown) {
      if (thisRequestId !== requestIdRef.current) return
      const msg = err instanceof Error ? err.message : 'Unknown error'
      console.error('[Dashboard] API error:', msg)
      setApiError('Unable to reach the server. Please check your connection and try again.')
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
    if (user) fetchLenders(filters, page)
  }, [user, filters, page, fetchLenders])

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
    products:        l.primary_loan_segments,
    operatingStates: l.operating_states,
    headquarters:    l.hq_location           || l.hq_state || 'N/A',
    employees:       fmtNum(l.employee_count),
    phone:           l.phone                 || null,
    email:           l.email                 || null,
    website:         l.website               || null,
  }))

  // ── Auth loading screen ─────────────────────────────────────
  if (authLoading || !user) {
    return (
      <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-[#3B5CCC]" />
      </div>
    )
  }

  // ── Render ──────────────────────────────────────────────────
  return (
    <div className="min-h-screen flex flex-col bg-gradient-to-b from-gray-50 to-white">
      <Navbar authenticated user={user} onSignOut={signOut} savedCount={savedCount} onSavedClick={() => setSavedOpen(o => !o)} />
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

        <main className="flex-1 min-w-0 py-8 px-4 sm:px-6 lg:px-8
                         bg-gradient-to-b from-gray-50 to-white">

          {/* Desktop Ask AI button */}
          <div className="hidden md:flex justify-end mb-4">
            <button
              onClick={() => setChatOpen(p => !p)}
              className="inline-flex items-center gap-2 px-4 py-2
                         bg-[#3B5CCC] text-white rounded-xl text-sm font-medium
                         hover:bg-[#2d4aa8] transition-colors shadow-sm"
            >
              {chatOpen ? 'Close AI' : 'Ask AI'}
            </button>
          </div>

          <div className="flex items-center justify-between mb-6 md:hidden">
            <button
              type="button"
              onClick={() => setSidebarOpen(true)}
              className="inline-flex items-center gap-2 px-4 py-2
                         bg-white border border-gray-200 rounded-xl
                         text-sm font-medium text-gray-700
                         hover:border-gray-300 transition-colors shadow-sm"
            >
              <SlidersHorizontal className="w-4 h-4 text-[#3B5CCC]" />
              Filters
            </button>
            <button
              type="button"
              onClick={() => setChatOpen(p => !p)}
              className="inline-flex items-center gap-2 px-4 py-2
                         bg-[#3B5CCC] text-white rounded-xl
                         text-sm font-medium
                         hover:bg-[#2d4aa8] transition-colors shadow-sm"
            >
              Ask AI
            </button>
            <span className="text-sm text-gray-600">
              <span className="font-bold text-[#3B5CCC]">
                {totalCount.toLocaleString('en-IN')}
              </span>
              {' '}lender{totalCount !== 1 ? 's' : ''}
            </span>
          </div>

          {filterLoading && !loading && (
            <div className="flex justify-center mb-6">
              <span className="flex items-center gap-2 text-sm text-gray-500
                               bg-white px-4 py-2 rounded-full border border-gray-200 shadow-sm">
                <span className="animate-spin inline-block w-4 h-4 border-b-2 border-[#3B5CCC] rounded-full" />
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

          {loading ? (
            <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-6">
              {Array.from({ length: 6 }).map((_, i) => (
                <div
                  key={i}
                  className="bg-white rounded-xl border border-gray-200 p-6 animate-pulse"
                >
                  <div className="h-5 bg-gray-200 rounded w-3/4 mb-3" />
                  <div className="h-4 bg-gray-200 rounded w-1/2 mb-2" />
                  <div className="h-4 bg-gray-200 rounded w-2/3 mb-2" />
                  <div className="h-4 bg-gray-200 rounded w-1/3 mb-5" />
                  <div className="flex gap-2">
                    <div className="h-6 bg-gray-200 rounded w-20" />
                    <div className="h-6 bg-gray-200 rounded w-16" />
                  </div>
                </div>
              ))}
            </div>

          ) : transformedLenders.length > 0 ? (
            <>
              <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-6">
                {transformedLenders.map((lender, index) => (
                  <LenderCard
                    key={lender.id}
                    lender={lender}
                    index={index}
                    isSaved={isSaved(lender.id)}
                    onSave={() => toggleSave({
                      id:          lender.id,
                      name:        lender.name,
                      companyType: lender.companyType,
                      aum:         lender.aum,
                      products:    lender.products,
                      website:     lender.website,
                      phone:       lender.phone,
                      email:       lender.email,
                    })}
                    onTagClick={tag => {
                      if (!filters.loanType.includes(tag)) {
                        handleFilterChange('loanType', [...filters.loanType, tag])
                      }
                    }}
                  />
                ))}
              </div>

              {totalCount > PAGE_SIZE && (
                <div className="flex items-center justify-center gap-4 mt-10">
                  <button
                    onClick={() => setPage(p => Math.max(0, p - 1))}
                    disabled={page === 0}
                    className="px-5 py-2 rounded-xl border border-gray-200 text-sm font-medium
                               bg-white hover:bg-gray-50 transition-colors
                               disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    ← Previous
                  </button>

                  <span className="text-sm text-gray-600">
                    <span className="font-semibold text-gray-900">
                      {(page * PAGE_SIZE + 1).toLocaleString('en-IN')}–
                      {Math.min((page + 1) * PAGE_SIZE, totalCount).toLocaleString('en-IN')}
                    </span>
                    {' '}of{' '}
                    <span className="font-semibold text-[#3B5CCC]">
                      {totalCount.toLocaleString('en-IN')}
                    </span>
                  </span>

                  <button
                    onClick={() => setPage(p => p + 1)}
                    disabled={(page + 1) * PAGE_SIZE >= totalCount}
                    className="px-5 py-2 rounded-xl border border-gray-200 text-sm font-medium
                               bg-white hover:bg-gray-50 transition-colors
                               disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    Next →
                  </button>
                </div>
              )}
            </>

          ) : (
            <div className="text-center py-16 bg-white rounded-2xl border border-gray-200 shadow-sm">
              <div className="text-5xl mb-4" aria-hidden="true">🔍</div>
              <p className="text-gray-800 font-semibold text-lg mb-1">
                No lenders found
              </p>
              <p className="text-gray-400 text-sm mb-6">
                Try removing some filters or searching with different terms
              </p>
              <button
                onClick={() => { setFilters(DEFAULT_FILTERS); setPage(0) }}
                className="px-6 py-2.5 bg-[#3B5CCC] text-white rounded-xl font-medium
                           hover:bg-[#2d4aa8] transition-colors"
              >
                Reset All Filters
              </button>
            </div>
          )}

        </main>

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

        {/* Saved lenders drawer */}
        {savedOpen && (
          <aside className="fixed inset-y-0 right-0 z-40 w-80 bg-white border-l border-gray-200 shadow-xl flex flex-col">
            <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
              <div>
                <h2 className="font-semibold text-gray-900">Shortlist</h2>
                <p className="text-xs text-gray-400 mt-0.5">{savedCount} lender{savedCount !== 1 ? 's' : ''} saved</p>
              </div>
              <button
                onClick={() => setSavedOpen(false)}
                className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
              >
                ✕
              </button>
            </div>

            <div className="flex-1 overflow-y-auto py-3 px-4 space-y-3">
              {saved.length === 0 ? (
                <div className="text-center py-12">
                  <p className="text-sm text-gray-400">No lenders saved yet.</p>
                  <p className="text-xs text-gray-300 mt-1">Click the bookmark icon on any card.</p>
                </div>
              ) : saved.map(l => (
                <div key={l.id} className="bg-gray-50 rounded-xl p-3 border border-gray-100">
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-semibold text-gray-900 truncate">{l.name}</p>
                      <p className="text-xs text-gray-400">{l.companyType}{l.aum ? ` · ${l.aum}` : ''}</p>
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
                        <span key={p} className="px-1.5 py-0.5 bg-blue-50 text-[#3B5CCC] text-[10px] rounded-md">{p}</span>
                      ))}
                      {l.products.length > 3 && (
                        <span className="px-1.5 py-0.5 bg-gray-100 text-gray-500 text-[10px] rounded-md">+{l.products.length - 3}</span>
                      )}
                    </div>
                  )}
                  <div className="flex gap-2">
                    <a href={`/lender/${l.id}`}
                       className="flex-1 text-center py-1.5 text-xs font-medium text-[#3B5CCC] bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors">
                      Details
                    </a>
                    {l.website && (
                      <a href={l.website} target="_blank" rel="noopener noreferrer"
                         className="flex-1 text-center py-1.5 text-xs font-medium text-white bg-[#3B5CCC] rounded-lg hover:bg-[#2d4aa8] transition-colors">
                        Apply
                      </a>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </aside>
        )}
      </div>

      <StatsSection totalLenders={totalCount} />
      <Footer />
    </div>
  )
}
