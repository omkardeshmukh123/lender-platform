'use client'

import { useRouter } from 'next/navigation'
import { useEffect } from 'react'
import { useAuth } from './components/AuthContext'
import Link from 'next/link'
import Image from 'next/image'
import { Search, Building2, Zap, ArrowRight } from 'lucide-react'

export default function LandingPage() {
  const router = useRouter()
  const { user, loading } = useAuth()

  // If already logged in, go to dashboard
  useEffect(() => {
    if (!loading && user) {
      router.push('/dashboard')
    }
  }, [user, loading, router])

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-[#3B5CCC]"></div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white">
      {/* Top Navigation Bar */}
      <nav className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-8 py-4">
          <div className="flex items-center justify-between">
            {/* Logo */}
            <div className="flex items-center gap-3">
              {/* Logo Image - reads from public/logo.png */}
              <div className="h-8 w-8 relative">
                <Image
                  src="/logo.png"
                  alt="MITRAM360 Logo"
                  width={32}
                  height={32}
                  className="rounded-lg"
                />
              </div>
              <span className="font-semibold text-xl">MITRAM360</span>
            </div>

            {/* Auth Buttons */}
            <div className="flex items-center gap-4">
              <Link
                href="/login"
                className="text-gray-700 hover:text-gray-900 transition-colors"
              >
                Login
              </Link>
              <Link
                href="/signup"
                className="bg-[#3B5CCC] text-white px-6 py-2 rounded-xl hover:bg-[#2d4aa8] transition-colors"
              >
                Sign Up
              </Link>
            </div>
          </div>
        </div>
      </nav>

      {/* Hero Section */}
      <section className="max-w-7xl mx-auto px-8 py-20">
        <div className="grid md:grid-cols-2 gap-16 items-center">
          {/* Left Side - Text Content */}
          <div>
            <h1 className="text-5xl font-bold text-gray-900 leading-tight mb-6">
              Discover Verified NBFC Lending Partners
            </h1>
            <p className="text-xl text-gray-600 leading-relaxed mb-8">
              Search and filter 928 RBI-registered lenders by loan type, geography and ticket size.
            </p>

            {/* CTA Button */}
            <div>
              <Link
                href="/signup"
                className="inline-flex items-center gap-2 bg-[#3B5CCC] text-white px-8 py-4 rounded-xl hover:bg-[#2d4aa8] transition-colors text-lg font-medium"
              >
                Get Started
                <ArrowRight className="w-5 h-5" />
              </Link>
            </div>
          </div>

          {/* Right Side - Hero Image */}
          <div className="relative">
            <div className="w-full h-auto rounded-2xl shadow-xl overflow-hidden border border-[#E5E7EB] bg-white">
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

      {/* Features Section */}
      <section className="py-16 bg-white">
        <div className="max-w-7xl mx-auto px-8">
          {/* Feature Cards */}
          <div className="grid md:grid-cols-3 gap-6 mb-16">
            {/* Smart Filtering */}
            <div className="bg-white border border-[#E5E7EB] rounded-xl p-8 text-center">
              <div className="flex justify-center mb-4">
                <div className="w-14 h-14 bg-blue-50 rounded-xl flex items-center justify-center">
                  <Search className="w-7 h-7 text-[#3B5CCC]" />
                </div>
              </div>
              <h3 className="font-semibold text-gray-900 mb-3">Smart Filtering</h3>
              <p className="text-sm text-gray-600 leading-relaxed">
                Filter by loan type, state, ticket size, and company type to find exactly what you need
              </p>
            </div>

            {/* Verified Lenders */}
            <div className="bg-white border border-[#E5E7EB] rounded-xl p-8 text-center">
              <div className="flex justify-center mb-4">
                <div className="w-14 h-14 bg-blue-50 rounded-xl flex items-center justify-center">
                  <Building2 className="w-7 h-7 text-[#3B5CCC]" />
                </div>
              </div>
              <h3 className="font-semibold text-gray-900 mb-3">Verified Lenders</h3>
              <p className="text-sm text-gray-600 leading-relaxed">
                Access NBFCs, PSU Banks, Private Banks, and Cooperative Banks - all verified
              </p>
            </div>

            {/* Instant Results */}
            <div className="bg-white border border-[#E5E7EB] rounded-xl p-8 text-center">
              <div className="flex justify-center mb-4">
                <div className="w-14 h-14 bg-blue-50 rounded-xl flex items-center justify-center">
                  <Zap className="w-7 h-7 text-[#3B5CCC]" />
                </div>
              </div>
              <h3 className="font-semibold text-gray-900 mb-3">Instant Results</h3>
              <p className="text-sm text-gray-600 leading-relaxed">
                Get instant access to lender details, contact information, and direct website links
              </p>
            </div>
          </div>

          {/* Stats Row */}
          <div className="grid md:grid-cols-3 gap-8">
            <div className="text-center">
              <div className="text-4xl font-bold text-[#3B5CCC] mb-2">928</div>
              <div className="text-sm text-gray-600">Verified Lenders</div>
            </div>

            <div className="text-center">
              <div className="text-4xl font-bold text-[#3B5CCC] mb-2">28+</div>
              <div className="text-sm text-gray-600">States Covered</div>
            </div>

            <div className="text-center">
              <div className="text-4xl font-bold text-[#3B5CCC] mb-2">100%</div>
              <div className="text-sm text-gray-600">Free to Use</div>
            </div>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="bg-white border-t border-gray-200 mt-20">
        <div className="max-w-7xl mx-auto px-8 py-8">
          <p className="text-center text-sm text-gray-500">
            © 2026 MITRAM360. All rights reserved.
          </p>
        </div>
      </footer>
    </div>
  )
}