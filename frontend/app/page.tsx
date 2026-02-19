'use client'

import { useState, useEffect, useMemo } from 'react'
import { createClient } from '@supabase/supabase-js'

const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL || '',
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || ''
)

// ── Types ──────────────────────────────────────────────────────
type Lender = {
  id: number
  company_name: string
  company_type: string
  aum_crores: number | null
  product_types: string[]
  primary_product: string | null
  established_year: number | null
  hq_location: string | null
  hq_state: string | null
  operating_states: string[]
  pan_india: boolean
  employee_count: number | null
  ticket_size_min: number | null
  ticket_size_max: number | null
  has_subsidiaries: boolean | null
  website: string | null
  phone: string | null
  email: string | null
}

// ── Constants ──────────────────────────────────────────────────
const TYPE_COLORS: Record<string, { bg: string; text: string; dot: string }> = {
  'NBFC':               { bg: 'bg-blue-50',   text: 'text-blue-700',   dot: 'bg-blue-500' },
  'Private Bank':       { bg: 'bg-purple-50', text: 'text-purple-700', dot: 'bg-purple-500' },
  'PSU Bank':           { bg: 'bg-green-50',  text: 'text-green-700',  dot: 'bg-green-500' },
  'Small Finance Bank': { bg: 'bg-orange-50', text: 'text-orange-700', dot: 'bg-orange-500' },
  'Foreign Bank':       { bg: 'bg-rose-50',   text: 'text-rose-700',   dot: 'bg-rose-500' },
  'Cooperative Bank':   { bg: 'bg-teal-50',   text: 'text-teal-700',   dot: 'bg-teal-500' },
}

const ALL_STATES = [
  'Andhra Pradesh','Arunachal Pradesh','Assam','Bihar','Chhattisgarh',
  'Goa','Gujarat','Haryana','Himachal Pradesh','Jharkhand','Karnataka',
  'Kerala','Madhya Pradesh','Maharashtra','Manipur','Meghalaya','Mizoram',
  'Nagaland','Odisha','Punjab','Rajasthan','Sikkim','Tamil Nadu','Telangana',
  'Tripura','Uttar Pradesh','Uttarakhand','West Bengal','Delhi',
  'Jammu & Kashmir','Ladakh','Puducherry','Chandigarh',
]

const TICKET_RANGES: Record<string, [number, number]> = {
  micro:  [0, 5],
  small:  [5, 50],
  medium: [50, 500],
  large:  [500, Infinity],
}

