'use client'

import Link from 'next/link'
import { MapPin, Calendar, TrendingUp, Users, Phone, Mail, Globe } from 'lucide-react'

interface Lender {
  id: string
  name: string
  city: string
  state: string
  companyType: string
  aum: string
  established: string
  ticketSize: string
  products: string[]
  operatingStates?: string[]
  headquarters?: string
  employees?: string | null
  phone?: string | null
  email?: string | null
  website?: string | null
}

interface LenderCardProps {
  lender: Lender
  index?: number
}

export function LenderCard({ lender, index = 0 }: LenderCardProps) {
  const hasContactInfo = lender.website || lender.phone || lender.email

  return (
    <div className="bg-white border border-[#E5E7EB] rounded-xl p-6 transition-all duration-300 ease-out hover:-translate-y-1.5 hover:shadow-lg hover:border-[#3B5CCC]/20">
      
      <div className="flex items-start justify-between mb-4">
        <h3 className="text-lg font-semibold text-gray-900 flex-1 pr-2 leading-tight">
          {lender.name}
        </h3>
        <span className="px-2.5 py-1 text-xs font-semibold bg-blue-50 text-[#3B5CCC] rounded-lg border border-blue-100 whitespace-nowrap flex-shrink-0">
          {lender.companyType}
        </span>
      </div>
      
      <div className="space-y-2.5 mb-4">
        <div className="flex items-center gap-2 text-sm text-gray-600">
          <MapPin className="w-4 h-4 text-gray-400 flex-shrink-0" />
          <span className="truncate">{lender.city}, {lender.state}</span>
        </div>
        
        <div className="flex items-center gap-2 text-sm text-gray-600">
          <Calendar className="w-4 h-4 text-gray-400 flex-shrink-0" />
          <span>Est. {lender.established}</span>
        </div>
        
        <div className="flex items-center gap-2 text-sm">
          <TrendingUp className="w-4 h-4 text-[#3B5CCC] flex-shrink-0" />
          <span className="font-semibold text-[#3B5CCC]">
            AUM: {lender.aum}
          </span>
        </div>
        
        {lender.employees && (
          <div className="flex items-center gap-2 text-sm text-gray-600">
            <Users className="w-4 h-4 text-gray-400 flex-shrink-0" />
            <span>{lender.employees} employees</span>
          </div>
        )}
      </div>
      
      <div className="mb-4">
        <span className="text-sm text-gray-500">Ticket Size: </span>
        <span className="text-sm text-gray-900">
          {lender.ticketSize}
        </span>
      </div>

      {/* Operating States Coverage */}
      {lender.operatingStates && lender.operatingStates.length > 0 && (
        <div className="mb-4 text-xs text-gray-600">
          <p>
            Operating in{" "}
            <span className="font-semibold">
              {lender.operatingStates.length}
            </span>{" "}
            state{lender.operatingStates.length > 1 ? "s" : ""}
          </p>
        </div>
      )}
      
      <div className="mb-4">
        <div className="flex flex-wrap gap-1.5">
          {lender.products.slice(0, 3).map((product, idx) => (
            <span
              key={idx}
              className="px-2.5 py-1 bg-blue-50 text-[#3B5CCC] text-xs font-medium rounded-md border border-blue-100 transition-all duration-200 hover:bg-blue-100 hover:border-blue-200"
            >
              {product}
            </span>
          ))}
          {lender.products.length > 3 && (
            <span className="px-2.5 py-1 bg-gray-100 text-gray-600 text-xs font-medium rounded-md border border-gray-200">
              +{lender.products.length - 3}
            </span>
          )}
        </div>
      </div>

      {hasContactInfo && (
        <div className="flex flex-wrap gap-2">

          {lender.website && (
            <a
              href={lender.website}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-[#3B5CCC] text-white rounded-lg hover:bg-[#2d4aa8] transition-colors"
            >
              <Globe className="w-3.5 h-3.5" />
              Website
            </a>
          )}

          {lender.phone && (
            <a
              href={`tel:${lender.phone}`}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors"
            >
              <Phone className="w-3.5 h-3.5" />
              Call
            </a>
          )}

          {lender.email && (
            <a
              href={`mailto:${lender.email}`}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors"
            >
              <Mail className="w-3.5 h-3.5" />
              Email
            </a>
          )}

        </div>
      )}

    </div>
  )
}