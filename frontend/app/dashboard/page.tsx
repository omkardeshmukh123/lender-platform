'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { createClient } from '@supabase/supabase-js'
import { useAuth } from '../components/AuthContext'

const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL || '',
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || ''
)

type Lender = {
  id: number
  company_name: string
  company_type: string
  aum_crores: number | null
  aum_category: string | null
  last_year_revenue: number | null
  is_listed: boolean | null
  stock_symbol: string | null
  primary_loan_segments: string[]
  primary_product: string | null
  product_types: string[]
  established_year: number | null
  hq_location: string | null
  hq_state: string | null
  operating_states: string[]
  operating_intensity: string | null
  pan_india: boolean | null
  rbi_category: string | null
  rbi_registration_number: string | null
  employee_count: number | null
  website: string | null
  recent_funding: string | null
  recent_funding_amount: number | null
  recent_funding_year: number | null
  phone: string | null
  email: string | null
}

export default function Dashboard() {
  const { user, signOut, loading: authLoading } = useAuth()
  const router = useRouter()
  const [lenders, setLenders] = useState<Lender[]>([])
  const [filteredLenders, setFilteredLenders] = useState<Lender[]>([])
  const [loading, setLoading] = useState(true)
  
  const [loanType, setLoanType] = useState('')
  const [state, setState] = useState('')
  const [aumCategory, setAumCategory] = useState('')
  const [companyType, setCompanyType] = useState('')
  const [listingStatus, setListingStatus] = useState('')

  // Redirect to landing if not logged in
  useEffect(() => {
    if (!authLoading && !user) {
      router.push('/')
    }
  }, [user, authLoading, router])

  useEffect(() => {
    if (user) {
      fetchLenders()
    }
  }, [user])

  useEffect(() => {
    applyFilters()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lenders, loanType, state, aumCategory, companyType, listingStatus])

  async function fetchLenders() {
    setLoading(true)
    
    const { data, error } = await supabase
      .from('lenders')
      .select('*')
      .order('aum_crores', { ascending: false, nullsFirst: false })
    
    if (error) {
      console.error('Error fetching lenders:', error)
    } else {
      // Parse JSON string fields to arrays
      const parsedData = (data || []).map(lender => ({
        ...lender,
        primary_loan_segments: safeJsonParse(lender.primary_loan_segments, []),
        product_types: safeJsonParse(lender.product_types, []),
        operating_states: safeJsonParse(lender.operating_states, [])
      }))
      setLenders(parsedData)
    }
    
    setLoading(false)
  }

  // Helper to safely parse JSON strings
  function safeJsonParse(value: any, fallback: any = []): any {
    if (!value) return fallback
    if (typeof value === 'string') {
      try {
        return JSON.parse(value)
      } catch {
        return fallback
      }
    }
    return value
  }

  function applyFilters() {
    let filtered = [...lenders]

    // Loan Type filter - check in primary_loan_segments array
    if (loanType) {
      filtered = filtered.filter(
        l => l.primary_loan_segments && l.primary_loan_segments.includes(loanType)
      )
    }

    // State filter - FIXED: shows pan-India banks for every state
    if (state) {
      filtered = filtered.filter(l => {
        // Show if pan-India
        if (l.pan_india) return true
        // Show if state is in operating_states array
        if (l.operating_states && l.operating_states.includes(state)) return true
        return false
      })
    }

    // AUM Category filter
    if (aumCategory) {
      filtered = filtered.filter(l => l.aum_category === aumCategory)
    }

    // Company Type filter
    if (companyType) {
      filtered = filtered.filter(l => l.company_type === companyType)
    }

    // Listing Status filter
    if (listingStatus) {
      if (listingStatus === 'listed') {
        filtered = filtered.filter(l => l.is_listed === true)
      } else if (listingStatus === 'unlisted') {
        filtered = filtered.filter(l => l.is_listed === false || l.is_listed === null)
      }
    }

    setFilteredLenders(filtered)
  }

  function resetFilters() {
    setLoanType('')
    setState('')
    setAumCategory('')
    setCompanyType('')
    setListingStatus('')
  }

  // Extract unique loan types from primary_loan_segments
  const loanTypes = Array.from(
    new Set(lenders.flatMap(l => l.primary_loan_segments || []))
  ).sort()
  
  // Extract unique states from operating_states arrays
  const states = Array.from(
    new Set(lenders.flatMap(l => l.operating_states || []))
  ).sort()

  const companyTypes = Array.from(
    new Set(lenders.map(l => l.company_type).filter(Boolean))
  ).sort() as string[]

  if (authLoading || !user) {
    return (
      <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
      </div>
    )
  }

  return (
    <main className="min-h-screen bg-gradient-to-b from-gray-50 to-white">
      {/* Header */}
      <div className="bg-blue-600 text-white py-6 shadow-lg">
        <div className="max-w-7xl mx-auto px-4">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold mb-1">Lender Discovery Platform</h1>
              <p className="text-blue-100 text-sm">Find the right financial partner for your needs</p>
            </div>
            
            <div className="flex items-center gap-4">
              <span className="text-sm text-blue-100 hidden md:block">{user.email}</span>
              <button
                onClick={() => signOut()}
                className="px-4 py-2 bg-white text-blue-600 rounded-md hover:bg-blue-50 font-medium text-sm transition-colors"
              >
                Sign Out
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 py-8">
        {/* Filters */}
        <div className="bg-white rounded-lg shadow-lg p-6 mb-8">
          <h2 className="text-xl font-semibold mb-4">Filter Lenders</h2>
          
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
            {/* Loan Type */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Loan Type
              </label>
              <select
                value={loanType}
                onChange={(e) => setLoanType(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:outline-none"
              >
                <option value="">All Loan Types</option>
                {loanTypes.map(type => (
                  <option key={type} value={type}>{type}</option>
                ))}
              </select>
            </div>

            {/* State */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                State
              </label>
              <select
                value={state}
                onChange={(e) => setState(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:outline-none"
              >
                <option value="">All States</option>
                {states.map(s => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>

            {/* AUM Category */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                AUM (Size)
              </label>
              <select
                value={aumCategory}
                onChange={(e) => setAumCategory(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:outline-none"
              >
                <option value="">All Sizes</option>
                <option value="Micro">Micro (&lt; ₹500 Cr)</option>
                <option value="Small">Small (₹500 - ₹5K Cr)</option>
                <option value="Mid">Mid (₹5K - ₹50K Cr)</option>
                <option value="Large">Large (&gt; ₹50K Cr)</option>
              </select>
            </div>

            {/* Company Type */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Company Type
              </label>
              <select
                value={companyType}
                onChange={(e) => setCompanyType(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:outline-none"
              >
                <option value="">All Types</option>
                {companyTypes.map(type => (
                  <option key={type} value={type}>{type}</option>
                ))}
              </select>
            </div>

            {/* Listing Status */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Listing Status
              </label>
              <select
                value={listingStatus}
                onChange={(e) => setListingStatus(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:outline-none"
              >
                <option value="">All</option>
                <option value="listed">Listed Only</option>
                <option value="unlisted">Unlisted Only</option>
              </select>
            </div>
          </div>

          <div className="mt-4 flex items-center justify-between">
            <p className="text-sm text-gray-600">
              Found <span className="font-semibold text-blue-600">{filteredLenders.length}</span> lender(s)
            </p>
            <button
              onClick={resetFilters}
              className="px-4 py-2 text-sm text-blue-600 hover:text-blue-800 font-medium hover:underline"
            >
              Reset Filters
            </button>
          </div>
        </div>

        {/* Results */}
        {loading ? (
          <div className="text-center py-12">
            <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
            <p className="mt-4 text-gray-600">Loading lenders...</p>
          </div>
        ) : filteredLenders.length === 0 ? (
          <div className="text-center py-12 bg-white rounded-lg shadow">
            <p className="text-gray-600">No lenders found matching your filters.</p>
            <button
              onClick={resetFilters}
              className="mt-4 px-6 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors"
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

function LenderCard({ lender }: { lender: Lender }) {
  // Format funding amount
  const formatFunding = () => {
    if (!lender.recent_funding_amount) return lender.recent_funding
    return `${lender.recent_funding} - ₹${lender.recent_funding_amount.toLocaleString()} Cr${lender.recent_funding_year ? ` (${lender.recent_funding_year})` : ''}`
  }

  return (
    <div className="bg-white rounded-lg shadow-md hover:shadow-xl transition-shadow p-6 border border-gray-100">
      <div className="flex items-start justify-between mb-3">
        <h3 className="text-lg font-bold text-gray-900 flex-1 pr-2">{lender.company_name}</h3>
        <div className="flex flex-col gap-1 flex-shrink-0">
          <span className="px-2 py-1 text-xs font-semibold bg-blue-100 text-blue-800 rounded text-center whitespace-nowrap">
            {lender.company_type}
          </span>
          {lender.is_listed && (
            <span className="px-2 py-1 text-xs font-semibold bg-green-100 text-green-800 rounded text-center whitespace-nowrap">
              {lender.stock_symbol || 'Listed'}
            </span>
          )}
        </div>
      </div>

      <div className="space-y-2 text-sm text-gray-600 mb-4">
        {/* AUM - Primary metric */}
        {lender.aum_crores !== null && lender.aum_crores !== undefined && (
          <div className="bg-blue-50 p-2 rounded">
            <p className="font-semibold text-blue-900">
              AUM: ₹{lender.aum_crores.toLocaleString()} Cr
              {lender.aum_category && (
                <span className="ml-2 px-2 py-0.5 bg-blue-200 text-blue-900 rounded text-xs">
                  {lender.aum_category}
                </span>
              )}
            </p>
          </div>
        )}

        {/* Revenue */}
        {lender.last_year_revenue && (
          <p><span className="font-medium">Revenue:</span> ₹{lender.last_year_revenue.toLocaleString()} Cr</p>
        )}

        {/* Primary Product */}
        {lender.primary_product && (
          <p><span className="font-medium">Primary:</span> {lender.primary_product}</p>
        )}
        
        {/* Operating Intensity */}
        {lender.operating_intensity && (
          <p><span className="font-medium">Coverage:</span> {lender.operating_intensity}</p>
        )}

        {/* HQ Location */}
        {lender.hq_location && (
          <p><span className="font-medium">HQ:</span> {lender.hq_location}</p>
        )}

        {/* RBI Category for NBFCs */}
        {lender.rbi_category && (
          <p><span className="font-medium">RBI:</span> {lender.rbi_category}</p>
        )}

        {/* RBI Registration Number */}
        {lender.rbi_registration_number && (
          <p className="text-xs text-gray-500">
            <span className="font-medium">RBI Reg:</span> {lender.rbi_registration_number}
          </p>
        )}
        
        {/* Established Year */}
        {lender.established_year && (
          <p><span className="font-medium">Since:</span> {lender.established_year}</p>
        )}

        {/* Employees */}
        {lender.employee_count && (
          <p><span className="font-medium">Employees:</span> {lender.employee_count.toLocaleString()}</p>
        )}

        {/* Recent Funding */}
        {(lender.recent_funding || lender.recent_funding_amount) && (
          <div className="text-xs bg-yellow-50 p-2 rounded border border-yellow-200">
            <p className="font-medium text-yellow-900">
              Recent Funding: {formatFunding()}
            </p>
          </div>
        )}
      </div>

      {/* Loan Segments */}
      {lender.primary_loan_segments && lender.primary_loan_segments.length > 0 && (
        <div className="mb-4">
          <p className="text-xs font-medium text-gray-700 mb-2">Loan Products:</p>
          <div className="flex flex-wrap gap-1">
            {lender.primary_loan_segments.slice(0, 3).map((product, idx) => (
              <span key={idx} className="px-2 py-1 text-xs bg-gray-100 text-gray-700 rounded border border-gray-200">
                {product}
              </span>
            ))}
            {lender.primary_loan_segments.length > 3 && (
              <span className="px-2 py-1 text-xs bg-gray-200 text-gray-700 rounded font-medium">
                +{lender.primary_loan_segments.length - 3} more
              </span>
            )}
          </div>
        </div>
      )}

      {/* Operating States */}
      {lender.operating_states && lender.operating_states.length > 0 && !lender.pan_india && (
        <div className="mb-4 text-xs text-gray-600">
          <p>
            Operating in <span className="font-semibold">{lender.operating_states.length}</span> state{lender.operating_states.length > 1 ? 's' : ''}
          </p>
        </div>
      )}

      {/* Contact Info */}
      <div className="flex flex-wrap gap-2">
        {lender.website && (
          <a
            href={lender.website}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center px-3 py-1.5 bg-blue-600 text-white text-xs font-medium rounded-md hover:bg-blue-700 transition-colors"
          >
            Website →
          </a>
        )}
        {lender.phone && (
          <a
            href={`tel:${lender.phone}`}
            className="inline-flex items-center px-3 py-1.5 bg-gray-100 text-gray-700 text-xs font-medium rounded-md hover:bg-gray-200 transition-colors"
          >
            Call
          </a>
        )}
        {lender.email && (
          <a
            href={`mailto:${lender.email}`}
            className="inline-flex items-center px-3 py-1.5 bg-gray-100 text-gray-700 text-xs font-medium rounded-md hover:bg-gray-200 transition-colors"
          >
            Email
          </a>
        )}
      </div>
    </div>
  )
}
