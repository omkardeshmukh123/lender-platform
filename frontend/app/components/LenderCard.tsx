'use client'

import Link from 'next/link'
import {
  MapPin, Calendar, TrendingUp, Users,
  Phone, Mail, Globe, ArrowRight, Bookmark, BookmarkCheck, Scale,
} from 'lucide-react'

interface Lender {
  id:              string
  name:            string
  city:            string
  state:           string
  companyType:     string
  aum:             string
  established:     string
  ticketSize:      string
  businessSector?: string | null
  products:        string[]
  operatingStates?: string[]
  headquarters?:   string
  employees?:      string | null
  phone?:          string | null
  email?:          string | null
  website?:        string | null
  qualityScore?:   number | null
  policyCount?:    number | null
}

interface LenderCardProps {
  lender:        Lender
  index?:        number
  onTagClick?:   (tag: string) => void
  isSaved?:      boolean
  onSave?:       () => void
  isComparing?:  boolean
  onCompare?:    () => void
}

function companyTypeBadgeStyle(type: string) {
  const t = type.toLowerCase()
  if (t.includes('nbfc-mfi') || t.includes('microfinance'))
    return { bg: '#FFF7ED', color: '#C2410C', border: '#FED7AA' }
  if (t.includes('nbfc'))
    return { bg: '#E6F4F4', color: '#1A7070', border: '#A8DADA' }
  if (t.includes('psu'))
    return { bg: '#F0FDF4', color: '#16A34A', border: '#BBF7D0' }
  if (t.includes('private'))
    return { bg: '#F0F9FF', color: '#0284C7', border: '#BAE6FD' }
  if (t.includes('foreign'))
    return { bg: '#FDF4FF', color: '#9333EA', border: '#E9D5FF' }
  if (t.includes('small finance'))
    return { bg: '#FFFBEB', color: '#D97706', border: '#FDE68A' }
  if (t.includes('cooperative'))
    return { bg: '#F0FDF4', color: '#15803D', border: '#BBF7D0' }
  return { bg: '#F8F9FC', color: '#6B7280', border: '#E5E7EB' }
}

function qualityDot(score: number | null | undefined) {
  if (score == null) return null
  const pct = Math.round(score * 100)
  const color = pct >= 70 ? '#16A34A' : pct >= 40 ? '#D97706' : '#DC2626'
  const bg    = pct >= 70 ? '#F0FDF4' : pct >= 40 ? '#FFFBEB' : '#FEF2F2'
  return { pct, color, bg }
}

