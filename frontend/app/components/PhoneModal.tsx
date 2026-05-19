'use client'

import { useState, useEffect } from 'react'
import { Phone, AlertCircle } from 'lucide-react'
import { useAuth } from './AuthContext'

export function PhoneModal() {
  const { savePhone } = useAuth()
  const [phone, setPhone] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = '' }
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    const digits = phone.replace(/\D/g, '')
    if (!/^[6-9]\d{9}$/.test(digits)) {
      setError('Please enter a valid 10-digit Indian mobile number')
      return
    }

    setLoading(true)
    const { error: saveError } = await savePhone(digits)
    if (saveError) {
      setError("Couldn't save your number, please try again")
      setLoading(false)
    }
    // On success AuthContext sets phoneRequired=false — modal unmounts automatically
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="phone-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
      style={{ background: 'rgba(13,51,51,0.6)', backdropFilter: 'blur(2px)' }}
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-8">
        <div className="text-center mb-6">
          <div
            className="w-14 h-14 rounded-full flex items-center justify-center mx-auto mb-4"
            style={{ background: '#E6F4F4' }}
          >
            <Phone className="w-6 h-6" style={{ color: '#1A7070' }} />
          </div>
          <h2 id="phone-modal-title" className="text-xl font-bold text-gray-900 mb-1">One last step</h2>
          <p className="text-sm text-gray-500">Enter your mobile number to continue</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div className="p-3 rounded-xl bg-red-50 border border-red-200 flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
              <p className="text-sm text-red-800">{error}</p>
            </div>
          )}

          <div>
            <label
              htmlFor="phone-modal"
              className="block text-sm font-medium text-gray-700 mb-2"
            >
              Mobile Number
            </label>
            <div className="flex">
              <span className="inline-flex items-center px-3 rounded-l-xl border border-r-0 border-gray-300 bg-gray-50 text-gray-500 text-sm select-none">
                +91
              </span>
              <input
                id="phone-modal"
                type="tel"
                value={phone}
                onChange={(e) =>
                  setPhone(e.target.value.replace(/\D/g, '').slice(0, 10))
                }
                className="flex-1 px-4 py-3.5 rounded-r-xl border border-gray-300 focus:outline-none focus:ring-2 focus:ring-[#1A7070]/20 focus:border-[#1A7070] transition-all placeholder:text-gray-400"
                placeholder="10-digit number"
                disabled={loading}
                inputMode="numeric"
              />
            </div>
            <p className="mt-1.5 text-xs text-gray-500">
              Indian mobile number starting with 6–9
            </p>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3.5 px-4 bg-[#1A7070] text-white rounded-xl font-medium hover:bg-[#0F4848] focus:outline-none focus:ring-2 focus:ring-[#1A7070]/20 transition-all disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-[#1A7070]/25"
          >
            {loading ? 'Saving...' : 'Continue'}
          </button>
        </form>
      </div>
    </div>
  )
}
