'use client'

import Link from 'next/link'
import { useState, useEffect, useRef } from 'react'
import { User, Settings, LogOut } from 'lucide-react'
import Image from 'next/image'

interface NavbarProps {
  authenticated?: boolean
  user?: any
  onSignOut?: () => void
}

export function Navbar({ authenticated = false, user, onSignOut }: NavbarProps) {
  const [isScrolled, setIsScrolled] = useState(false)
  const [isDropdownOpen, setIsDropdownOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 20)
    }

    window.addEventListener('scroll', handleScroll)
    return () => window.removeEventListener('scroll', handleScroll)
  }, [])

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false)
      }
    }

    if (isDropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isDropdownOpen])

  const handleSignOut = () => {
    setIsDropdownOpen(false)
    onSignOut?.()
  }

  return (
    <nav
      className={`sticky top-0 z-50 transition-all duration-300 ease-out ${
        isScrolled
          ? 'bg-white/75 backdrop-blur-xl border-b border-[#E5E7EB] shadow-sm'
          : 'bg-white/40 backdrop-blur-md border-b border-transparent'
      }`}
    >
      <div className="max-w-7xl mx-auto px-8">
        <div className="flex items-center justify-between h-16">
          {/* Left: Logo */}
          <Link href="/dashboard" className="flex items-center gap-3">
            {/* Logo Image */}
            <div className="h-8 w-8 relative">
              <Image
                src="/logo.png"
                alt="MITRAM360"
                width={32}
                height={32}
                className="rounded-lg"
              />
            </div>
            {/* CHANGED: font-semibold → font-bold, text-base → text-lg */}
            <span className="text-lg font-bold text-gray-900">MITRAM360</span>
          </Link>

          {/* Right: Profile or Auth buttons */}
          {authenticated && user ? (
            <div className="relative" ref={dropdownRef}>
              <button
                onClick={() => setIsDropdownOpen(!isDropdownOpen)}
                className="w-9 h-9 rounded-full bg-white flex items-center justify-center shadow-sm hover:shadow-md transition-all duration-200 border border-gray-100"
              >
                <User className="w-4 h-4 text-gray-600" />
              </button>

              {isDropdownOpen && (
                <div className="absolute right-0 mt-3 w-48 bg-white rounded-lg shadow-lg overflow-hidden">
                  <div className="px-4 py-3 border-b border-gray-100">
                    <p className="text-sm text-gray-500">Signed in as</p>
                    <p className="text-sm font-medium text-gray-900 truncate">{user.email}</p>
                  </div>

                  <div className="py-2">
                    <button
                      onClick={() => setIsDropdownOpen(false)}
                      className="w-full px-4 py-2.5 text-left text-sm text-gray-700 hover:bg-gray-50 transition-colors duration-150 flex items-center gap-3"
                    >
                      <Settings className="w-4 h-4 text-[#3B5CCC]" />
                      Settings
                    </button>
                    
                    <button
                      onClick={handleSignOut}
                      className="w-full px-4 py-2.5 text-left text-sm text-red-600 hover:bg-red-50 transition-colors duration-150 flex items-center gap-3"
                    >
                      <LogOut className="w-4 h-4 text-red-600" />
                      Sign Out
                    </button>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <Link
                href="/login"
                className="px-4 py-1.5 text-sm text-gray-700 hover:text-gray-900 transition-colors"
              >
                Login
              </Link>
              <Link
                href="/signup"
                className="px-4 py-1.5 text-sm font-medium bg-[#3B5CCC] text-white rounded-lg transition-all duration-300 ease-out hover:bg-[#2d4aa8] hover:shadow-lg hover:scale-[1.03]"
              >
                Sign Up
              </Link>
            </div>
          )}
        </div>
      </div>
    </nav>
  )
}