export function LenderCard({ lender, index = 0, onTagClick, isSaved = false, onSave, isComparing = false, onCompare }: LenderCardProps) {
  const hasContactInfo = lender.website || lender.phone || lender.email
  const badgeStyle     = companyTypeBadgeStyle(lender.companyType)
  const quality        = qualityDot(lender.qualityScore)

  const location = (() => {
    const city  = lender.city  !== 'N/A' ? lender.city  : ''
    const state = lender.state !== 'N/A' ? lender.state : ''
    if (city && state && city !== state) return `${city}, ${state}`
    return city || state || 'Location N/A'
  })()

  const visibleProducts = (lender.products ?? []).slice(0, 4)
  const extraCount      = Math.max(0, (lender.products ?? []).length - 4)

  return (
    <div
      className="lender-card animate-fade-in-up"
      style={{ animationDelay: `${Math.min(index, 5) * 60}ms` }}
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-4 gap-2">
        <div className="flex-1 min-w-0">
          <h3 className="text-base font-bold leading-tight truncate"
              style={{ color: '#0D3333' }}>
            {lender.name || 'Unknown Lender'}
          </h3>
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {lender.companyType && lender.companyType !== 'N/A' && (
            <span className="px-2 py-0.5 text-[11px] font-bold rounded-md whitespace-nowrap"
                  style={{ background: badgeStyle.bg, color: badgeStyle.color, border: `1px solid ${badgeStyle.border}` }}>
              {lender.companyType}
            </span>
          )}
          {onCompare !== undefined && (
            <button
              type="button"
              onClick={e => { e.preventDefault(); onCompare() }}
              title={isComparing ? 'Remove from compare' : 'Add to compare'}
              className={`p-1.5 rounded-lg transition-all duration-200 ${
                isComparing
                  ? 'text-[#C9A227] bg-[#FDF8E4]'
                  : 'text-gray-300 hover:text-[#C9A227] hover:bg-[#FDF8E4]'
              }`}
            >
              <Scale className="w-4 h-4" />
            </button>
          )}
          {onSave && (
            <button
              type="button"
              onClick={e => { e.preventDefault(); onSave() }}
              title={isSaved ? 'Remove from shortlist' : 'Add to shortlist'}
              className={`p-1.5 rounded-lg transition-all duration-200 ${
                isSaved
                  ? 'text-[#1A7070] bg-[#E6F4F4]'
                  : 'text-gray-300 hover:text-[#1A7070] hover:bg-[#E6F4F4]'
              }`}
            >
              {isSaved
                ? <BookmarkCheck className="w-4 h-4" />
                : <Bookmark className="w-4 h-4" />}
            </button>
          )}
        </div>
      </div>

      {/* Key info rows */}
      <div className="space-y-2 mb-3 flex-1">
        <div className="flex items-center gap-2 text-sm" style={{ color: '#7A9E9E' }}>
          <MapPin className="w-3.5 h-3.5 flex-shrink-0" style={{ color: '#A8CECE' }} />
          <span className="truncate">{location}</span>
        </div>

        {lender.established && lender.established !== 'N/A' && (
          <div className="flex items-center gap-2 text-sm" style={{ color: '#7A9E9E' }}>
            <Calendar className="w-3.5 h-3.5 flex-shrink-0" style={{ color: '#A8CECE' }} />
            <span>Est. {lender.established}</span>
          </div>
        )}

        <div className="flex items-center gap-2 text-sm">
          <TrendingUp className="w-3.5 h-3.5 flex-shrink-0" style={{ color: '#C9A227' }} />
          <span className="font-semibold" style={{ color: '#0F4848' }}>
            AUM: {lender.aum}
          </span>
        </div>

        {lender.employees && (
          <div className="flex items-center gap-2 text-sm" style={{ color: '#7A9E9E' }}>
            <Users className="w-3.5 h-3.5 flex-shrink-0" style={{ color: '#A8CECE' }} />
            <span>{lender.employees} employees</span>
          </div>
        )}
      </div>

      {/* Metadata chips */}
      {((lender.ticketSize && lender.ticketSize !== 'N/A') || lender.businessSector) && (
        <div className="mb-3 flex flex-wrap gap-1.5">
          {lender.ticketSize && lender.ticketSize !== 'N/A' && (
            <span className="px-2 py-0.5 text-[11px] font-semibold rounded-md"
                  style={{ background: '#F7FAFA', color: '#3D6363', border: '1px solid #D8EBEB' }}>
              {lender.ticketSize}
            </span>
          )}
          {lender.businessSector && (
            <span className="px-2 py-0.5 text-[11px] font-semibold rounded-md"
                  style={{ background: '#FDF8E4', color: '#A07E1A', border: '1px solid #F5E8A8' }}>
              {lender.businessSector}
            </span>
          )}
        </div>
      )}

      {/* Operating states */}
      {lender.operatingStates && lender.operatingStates.length > 0 && (
        <p className="mb-3 text-[11px]" style={{ color: '#A8CECE' }}>
          Operating in{' '}
          <span className="font-semibold" style={{ color: '#3D6363' }}>
            {lender.operatingStates.length}
          </span>
          {' '}state{lender.operatingStates.length !== 1 ? 's' : ''}
        </p>
      )}

      {/* Product tags */}
      {visibleProducts.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-1.5">
          {visibleProducts.map((product, idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => onTagClick?.(product)}
              title={onTagClick ? `Filter by ${product}` : product}
              className={`px-2 py-0.5 text-[11px] font-medium rounded-md transition-all duration-150
                          ${onTagClick ? 'cursor-pointer' : 'cursor-default'}`}
              style={{
                background: '#E6F4F4',
                color: '#1A7070',
                border: '1px solid #A8DADA',
              }}
              onMouseEnter={e => {
                if (onTagClick) (e.currentTarget as HTMLButtonElement).style.background = '#CCE9E9'
              }}
              onMouseLeave={e => {
                (e.currentTarget as HTMLButtonElement).style.background = '#E6F4F4'
              }}
            >
              {product}
            </button>
          ))}
          {extraCount > 0 && (
            <span className="px-2 py-0.5 text-[11px] font-medium rounded-md"
                  style={{ background: '#F7FAFA', color: '#7A9E9E' }}>
              +{extraCount}
            </span>
          )}
        </div>
      )}

      {/* Policy count badge */}
      {lender.policyCount != null && lender.policyCount > 0 && (
        <div className="mb-3">
          <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-semibold rounded-md"
                style={{ background: '#F0FDF4', color: '#16A34A', border: '1px solid #BBF7D0' }}>
            <span className="w-1.5 h-1.5 rounded-full bg-green-500 flex-shrink-0" />
            {lender.policyCount} {lender.policyCount === 1 ? 'policy' : 'policies'} available
          </span>
        </div>
      )}

      {/* Detail link + quality badge */}
      <div className="flex items-center gap-2 mb-3">
        <Link
          href={`/lender/${lender.id}`}
          className="inline-flex items-center justify-center gap-1.5 flex-1 py-2
                     rounded-lg text-xs font-semibold border transition-all duration-200"
          style={{ borderColor: '#D8EBEB', color: '#3D6363' }}
          onMouseEnter={e => {
            (e.currentTarget as HTMLAnchorElement).style.borderColor = '#1A7070'
            ;(e.currentTarget as HTMLAnchorElement).style.color = '#1A7070'
            ;(e.currentTarget as HTMLAnchorElement).style.background = '#E6F4F4'
          }}
          onMouseLeave={e => {
            (e.currentTarget as HTMLAnchorElement).style.borderColor = '#D8EBEB'
            ;(e.currentTarget as HTMLAnchorElement).style.color = '#3D6363'
            ;(e.currentTarget as HTMLAnchorElement).style.background = 'transparent'
          }}
        >
          View Profile
          <ArrowRight className="w-3 h-3" />
        </Link>
        {quality && (
          <span
            title={`Data quality: ${quality.pct}%`}
            className="flex-shrink-0 inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold"
            style={{ background: quality.bg, color: quality.color }}
          >
            <span className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                  style={{ background: quality.color }} />
            {quality.pct}%
          </span>
        )}
      </div>

      {/* Contact row */}
      {hasContactInfo && (
        <div className="flex flex-wrap gap-1.5 pt-2 mt-auto border-t" style={{ borderColor: '#F0F9F9' }}>
          {lender.website && (
            <a href={lender.website} target="_blank" rel="noopener noreferrer"
               className="inline-flex items-center gap-1 px-3 py-1.5 text-[11px] font-semibold
                          text-white rounded-lg hover:shadow-md transition-all duration-200 hover:-translate-y-0.5"
               style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)' }}>
              <Globe className="w-3 h-3" />
              Website
            </a>
          )}
          {lender.phone && (
            <a href={`tel:${lender.phone}`}
               className="inline-flex items-center gap-1 px-3 py-1.5 text-[11px] font-semibold
                          rounded-lg transition-colors"
               style={{ background: '#F7FAFA', color: '#3D6363' }}>
              <Phone className="w-3 h-3" />
              Call
            </a>
          )}
          {lender.email && (
            <a href={`mailto:${lender.email}`}
               className="inline-flex items-center gap-1 px-3 py-1.5 text-[11px] font-semibold
                          rounded-lg transition-colors"
               style={{ background: '#F7FAFA', color: '#3D6363' }}>
              <Mail className="w-3 h-3" />
              Email
            </a>
          )}
        </div>
      )}
    </div>
  )
}