// ── Main Page ──────────────────────────────────────────────────
export default function Home() {
  const [lenders, setLenders]   = useState<Lender[]>([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState('')

  // Filters
  const [search, setSearch]         = useState('')
  const [loanType, setLoanType]     = useState('')
  const [state, setState]           = useState('')
  const [ticketSize, setTicketSize] = useState('')
  const [companyType, setCompanyType] = useState('')

  useEffect(() => { fetchLenders() }, [])

  async function fetchLenders() {
    setLoading(true)
    const { data, error } = await supabase
      .from('lenders')
      .select('*')
      .eq('extraction_status', 'success')   // only show successfully extracted
      .order('aum_crores', { ascending: false, nullsFirst: false })

    if (error) {
      setError('Could not load lenders. Please refresh.')
    } else {
      const parsed = (data || []).map(l => ({
        ...l,
        product_types:    Array.isArray(l.product_types)    ? l.product_types    : [],
        operating_states: Array.isArray(l.operating_states) ? l.operating_states : [],
      }))
      setLenders(parsed)
    }
    setLoading(false)
  }

  // ── Filter logic (your decisions) ───────────────────────────
  const filtered = useMemo(() => {
    let result = [...lenders]

    // 1. Text search
    if (search.trim()) {
      const q = search.toLowerCase()
      result = result.filter(l => l.company_name.toLowerCase().includes(q))
    }

    // 2. Loan type — check product_types array
    if (loanType) {
      result = result.filter(l =>
        l.product_types?.some(p => p.toLowerCase().includes(loanType.toLowerCase()))
      )
    }

    // 3. State — YOUR DECISION:
    //    Only show if they OPERATE in that state (not just HQ)
    //    Pan-India lenders always show (pan_india = true)
    if (state) {
      result = result.filter(l =>
        l.pan_india === true ||
        l.operating_states?.includes(state)
      )
    }

    // 4. Ticket size — range overlap
    if (ticketSize) {
      const [min, max] = TICKET_RANGES[ticketSize] || [0, Infinity]
      result = result.filter(l => {
        if (l.ticket_size_min == null && l.ticket_size_max == null) return true
        const lMin = l.ticket_size_min ?? 0
        const lMax = l.ticket_size_max ?? Infinity
        return lMin <= max && lMax >= min
      })
    }

    // 5. Company type — YOUR DECISION: all in one list, filter by type
    if (companyType) {
      result = result.filter(l => l.company_type === companyType)
    }

    return result
  }, [lenders, search, loanType, state, ticketSize, companyType])

  // Derived filter options from live data
  const loanTypes = useMemo(() =>
    Array.from(new Set(lenders.flatMap(l => l.product_types || []))).sort()
  , [lenders])

  const companyTypes = useMemo(() =>
    Array.from(new Set(lenders.map(l => l.company_type))).sort()
  , [lenders])

  const activeCount = [search, loanType, state, ticketSize, companyType].filter(Boolean).length

  function reset() {
    setSearch(''); setLoanType(''); setState('')
    setTicketSize(''); setCompanyType('')
  }

  // Summary counts
  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    lenders.forEach(l => { c[l.company_type] = (c[l.company_type] || 0) + 1 })
    return c
  }, [lenders])

  return (
    <main className="min-h-screen bg-gray-50">

      {/* Header */}
      <div className="bg-gradient-to-br from-slate-900 to-blue-900 text-white">
        <div className="max-w-7xl mx-auto px-4 py-10">
          <h1 className="text-3xl font-bold mb-1 tracking-tight">
            🏦 Lender Discovery Platform
          </h1>
          <p className="text-blue-200 text-sm mb-6">
            Find NBFCs, Private Banks, PSU Banks & more across India
          </p>

          {/* Type pills */}
          <div className="flex flex-wrap gap-2">
            {Object.entries(counts).map(([type, count]) => {
              const c = TYPE_COLORS[type] || { bg: 'bg-gray-50', text: 'text-gray-700', dot: 'bg-gray-400' }
              return (
                <button
                  key={type}
                  onClick={() => setCompanyType(companyType === type ? '' : type)}
                  className={`px-3 py-1 rounded-full text-xs font-semibold border transition-all
                    ${companyType === type
                      ? 'bg-white text-slate-900 border-white'
                      : 'bg-white/10 text-white border-white/20 hover:bg-white/20'
                    }`}
                >
                  {type} ({count})
                </button>
              )
            })}
            {companyType && (
              <button onClick={() => setCompanyType('')}
                className="px-3 py-1 rounded-full text-xs text-white/60 hover:text-white">
                ✕ clear
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 py-6">

        {/* Filter Panel */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-5 mb-6">

          {/* Search */}
          <div className="mb-4">
            <div className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">🔍</span>
              <input
                type="text"
                placeholder="Search by lender name..."
                value={search}
                onChange={e => setSearch(e.target.value)}
                className="w-full pl-9 pr-4 py-2.5 border border-gray-200 rounded-lg text-sm
                           focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">

            {/* Loan Type */}
            <div>
              <label className="block text-xs font-semibold text-gray-500 mb-1.5 uppercase tracking-wide">
                Loan Type
              </label>
              <select value={loanType} onChange={e => setLoanType(e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm
                           focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white">
                <option value="">All Loan Types</option>
                {loanTypes.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>

            {/* State — filters by operating_states */}
            <div>
              <label className="block text-xs font-semibold text-gray-500 mb-1.5 uppercase tracking-wide">
                State
              </label>
              <select value={state} onChange={e => setState(e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm
                           focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white">
                <option value="">All States</option>
                {ALL_STATES.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>

            {/* Ticket Size */}
            <div>
              <label className="block text-xs font-semibold text-gray-500 mb-1.5 uppercase tracking-wide">
                Ticket Size
              </label>
              <select value={ticketSize} onChange={e => setTicketSize(e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm
                           focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white">
                <option value="">All Sizes</option>
                <option value="micro">Micro — under ₹5L</option>
                <option value="small">Small — ₹5L to ₹50L</option>
                <option value="medium">Medium — ₹50L to ₹5Cr</option>
                <option value="large">Large — above ₹5Cr</option>
              </select>
            </div>

            {/* Company Type */}
            <div>
              <label className="block text-xs font-semibold text-gray-500 mb-1.5 uppercase tracking-wide">
                Institution Type
              </label>
              <select value={companyType} onChange={e => setCompanyType(e.target.value)}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm
                           focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white">
                <option value="">All Types</option>
                {companyTypes.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
          </div>

          {/* Filter summary row */}
          <div className="flex items-center justify-between mt-4 pt-3 border-t border-gray-100">
            <p className="text-sm text-gray-600">
              Showing{' '}
              <span className="font-bold text-gray-900">{filtered.length}</span>
              {' '}of{' '}
              <span className="font-semibold">{lenders.length}</span>
              {' '}lenders
              {state && (
                <span className="ml-1 text-blue-600 font-medium">
                  operating in {state}
                </span>
              )}
            </p>
            {activeCount > 0 && (
              <button onClick={reset}
                className="text-sm text-red-500 hover:text-red-700 font-medium">
                ✕ Clear {activeCount} filter{activeCount > 1 ? 's' : ''}
              </button>
            )}
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 mb-6 text-sm">
            {error}
          </div>
        )}

        {/* Results */}
        {loading ? (
          <div className="flex flex-col items-center justify-center py-24">
            <div className="w-10 h-10 border-2 border-blue-600 border-t-transparent
                            rounded-full animate-spin mb-4" />
            <p className="text-gray-500 text-sm">Loading lenders...</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-24 bg-white rounded-xl border border-gray-200">
            <p className="text-4xl mb-3">🔍</p>
            <p className="font-semibold text-gray-700">No lenders found</p>
            <p className="text-gray-400 text-sm mt-1">Try adjusting your filters</p>
            <button onClick={reset}
              className="mt-4 px-5 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700">
              Reset Filters
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {filtered.map(l => <LenderCard key={l.id} lender={l} />)}
          </div>
        )}
      </div>
    </main>
  )
}

// ── Lender Card ────────────────────────────────────────────────
function formatAUM(v: number | null) {
  if (!v) return null
  if (v >= 100000) return `₹${(v / 100000).toFixed(1)}L Cr`
  if (v >= 1000)   return `₹${(v / 1000).toFixed(1)}K Cr`
  return `₹${v} Cr`
}

function LenderCard({ lender: l }: { lender: Lender }) {
  const colors = TYPE_COLORS[l.company_type] || TYPE_COLORS['NBFC']

  return (
    <div className="bg-white rounded-xl border border-gray-200 hover:border-blue-300
                    hover:shadow-md transition-all duration-200 flex flex-col p-5">

      {/* Top row */}
      <div className="flex items-start justify-between gap-2 mb-3">
        <h3 className="font-bold text-gray-900 text-base leading-snug flex-1">
          {l.company_name}
        </h3>
        <span className={`flex items-center gap-1 px-2 py-1 rounded-full text-xs
                         font-semibold whitespace-nowrap flex-shrink-0
                         ${colors.bg} ${colors.text}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${colors.dot}`} />
          {l.company_type}
        </span>
      </div>

      {/* Info rows */}
      <div className="space-y-1.5 text-sm text-gray-600 flex-1 mb-3">
        {l.primary_product && (
          <p><span className="font-medium text-gray-700">Primary: </span>{l.primary_product}</p>
        )}
        {l.hq_location && (
          <p><span className="font-medium text-gray-700">📍 </span>{l.hq_location}</p>
        )}
        {formatAUM(l.aum_crores) && (
          <p><span className="font-medium text-gray-700">AUM: </span>{formatAUM(l.aum_crores)}</p>
        )}
        {l.ticket_size_min != null && l.ticket_size_max != null && (
          <p>
            <span className="font-medium text-gray-700">Ticket: </span>
            ₹{l.ticket_size_min}L – ₹{l.ticket_size_max}L
          </p>
        )}
        {l.established_year && (
          <p><span className="font-medium text-gray-700">Est: </span>{l.established_year}</p>
        )}

        {/* Pan India or operating states */}
        {l.pan_india ? (
          <p className="text-green-600 font-semibold text-xs">🌏 Pan India</p>
        ) : l.operating_states?.length > 0 && (
          <p className="text-xs text-gray-500">
            <span className="font-medium text-gray-600">Operates in: </span>
            {l.operating_states.slice(0, 3).join(', ')}
            {l.operating_states.length > 3 && ` +${l.operating_states.length - 3} more`}
          </p>
        )}

        {l.has_subsidiaries && (
          <p className="text-xs text-indigo-600 font-medium">🏢 Has subsidiaries</p>
        )}
      </div>

      {/* Products */}
      {l.product_types?.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-4">
          {l.product_types.slice(0, 4).map(p => (
            <span key={p}
              className="px-2 py-0.5 text-xs bg-gray-100 text-gray-600 rounded-full border border-gray-200">
              {p}
            </span>
          ))}
          {l.product_types.length > 4 && (
            <span className="px-2 py-0.5 text-xs bg-gray-100 text-gray-400 rounded-full border border-gray-200">
              +{l.product_types.length - 4}
            </span>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-2 mt-auto pt-3 border-t border-gray-100">
        {l.website && (
          <a href={l.website} target="_blank" rel="noopener noreferrer"
            className="flex-1 text-center py-2 bg-blue-600 text-white text-xs
                       font-semibold rounded-lg hover:bg-blue-700 transition-colors">
            Visit Website →
          </a>
        )}
        {l.phone && (
          <a href={`tel:${l.phone}`} title={l.phone}
            className="p-2 border border-gray-200 rounded-lg hover:bg-gray-50
                       text-sm transition-colors">
            📞
          </a>
        )}
        {l.email && (
          <a href={`mailto:${l.email}`} title={l.email}
            className="p-2 border border-gray-200 rounded-lg hover:bg-gray-50
                       text-sm transition-colors">
            ✉️
          </a>
        )}
      </div>
    </div>
  )
}
