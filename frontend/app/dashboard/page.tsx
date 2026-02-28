'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { createClient } from '@supabase/supabase-js'
import { useAuth } from '../components/AuthContext'
import { Navbar } from '../components/Navbar'
import { Hero } from '../components/Hero'
import { SearchFilter } from '../components/SearchFilter'
import { LenderCard } from '../components/LenderCard'
import { StatsSection } from '../components/StatsSection'
import { Footer } from '../components/Footer'

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
  
  // Filter states matching new design
  const [filters, setFilters] = useState({
    search: '',
    loanType: 'All Loan Types',
    state: 'All States',
    ticketSize: 'All Sizes',
    companyType: 'All Types',
    sortBy: 'All'
  })

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
  }, [lenders, filters])

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

    // Search filter
    if (filters.search) {
      const searchLower = filters.search.toLowerCase()
      filtered = filtered.filter(l =>
        l.company_name?.toLowerCase().includes(searchLower) ||
        l.hq_location?.toLowerCase().includes(searchLower) ||
        l.hq_state?.toLowerCase().includes(searchLower) ||
        l.primary_product?.toLowerCase().includes(searchLower)
      )
    }

    // Loan Type filter
    if (filters.loanType && filters.loanType !== 'All Loan Types') {
      filtered = filtered.filter(
        l => l.primary_loan_segments && l.primary_loan_segments.includes(filters.loanType)
      )
    }

    // State filter - shows pan-India banks for every state
    if (filters.state && filters.state !== 'All States') {
      filtered = filtered.filter(l => {
        if (l.pan_india) return true
        if (l.operating_states && l.operating_states.includes(filters.state)) return true
        return false
      })
    }

    // AUM/Ticket Size filter
    if (filters.ticketSize && filters.ticketSize !== 'All Sizes') {
      filtered = filtered.filter(l => l.aum_category === filters.ticketSize)
    }

    // Company Type filter
    if (filters.companyType && filters.companyType !== 'All Types') {
      filtered = filtered.filter(l => l.company_type === filters.companyType)
    }

    // Listing Status filter
    if (filters.sortBy && filters.sortBy !== 'All') {
      if (filters.sortBy === 'Listed Only') {
        filtered = filtered.filter(l => l.is_listed === true)
      } else if (filters.sortBy === 'Unlisted Only') {
        filtered = filtered.filter(l => l.is_listed === false || l.is_listed === null)
      }
    }

    setFilteredLenders(filtered)
  }

  // Handle filter changes
  const handleFilterChange = (key: string, value: string) => {
    setFilters(prev => ({
      ...prev,
      [key]: value
    }))
  }

  // Extract unique values for filters
  const loanTypes = ['All Loan Types', ...Array.from(
    new Set(lenders.flatMap(l => l.primary_loan_segments || []))
  ).sort()]
  
  const states = ['All States', ...Array.from(
    new Set(lenders.flatMap(l => l.operating_states || []))
  ).sort()]

  const ticketSizes = ['All Sizes', 'Micro', 'Small', 'Mid', 'Large']

  const companyTypes = ['All Types', ...Array.from(
    new Set(lenders.map(l => l.company_type).filter(Boolean))
  ).sort()]

  const listingStatus = ['All', 'Listed Only', 'Unlisted Only']

  if (authLoading || !user) {
    return (
      <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-[#3B5CCC]"></div>
      </div>
    )
  }

  // Transform Supabase data to match LenderCard component format
  const transformedLenders = filteredLenders.map(lender => ({
    id: lender.id.toString(),
    name: lender.company_name,
    city: lender.hq_location?.split(',')[0]?.trim() || lender.hq_state || 'N/A',
    state: lender.hq_state || 'N/A',
    companyType: lender.company_type,
    aum: lender.aum_crores ? `₹${lender.aum_crores.toLocaleString()} Cr` : 'N/A',
    established: lender.established_year?.toString() || 'N/A',
    ticketSize: lender.aum_category || 'N/A',
    products: lender.primary_loan_segments || [],
    operatingStates: lender.operating_states || [],
    headquarters: lender.hq_location || lender.hq_state || 'N/A',
    employees: lender.employee_count?.toLocaleString() || null,
    phone: lender.phone || null,
    email: lender.email || null,
    website: lender.website || null
  }))

  return (
    <div className="min-h-screen flex flex-col bg-gradient-to-b from-gray-50 to-white">
      <Navbar 
        authenticated={true} 
        user={user}
        onSignOut={signOut}
      />
      
      <Hero />
      
      <SearchFilter
        filters={filters}
        onFilterChange={handleFilterChange}
        resultsCount={filteredLenders.length}
        loanTypes={loanTypes}
        states={states}
        ticketSizes={ticketSizes}
        companyTypes={companyTypes}
        listingStatus={listingStatus}
      />

      {/* Results Section */}
      <section className="relative flex-1 py-12 bg-gradient-to-b from-gray-50 to-white">
        <div className="max-w-7xl mx-auto px-8">
          {loading ? (
            <div className="text-center py-12">
              <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-[#3B5CCC]"></div>
              <p className="mt-4 text-gray-600">Loading lenders...</p>
            </div>
          ) : transformedLenders.length > 0 ? (
            <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
              {transformedLenders.map((lender, index) => (
                <LenderCard key={lender.id} lender={lender} index={index} />
              ))}
            </div>
          ) : (
            <div className="text-center py-12 bg-white rounded-2xl shadow-md shadow-gray-200/50 border border-[#E5E7EB]">
              <p className="text-gray-600 mb-4">No lenders found matching your criteria.</p>
              <button
                onClick={() => setFilters({
                  search: '',
                  loanType: 'All Loan Types',
                  state: 'All States',
                  ticketSize: 'All Sizes',
                  companyType: 'All Types',
                  sortBy: 'All'
                })}
                className="px-6 py-2.5 bg-[#3B5CCC] text-white rounded-xl hover:bg-[#2d4aa8] transition-colors font-medium"
              >
                Reset Filters
              </button>
            </div>
          )}
        </div>
      </section>

      <StatsSection totalLenders={lenders.length} />
      
      <Footer />
    </div>
  )
}