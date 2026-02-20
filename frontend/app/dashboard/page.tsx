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
  product_types: string[]
  primary_product: string | null
  established_year: number | null
  hq_location: string | null
  hq_state: string | null
  employee_count: number | null
  ticket_size_min: number | null
  ticket_size_max: number | null
  has_subsidiaries: boolean | null
  website: string | null
}

export default function Dashboard() {
  const { user, signOut, loading: authLoading } = useAuth()
  const router = useRouter()
  const [lenders, setLenders] = useState<Lender[]>([])
  const [filteredLenders, setFilteredLenders] = useState<Lender[]>([])
  const [loading, setLoading] = useState(true)
  
  const [loanType, setLoanType] = useState('')
  const [state, setState] = useState('')
  const [ticketSize, setTicketSize] = useState('')
  const [companyType, setCompanyType] = useState('')

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
  }, [lenders, loanType, state, ticketSize, companyType])

  async function fetchLenders() {
    setLoading(true)
    
    const { data, error } = await supabase
      .from('lenders')
      .select('*')
      .order('aum_crores', { ascending: false, nullsFirst: false })
    
    if (error) {
      console.error('Error fetching lenders:', error)
    } else {
      setLenders(data || [])
    }
    
    setLoading(false)
  }

  function applyFilters() {
    let filtered = [...lenders]

    if (loanType) {
      filtered = filtered.filter(
        l => l.product_types && l.product_types.includes(loanType)
      )
    }

    if (state) {
      filtered = filtered.filter(l => l.hq_state === state)
    }

    if (ticketSize) {
      const [min, max] = getTicketSizeRange(ticketSize)
      
      filtered = filtered.filter(l => {
        if (l.ticket_size_min == null && l.ticket_size_max == null)
          return true
        
        const lenderMin = l.ticket_size_min ?? 0
        const lenderMax = l.ticket_size_max ?? Infinity
        
        return lenderMin <= max && lenderMax >= min
      })
    }

    if (companyType) {
      filtered = filtered.filter(l => l.company_type === companyType)
    }

    setFilteredLenders(filtered)
  }

  function getTicketSizeRange(range: string): [number, number] {
    switch (range) {
      case 'micro': return [0, 5]
      case 'small': return [5, 50]
      case 'medium': return [50, 500]
      case 'large': return [500, Infinity]
      default: return [0, Infinity]
    }
  }

  function resetFilters() {
    setLoanType('')
    setState('')
    setTicketSize('')
    setCompanyType('')
  }

  const loanTypes = Array.from(
    new Set(lenders.flatMap(l => l.product_types || []))
  ).sort()
  
  const states = Array.from(
    new Set(lenders.map(l => l.hq_state).filter(Boolean))
  ).sort() as string[]

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
      <div className="bg-blue-600 text-white py-6 shadow-lg">
        <div className="max-w-7xl mx-auto px-4">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold mb-1">🏦 Lender Discovery Platform</h1>
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
        <div className="bg-white rounded-lg shadow-lg p-6 mb-8">
          <h2 className="text-xl font-semibold mb-4">Filter Lenders</h2>
          
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Loan Type
              </label>
              <select
                value={loanType}
                onChange={(e) => setLoanType(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500"
              >
                <option value="">All Loan Types</option>
                {loanTypes.map(type => (
                  <option key={type} value={type}>{type}</option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                State
              </label>
              <select
                value={state}
                onChange={(e) => setState(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500"
              >
                <option value="">All States</option>
                {states.map(s => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Ticket Size
              </label>
              <select
                value={ticketSize}
                onChange={(e) => setTicketSize(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500"
              >
                <option value="">All Sizes</option>
                <option value="micro">Micro (&lt; ₹5L)</option>
                <option value="small">Small (₹5L - ₹50L)</option>
                <option value="medium">Medium (₹50L - ₹5Cr)</option>
                <option value="large">Large (&gt; ₹5Cr)</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Company Type
              </label>
              <select
                value={companyType}
                onChange={(e) => setCompanyType(e.target.value)}
                className="w-full px-4 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500"
              >
                <option value="">All Types</option>
                {companyTypes.map(type => (
                  <option key={type} value={type}>{type}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="mt-4 flex items-center justify-between">
            <p className="text-sm text-gray-600">
              Found <span className="font-semibold">{filteredLenders.length}</span> lender(s)
            </p>
            <button
              onClick={resetFilters}
              className="px-4 py-2 text-sm text-blue-600 hover:text-blue-800 font-medium"
            >
              Reset Filters
            </button>
          </div>
        </div>

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
              className="mt-4 px-6 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700"
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
  return (
    <div className="bg-white rounded-lg shadow-md hover:shadow-xl transition-shadow p-6">
      <div className="flex items-start justify-between mb-3">
        <h3 className="text-lg font-bold text-gray-900">{lender.company_name}</h3>
        <span className="px-2 py-1 text-xs font-semibold bg-blue-100 text-blue-800 rounded">
          {lender.company_type}
        </span>
      </div>

      <div className="space-y-2 text-sm text-gray-600 mb-4">
        {lender.primary_product && (
          <p><span className="font-medium">Primary:</span> {lender.primary_product}</p>
        )}
        
        {lender.hq_location && (
          <p><span className="font-medium">HQ:</span> {lender.hq_location}</p>
        )}
        
        {lender.aum_crores && (
          <p><span className="font-medium">AUM:</span> ₹{lender.aum_crores.toLocaleString()} Cr</p>
        )}
        
        {lender.established_year && (
          <p><span className="font-medium">Since:</span> {lender.established_year}</p>
        )}

        {lender.ticket_size_min != null && lender.ticket_size_max != null && (
          <p>
            <span className="font-medium">Ticket Size:</span> ₹{lender.ticket_size_min}L - ₹{lender.ticket_size_max}L
          </p>
        )}

        {lender.employee_count && (
          <p><span className="font-medium">Employees:</span> {lender.employee_count.toLocaleString()}</p>
        )}
      </div>

      {lender.product_types && lender.product_types.length > 0 && (
        <div className="mb-4">
          <p className="text-xs font-medium text-gray-700 mb-2">Products:</p>
          <div className="flex flex-wrap gap-1">
            {lender.product_types.slice(0, 3).map((product, idx) => (
              <span key={idx} className="px-2 py-1 text-xs bg-gray-100 text-gray-700 rounded">
                {product}
              </span>
            ))}
            {lender.product_types.length > 3 && (
              <span className="px-2 py-1 text-xs bg-gray-100 text-gray-700 rounded">
                +{lender.product_types.length - 3} more
              </span>
            )}
          </div>
        </div>
      )}

      {lender.website && (
        <a
          href={lender.website}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 transition-colors"
        >
          Visit Website →
        </a>
      )}
    </div>
  )
}