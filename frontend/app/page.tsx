'use client'

import { useRouter } from 'next/navigation'
import { useEffect } from 'react'
import { useAuth } from './components/AuthContext'

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
      <div className="min-h-screen bg-gradient-to-b from-blue-50 to-white flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-blue-50 to-white">
      {/* Hero Section */}
      <div className="max-w-7xl mx-auto px-4 py-20">
        <div className="text-center mb-16">
          <h1 className="text-6xl font-bold text-gray-900 mb-6">
            🏦 Lender Discovery Platform
          </h1>
          <p className="text-2xl text-gray-600 mb-4">
            Find the perfect financial partner for your needs
          </p>
          <p className="text-lg text-gray-500 max-w-2xl mx-auto">
            Access thousands of verified lenders across India. Filter by loan type, 
            location, and ticket size to find your ideal match.
          </p>
        </div>

        {/* CTA Buttons */}
        <div className="flex items-center justify-center gap-6 mb-20">
          <button
            onClick={() => router.push('/signup')}
            className="px-8 py-4 bg-blue-600 text-white text-lg font-semibold rounded-lg hover:bg-blue-700 shadow-lg hover:shadow-xl transition-all"
          >
            Get Started Free
          </button>
          <button
            onClick={() => router.push('/login')}
            className="px-8 py-4 bg-white text-blue-600 text-lg font-semibold rounded-lg border-2 border-blue-600 hover:bg-blue-50 shadow-lg hover:shadow-xl transition-all"
          >
            Sign In
          </button>
        </div>

        {/* Features */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8 max-w-5xl mx-auto">
          <div className="bg-white rounded-lg shadow-md p-8 text-center">
            <div className="text-4xl mb-4">🔍</div>
            <h3 className="text-xl font-semibold mb-3">Smart Filtering</h3>
            <p className="text-gray-600">
              Filter by loan type, state, ticket size, and company type to find exactly what you need
            </p>
          </div>

          <div className="bg-white rounded-lg shadow-md p-8 text-center">
            <div className="text-4xl mb-4">🏢</div>
            <h3 className="text-xl font-semibold mb-3">Verified Lenders</h3>
            <p className="text-gray-600">
              Access NBFCs, PSU Banks, Private Banks, and Cooperative Banks - all verified
            </p>
          </div>

          <div className="bg-white rounded-lg shadow-md p-8 text-center">
            <div className="text-4xl mb-4">⚡</div>
            <h3 className="text-xl font-semibold mb-3">Instant Results</h3>
            <p className="text-gray-600">
              Get instant access to lender details, contact information, and direct website links
            </p>
          </div>
        </div>

        {/* Stats */}
        <div className="mt-20 grid grid-cols-1 md:grid-cols-3 gap-8 max-w-4xl mx-auto text-center">
          <div>
            <div className="text-4xl font-bold text-blue-600 mb-2">3000+</div>
            <div className="text-gray-600">Verified Lenders</div>
          </div>
          <div>
            <div className="text-4xl font-bold text-blue-600 mb-2">28</div>
            <div className="text-gray-600">States Covered</div>
          </div>
          <div>
            <div className="text-4xl font-bold text-blue-600 mb-2">100%</div>
            <div className="text-gray-600">Free to Use</div>
          </div>
        </div>
      </div>
    </div>
  )
}
