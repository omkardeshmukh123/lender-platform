'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import Image from 'next/image'
import { useAuth } from '../components/AuthContext'
import { createClient } from '@supabase/supabase-js'
import { ArrowLeft, Mail, Lock, Eye, EyeOff, Phone, AlertCircle, CheckCircle2 } from 'lucide-react'

const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL || '',
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || ''
)

export default function SignUp() {
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [signupSuccess, setSignupSuccess] = useState(false)
  const router = useRouter()
  const { signUp } = useAuth()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    // Validation
    if (!email || !phone || !password || !confirmPassword) {
      setError('All fields are required')
      return
    }

    // Phone validation (Indian format)
    const phoneRegex = /^[6-9]\d{9}$/
    if (!phoneRegex.test(phone.replace(/\D/g, ''))) {
      setError('Please enter a valid 10-digit Indian phone number')
      return
    }

    if (password.length < 6) {
      setError('Password must be at least 6 characters')
      return
    }

    if (password !== confirmPassword) {
      setError('Passwords do not match')
      return
    }

    setLoading(true)

    try {
      // Sign up with Supabase Auth
      const { data: authData, error: signUpError } = await signUp(email, password)

      if (signUpError) {
        setError(signUpError.message)
        setLoading(false)
        return
      }

      // Check if email confirmation is required
      if (authData?.user && !authData.user.confirmed_at) {
        // Email confirmation required - show message
        setSignupSuccess(true)
        setLoading(false)
        return
      }

      // Save phone number to user_profiles table
      if (authData?.user) {
        const profileResult = await supabase
          .from('user_profiles')
          .insert({
            user_id: authData.user.id,
            email: email,
            phone: phone,
          })

        if (profileResult.error) {
          console.error('Profile save error:', profileResult.error)
          // Continue anyway - user is created, just phone not saved
        }
      }

      // Redirect to dashboard
      router.push('/dashboard')
    } catch (err: any) {
      setError(err.message || 'An error occurred during signup')
      setLoading(false)
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
          <p className="text-gray-600">Create your account</p>
        </div>

        {/* Signup Card */}
        <div className="bg-white rounded-2xl shadow-lg shadow-gray-200/50 p-8">
          {!signupSuccess ? (
            <>
              <div className="mb-8">
                <h2 className="text-2xl font-semibold text-gray-900 mb-2">
                  Get Started
                </h2>
                <p className="text-sm text-gray-500">Join thousands finding the right lenders</p>
              </div>

              <form onSubmit={handleSubmit} className="space-y-5">
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

                {/* Phone Field */}
                <div>
                  <label htmlFor="phone" className="block text-sm font-medium text-gray-700 mb-2">
                    Phone Number
                  </label>
                  <div className="relative">
                    <Phone className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
                    <input
                      id="phone"
                      type="tel"
                      value={phone}
                      onChange={(e) => setPhone(e.target.value)}
                      className="w-full pl-12 pr-4 py-3.5 rounded-xl border border-gray-300 focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC] transition-all placeholder:text-gray-400"
                      placeholder="Enter 10-digit mobile number"
                      maxLength={10}
                      disabled={loading}
                    />
                  </div>
                  <p className="mt-1.5 text-xs text-gray-500">
                    Enter 10-digit Indian mobile number
                  </p>
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
                      placeholder="Create a password"
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
                  <p className="mt-1.5 text-xs text-gray-500">
                    Must be at least 6 characters
                  </p>
                </div>

                {/* Confirm Password Field */}
                <div>
                  <label htmlFor="confirmPassword" className="block text-sm font-medium text-gray-700 mb-2">
                    Confirm Password
                  </label>
                  <div className="relative">
                    <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
                    <input
                      id="confirmPassword"
                      type={showConfirmPassword ? 'text' : 'password'}
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      className="w-full pl-12 pr-12 py-3.5 rounded-xl border border-gray-300 focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC] transition-all placeholder:text-gray-400"
                      placeholder="Confirm your password"
                      disabled={loading}
                    />
                    <button
                      type="button"
                      onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                      className="absolute right-4 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 transition-colors"
                    >
                      {showConfirmPassword ? (
                        <EyeOff className="w-5 h-5" />
                      ) : (
                        <Eye className="w-5 h-5" />
                      )}
                    </button>
                  </div>
                </div>

                {/* Submit Button */}
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full py-3.5 px-4 bg-[#3B5CCC] text-white rounded-xl font-medium hover:bg-[#2d4aa8] focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 transition-all disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-[#3B5CCC]/25 hover:scale-[1.02] active:scale-[0.98]"
                >
                  {loading ? 'Creating account...' : 'Create Account'}
                </button>

                {/* Terms */}
                <p className="text-center text-xs text-gray-500">
                  By signing up, you agree to our{' '}
                  <Link href="#" className="text-[#3B5CCC] hover:text-[#2d4aa8] transition-colors">
                    Terms of Service
                  </Link>{' '}
                  and{' '}
                  <Link href="#" className="text-[#3B5CCC] hover:text-[#2d4aa8] transition-colors">
                    Privacy Policy
                  </Link>
                </p>
              </form>

              {/* Login Link */}
              <div className="mt-6 text-center">
                <p className="text-sm text-gray-600">
                  Already have an account?{' '}
                  <Link href="/login" className="text-[#3B5CCC] font-semibold hover:text-[#2d4aa8] transition-colors">
                    Sign In
                  </Link>
                </p>
              </div>
            </>
          ) : (
            /* Success Message */
            <div className="text-center py-8">
              <div className="p-6 rounded-xl bg-green-50 border border-green-200">
                <div className="flex justify-center mb-4">
                  <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center">
                    <CheckCircle2 className="w-8 h-8 text-green-600" />
                  </div>
                </div>
                <h2 className="text-2xl font-bold text-gray-900 mb-3">Check Your Email!</h2>
                <p className="text-gray-600 mb-2">We sent a confirmation link to:</p>
                <p className="font-semibold text-[#3B5CCC] mb-4">{email}</p>
                <p className="text-sm text-gray-600">Click the link in the email to activate your account.</p>
                <p className="text-xs text-gray-500 mt-4">Didn't receive the email? Check your spam folder.</p>
              </div>
              <div className="mt-6">
                <Link 
                  href="/login" 
                  className="inline-flex items-center gap-2 text-[#3B5CCC] font-semibold hover:text-[#2d4aa8] transition-colors"
                >
                  Go to Sign In
                  <ArrowLeft className="w-4 h-4 rotate-180" />
                </Link>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}