'use client'

/**
 * LenderCard.tsx — Updated final version
 * ========================================
 * Changes from original:
 *   ✅ All props marked optional where data might be missing
 *   ✅ Safe rendering — no crash on null/undefined fields
 *   ✅ city/state display handles missing hq_location gracefully
 *   ✅ Products: up to 4 tags shown (was 3), rest collapsed to "+N"
 *   ✅ Consistent card height via flex-col layout
 */

import Link from 'next/link'
import { MapPin, Calendar, TrendingUp, Users, Phone, Mail, Globe, ArrowRight, Bookmark, BookmarkCheck } from 'lucide-react'

interface Lender {
  id:              string
  name:            string
  city:            string
  state:           string
  companyType:     string
  aum:             string
  established:     string
  ticketSize:      string
  products:        string[]
  operatingStates?: string[]
  headquarters?:   string
  employees?:      string | null
  phone?:          string | null
  email?:          string | null
  website?:        string | null
}

interface LenderCardProps {
  lender:       Lender
  index?:       number
  onTagClick?:  (tag: string) => void
  isSaved?:     boolean
  onSave?:      () => void
}

export function LenderCard({ lender, index = 0, onTagClick, isSaved = false, onSave }: LenderCardProps) {
  const hasContactInfo = lender.website || lender.phone || lender.email

  // Safely build location string — avoid "N/A, N/A"
  const location = (() => {
    const city  = lender.city  !== 'N/A' ? lender.city  : ''
    const state = lender.state !== 'N/A' ? lender.state : ''
    if (city && state && city !== state) return `${city}, ${state}`
    return city || state || 'Location N/A'
  })()

  // Products to display (cap at 4 visible)
  const visibleProducts = (lender.products ?? []).slice(0, 4)
  const extraCount      = Math.max(0, (lender.products ?? []).length - 4)

  return (
    <div className="bg-white border border-[#E5E7EB] rounded-xl p-6
                    flex flex-col
                    transition-all duration-300 ease-out
                    hover:-translate-y-1.5 hover:shadow-lg hover:border-[#3B5CCC]/20">

      {/* Header: name + company type badge + save */}
      <div className="flex items-start justify-between mb-4 gap-2">
        <h3 className="text-lg font-semibold text-gray-900 flex-1 leading-tight">
          {lender.name || 'Unknown Lender'}
        </h3>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {lender.companyType && lender.companyType !== 'N/A' && (
            <span className="px-2.5 py-1 text-xs font-semibold
                             bg-blue-50 text-[#3B5CCC] rounded-lg border border-blue-100
                             whitespace-nowrap">
              {lender.companyType}
            </span>
          )}
          {onSave && (
            <button
              type="button"
              onClick={e => { e.preventDefault(); onSave() }}
              title={isSaved ? 'Remove from shortlist' : 'Add to shortlist'}
              className={[
                'p-1.5 rounded-lg transition-all',
                isSaved
                  ? 'text-[#3B5CCC] bg-blue-50 hover:bg-blue-100'
                  : 'text-gray-400 hover:text-[#3B5CCC] hover:bg-blue-50',
              ].join(' ')}
            >
              {isSaved
                ? <BookmarkCheck className="w-4 h-4" />
                : <Bookmark className="w-4 h-4" />}
            </button>
          )}
        </div>
      </div>

      {/* Key info rows */}
      <div className="space-y-2.5 mb-4 flex-1">

        <div className="flex items-center gap-2 text-sm text-gray-600">
          <MapPin className="w-4 h-4 text-gray-400 flex-shrink-0" aria-hidden="true" />
          <span className="truncate">{location}</span>
        </div>

        {lender.established && lender.established !== 'N/A' && (
          <div className="flex items-center gap-2 text-sm text-gray-600">
            <Calendar className="w-4 h-4 text-gray-400 flex-shrink-0" aria-hidden="true" />
            <span>Est. {lender.established}</span>
          </div>
        )}

        <div className="flex items-center gap-2 text-sm">
          <TrendingUp className="w-4 h-4 text-[#3B5CCC] flex-shrink-0" aria-hidden="true" />
          <span className="font-semibold text-[#3B5CCC]">
            AUM: {lender.aum}
          </span>
        </div>

        {lender.employees && (
          <div className="flex items-center gap-2 text-sm text-gray-600">
            <Users className="w-4 h-4 text-gray-400 flex-shrink-0" aria-hidden="true" />
            <span>{lender.employees} employees</span>
          </div>
        )}
      </div>

      {/* Ticket size */}
      {lender.ticketSize && lender.ticketSize !== 'N/A' && (
        <div className="mb-3 text-sm">
          <span className="text-gray-500">Size: </span>
          <span className="text-gray-900 font-medium">{lender.ticketSize}</span>
        </div>
      )}

      {/* Operating states coverage */}
      {lender.operatingStates && lender.operatingStates.length > 0 && (
        <div className="mb-3 text-xs text-gray-500">
          Operating in{' '}
          <span className="font-semibold text-gray-700">
            {lender.operatingStates.length}
          </span>
          {' '}state{lender.operatingStates.length !== 1 ? 's' : ''}
        </div>
      )}

      {/* Product tags */}
      {visibleProducts.length > 0 && (
        <div className="mb-4">
          <div className="flex flex-wrap gap-1.5">
            {visibleProducts.map((product, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => onTagClick?.(product)}
                title={onTagClick ? `Filter by ${product}` : product}
                className={[
                  'px-2.5 py-1 bg-blue-50 text-[#3B5CCC] text-xs font-medium',
                  'rounded-md border border-blue-100',
                  'hover:bg-blue-100 hover:border-blue-200 transition-all duration-200',
                  onTagClick ? 'cursor-pointer' : 'cursor-default',
                ].join(' ')}
              >
                {product}
              </button>
            ))}
            {extraCount > 0 && (
              <span className="px-2.5 py-1 bg-gray-100 text-gray-600 text-xs font-medium
                               rounded-md border border-gray-200">
                +{extraCount}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Detail link */}
      <Link
        href={`/lender/${lender.id}`}
        className="inline-flex items-center justify-center gap-1.5 w-full py-2 mb-3
                   rounded-lg text-xs font-medium border border-gray-200 text-gray-600
                   hover:border-[#3B5CCC]/40 hover:text-[#3B5CCC] transition-colors"
      >
        View Details
        <ArrowRight className="w-3 h-3" />
      </Link>

      {/* Contact buttons */}
      {hasContactInfo && (
        <div className="flex flex-wrap gap-2 pt-2 mt-auto border-t border-gray-50">

          {lender.website && (
            <a
              href={lender.website}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium
                         bg-[#3B5CCC] text-white rounded-lg
                         hover:bg-[#2d4aa8] transition-colors"
            >
              <Globe className="w-3.5 h-3.5" aria-hidden="true" />
              Website
            </a>
          )}

          {lender.phone && (
            <a
              href={`tel:${lender.phone}`}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium
                         bg-gray-100 text-gray-700 rounded-lg
                         hover:bg-gray-200 transition-colors"
            >
              <Phone className="w-3.5 h-3.5" aria-hidden="true" />
              Call
            </a>
          )}

          {lender.email && (
            <a
              href={`mailto:${lender.email}`}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium
                         bg-gray-100 text-gray-700 rounded-lg
                         hover:bg-gray-200 transition-colors"
            >
              <Mail className="w-3.5 h-3.5" aria-hidden="true" />
              Email
            </a>
          )}

        </div>
      )}
    </div>
  )
}
