'use client'

/**
 * MatchResultCard.tsx
 * ====================
 * Card for /match results page.
 *
 * Displays a ranked loan match returned by POST /v1/loans/match.
 * Key features:
 *   - Match score badge (green ≥80, amber ≥60, gray otherwise)
 *   - "Best Match" ribbon for rank=1
 *   - Interest rate, loan range, tenure, processing fee tiles
 *   - Collapsible "Why this match?" section with match_reasons
 *   - Apply Now (links to lender website) + Save/Bookmark button
 *   - Collateral warning if required
 */

import { useState } from 'react'
import {
  BadgeCheck,
  Bookmark,
  BookmarkCheck,
  ChevronDown,
  ChevronUp,
  Clock,
  Globe,
  IndianRupee,
  Percent,
  ShieldAlert,
} from 'lucide-react'
import { useSaved } from './SaveContext'

// ─────────────────────────────────────────────────────────────
// TYPES  (matches MatchedLoan schema from backend)
// ─────────────────────────────────────────────────────────────

export interface MatchedLoanItem {
  lender_id:          number
  lender_name:        string
  policy_id:          number
  product_name?:      string | null
  loan_type?:         string | null
  match_score:        number        // 0–100
  match_reasons:      string[]
  interest_rate_min?: number | null
  interest_rate_max?: number | null
  loan_amount_min?:   number | null
  loan_amount_max?:   number | null
  processing_fee?:    number | null
  tenure_min?:        number | null
  tenure_max?:        number | null
  credit_score_min?:  number | null
  collateral_required: boolean
  eligibility_notes?: string | null
  website?:           string | null
}

// ─────────────────────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────────────────────

function fmtAmount(val: number | null | undefined): string | null {
  if (!val) return null
  if (val >= 10_000_000) return `₹${(val / 10_000_000).toFixed(1)}Cr`
  if (val >= 100_000)    return `₹${(val / 100_000).toFixed(0)}L`
  return `₹${val.toLocaleString('en-IN')}`
}

function fmtRate(
  min?: number | null,
  max?: number | null,
): string | null {
  if (!min && !max) return null
  if (min && max && min !== max) return `${min}% – ${max}%`
  return `${(min || max)!}%`
}

function scoreColor(score: number) {
  if (score >= 80) return 'bg-emerald-500'
  if (score >= 60) return 'bg-amber-500'
  return 'bg-gray-400'
}

// ─────────────────────────────────────────────────────────────
// SUB-COMPONENTS
// ─────────────────────────────────────────────────────────────

function ScoreBadge({ score }: { score: number }) {
  return (
    <div
      className={`${scoreColor(score)} text-white rounded-xl px-3 py-2
                  text-center min-w-[58px] flex-shrink-0`}
    >
      <div className="text-xl font-bold leading-none">{Math.round(score)}</div>
      <div className="text-[9px] opacity-80 uppercase tracking-wide font-medium mt-0.5">
        match
      </div>
    </div>
  )
}

