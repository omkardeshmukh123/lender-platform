'use client'

import Link from 'next/link'
import Image from 'next/image'
import { useState, useEffect, useRef } from 'react'
import { User, LogOut, Bookmark } from 'lucide-react'

interface NavbarProps {
  authenticated?: boolean
  user?: { email?: string; access_token?: string } | null
  onSignOut?: () => void
  savedCount?: number
  onSavedClick?: () => void
}

export function Navbar({ authenticated = false, user, onSignOut, savedCount = 0, onSavedClick }: NavbarProps) {
  const [isScrolled,     setIsScrolled]     = useState(false)
  const [isDropdownOpen, setIsDropdownOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleScroll = () => setIsScrolled(window.scrollY > 12)
    window.addEventListener('scroll', handleScroll, { passive: true })
    return () => window.removeEventListener('scroll', handleScroll)
  }, [])

  useEffect(() => {
    if (!isDropdownOpen) return
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node))
        setIsDropdownOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [isDropdownOpen])

  return (
    <nav
      className={`sticky top-0 z-50 transition-all duration-300 ${
        isScrolled
          ? 'bg-white/96 backdrop-blur-xl shadow-sm'
          : 'bg-white/85 backdrop-blur-md'
      }`}
      style={{ borderBottom: '1px solid rgba(26,112,112,0.12)' }}
    >
      <div className="max-w-7xl mx-auto px-6 sm:px-8">
        <div className="flex items-center justify-between h-16 gap-4">

          {/* ── Logo ── */}
          <Link href="/dashboard" className="flex items-center gap-3 flex-shrink-0 group">
            <div className="relative w-10 h-10 transition-transform duration-200 group-hover:scale-105">
              <Image
                src="/logo.png"
                alt="MITRAM360 — Phygital Lending Platform"
                width={40}
                height={40}
                className="rounded-full object-cover"
                priority
              />
            </div>
            <div className="hidden sm:block">
              <span
                className="font-extrabold text-lg tracking-tight leading-none"
                style={{ color: '#0F4848' }}
              >
                MITRAM360
              </span>
              <p className="text-[9px] font-semibold uppercase tracking-widest leading-none mt-0.5"
                 style={{ color: '#C9A227' }}>
                Phygital Lending Platform
              </p>
            </div>
          </Link>

          {/* ── Center nav (desktop) ── */}
          <div className="hidden md:flex items-center gap-7 text-sm font-medium"
               style={{ color: '#3D6363' }}>
            <Link href="/dashboard"
              className="hover:text-[#1A7070] transition-colors">
              Browse
            </Link>
          </div>

          {/* ── Right actions ── */}
          <div className="flex items-center gap-2">
            {authenticated && user ? (
              <>
                {onSavedClick && (
                  <button
                    onClick={onSavedClick}
                    title="Shortlisted lenders"
                    className="relative p-2 rounded-lg transition-all duration-200"
                    style={{ color: '#7A9E9E' }}
                    onMouseEnter={e => {
                      (e.currentTarget as HTMLButtonElement).style.color = '#1A7070'
                      ;(e.currentTarget as HTMLButtonElement).style.background = '#E6F4F4'
                    }}
                    onMouseLeave={e => {
                      (e.currentTarget as HTMLButtonElement).style.color = '#7A9E9E'
                      ;(e.currentTarget as HTMLButtonElement).style.background = 'transparent'
                    }}
                  >
                    <Bookmark className="w-5 h-5" />
                    {savedCount > 0 && (
                      <span className="absolute -top-0.5 -right-0.5 w-4 h-4 text-[10px] font-bold
                                       rounded-full flex items-center justify-center text-white leading-none"
                            style={{ background: 'linear-gradient(135deg,#C9A227,#A07E1A)' }}>
                        {savedCount > 9 ? '9+' : savedCount}
                      </span>
                    )}
                  </button>
                )}

                {/* User dropdown */}
                <div className="relative" ref={dropdownRef}>
                  <button
                    onClick={() => setIsDropdownOpen(p => !p)}
                    className="w-9 h-9 rounded-full flex items-center justify-center
                               border transition-all duration-200"
                    style={{
                      borderColor: '#D8EBEB',
                      color: '#3D6363',
                      background: '#F7FAFA',
                    }}
                  >
                    <User className="w-4 h-4" />
                  </button>

                  {isDropdownOpen && (
                    <div className="absolute right-0 mt-2 w-52 bg-white rounded-xl overflow-hidden
                                    animate-fade-in"
                         style={{ border: '1px solid #D8EBEB', boxShadow: '0 12px 32px rgba(26,112,112,0.14)' }}>
                      <div className="px-4 py-3 border-b"
                           style={{ background: '#F7FAFA', borderColor: '#EFF7F7' }}>
                        <p className="text-[11px] font-semibold uppercase tracking-wide"
                           style={{ color: '#7A9E9E' }}>Signed in as</p>
                        <p className="text-sm font-semibold truncate mt-0.5"
                           style={{ color: '#0F4848' }}>{user.email}</p>
                      </div>
                      <div className="py-1.5">
                        <button
                          onClick={() => { setIsDropdownOpen(false); onSignOut?.() }}
                          className="w-full px-4 py-2.5 text-left text-sm flex items-center gap-2.5
                                     transition-colors hover:bg-red-50 text-red-500"
                        >
                          <LogOut className="w-4 h-4" />
                          Sign Out
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </>
            ) : (
              <>
                <Link href="/login"
                  className="px-4 py-1.5 text-sm font-medium transition-colors hidden sm:block"
                  style={{ color: '#3D6363' }}
                  onMouseEnter={e => (e.currentTarget.style.color = '#1A7070')}
                  onMouseLeave={e => (e.currentTarget.style.color = '#3D6363')}>
                  Login
                </Link>
                <Link href="/signup"
                  className="px-4 py-2 text-sm font-semibold text-white rounded-xl
                             transition-all hover:-translate-y-0.5"
                  style={{
                    background: 'linear-gradient(135deg,#0F4848,#1A7070)',
                    boxShadow: '0 2px 8px rgba(26,112,112,0.25)',
                  }}>
                  Get Started
                </Link>
              </>
            )}
          </div>

        </div>
      </div>
    </nav>
  )
}