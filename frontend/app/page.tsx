'use client'

import { useState, useEffect } from 'react'
import { createClient } from '@supabase/supabase-js'

const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL || '',
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || ''
)

// ============================================================
// TYPES
// ============================================================

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
  operating_states: string[]       // FIX: added
  employee_count: number | null
  ticket_size_min: number | null
  ticket_size_max: number | null
  has_subsidiaries: boolean | null // FIX: added
  website: string | null
  phone: string | null
  email: string | null
}

// ============================================================
// CONSTANTS
// ============================================================

const COMPANY_TYPE_COLORS: Record<string, string> = {
  'NBFC':             'bg-blue-100 text-blue-800',
  'PSU Bank':         'bg-green-100 text-green-800',
  'Private Bank':     'bg-purple-100 text-purple-800',
  'Cooperative Bank': 'bg-orange-100 text-orange-800',
  'Corporate Bank':   'bg-gray-100 text-gray-800',
}

const TICKET_RANGES: Record<string, [number, number]> = {
  micro:  [0, 5],
  small:  [5, 50],
  medium: [50, 500],
  large:  [500, Infinity],
}

// All Indian states for the filter dropdown
const ALL_STATES = [
  'Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chhattisgarh',
  'Goa', 'Gujarat', 'Haryana', 'Himachal Pradesh', 'Jharkhand', 'Karnataka',
  'Kerala', 'Madhya Pradesh', 'Maharashtra', 'Manipur', 'Meghalaya', 'Mizoram',
  'Nagaland', 'Odisha', 'Punjab', 'Rajasthan', 'Sikkim', 'Tamil Nadu',
  'Telangana', 'Tripura', 'Uttar Pradesh', 'Uttarakhand', 'West Bengal',
  'Delhi', 'Jammu & Kashmir', 'Ladakh',
]

// ============================================================
// MAIN PAGE
// ============================================================

