'use client'

import { useEffect, useState } from 'react'
import { useAuth } from './components/AuthContext'
import Link from 'next/link'
import Image from 'next/image'
import { Search, Building2, ArrowRight, BadgeCheck, Filter, ExternalLink } from 'lucide-react'

// ─────────────────────────────────────────────────────────────
// TYPES
// ─────────────────────────────────────────────────────────────

interface PlatformStats {
  total_lenders:  number
  total_policies: number
  states_covered: number
  company_types:  number
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

// ─────────────────────────────────────────────────────────────
// COMPONENT
// ─────────────────────────────────────────────────────────────

export default function LandingPage() {
  const { user, loading: authLoading } = useAuth()

  const [stats, setStats] = useState<PlatformStats | null>(null)

  useEffect(() => {
    fetch(`${API_URL}/v1/lenders/stats`)
      .then(r => r.ok ? r.json() : null)
      .then((d: PlatformStats | null) => { if (d) setStats(d) })
      .catch(() => {})
  }, [])

  if (authLoading) {
    return (
      <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-[#3B5CCC]" />
      </div>
    )
  }

  const lenderCount = stats?.total_lenders ?? null

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white">

      {/* ── Nav ───────────────────────────────────────────────── */}
      <nav className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-6 sm:px-8 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="h-8 w-8 relative">
              <Image src="/logo.png" alt="MITRAM360" width={32} height={32} className="rounded-lg" />
            </div>
            <span className="font-semibold text-xl">MITRAM360</span>
          </div>
          <div className="flex items-center gap-3 sm:gap-5">
            {user ? (
              <>
                <Link href="/dashboard" className="text-sm text-gray-600 hover:text-gray-900 transition-colors hidden sm:block">
                  Browse Lenders
                </Link>
                <Link href="/dashboard" className="bg-[#3B5CCC] text-white px-5 py-2 rounded-xl text-sm font-medium hover:bg-[#2d4aa8] transition-colors">
                  Dashboard
                </Link>
              </>
            ) : (
              <>
                <Link href="/dashboard" className="text-sm text-gray-600 hover:text-gray-900 transition-colors hidden sm:block">
                  Browse Lenders
                </Link>
                <Link href="/login"    className="text-sm text-gray-700 hover:text-gray-900 transition-colors">
                  Login
                </Link>
                <Link href="/signup"   className="bg-[#3B5CCC] text-white px-5 py-2 rounded-xl text-sm font-medium hover:bg-[#2d4aa8] transition-colors">
                  Sign Up
                </Link>
              </>
            )}
          </div>
        </div>
      </nav>

      {/* ── Hero: borrower-first ───────────────────────────────── */}
      <section className="max-w-7xl mx-auto px-6 sm:px-8 py-16 sm:py-20">
        <div className="grid md:grid-cols-2 gap-12 lg:gap-20 items-center">

          {/* Left — copy + CTA */}
          <div>
            <div className="inline-flex items-center gap-2 bg-blue-50 text-[#3B5CCC] text-xs font-semibold
                            px-3 py-1.5 rounded-full border border-blue-100 mb-5">
              <BadgeCheck className="w-3.5 h-3.5" />
              {lenderCount
                ? `${lenderCount.toLocaleString('en-IN')} verified lenders`
                : 'RBI-registered lenders'}
            </div>

            <h1 className="text-4xl sm:text-5xl font-bold text-gray-900 leading-tight mb-5">
              India&apos;s most complete{' '}
              <span className="text-[#3B5CCC]">NBFC &amp; bank</span>
              {' '}directory
            </h1>

            <p className="text-lg text-gray-600 leading-relaxed mb-8">
              Search and filter{' '}
              {lenderCount ? `${lenderCount.toLocaleString('en-IN')}` : 'hundreds of'}{' '}
              RBI-registered NBFCs and banks by loan type, state, company size, and more.
              Free to use.
            </p>

            <div className="flex flex-wrap gap-3">
              <Link
                href="/dashboard"
                className="inline-flex items-center gap-2 bg-[#3B5CCC] text-white
                           px-8 py-3.5 rounded-xl text-base font-semibold
                           hover:bg-[#2d4aa8] transition-all hover:shadow-lg
                           hover:shadow-[#3B5CCC]/25 hover:scale-[1.02]"
              >
                <Search className="w-4 h-4" />
                Browse Lenders
                <ArrowRight className="w-4 h-4" />
              </Link>
            </div>

            <p className="text-xs text-gray-400 mt-4">
              Filter by loan type · state · AUM size · company type
            </p>
          </div>

          {/* Right — hero image */}
          <div className="relative hidden md:block">
            <div className="w-full rounded-2xl shadow-xl overflow-hidden border border-[#E5E7EB] bg-white">
              <Image
                src="/hero-image.jpg"
                alt="Financial consultation"
                width={800}
                height={600}
                className="w-full h-auto object-cover"
                priority
              />
            </div>
          </div>
        </div>
      </section>

      {/* ── Live stats bar ─────────────────────────────────────── */}
      <section className="bg-white border-y border-gray-100 py-10">
        <div className="max-w-5xl mx-auto px-6 sm:px-8">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 text-center">
            <div>
              <div className="text-3xl sm:text-4xl font-bold text-[#3B5CCC] mb-1">
                {lenderCount !== null ? lenderCount.toLocaleString('en-IN') : '—'}
              </div>
              <div className="text-sm text-gray-500">Verified Lenders</div>
            </div>
            <div>
              <div className="text-3xl sm:text-4xl font-bold text-[#3B5CCC] mb-1">
                {stats?.states_covered ?? '—'}
              </div>
              <div className="text-sm text-gray-500">States Covered</div>
            </div>
            <div>
              <div className="text-3xl sm:text-4xl font-bold text-[#3B5CCC] mb-1">
                {stats?.total_policies !== undefined
                  ? stats.total_policies > 0
                    ? stats.total_policies.toLocaleString('en-IN')
                    : '—'
                  : '—'}
              </div>
              <div className="text-sm text-gray-500">Loan Policies</div>
            </div>
            <div>
              <div className="text-3xl sm:text-4xl font-bold text-[#3B5CCC] mb-1">100%</div>
              <div className="text-sm text-gray-500">Free to Use</div>
            </div>
          </div>
        </div>
      </section>

      {/* ── How it works ──────────────────────────────────────── */}
      <section className="py-16 sm:py-20">
        <div className="max-w-5xl mx-auto px-6 sm:px-8">
          <h2 className="text-2xl sm:text-3xl font-bold text-gray-900 text-center mb-12">
            How it works
          </h2>

          <div className="grid md:grid-cols-3 gap-8">
            <div className="text-center">
              <div className="w-14 h-14 bg-blue-50 rounded-2xl flex items-center justify-center mx-auto mb-4">
                <Search className="w-6 h-6 text-[#3B5CCC]" />
              </div>
              <h3 className="font-semibold text-gray-900 mb-2">1. Search &amp; filter</h3>
              <p className="text-sm text-gray-500 leading-relaxed">
                Filter by loan type, state, AUM size, company type, and more — instantly.
              </p>
            </div>

            <div className="text-center">
              <div className="w-14 h-14 bg-blue-50 rounded-2xl flex items-center justify-center mx-auto mb-4">
                <Filter className="w-6 h-6 text-[#3B5CCC]" />
              </div>
              <h3 className="font-semibold text-gray-900 mb-2">2. Compare lenders</h3>
              <p className="text-sm text-gray-500 leading-relaxed">
                See contact info, operating states, loan products, and company details side by side.
              </p>
            </div>

            <div className="text-center">
              <div className="w-14 h-14 bg-blue-50 rounded-2xl flex items-center justify-center mx-auto mb-4">
                <ExternalLink className="w-6 h-6 text-[#3B5CCC]" />
              </div>
              <h3 className="font-semibold text-gray-900 mb-2">3. Go direct</h3>
              <p className="text-sm text-gray-500 leading-relaxed">
                One click to the lender&apos;s website. No middlemen, no commission, no credit hit.
              </p>
            </div>
          </div>

          <div className="text-center mt-10">
            <Link
              href="/dashboard"
              className="inline-flex items-center gap-2 bg-[#3B5CCC] text-white px-8 py-3.5
                         rounded-xl text-base font-semibold hover:bg-[#2d4aa8] transition-all
                         hover:shadow-lg hover:shadow-[#3B5CCC]/25 hover:scale-[1.02]"
            >
              Browse Lenders — It&apos;s Free
              <ArrowRight className="w-5 h-5" />
            </Link>
          </div>
        </div>
      </section>

      {/* ── Footer ────────────────────────────────────────────── */}
      <footer className="bg-white border-t border-gray-200">
        <div className="max-w-7xl mx-auto px-8 py-8">
          <p className="text-center text-sm text-gray-500">
            © 2026 MITRAM360. All rights reserved.
          </p>
        </div>
      </footer>
    </div>
  )
}
