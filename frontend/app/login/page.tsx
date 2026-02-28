'use client'

import { Suspense, useState, useEffect } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import Image from 'next/image'
import { useAuth } from '../components/AuthContext'
import { ArrowLeft, Mail, Lock, Eye, EyeOff, AlertCircle } from 'lucide-react'

export const dynamic = 'force-dynamic'

function LoginContent() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(false)
  const router = useRouter()
  const searchParams = useSearchParams()
  const { signIn, user } = useAuth()

  useEffect(() => {
    // Check for success message from signup
    const msg = searchParams.get('message')
    if (msg) {
      setMessage(msg)
    }

    // Redirect if already logged in
    if (user) {
      router.push('/dashboard')
    }
  }, [searchParams, user, router])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setMessage('')

    if (!email || !password) {
      setError('Email and password are required')
      return
    }

    setLoading(true)

    const { error: signInError } = await signIn(email, password)

    if (signInError) {
      setError(signInError.message)
      setLoading(false)
    } else {
      // Success - redirect to dashboard
      router.push('/dashboard')
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white flex items-center justify-center px-4 py-16">
      <div className="w-full max-w-md">
        {/* Go Back Button */}
        <Link
          href="/"
          className="inline-flex items-center gap-2 text-sm text-gray-600 hover:text-gray-900 transition-colors mb-8"
        >
          <ArrowLeft className="w-4 h-4" />
          Go Back
        </Link>

        {/* Header with Logo */}
        <div className="text-center mb-8">
          <div className="flex items-center justify-center gap-3 mb-4">
            {/* Logo Image */}
            <div className="h-10 w-10 relative">
              <Image
                src="/logo.png"
                alt="MITRAM360 Logo"
                width={40}
                height={40}
                className="rounded-lg"
              />
            </div>
            <h1 className="text-3xl font-bold text-gray-900">MITRAM360</h1>
          </div>
          <p className="text-gray-600">Sign in to your account</p>
        </div>

        {/* Login Card */}
        <div className="bg-white rounded-2xl shadow-lg shadow-gray-200/50 p-8">
          <div className="mb-8">
            <h2 className="text-2xl font-semibold text-gray-900 mb-2">
              Welcome back
            </h2>
            <p className="text-sm text-gray-500">Login to access your dashboard</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-6">
            {/* Success Message */}
            {message && (
              <div className="p-4 rounded-xl bg-green-50 border border-green-200 flex items-start gap-3">
                <AlertCircle className="w-5 h-5 text-green-600 flex-shrink-0 mt-0.5" />
                <p className="text-sm text-green-800">{message}</p>
              </div>
            )}

            {/* Error Message */}
            {error && (
              <div className="p-4 rounded-xl bg-red-50 border border-red-200 flex items-start gap-3">
                <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
                <p className="text-sm text-red-800">{error}</p>
              </div>
            )}

            {/* Email Field */}
            <div>
              <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-2">
                Email Address
              </label>
              <div className="relative">
                <Mail className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
                <input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full pl-12 pr-4 py-3.5 rounded-xl border border-gray-300 focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC] transition-all placeholder:text-gray-400"
                  placeholder="Enter your email"
                  disabled={loading}
                />
              </div>
            </div>

            {/* Password Field */}
            <div>
              <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-2">
                Password
              </label>
              <div className="relative">
                <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
                <input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full pl-12 pr-12 py-3.5 rounded-xl border border-gray-300 focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC] transition-all placeholder:text-gray-400"
                  placeholder="Enter your password"
                  disabled={loading}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-4 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 transition-colors"
                >
                  {showPassword ? (
                    <EyeOff className="w-5 h-5" />
                  ) : (
                    <Eye className="w-5 h-5" />
                  )}
                </button>
              </div>
            </div>

            {/* Forgot Password */}
            <div className="flex justify-end">
              <Link href="/forgot-password" className="text-sm text-[#3B5CCC] hover:text-[#2d4aa8] transition-colors">
                Forgot password?
              </Link>
            </div>

            {/* Submit Button */}
            <button
              type="submit"
              disabled={loading}
              className="w-full py-3.5 px-4 bg-[#3B5CCC] text-white rounded-xl font-medium hover:bg-[#2d4aa8] focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 transition-all disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-[#3B5CCC]/25 hover:scale-[1.02] active:scale-[0.98]"
            >
              {loading ? 'Signing in...' : 'Sign In'}
            </button>
          </form>

          {/* Signup Link */}
          <div className="mt-6 text-center">
            <p className="text-sm text-gray-600">
              Don't have an account?{' '}
              <Link href="/signup" className="text-[#3B5CCC] font-semibold hover:text-[#2d4aa8] transition-colors">
                Sign Up
              </Link>
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function LoginPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-[#3B5CCC]"></div>
      </div>
    }>
      <LoginContent />
    </Suspense>
  )
}