'use client'

import { useState } from 'react'
import Link from 'next/link'
import { Search, ArrowLeft, BadgeCheck, AlertCircle, Building2, ExternalLink } from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

interface LenderResult {
  id: number
  company_name: string
  company_type: string
  rbi_category: string | null
  cin: string | null
  company_status: string | null
  established_year: number | null
  aum_crores: number | null
  aum_category: string | null
  hq_state: string | null
  is_listed: boolean
  website: string | null
}

export default function VerifyPage() {
  const [query, setQuery]     = useState('')
  const [results, setResults] = useState<LenderResult[]>([])
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!query.trim()) return
    setLoading(true)
    setSearched(true)
    try {
      const res = await fetch(`${API_URL}/v1/lenders/search?q=${encodeURIComponent(query.trim())}&limit=10`)
      const data = await res.json()
      setResults(data.results ?? [])
    } catch {
      setResults([])
    } finally {
      setLoading(false)
    }
  }

  const statusColor = (status: string | null) => {
    if (!status) return { bg: '#F3F4F6', color: '#6B7280' }
    const s = status.toLowerCase()
    if (s === 'active') return { bg: '#F0FDF4', color: '#16A34A' }
    if (s.includes('strike') || s.includes('dissolv')) return { bg: '#FEF2F2', color: '#DC2626' }
    return { bg: '#FFFBEB', color: '#D97706' }
  }

  return (
    <div className="min-h-screen" style={{ background: '#F7FAFA', fontFamily: 'Inter, sans-serif' }}>
      <div className="max-w-3xl mx-auto px-6 py-10">

        <Link href="/" className="inline-flex items-center gap-2 text-sm mb-8 transition-colors"
              style={{ color: '#1A7070' }}>
          <ArrowLeft className="w-4 h-4" /> Home
        </Link>

        <div className="mb-8">
          <div className="inline-flex items-center gap-2 text-xs font-semibold px-3 py-1.5 rounded-full mb-4"
               style={{ background: '#E6F4F4', color: '#1A7070' }}>
            <BadgeCheck className="w-3.5 h-3.5" /> Free Verification Tool
          </div>
          <h1 className="text-2xl font-extrabold mb-2" style={{ color: '#0F4848' }}>
            Is This Lender Legitimate?
          </h1>
          <p className="text-sm" style={{ color: '#7A9E9E' }}>
            Search any NBFC or bank by name to verify their RBI registration, MCA21 status, CIN, and basic profile. No login required.
          </p>
        </div>

        <form onSubmit={handleSearch} className="flex gap-3 mb-8">
          <div className="relative flex-1">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="e.g. Bajaj Finance, Muthoot, SBI..."
              className="w-full pl-11 pr-4 py-3 rounded-xl border text-sm focus:outline-none focus:ring-2 focus:ring-[#1A7070]/20 focus:border-[#1A7070]"
              style={{ borderColor: '#D8EBEB', background: 'white' }}
            />
          </div>
          <button type="submit" disabled={loading}
                  className="px-5 py-3 rounded-xl text-sm font-semibold text-white transition-all disabled:opacity-50"
                  style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)' }}>
            {loading ? 'Searching…' : 'Verify'}
          </button>
        </form>

        {searched && !loading && results.length === 0 && (
          <div className="flex items-center gap-3 p-4 rounded-xl border"
               style={{ background: '#FEF2F2', borderColor: '#FECACA' }}>
            <AlertCircle className="w-5 h-5 flex-shrink-0" style={{ color: '#DC2626' }} />
            <div>
              <p className="text-sm font-semibold" style={{ color: '#991B1B' }}>Not found in our database</p>
              <p className="text-xs mt-0.5" style={{ color: '#B91C1C' }}>
                This lender may not be RBI-registered or may be too new. Verify directly at rbi.org.in.
              </p>
            </div>
          </div>
        )}

        <div className="flex flex-col gap-4">
          {results.map(l => {
            const sc = statusColor(l.company_status)
            return (
              <div key={l.id} className="bg-white rounded-2xl p-5 border"
                   style={{ borderColor: '#E6F4F4', boxShadow: '0 2px 8px rgba(26,112,112,0.06)' }}>

                <div className="flex items-start justify-between gap-4 mb-4">
                  <div>
                    <h2 className="font-bold text-base" style={{ color: '#0F4848' }}>{l.company_name}</h2>
                    <p className="text-xs mt-0.5" style={{ color: '#7A9E9E' }}>{l.company_type}</p>
                  </div>
                  <span className="text-xs font-semibold px-2.5 py-1 rounded-full flex-shrink-0"
                        style={{ background: sc.bg, color: sc.color }}>
                    {l.company_status ?? 'Status unknown'}
                  </span>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-4">
                  {[
                    { label: 'RBI Category', value: l.rbi_category },
                    { label: 'CIN', value: l.cin },
                    { label: 'Established', value: l.established_year?.toString() },
                    { label: 'HQ State', value: l.hq_state },
                    { label: 'AUM', value: l.aum_category },
                    { label: 'Listed', value: l.is_listed ? 'Yes' : 'No' },
                  ].map(({ label, value }) => (
                    <div key={label} className="rounded-lg p-2.5" style={{ background: '#F7FAFA' }}>
                      <p className="text-[10px] font-semibold uppercase tracking-wide mb-0.5" style={{ color: '#7A9E9E' }}>{label}</p>
                      <p className="text-xs font-medium" style={{ color: value ? '#0F4848' : '#CBD5E1' }}>
                        {value ?? '—'}
                      </p>
                    </div>
                  ))}
                </div>

                <div className="flex items-center gap-3">
                  <Link href={`/lender/${l.id}`}
                        className="text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors"
                        style={{ background: '#E6F4F4', color: '#1A7070' }}>
                    Full Profile
                  </Link>
                  {l.website && (
                    <a href={l.website} target="_blank" rel="noopener noreferrer"
                       className="inline-flex items-center gap-1 text-xs font-medium transition-colors"
                       style={{ color: '#7A9E9E' }}>
                      <ExternalLink className="w-3.5 h-3.5" /> Website
                    </a>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        {!searched && (
          <div className="mt-8 rounded-2xl p-5 border" style={{ background: 'white', borderColor: '#E6F4F4' }}>
            <div className="flex items-center gap-2 mb-3">
              <Building2 className="w-4 h-4" style={{ color: '#1A7070' }} />
              <p className="text-sm font-semibold" style={{ color: '#0F4848' }}>What you can verify</p>
            </div>
            <ul className="space-y-1.5">
              {[
                'RBI registration category (NBFC, Bank, NBFC-MFI etc.)',
                'MCA21 company status — Active, Struck Off, Dissolved',
                'CIN (Corporate Identity Number)',
                'Headquarters state and established year',
                'Stock listing status',
              ].map(item => (
                <li key={item} className="flex items-center gap-2 text-xs" style={{ color: '#7A9E9E' }}>
                  <span style={{ color: '#1A7070' }}>✓</span> {item}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}
