'use client'

import { useState } from 'react'
import { Building2, MapPin, CheckCircle, AlertCircle } from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export interface RegistryStub {
  id: number
  company_name: string
  cin: string | null
  rbi_registration_number: string | null
  regulatory_tier: string | null
  hq_state: string | null
  established_year: number | null
}

const TIER_LABELS: Record<string, string> = {
  'ND-UL': 'Upper Layer',
  'ND-ML': 'Middle Layer',
  'ND-BL': 'Base Layer',
}
const TIER_COLORS: Record<string, string> = {
  'ND-UL': 'bg-purple-50 text-purple-700',
  'ND-ML': 'bg-blue-50 text-blue-700',
  'ND-BL': 'bg-gray-100 text-gray-600',
}

interface Props {
  stub: RegistryStub
  userEmail?: string | null
}

export default function StubCard({ stub, userEmail }: Props) {
  const [state, setState] = useState<'idle' | 'loading' | 'done' | 'error'>('idle')

  const handleRequest = async () => {
    setState('loading')
    try {
      const res = await fetch(`${API_URL}/v1/lenders/request`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          company_name: stub.company_name,
          cin: stub.cin,
          requested_by: userEmail ?? 'anonymous',
        }),
      })
      if (!res.ok) throw new Error('failed')
      setState('done')
    } catch {
      setState('error')
      setTimeout(() => setState('idle'), 3000)
    }
  }

  const tierLabel = stub.regulatory_tier ? (TIER_LABELS[stub.regulatory_tier] ?? stub.regulatory_tier) : null
  const tierColor = stub.regulatory_tier ? (TIER_COLORS[stub.regulatory_tier] ?? 'bg-gray-100 text-gray-600') : ''

  return (
    <div className="relative bg-gray-50 border border-dashed border-gray-300 rounded-2xl p-5 flex flex-col gap-3 opacity-80">
      <div className="absolute top-3 right-3">
        <span className="text-xs font-medium bg-gray-200 text-gray-500 px-2 py-0.5 rounded-full">
          Data not yet available
        </span>
      </div>

      <div className="flex items-start gap-3 pr-36">
        <div className="w-9 h-9 rounded-xl bg-gray-200 flex items-center justify-center flex-shrink-0">
          <Building2 className="w-4 h-4 text-gray-400" />
        </div>
        <div>
          <h3 className="font-semibold text-gray-700 leading-tight">{stub.company_name}</h3>
          {stub.rbi_registration_number && (
            <p className="text-xs text-gray-400 mt-0.5">RBI Reg: {stub.rbi_registration_number}</p>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-2 text-xs">
        {tierLabel && (
          <span className={`px-2 py-0.5 rounded-full font-medium ${tierColor}`}>{tierLabel}</span>
        )}
        {stub.hq_state && (
          <span className="flex items-center gap-1 text-gray-500">
            <MapPin className="w-3 h-3" />{stub.hq_state}
          </span>
        )}
        {stub.established_year && (
          <span className="text-gray-400">Est. {stub.established_year}</span>
        )}
        {stub.cin && (
          <span className="text-gray-400 font-mono text-xs">{stub.cin}</span>
        )}
      </div>

      <div className="mt-1">
        {state === 'done' ? (
          <div className="flex items-center gap-1.5 text-green-600 text-xs font-medium">
            <CheckCircle className="w-3.5 h-3.5" />
            Request submitted — we&apos;ll enrich this soon
          </div>
        ) : state === 'error' ? (
          <div className="flex items-center gap-1.5 text-red-500 text-xs font-medium">
            <AlertCircle className="w-3.5 h-3.5" />
            Failed — please try again
          </div>
        ) : (
          <button
            onClick={handleRequest}
            disabled={state === 'loading'}
            className="text-xs font-medium text-[#3B5CCC] hover:underline disabled:opacity-50"
          >
            {state === 'loading' ? 'Submitting…' : 'Request full profile →'}
          </button>
        )}
      </div>
    </div>
  )
}