interface MetricTileProps {
  icon:  React.ReactNode
  label: string
  value: string
  accent?: boolean
}
function MetricTile({ icon, label, value, accent }: MetricTileProps) {
  return (
    <div className={`rounded-xl p-3 ${accent ? 'bg-[#E6F4F4]' : 'bg-gray-50'}`}>
      <div className="flex items-center gap-1.5 mb-1">
        {icon}
        <span className="text-[10px] text-gray-500 uppercase tracking-wide font-semibold">
          {label}
        </span>
      </div>
      <div className={`text-sm font-bold ${accent ? 'text-[#1A7070]' : 'text-gray-800'}`}>
        {value}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────
// MAIN COMPONENT
// ─────────────────────────────────────────────────────────────

interface Props {
  item: MatchedLoanItem
  rank: number
}

export function MatchResultCard({ item, rank }: Props) {
  const [reasonsOpen, setReasonsOpen] = useState(rank <= 2)  // top 2 expanded by default
  const { isSaved, toggle } = useSaved()

  const savedEntry = {
    id:          String(item.lender_id),
    name:        item.lender_name,
    companyType: '',
    website:     item.website,
  }
  const saved = isSaved(String(item.lender_id))

  const rate       = fmtRate(item.interest_rate_min, item.interest_rate_max)
  const amtMin     = fmtAmount(item.loan_amount_min)
  const amtMax     = fmtAmount(item.loan_amount_max)
  const amtRange   = [amtMin, amtMax].filter(Boolean).join(' – ') || null
  const tenureText = [item.tenure_min, item.tenure_max]
    .filter(Boolean).join(' – ') + (item.tenure_min || item.tenure_max ? ' mo' : '')

  const metrics: MetricTileProps[] = [
    ...(rate      ? [{ icon: <Percent    className="w-3.5 h-3.5 text-[#1A7070]" />, label: 'Interest', value: `${rate} p.a.`, accent: true  }] : []),
    ...(amtRange  ? [{ icon: <IndianRupee className="w-3.5 h-3.5 text-gray-400" />, label: 'Loan range', value: amtRange }] : []),
    ...((item.tenure_min || item.tenure_max) ? [{ icon: <Clock className="w-3.5 h-3.5 text-gray-400" />, label: 'Tenure', value: tenureText }] : []),
    ...(item.processing_fee != null ? [{ icon: <Percent className="w-3.5 h-3.5 text-gray-400" />, label: 'Proc. fee', value: `${item.processing_fee}%` }] : []),
  ]

  return (
    <div
      className={`bg-white rounded-xl border overflow-hidden
                  transition-shadow duration-200 hover:shadow-md ${
        rank === 1
          ? 'border-emerald-200 shadow-sm shadow-emerald-50'
          : 'border-gray-200'
      }`}
    >
      {/* Best match ribbon */}
      {rank === 1 && (
        <div className="bg-emerald-50 border-b border-emerald-100
                        px-4 py-1.5 flex items-center gap-1.5">
          <BadgeCheck className="w-3.5 h-3.5 text-emerald-600" />
          <span className="text-xs font-semibold text-emerald-700">
            Best Match
          </span>
        </div>
      )}

      <div className="p-5">
        {/* Header */}
        <div className="flex items-start gap-3 mb-4">
          <div className="flex-1 min-w-0">
            <h3 className="font-semibold text-gray-900 text-base leading-tight">
              {item.lender_name}
            </h3>
            {item.product_name && (
              <p className="text-sm text-gray-500 mt-0.5 truncate">
                {item.product_name}
              </p>
            )}
            {item.credit_score_min && (
              <p className="text-xs text-gray-400 mt-1">
                Min. CIBIL: {item.credit_score_min}
              </p>
            )}
          </div>
          <ScoreBadge score={item.match_score} />
        </div>

        {/* Metrics grid */}
        {metrics.length > 0 && (
          <div className={`grid gap-2 mb-4 ${
            metrics.length >= 3 ? 'grid-cols-2 sm:grid-cols-4' : 'grid-cols-2'
          }`}>
            {metrics.map((m, i) => <MetricTile key={i} {...m} />)}
          </div>
        )}

        {/* Collateral warning */}
        {item.collateral_required && (
          <div className="flex items-center gap-2 text-xs text-amber-700
                          bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-4">
            <ShieldAlert className="w-3.5 h-3.5 flex-shrink-0" />
            Collateral / security required
          </div>
        )}

        {/* Why this match */}
        {item.match_reasons.length > 0 && (
          <div className="border border-gray-100 rounded-xl overflow-hidden mb-4">
            <button
              type="button"
              onClick={() => setReasonsOpen(o => !o)}
              className="w-full flex items-center justify-between
                         px-4 py-2.5 bg-gray-50 hover:bg-gray-100
                         text-sm font-medium text-gray-700 transition-colors"
            >
              <span className="flex items-center gap-1.5">
                <BadgeCheck className="w-3.5 h-3.5 text-[#1A7070]" />
                Why this match?
              </span>
              {reasonsOpen
                ? <ChevronUp   className="w-4 h-4 text-gray-400" />
                : <ChevronDown className="w-4 h-4 text-gray-400" />}
            </button>
            {reasonsOpen && (
              <ul className="px-4 py-3 space-y-1.5 bg-white">
                {item.match_reasons.map((reason, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-gray-600">
                    <span className="text-emerald-500 mt-0.5 flex-shrink-0 font-bold">✓</span>
                    {reason}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {/* Eligibility notes */}
        {item.eligibility_notes && (
          <p className="text-xs text-gray-500 mb-4 leading-relaxed">
            {item.eligibility_notes}
          </p>
        )}

        {/* Action row */}
        <div className="flex items-center gap-2">
          {item.website ? (
            <a
              href={item.website}
              target="_blank"
              rel="noopener noreferrer"
              className="flex-1 inline-flex items-center justify-center gap-2
                         px-4 py-2.5 bg-[#1A7070] text-white text-sm font-semibold
                         rounded-xl hover:bg-[#0F4848] transition-colors"
            >
              <Globe className="w-4 h-4" />
              Apply Now
            </a>
          ) : (
            <div className="flex-1 text-xs text-gray-400 italic py-2">
              Visit lender website to apply
            </div>
          )}

          <button
            type="button"
            onClick={() => toggle(savedEntry)}
            title={saved ? 'Remove from shortlist' : 'Shortlist this lender'}
            className={`p-2.5 rounded-xl border transition-all ${
              saved
                ? 'bg-[#EEF2FF] border-blue-200 text-[#1A7070]'
                : 'bg-white border-gray-200 text-gray-400 hover:text-[#1A7070] hover:border-blue-200'
            }`}
          >
            {saved
              ? <BookmarkCheck className="w-4 h-4" />
              : <Bookmark      className="w-4 h-4" />}
          </button>
        </div>
      </div>
    </div>
  )
}