export default function Home() {
  const [lenders, setLenders]               = useState<Lender[]>([])
  const [filteredLenders, setFilteredLenders] = useState<Lender[]>([])
  const [loading, setLoading]               = useState(true)
  const [error, setError]                   = useState('')

  // Filters
  const [loanType, setLoanType]       = useState('')
  const [state, setState]             = useState('')
  const [ticketSize, setTicketSize]   = useState('')
  const [companyType, setCompanyType] = useState('')
  const [searchText, setSearchText]   = useState('')

  useEffect(() => { fetchLenders() }, [])
  useEffect(() => { applyFilters() }, [lenders, loanType, state, ticketSize, companyType, searchText])

  async function fetchLenders() {
    setLoading(true)
    setError('')
    const { data, error } = await supabase
      .from('lenders')
      .select('*')
      .order('aum_crores', { ascending: false, nullsFirst: false })

    if (error) {
      setError('Failed to load lenders. Please try again.')
      console.error(error)
    } else {
      // Ensure JSONB arrays are parsed correctly
      const parsed = (data || []).map(l => ({
        ...l,
        product_types:    Array.isArray(l.product_types)    ? l.product_types    : [],
        operating_states: Array.isArray(l.operating_states) ? l.operating_states : [],
      }))
      setLenders(parsed)
    }
    setLoading(false)
  }

  function applyFilters() {
    let filtered = [...lenders]

    // Text search by company name
    if (searchText.trim()) {
      const q = searchText.toLowerCase()
      filtered = filtered.filter(l =>
        l.company_name.toLowerCase().includes(q)
      )
    }

    // Filter by loan type (checks product_types array)
    if (loanType) {
      filtered = filtered.filter(l =>
        l.product_types?.some(p => p.toLowerCase().includes(loanType.toLowerCase()))
      )
    }

    // Filter by state: checks BOTH hq_state AND operating_states
    if (state) {
      filtered = filtered.filter(l =>
        l.hq_state === state ||
        l.operating_states?.includes(state)
      )
    }

    // Filter by ticket size
    if (ticketSize) {
      const [min, max] = TICKET_RANGES[ticketSize] || [0, Infinity]
      filtered = filtered.filter(l => {
        if (!l.ticket_size_min && !l.ticket_size_max) return true
        const lMin = l.ticket_size_min ?? 0
        const lMax = l.ticket_size_max ?? Infinity
        return lMin <= max && lMax >= min
      })
    }

    // Filter by company type
    if (companyType) {
      filtered = filtered.filter(l => l.company_type === companyType)
    }

    setFilteredLenders(filtered)
  }

  function resetFilters() {
    setLoanType('')
    setState('')
    setTicketSize('')
    setCompanyType('')
    setSearchText('')
  }

  // Derive unique loan types from live data
  const loanTypes = Array.from(
    new Set(lenders.flatMap(l => l.product_types || []))
  ).sort()

  const companyTypes = Array.from(
    new Set(lenders.map(l => l.company_type))
  ).sort()

  const activeFilters = [loanType, state, ticketSize, companyType, searchText].filter(Boolean).length

  return (
    <main className="min-h-screen bg-gray-50">

      {/* ── Header ── */}
      <div className="bg-gradient-to-r from-blue-700 to-blue-500 text-white py-10 shadow-md">
        <div className="max-w-7xl mx-auto px-4">
          <h1 className="text-3xl font-bold mb-1">🏦 Lender Discovery Platform</h1>
          <p className="text-blue-100 text-sm">
            Find NBFCs, Banks, PSU Banks & Cooperative Banks across India
          </p>
          <p className="text-blue-200 text-xs mt-1">
            {lenders.length} institutions · {lenders.filter(l => l.company_type === 'NBFC').length} NBFCs ·{' '}
            {lenders.filter(l => l.company_type.includes('Bank')).length} Banks
          </p>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 py-8">

        {/* ── Filter Panel ── */}
        <div className="bg-white rounded-xl shadow p-6 mb-8">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-gray-800">Filter Lenders</h2>
            {activeFilters > 0 && (
              <button
                onClick={resetFilters}
                className="text-sm text-red-500 hover:text-red-700 font-medium"
              >
                ✕ Clear {activeFilters} filter{activeFilters > 1 ? 's' : ''}
              </button>
            )}
          </div>

          {/* Search */}
          <div className="mb-4">
            <input
              type="text"
              placeholder="🔍 Search by company name..."
              value={searchText}
              onChange={e => setSearchText(e.target.value)}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">

            {/* Institution Type — FIX: shows all 5 types */}
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1 uppercase tracking-wide">
                Institution Type
              </label>
              <select
                value={companyType}
                onChange={e => setCompanyType(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 text-sm"
              >
                <option value="">All Types</option>
                {companyTypes.map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>

            {/* Loan Type */}
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1 uppercase tracking-wide">
                Loan Type
              </label>
              <select
                value={loanType}
                onChange={e => setLoanType(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 text-sm"
              >
                <option value="">All Loan Types</option>
                {loanTypes.map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>

            {/* State — FIX: now filters by operating_states too */}
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1 uppercase tracking-wide">
                State (HQ or Operating)
              </label>
              <select
                value={state}
                onChange={e => setState(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 text-sm"
              >
                <option value="">All States</option>
                {ALL_STATES.map(s => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>

            {/* Ticket Size */}
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1 uppercase tracking-wide">
                Ticket Size
              </label>
              <select
                value={ticketSize}
                onChange={e => setTicketSize(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 text-sm"
              >
                <option value="">All Sizes</option>
                <option value="micro">Micro (&lt; ₹5L)</option>
                <option value="small">Small (₹5L – ₹50L)</option>
                <option value="medium">Medium (₹50L – ₹5Cr)</option>
                <option value="large">Large (&gt; ₹5Cr)</option>
              </select>
            </div>
          </div>

          <div className="mt-4 text-sm text-gray-500">
            Showing <span className="font-semibold text-gray-800">{filteredLenders.length}</span> of {lenders.length} institutions
          </div>
        </div>

        {/* ── Results ── */}
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg mb-6">
            {error}
          </div>
        )}

        {loading ? (
          <div className="text-center py-20">
            <div className="inline-block animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600 mb-4"></div>
            <p className="text-gray-500">Loading institutions...</p>
          </div>
        ) : filteredLenders.length === 0 ? (
          <div className="text-center py-20 bg-white rounded-xl shadow">
            <p className="text-4xl mb-3">🔍</p>
            <p className="text-gray-600 font-medium">No lenders match your filters</p>
            <p className="text-gray-400 text-sm mt-1">Try removing some filters</p>
            <button
              onClick={resetFilters}
              className="mt-4 px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm"
            >
              Reset Filters
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {filteredLenders.map(lender => (
              <LenderCard key={lender.id} lender={lender} />
            ))}
          </div>
        )}
      </div>
    </main>
  )
}

// ============================================================
// LENDER CARD
// ============================================================

function formatAUM(aum: number | null): string {
  if (!aum) return '—'
  if (aum >= 100000) return `₹${(aum / 100000).toFixed(1)}L Cr`
  if (aum >= 1000)   return `₹${(aum / 1000).toFixed(1)}K Cr`
  return `₹${aum} Cr`
}

function LenderCard({ lender }: { lender: Lender }) {
  const badgeClass = COMPANY_TYPE_COLORS[lender.company_type] || 'bg-gray-100 text-gray-700'

  return (
    <div className="bg-white rounded-xl shadow hover:shadow-lg transition-shadow duration-200 p-5 flex flex-col">

      {/* Header */}
      <div className="flex items-start justify-between gap-2 mb-3">
        <h3 className="text-base font-bold text-gray-900 leading-snug">{lender.company_name}</h3>
        <span className={`px-2 py-0.5 text-xs font-semibold rounded-full whitespace-nowrap ${badgeClass}`}>
          {lender.company_type}
        </span>
      </div>

      {/* Key Info */}
      <div className="space-y-1.5 text-sm text-gray-600 mb-4 flex-1">
        {lender.primary_product && (
          <p>
            <span className="font-medium text-gray-700">Primary:</span>{' '}
            {lender.primary_product}
          </p>
        )}

        {lender.hq_location && (
          <p>
            <span className="font-medium text-gray-700">📍 HQ:</span>{' '}
            {lender.hq_location}
          </p>
        )}

        {lender.aum_crores && (
          <p>
            <span className="font-medium text-gray-700">AUM:</span>{' '}
            {formatAUM(lender.aum_crores)}
          </p>
        )}

        {lender.established_year && (
          <p>
            <span className="font-medium text-gray-700">Est:</span>{' '}
            {lender.established_year}
          </p>
        )}

        {lender.ticket_size_min != null && lender.ticket_size_max != null && (
          <p>
            <span className="font-medium text-gray-700">Ticket:</span>{' '}
            ₹{lender.ticket_size_min}L – ₹{lender.ticket_size_max}L
          </p>
        )}

        {lender.employee_count && (
          <p>
            <span className="font-medium text-gray-700">Employees:</span>{' '}
            {lender.employee_count.toLocaleString()}
          </p>
        )}

        {/* Operating States — FIX: now displayed */}
        {lender.operating_states && lender.operating_states.length > 0 && (
          <p>
            <span className="font-medium text-gray-700">Operates in:</span>{' '}
            {lender.operating_states.slice(0, 3).join(', ')}
            {lender.operating_states.length > 3 && ` +${lender.operating_states.length - 3} more`}
          </p>
        )}

        {/* Subsidiaries badge — FIX: now displayed */}
        {lender.has_subsidiaries && (
          <p className="text-xs text-indigo-600 font-medium">🏢 Has subsidiaries</p>
        )}
      </div>

      {/* Products */}
      {lender.product_types && lender.product_types.length > 0 && (
        <div className="mb-4">
          <div className="flex flex-wrap gap-1">
            {lender.product_types.slice(0, 3).map(product => (
              <span key={product} className="px-2 py-0.5 text-xs bg-blue-50 text-blue-700 rounded-full border border-blue-100">
                {product}
              </span>
            ))}
            {lender.product_types.length > 3 && (
              <span className="px-2 py-0.5 text-xs bg-gray-100 text-gray-500 rounded-full">
                +{lender.product_types.length - 3}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Footer Actions */}
      <div className="flex items-center gap-2 mt-auto pt-2 border-t border-gray-100">
        {lender.website && (
          <a
            href={lender.website}
            target="_blank"
            rel="noopener noreferrer"
            className="flex-1 text-center px-3 py-2 bg-blue-600 text-white text-xs font-semibold rounded-lg hover:bg-blue-700 transition-colors"
          >
            Visit Website →
          </a>
        )}
        {lender.phone && (
          <a
            href={`tel:${lender.phone}`}
            className="px-3 py-2 border border-gray-200 text-gray-600 text-xs rounded-lg hover:bg-gray-50 transition-colors"
            title={lender.phone}
          >
            📞
          </a>
        )}
        {lender.email && (
          <a
            href={`mailto:${lender.email}`}
            className="px-3 py-2 border border-gray-200 text-gray-600 text-xs rounded-lg hover:bg-gray-50 transition-colors"
            title={lender.email}
          >
            ✉️
          </a>
        )}
      </div>
    </div>
  )
}
