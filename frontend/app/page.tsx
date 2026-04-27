'use client'

import { useEffect, useState } from 'react'
import { useAuth } from './components/AuthContext'
import Link from 'next/link'
import Image from 'next/image'
import {
  Search, ArrowRight, Sparkles,
  SlidersHorizontal, GitCompare, Shield, Zap, Globe, Handshake,
} from 'lucide-react'

interface PlatformStats {
  total_lenders:  number
  total_policies: number
  states_covered: number
  company_types:  number
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

function AnimatedCounter({ target, duration = 1800 }: { target: number; duration?: number }) {
  const [count, setCount] = useState(0)
  useEffect(() => {
    if (!target) return
    let start = 0
    const step = target / (duration / 16)
    const timer = setInterval(() => {
      start += step
      if (start >= target) { setCount(target); clearInterval(timer) }
      else setCount(Math.floor(start))
    }, 16)
    return () => clearInterval(timer)
  }, [target, duration])
  return <>{count.toLocaleString('en-IN')}</>
}

export default function LandingPage() {
  const { user, loading: authLoading } = useAuth()
  const [stats, setStats] = useState<PlatformStats | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    fetch(`${API_URL}/v1/lenders/stats`)
      .then(r => r.ok ? r.json() : null)
      .then((d: PlatformStats | null) => { if (d) setStats(d) })
      .catch(() => {})
    const t = setTimeout(() => setLoaded(true), 80)
    return () => clearTimeout(t)
  }, [])

  if (authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: '#F7FAFA' }}>
        <div className="flex flex-col items-center gap-3">
          <div className="w-10 h-10 rounded-full border-2 border-t-transparent animate-spin"
               style={{ borderColor: '#1A7070', borderTopColor: 'transparent' }} />
          <p className="text-sm" style={{ color: '#7A9E9E' }}>Loading…</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen" style={{ fontFamily: 'Inter, sans-serif', background: '#F7FAFA' }}>

      {/* ── Navbar ──────────────────────────────────────────────── */}
      <nav className="sticky top-0 z-50 bg-white/95 backdrop-blur-xl"
           style={{ borderBottom: '1px solid rgba(26,112,112,0.12)', boxShadow: '0 1px 4px rgba(26,112,112,0.06)' }}>
        <div className="max-w-7xl mx-auto px-6 sm:px-8 py-3 flex items-center justify-between">
          {/* Logo */}
          <Link href="/" className="flex items-center gap-3 group">
            <div className="w-10 h-10 transition-transform duration-200 group-hover:scale-105">
              <Image src="/logo.png" alt="MITRAM360" width={40} height={40}
                     className="rounded-full object-cover" priority />
            </div>
            <div className="hidden sm:block">
              <p className="font-extrabold text-lg leading-tight tracking-tight"
                 style={{ color: '#0F4848' }}>MITRAM360</p>
              <p className="text-[9px] font-semibold uppercase tracking-widest"
                 style={{ color: '#C9A227' }}>Phygital Lending Platform</p>
            </div>
          </Link>

          {/* Nav links */}
          <div className="hidden md:flex items-center gap-7 text-sm font-medium"
               style={{ color: '#3D6363' }}>
            <Link href={user ? '/dashboard' : '/login'} className="hover:text-[#1A7070] transition-colors">Browse Lenders</Link>
            <a href="#how-it-works" className="hover:text-[#1A7070] transition-colors">How It Works</a>
          </div>

          {/* Auth buttons */}
          <div className="flex items-center gap-3">
            {user ? (
              <Link href="/dashboard"
                className="px-5 py-2 rounded-xl text-sm font-semibold text-white
                           transition-all hover:-translate-y-0.5 hover:shadow-lg"
                style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)', boxShadow: '0 2px 8px rgba(26,112,112,0.25)' }}>
                Dashboard
              </Link>
            ) : (
              <>
                <Link href="/login"
                  className="text-sm font-medium hidden sm:block transition-colors"
                  style={{ color: '#3D6363' }}>
                  Login
                </Link>
                <Link href="/signup"
                  className="px-5 py-2 rounded-xl text-sm font-semibold text-white
                             transition-all hover:-translate-y-0.5 hover:shadow-lg"
                  style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)', boxShadow: '0 2px 8px rgba(26,112,112,0.25)' }}>
                  Get Started
                </Link>
              </>
            )}
          </div>
        </div>
      </nav>

      {/* ── Hero ────────────────────────────────────────────────── */}
      <section className="relative overflow-hidden"
               style={{ background: 'linear-gradient(160deg, #082E2E 0%, #0F4848 42%, #1A7070 100%)' }}>
        {/* Subtle grid */}
        <div className="absolute inset-0 opacity-[0.04]"
             style={{ backgroundImage: 'linear-gradient(rgba(255,255,255,0.3) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.3) 1px, transparent 1px)', backgroundSize: '48px 48px' }} />

        {/* Gold accent glow top-right */}
        <div className="absolute top-0 right-0 w-96 h-96 rounded-full opacity-10 pointer-events-none"
             style={{ background: 'radial-gradient(circle, #C9A227 0%, transparent 70%)', transform: 'translate(30%, -30%)' }} />

        <div className="relative max-w-7xl mx-auto px-6 sm:px-8 py-20 sm:py-28">
          <div className="grid md:grid-cols-2 gap-14 items-center">

            {/* Left copy */}
            <div className={loaded ? 'animate-fade-in-up' : 'opacity-0'}>
              {/* Tagline badge */}
              <div className="inline-flex items-center gap-2 text-xs font-semibold px-4 py-1.5
                              rounded-full border mb-6"
                   style={{ background: 'rgba(201,162,39,0.15)', borderColor: 'rgba(201,162,39,0.3)', color: '#F5D97A' }}>
                <Handshake className="w-3.5 h-3.5" />
                Empowering Bharat · Phygital Lending Platform
              </div>

              <h1 className="font-extrabold text-white leading-[1.1] mb-6 tracking-tight"
                  style={{ fontSize: 'clamp(2.25rem,5vw,3.75rem)' }}>
                India&apos;s Most Complete{' '}
                <span className="text-transparent bg-clip-text"
                      style={{ backgroundImage: 'linear-gradient(90deg, #F5D97A, #C9A227)' }}>
                  NBFC &amp; Bank
                </span>{' '}
                Directory
              </h1>

              <p className="text-lg leading-relaxed mb-8 max-w-xl"
                 style={{ color: 'rgba(255,255,255,0.68)' }}>
                Instantly search and compare{' '}
                <span className="text-white font-semibold">1,000+ lenders</span> by loan type,
                state, AUM, eligibility criteria, and more. No middlemen. Free forever.
              </p>

              <div className="flex flex-wrap gap-3 mb-6">
                <Link href={user ? '/dashboard' : '/login'}
                  className="inline-flex items-center gap-2 px-7 py-3.5 rounded-xl text-base font-semibold
                             text-white transition-all hover:-translate-y-0.5 hover:shadow-xl"
                  style={{ background: 'linear-gradient(135deg,#C9A227,#A07E1A)', boxShadow: '0 4px 14px rgba(201,162,39,0.4)' }}>
                  <Search className="w-4 h-4" />
                  Browse Lenders
                  <ArrowRight className="w-4 h-4" />
                </Link>
                <Link href={user ? '/dashboard' : '/signup'}
                  className="inline-flex items-center gap-2 px-7 py-3.5 rounded-xl text-base font-semibold
                             transition-all hover:bg-white/10 hover:-translate-y-0.5"
                  style={{ color: 'white', border: '1px solid rgba(255,255,255,0.22)', backdropFilter: 'blur(8px)' }}>
                  <Sparkles className="w-4 h-4" style={{ color: '#F5D97A' }} />
                  Try AI Match
                </Link>
              </div>

              <p className="text-xs" style={{ color: 'rgba(255,255,255,0.35)' }}>
                Filter by loan type · state · AUM · company type · eligibility
              </p>
            </div>

            {/* Right — logo + stats widget */}
            <div className={`hidden md:flex flex-col items-center gap-6 ${loaded ? 'animate-slide-in-right' : 'opacity-0'}`}
                 style={{ animationDelay: '150ms' }}>
              {/* Glowing logo display */}
              <div className="relative">
                <div className="absolute inset-0 rounded-full opacity-30 animate-pulse-glow-gold"
                     style={{ background: 'radial-gradient(circle, #C9A227 0%, transparent 60%)', filter: 'blur(20px)' }} />
                <div className="relative w-40 h-40 rounded-full p-1"
                     style={{ background: 'linear-gradient(135deg,#C9A227,#F5D97A,#C9A227)', boxShadow: '0 0 40px rgba(201,162,39,0.4)' }}>
                  <div className="w-full h-full rounded-full overflow-hidden">
                    <Image src="/logo.png" alt="MITRAM360" width={152} height={152}
                           className="w-full h-full object-cover" />
                  </div>
                </div>
              </div>

              {/* Stats card */}
              <div className="w-full rounded-2xl p-5 border"
                   style={{ background: 'rgba(255,255,255,0.07)', backdropFilter: 'blur(20px)', borderColor: 'rgba(255,255,255,0.12)' }}>
                <p className="text-xs font-semibold uppercase tracking-widest mb-4"
                   style={{ color: 'rgba(255,255,255,0.45)' }}>Live Platform Stats</p>
                <div className="grid grid-cols-2 gap-3">
                  {[
                    { label: 'NBFCs',      value: stats ? Math.floor(stats.total_lenders * 0.84) : 934 },
                    { label: 'Banks',      value: 177 },
                    { label: 'States',     value: stats?.states_covered ?? 28 },
                    { label: 'Loan Types', value: 18 },
                  ].map(({ label, value }) => (
                    <div key={label} className="rounded-xl p-3"
                         style={{ background: 'rgba(255,255,255,0.06)' }}>
                      <div className="text-2xl font-extrabold text-white mb-0.5">
                        <AnimatedCounter target={value} />
                      </div>
                      <div className="text-xs font-medium" style={{ color: 'rgba(255,255,255,0.45)' }}>{label}</div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Trust chips */}
              <div className="flex flex-wrap gap-2 justify-center">
                {['RBI Verified', 'AI-Powered', 'Free Forever', 'Pan India'].map(t => (
                  <span key={t} className="px-3 py-1 rounded-full text-xs font-medium border"
                        style={{ background: 'rgba(201,162,39,0.12)', borderColor: 'rgba(201,162,39,0.25)', color: '#F5D97A' }}>
                    ✓ {t}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Wave divider */}
        <div className="absolute bottom-0 left-0 right-0">
          <svg viewBox="0 0 1440 60" preserveAspectRatio="none" className="w-full h-10 sm:h-14 block"
               fill="#F7FAFA">
            <path d="M0,40 C360,0 1080,60 1440,20 L1440,60 L0,60 Z" />
          </svg>
        </div>
      </section>

      {/* ── Stats Bar ───────────────────────────────────────────── */}
      <section className="bg-white" style={{ borderBottom: '1px solid #E6F4F4' }}>
        <div className="max-w-5xl mx-auto px-6 sm:px-8 py-10">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 text-center">
            {[
              { value: stats?.total_lenders, label: 'Verified Lenders', suffix: '+' },
              { value: stats?.states_covered, label: 'States Covered', suffix: '' },
              { value: stats?.total_policies, label: 'Loan Policies', suffix: '+' },
              { value: null, label: 'Free to Use', display: '100%' },
            ].map(({ value, label, suffix, display }) => (
              <div key={label} className="animate-fade-in-up" style={{ animationDelay: '200ms' }}>
                <div className="text-3xl sm:text-4xl font-extrabold mb-1.5 text-transparent bg-clip-text"
                     style={{ backgroundImage: 'linear-gradient(135deg,#0F4848,#1A7070)' }}>
                  {display ?? (value != null
                    ? <>{<AnimatedCounter target={value} />}{suffix}</>
                    : '—')}
                </div>
                <div className="text-sm font-medium" style={{ color: '#7A9E9E' }}>{label}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── How It Works ────────────────────────────────────────── */}
      <section id="how-it-works" className="py-20 sm:py-24">
        <div className="max-w-5xl mx-auto px-6 sm:px-8">
          <div className="text-center mb-14">
            <p className="text-xs font-semibold uppercase tracking-widest mb-2"
               style={{ color: '#C9A227' }}>Simple Process</p>
            <h2 className="text-3xl sm:text-4xl font-extrabold tracking-tight"
                style={{ color: '#0F4848' }}>How It Works</h2>
          </div>

          <div className="grid md:grid-cols-3 gap-8">
            {[
              { num: '1', icon: Search, title: 'Search & Filter',
                body: 'Multi-filter by loan type, state, AUM size, company type, sector, and listing status — instantly.' },
              { num: '2', icon: GitCompare, title: 'Compare Side-by-Side',
                body: 'View lender profiles, loan policies, eligibility rules, and contact info. Use AI to compare any two.' },
              { num: '3', icon: Globe, title: 'Go Direct',
                body: 'One click to the lender\'s website. No middlemen, no commission, no credit inquiry.' },
            ].map(({ num, icon: Icon, title, body }, i) => (
              <div key={num} className="text-center group animate-fade-in-up"
                   style={{ animationDelay: `${i * 100}ms` }}>
                <div className="relative inline-flex w-16 h-16 rounded-2xl items-center
                                justify-center mb-5 transition-all duration-300 group-hover:-translate-y-1"
                     style={{ background: 'linear-gradient(135deg,#E6F4F4,#CCE9E9)' }}>
                  <Icon className="w-7 h-7" style={{ color: '#1A7070' }} />
                  <span className="absolute -top-2 -right-2 w-6 h-6 rounded-full text-[10px]
                                   font-bold text-white flex items-center justify-center"
                        style={{ background: 'linear-gradient(135deg,#C9A227,#A07E1A)' }}>
                    {num}
                  </span>
                </div>
                <h3 className="font-bold text-lg mb-2" style={{ color: '#0F4848' }}>{title}</h3>
                <p className="text-sm leading-relaxed max-w-xs mx-auto" style={{ color: '#7A9E9E' }}>{body}</p>
              </div>
            ))}
          </div>

          <div className="text-center mt-12">
            <Link href={user ? '/dashboard' : '/login'}
              className="inline-flex items-center gap-2 px-8 py-4 rounded-xl text-base font-semibold
                         text-white transition-all hover:-translate-y-0.5 hover:shadow-xl"
              style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)', boxShadow: '0 4px 14px rgba(26,112,112,0.3)' }}>
              Browse Lenders — It&apos;s Free
              <ArrowRight className="w-5 h-5" />
            </Link>
          </div>
        </div>
      </section>

      {/* ── Feature Highlights ──────────────────────────────────── */}
      <section className="py-20 sm:py-24 bg-white">
        <div className="max-w-5xl mx-auto px-6 sm:px-8">
          <div className="text-center mb-14">
            <p className="text-xs font-semibold uppercase tracking-widest mb-2"
               style={{ color: '#C9A227' }}>Built for Speed</p>
            <h2 className="text-3xl sm:text-4xl font-extrabold tracking-tight"
                style={{ color: '#0F4848' }}>Platform Features</h2>
          </div>

          <div className="grid md:grid-cols-3 gap-6">
            {[
              { icon: Sparkles, color: '#1A7070', bg: '#E6F4F4',
                title: 'AI-Powered Chat',
                body: 'Ask in plain English. "Show me NBFCs in Maharashtra for MSME loans" — filters, compares, and explains instantly.' },
              { icon: SlidersHorizontal, color: '#C9A227', bg: '#FDF8E4',
                title: 'Smart Multi-Filters',
                body: 'Filter by 9+ dimensions: loan type, state, AUM, company type, sector, listing status, and established year.' },
              { icon: GitCompare, color: '#1A7070', bg: '#E6F4F4',
                title: 'Side-by-Side Compare',
                body: 'Select any two lenders and get a structured comparison table, exportable as CSV for your analysis.' },
              { icon: Shield, color: '#A07E1A', bg: '#FDF8E4',
                title: 'Admin-Verified Data',
                body: 'Every lender goes through AI extraction + human review before going live. Only approved, quality-checked entries.' },
              { icon: Zap, color: '#1A7070', bg: '#E6F4F4',
                title: 'Real-Time Results',
                body: 'Redis-cached API returns results in under 100ms. Filters update the grid instantly — no page reloads.' },
              { icon: Globe, color: '#C9A227', bg: '#FDF8E4',
                title: 'Pan-India Coverage',
                body: '28 states covered, 18 loan product categories, 7 company types — from micro-NBFCs to PSU banks.' },
            ].map(({ icon: Icon, color, bg, title, body }) => (
              <div key={title}
                   className="rounded-2xl p-6 bg-white hover:-translate-y-1 transition-all duration-300 group"
                   style={{ border: '1px solid #E6F4F4', boxShadow: '0 2px 8px rgba(26,112,112,0.06)' }}
                   onMouseEnter={e => (e.currentTarget.style.boxShadow = '0 8px 24px rgba(26,112,112,0.14)')}
                   onMouseLeave={e => (e.currentTarget.style.boxShadow = '0 2px 8px rgba(26,112,112,0.06)')}>
                <div className="w-11 h-11 rounded-xl flex items-center justify-center mb-4
                                transition-transform duration-300 group-hover:scale-110"
                     style={{ background: bg }}>
                  <Icon className="w-5 h-5" style={{ color }} />
                </div>
                <h3 className="font-bold mb-2" style={{ color: '#0F4848' }}>{title}</h3>
                <p className="text-sm leading-relaxed" style={{ color: '#7A9E9E' }}>{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── CTA Banner ──────────────────────────────────────────── */}
      <section className="py-16 sm:py-20"
               style={{ background: 'linear-gradient(135deg, #082E2E 0%, #1A7070 100%)' }}>
        <div className="max-w-3xl mx-auto px-6 text-center">
          {/* Logo in CTA */}
          <div className="w-16 h-16 rounded-full mx-auto mb-6 p-0.5"
               style={{ background: 'linear-gradient(135deg,#C9A227,#F5D97A)' }}>
            <div className="w-full h-full rounded-full overflow-hidden">
              <Image src="/logo.png" alt="MITRAM360" width={60} height={60}
                     className="w-full h-full object-cover" />
            </div>
          </div>
          <h2 className="text-3xl sm:text-4xl font-extrabold text-white mb-4 tracking-tight">
            Find your lender in seconds
          </h2>
          <p className="text-lg mb-8" style={{ color: 'rgba(255,255,255,0.60)' }}>
            No registration required to browse. Sign up to save lenders and use AI.
          </p>
          <div className="flex flex-wrap gap-3 justify-center">
            <Link href={user ? '/dashboard' : '/login'}
              className="inline-flex items-center gap-2 px-8 py-4 rounded-xl text-base font-semibold
                         transition-all hover:-translate-y-0.5 hover:shadow-xl"
              style={{ background: 'linear-gradient(135deg,#C9A227,#A07E1A)', color: 'white', boxShadow: '0 4px 14px rgba(201,162,39,0.4)' }}>
              <Search className="w-4 h-4" />
              Browse All Lenders
            </Link>
            <Link href="/signup"
              className="inline-flex items-center gap-2 px-8 py-4 rounded-xl text-base font-semibold
                         text-white border hover:bg-white/10 transition-all"
              style={{ borderColor: 'rgba(255,255,255,0.22)' }}>
              Create Free Account
            </Link>
          </div>
        </div>
      </section>

      {/* ── Footer ──────────────────────────────────────────────── */}
      <footer style={{ background: '#051A1A' }}>
        <div className="max-w-7xl mx-auto px-8 py-10 flex flex-col sm:flex-row
                        items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full overflow-hidden">
              <Image src="/logo.png" alt="MITRAM360" width={32} height={32}
                     className="w-full h-full object-cover" />
            </div>
            <div>
              <p className="font-bold text-sm leading-tight" style={{ color: 'rgba(255,255,255,0.75)' }}>MITRAM360</p>
              <p className="text-[9px] uppercase tracking-widest" style={{ color: '#C9A227' }}>Empowering Bharat</p>
            </div>
          </div>
          <p className="text-sm" style={{ color: 'rgba(255,255,255,0.25)' }}>© 2026 MITRAM360. All rights reserved.</p>
          <div className="flex items-center gap-5 text-sm" style={{ color: 'rgba(255,255,255,0.35)' }}>
            <Link href="/dashboard" className="hover:text-white/70 transition-colors">Browse</Link>
            <Link href="/login"     className="hover:text-white/70 transition-colors">Login</Link>
            <Link href="/signup"    className="hover:text-white/70 transition-colors">Sign Up</Link>
          </div>
        </div>
      </footer>
    </div>
  )
}
