'use client'

/**
 * SearchFilter.tsx — Final production version
 * =============================================
 *
 * WHAT'S IN HERE:
 *   • Multi-select dropdowns for: Loan Type, AUM Size, Company Type
 *   • Single select (native) for: State, Listing Status
 *   • Sort buttons: AUM and Est. Year (click cycles desc → asc → clear)
 *   • Search bar with 400ms debounce
 *   • Active filter tags row — remove individual filters with ×
 *   • "Reset all" clears every filter including sort
 *
 * EDGE CASES HANDLED:
 *   ✅ Dropdown closes on click outside
 *   ✅ Dropdown closes on Escape key
 *   ✅ Search input clears on Escape key
 *   ✅ Debounce timer cleaned up on component unmount
 *   ✅ Parent reset syncs local search state (useEffect on filters.search)
 *   ✅ Empty options list → shows "No options" message
 *   ✅ Sort cycles: 1st click = desc, 2nd = asc, 3rd = clear sort
 *   ✅ Active filter count badge on header
 *   ✅ Accessible: aria-expanded, aria-label, role=listbox, role=option
 *   ✅ Responsive: 1-col mobile → 2-col tablet → 5-col desktop
 *   ✅ TypeScript: all types exported for use in dashboard/page.tsx
 */

import {
  useState,
  useRef,
  useEffect,
  useCallback,
  KeyboardEvent,
} from 'react'
import {
  Search,
  ChevronDown,
  X,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  Check,
  SlidersHorizontal,
} from 'lucide-react'

// ─────────────────────────────────────────────────────────────
// EXPORTED TYPES  ← dashboard/page.tsx imports these
// ─────────────────────────────────────────────────────────────

export type SortField     = 'aum_crores' | 'established_year' | ''
export type SortDirection = 'asc' | 'desc'

export const YEAR_RANGE_OPTIONS = [
  'All Years', 'Before 2000', '2000–2009', '2010–2019', '2020 & after',
] as const
export type YearRange = typeof YEAR_RANGE_OPTIONS[number]

export interface MultiFilters {
  search:               string     // raw text — dashboard debounces via SearchFilter
  loanType:             string[]   // multi  e.g. ["MSME Loan", "Gold Loan"]
  state:                string     // single e.g. "Maharashtra" | "All States"
  ticketSize:           string[]   // multi  e.g. ["Small", "Mid"]
  companyType:          string[]   // multi  e.g. ["NBFC", "PSU Bank"]
  operatingIntensity:   string[]   // multi  e.g. ["Pan India", "Regional"]
  businessSector:       string[]   // multi  e.g. ["MSME", "Housing"]
  listingStatus:        string     // single "All" | "Listed Only" | "Unlisted Only"
  establishedYearRange: string     // single from YEAR_RANGE_OPTIONS
  sortField:            SortField
  sortDirection:        SortDirection
}

/** Use this as the initial state in dashboard/page.tsx */
export const DEFAULT_FILTERS: MultiFilters = {
  search:               '',
  loanType:             [],
  state:                'All States',
  ticketSize:           [],
  companyType:          [],
  operatingIntensity:   [],
  businessSector:       [],
  listingStatus:        'All',
  establishedYearRange: 'All Years',
  sortField:            '',
  sortDirection:        'desc',
}

// ─────────────────────────────────────────────────────────────
// PROPS
// ─────────────────────────────────────────────────────────────

export interface SearchFilterProps {
  filters:        MultiFilters
  /**
   * Called whenever any filter changes.
   * Key is the filter name, value is the new value.
   * Uses a generic so TypeScript knows the value type matches the key.
   */
  onFilterChange: <K extends keyof MultiFilters>(key: K, value: MultiFilters[K]) => void
  resultsCount:   number
  loanTypes:      string[]   // Do NOT include an "All" prefix item
  states:         string[]   // MUST include "All States" as first item
  ticketSizes:    string[]   // e.g. ['Micro','Small','Mid','Large']
  companyTypes:          string[]   // e.g. ['NBFC','Private Bank',...]
  operatingIntensities?: string[]   // e.g. ['Pan India','Regional','Single State']
  businessSectors?:      string[]   // e.g. ['MSME','Housing','Gold',...]
  listingStatus:         string[]   // ['All','Listed Only','Unlisted Only']
  yearRanges?:    string[]   // defaults to YEAR_RANGE_OPTIONS
  /** Render as a left sidebar instead of a horizontal bar (default: false) */
  sidebar?:       boolean
  /** Controls the mobile drawer open/closed state */
  sidebarOpen?:   boolean
  /** Called when the mobile close button or backdrop is clicked */
  onClose?:       () => void
}

// ─────────────────────────────────────────────────────────────
// MULTI-SELECT DROPDOWN
// ─────────────────────────────────────────────────────────────

interface MultiSelectProps {
  id:          string
  label:       string
  options:     string[]
  selected:    string[]
  onChange:    (next: string[]) => void
  placeholder: string
}

function MultiSelectDropdown({
  id, label, options, selected, onChange, placeholder,
}: MultiSelectProps) {
  const [open, setOpen] = useState(false)
  const ref             = useRef<HTMLDivElement>(null)

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  // Close on Escape
  useEffect(() => {
    if (!open) return
    const handler = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open])

  const toggle    = useCallback((opt: string) => {
    onChange(selected.includes(opt)
      ? selected.filter(s => s !== opt)
      : [...selected, opt])
  }, [selected, onChange])

  const selectAll = () => onChange([...options])
  const clearAll  = (e: React.MouseEvent) => { e.stopPropagation(); onChange([]) }

  const hasSelection = selected.length > 0
  const displayText  = !hasSelection
    ? placeholder
    : selected.length === 1
      ? selected[0]
      : `${selected.length} selected`

  return (
    <div ref={ref} className="relative">

      {/* Label + selection count badge */}
      <label
        htmlFor={id}
        className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2 cursor-pointer"
      >
        {label}
        {hasSelection && (
          <span
            className="ml-1.5 inline-flex items-center justify-center
                       w-[18px] h-[18px] bg-[#1A7070] text-white
                       text-[10px] font-bold rounded-full align-middle"
          >
            {selected.length}
          </span>
        )}
      </label>

      {/* Trigger button */}
      <button
        id={id}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${label} filter — ${hasSelection ? `${selected.length} selected` : 'none selected'}`}
        onClick={() => setOpen(o => !o)}
        className={[
          'w-full px-3.5 py-2.5 text-sm text-left',
          'flex items-center justify-between gap-2',
          'border rounded-xl transition-all duration-150 bg-white cursor-pointer min-h-[42px]',
          open
            ? 'border-[#1A7070] ring-2 ring-[#1A7070]/20 shadow-sm'
            : hasSelection
              ? 'border-[#1A7070]/60 bg-[#E6F4F4]'
              : 'border-gray-200 hover:border-gray-300',
        ].join(' ')}
      >
        <span className={`truncate text-sm ${
          hasSelection ? 'text-[#1A7070] font-medium' : 'text-gray-400'
        }`}>
          {displayText}
        </span>

        <span className="flex items-center gap-1 flex-shrink-0">
          {hasSelection && (
            <span
              role="button"
              tabIndex={0}
              aria-label={`Clear all ${label} selections`}
              onClick={clearAll}
              onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.stopPropagation(); onChange([]) } }}
              className="p-0.5 rounded hover:bg-blue-200/60 text-[#3B5CCC]"
            >
              <X className="w-3 h-3" />
            </span>
          )}
          <ChevronDown
            className={`w-4 h-4 text-gray-400 flex-shrink-0 transition-transform duration-200 ${
              open ? 'rotate-180' : ''
            }`}
          />
        </span>
      </button>

      {/* Dropdown panel */}
      {open && (
        <div
          role="listbox"
          aria-multiselectable="true"
          aria-label={`${label} options`}
          className="absolute z-50 top-full mt-1.5 w-full min-w-[200px]
                     bg-white border border-gray-200 rounded-xl
                     shadow-xl shadow-gray-200/60 overflow-hidden"
        >
          {/* Select all / Clear header */}
          <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100 bg-gray-50">
            <button
              type="button"
              onClick={selectAll}
              className="text-xs text-[#1A7070] font-semibold hover:underline"
            >
              Select all
            </button>
            <button
              type="button"
              onClick={() => onChange([])}
              className="text-xs text-gray-400 font-medium hover:text-gray-700 hover:underline"
            >
              Clear
            </button>
          </div>

          {/* Options list */}
          <ul className="max-h-56 overflow-y-auto py-1">
            {options.length === 0 && (
              <li className="px-4 py-3 text-sm text-gray-400 text-center italic">
                No options available
              </li>
            )}
            {options.map(opt => {
              const isSel = selected.includes(opt)
              return (
                <li key={opt}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={isSel}
                    onClick={() => toggle(opt)}
                    className={[
                      'w-full flex items-center gap-3 px-3 py-2',
                      'text-sm text-left transition-colors duration-100',
                      isSel
                        ? 'bg-[#E6F4F4] text-[#1A7070]'
                        : 'text-gray-700 hover:bg-gray-50',
                    ].join(' ')}
                  >
                    {/* Checkbox visual */}
                    <span className={[
                      'flex-shrink-0 w-4 h-4 rounded border-[1.5px]',
                      'flex items-center justify-center transition-all',
                      isSel
                        ? 'bg-[#1A7070] border-[#1A7070]'
                        : 'border-gray-300 bg-white',
                    ].join(' ')}>
                      {isSel && (
                        <Check className="w-2.5 h-2.5 text-white" strokeWidth={3} />
                      )}
                    </span>
                    <span className="truncate">{opt}</span>
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────
// SINGLE SELECT  (native <select> — no custom logic needed)
// ─────────────────────────────────────────────────────────────

interface SingleSelectProps {
  id:       string
  label:    string
  options:  string[]
  value:    string
  onChange: (val: string) => void
}

function SingleSelect({ id, label, options, value, onChange }: SingleSelectProps) {
  // Active = value is not the first "All …" option
  const isActive = Boolean(value) && value !== options[0]

  return (
    <div>
      <label
        htmlFor={id}
        className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2 cursor-pointer"
      >
        {label}
      </label>
      <div className="relative">
        <select
          id={id}
          value={value}
          onChange={e => onChange(e.target.value)}
          className={[
            'w-full px-3.5 py-2.5 text-sm appearance-none border rounded-xl cursor-pointer min-h-[42px]',
            'focus:outline-none focus:ring-2 focus:ring-[#1A7070]/20 focus:border-[#1A7070]',
            'transition-all duration-150 bg-white',
            isActive
              ? 'border-[#1A7070]/60 bg-[#E6F4F4] text-[#1A7070] font-medium'
              : 'border-gray-200 hover:border-gray-300 text-gray-400',
          ].join(' ')}
        >
          {options.map(opt => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
        <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────
// SORT BUTTON
// ─────────────────────────────────────────────────────────────

interface SortBtnProps {
  label:       string
  field:       SortField
  activeField: SortField
  activeDir:   SortDirection
  onSort:      (f: SortField, d: SortDirection) => void
  onClear:     () => void
}

function SortBtn({ label, field, activeField, activeDir, onSort, onClear }: SortBtnProps) {
  const isActive = activeField === field

  // 1st click → desc  |  2nd click → asc  |  3rd click → clear
  const handleClick = () => {
    if (!isActive)              onSort(field, 'desc')
    else if (activeDir === 'desc') onSort(field, 'asc')
    else                        onClear()
  }

  const Icon = !isActive
    ? ArrowUpDown
    : activeDir === 'desc'
      ? ArrowDown
      : ArrowUp

  const tooltip = !isActive
    ? `Sort by ${label} (highest first)`
    : activeDir === 'desc'
      ? `${label}: high → low. Click to flip`
      : `${label}: low → high. Click to clear`

  return (
    <button
      type="button"
      onClick={handleClick}
      title={tooltip}
      className={[
        'inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium',
        'border transition-all duration-150 select-none whitespace-nowrap',
        isActive
          ? 'bg-[#1A7070] text-white border-[#1A7070] shadow-sm shadow-teal-200'
          : 'bg-white text-gray-600 border-gray-200 hover:border-gray-300 hover:text-gray-900',
      ].join(' ')}
    >
      <Icon className={`w-3.5 h-3.5 ${!isActive ? 'opacity-40' : ''}`} />
      {label}
      {isActive && (
        <span className="text-[10px] opacity-80 font-normal">
          {activeDir === 'desc' ? '↓' : '↑'}
        </span>
      )}
    </button>
  )
}

// ─────────────────────────────────────────────────────────────
// ACTIVE FILTER TAGS ROW
// ─────────────────────────────────────────────────────────────

function ActiveFilterTags({
  filters,
  onFilterChange,
}: {
  filters:        MultiFilters
  onFilterChange: SearchFilterProps['onFilterChange']
}) {
  const tags: Array<{ key: string; label: string; onRemove: () => void }> = []

  // Loan type tags
  filters.loanType.forEach(t => tags.push({
    key:      `loan-${t}`,
    label:    t,
    onRemove: () => onFilterChange('loanType', filters.loanType.filter(x => x !== t)),
  }))

  // AUM size tags
  filters.ticketSize.forEach(t => tags.push({
    key:      `size-${t}`,
    label:    `Size: ${t}`,
    onRemove: () => onFilterChange('ticketSize', filters.ticketSize.filter(x => x !== t)),
  }))

  // Company type tags
  filters.companyType.forEach(t => tags.push({
    key:      `type-${t}`,
    label:    t,
    onRemove: () => onFilterChange('companyType', filters.companyType.filter(x => x !== t)),
  }))

  // Business sector tags
  filters.businessSector.forEach(t => tags.push({
    key:      `sector-${t}`,
    label:    `Sector: ${t}`,
    onRemove: () => onFilterChange('businessSector', filters.businessSector.filter(x => x !== t)),
  }))

  // Operating intensity tags
  filters.operatingIntensity.forEach(t => tags.push({
    key:      `intensity-${t}`,
    label:    `Coverage: ${t}`,
    onRemove: () => onFilterChange('operatingIntensity', filters.operatingIntensity.filter(x => x !== t)),
  }))

  // State tag
  if (filters.state && filters.state !== 'All States') {
    tags.push({
      key:      'state',
      label:    `📍 ${filters.state}`,
      onRemove: () => onFilterChange('state', 'All States'),
    })
  }

  // Listing status tag
  if (filters.listingStatus && filters.listingStatus !== 'All') {
    tags.push({
      key:      'listing',
      label:    filters.listingStatus,
      onRemove: () => onFilterChange('listingStatus', 'All'),
    })
  }

  // Established year tag
  if (filters.establishedYearRange && filters.establishedYearRange !== 'All Years') {
    tags.push({
      key:      'year',
      label:    `Est. ${filters.establishedYearRange}`,
      onRemove: () => onFilterChange('establishedYearRange', 'All Years'),
    })
  }

  // Sort tag
  if (filters.sortField) {
    const sortLabel = filters.sortField === 'aum_crores' ? 'AUM' : 'Est. Year'
    const dirLabel  = filters.sortDirection === 'desc' ? '↓' : '↑'
    tags.push({
      key:      'sort',
      label:    `Sort: ${sortLabel} ${dirLabel}`,
      onRemove: () => {
        onFilterChange('sortField', '')
        onFilterChange('sortDirection', 'desc')
      },
    })
  }

  if (tags.length === 0) return null

  return (
    <div className="flex flex-wrap items-center gap-2 pt-4 mt-2 border-t border-gray-100">
      <span className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide flex-shrink-0">
        Active:
      </span>
      {tags.map(tag => (
        <button
          key={tag.key}
          type="button"
          onClick={tag.onRemove}
          className="inline-flex items-center gap-1.5 px-2.5 py-1
                     bg-[#E6F4F4] text-[#1A7070] text-xs font-medium
                     rounded-lg border border-teal-100
                     hover:bg-teal-100 transition-colors duration-100 group"
        >
          {tag.label}
          <X className="w-3 h-3 opacity-50 group-hover:opacity-100" />
        </button>
      ))}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────
// MAIN COMPONENT
// ─────────────────────────────────────────────────────────────

export function SearchFilter({
  filters,
  onFilterChange,
  resultsCount,
  loanTypes,
  states,
  ticketSizes,
  companyTypes,
  operatingIntensities = ['Pan India', 'Regional', 'Single State'],
  businessSectors = ['MSME', 'Housing', 'Gold', 'Vehicle', 'Microfinance', 'Agriculture', 'Retail'],
  listingStatus,
  yearRanges = [...YEAR_RANGE_OPTIONS],
  sidebar = false,
  sidebarOpen = false,
  onClose,
}: SearchFilterProps) {

  // Local search state for instant visual feedback while debounce runs
  const [localSearch, setLocalSearch] = useState(filters.search)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // If parent resets filters (e.g. "Reset all"), sync the search input
  useEffect(() => {
    setLocalSearch(filters.search)
  }, [filters.search])

  // Cleanup debounce on unmount
  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [])

  const handleSearchChange = (val: string) => {
    setLocalSearch(val)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      onFilterChange('search', val)
    }, 400)
  }

  const clearSearch = () => {
    setLocalSearch('')
    if (debounceRef.current) clearTimeout(debounceRef.current)
    onFilterChange('search', '')
  }

  const handleResetAll = () => {
    clearSearch()
    onFilterChange('loanType',             [])
    onFilterChange('state',                'All States')
    onFilterChange('ticketSize',           [])
    onFilterChange('companyType',          [])
    onFilterChange('operatingIntensity',   [])
    onFilterChange('businessSector',       [])
    onFilterChange('listingStatus',        'All')
    onFilterChange('establishedYearRange', 'All Years')
    onFilterChange('sortField',            '')
    onFilterChange('sortDirection',        'desc')
  }

  const handleSort = (field: SortField, dir: SortDirection) => {
    onFilterChange('sortField',     field)
    onFilterChange('sortDirection', dir)
  }

  const clearSort = () => {
    onFilterChange('sortField',     '')
    onFilterChange('sortDirection', 'desc')
  }

  // Count active filters for the badge
  const activeCount =
    (filters.search.trim()                                    ? 1 : 0) +
    filters.loanType.length +
    filters.ticketSize.length +
    filters.companyType.length +
    filters.operatingIntensity.length +
    filters.businessSector.length +
    (filters.state                !== 'All States'            ? 1 : 0) +
    (filters.listingStatus        !== 'All'                   ? 1 : 0) +
    (filters.establishedYearRange !== 'All Years'             ? 1 : 0) +
    (filters.sortField                                        ? 1 : 0)

  // ── Shared sub-sections ─────────────────────────────────────────

  const searchBar = (
    <div className="relative">
      <Search
        className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400 pointer-events-none"
        aria-hidden="true"
      />
      <input
        type="text"
        aria-label="Search lenders"
        placeholder={sidebar ? 'Search lenders…' : 'Search by company name, city or product...'}
        value={localSearch}
        onChange={e => handleSearchChange(e.target.value)}
        onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
          if (e.key === 'Escape') clearSearch()
        }}
        className="w-full pl-12 pr-10 py-3.5
                   border border-gray-200 rounded-xl bg-white
                   text-gray-800 placeholder-gray-400 text-sm
                   focus:outline-none focus:ring-2 focus:ring-[#1A7070]/30
                   focus:border-[#1A7070] transition-all duration-150"
      />
      {localSearch && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={clearSearch}
          className="absolute right-3.5 top-1/2 -translate-y-1/2
                     p-1 rounded-full text-gray-400
                     hover:text-gray-600 hover:bg-gray-100 transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      )}
    </div>
  )

  const filterHeader = (
    <div className="flex items-center justify-between">
      <h2 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
        <SlidersHorizontal className="w-4 h-4 text-[#1A7070]" aria-hidden="true" />
        Filter Lenders
        {activeCount > 0 && (
          <span className="text-xs font-bold text-[#1A7070] bg-[#E6F4F4] px-2 py-0.5 rounded-full">
            {activeCount} active
          </span>
        )}
      </h2>
      {activeCount > 0 && (
        <button
          type="button"
          onClick={handleResetAll}
          className="text-xs font-medium text-gray-400 hover:text-[#1A7070]
                     flex items-center gap-1.5 transition-colors"
        >
          <X className="w-3.5 h-3.5" />
          Reset all
        </button>
      )}
    </div>
  )

  const sortRow = (
    <div className="flex flex-wrap items-center gap-2 pt-3 border-t border-gray-100">
      <span className="text-xs font-semibold text-gray-400 uppercase tracking-wide">
        Sort:
      </span>
      <SortBtn
        label="AUM"
        field="aum_crores"
        activeField={filters.sortField}
        activeDir={filters.sortDirection}
        onSort={handleSort}
        onClear={clearSort}
      />
      <SortBtn
        label="Est. Year"
        field="established_year"
        activeField={filters.sortField}
        activeDir={filters.sortDirection}
        onSort={handleSort}
        onClear={clearSort}
      />
      {!sidebar && (
        <span className="text-[11px] text-gray-300 hidden sm:block">
          click again to flip ↑↓ · 3rd click clears
        </span>
      )}
      {!sidebar && (
        <div className="ml-auto text-sm text-gray-600">
          <span className="font-bold text-[#1A7070]">
            {resultsCount.toLocaleString('en-IN')}
          </span>
          {' '}lender{resultsCount !== 1 ? 's' : ''}
        </div>
      )}
    </div>
  )

  // ── Sidebar variant ──────────────────────────────────────────

  if (sidebar) {
    return (
      <>
        {/* Mobile backdrop */}
        {sidebarOpen && (
          <div
            className="fixed inset-0 bg-black/50 z-30 md:hidden"
            aria-hidden="true"
            onClick={onClose}
          />
        )}

        <aside
          aria-label="Filter panel"
          className={[
            'flex-shrink-0 overflow-y-auto',
            'fixed top-0 left-0 h-full z-40 transition-transform duration-300 ease-in-out',
            sidebarOpen ? 'translate-x-0' : '-translate-x-full',
            'md:sticky md:top-0 md:h-screen md:z-auto md:translate-x-0',
          ].join(' ')}
          style={{
            width: '280px',
            background: '#FAFBFD',
            borderRight: '1px solid #E9ECF2',
            boxShadow: 'inset -1px 0 0 rgba(26,43,107,0.04)',
          }}
        >
          <div className="p-4 flex flex-col gap-4">

            {/* Mobile close button */}
            <div className="flex items-center justify-between pb-3 border-b border-gray-100 md:hidden">
              <span className="font-semibold text-sm text-gray-900 flex items-center gap-2">
                <SlidersHorizontal className="w-4 h-4 text-[#1A7070]" aria-hidden="true" />
                Filters
              </span>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close filter panel"
                className="p-1.5 rounded-lg text-gray-400 hover:bg-gray-100
                           hover:text-gray-600 transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {searchBar}
            {filterHeader}

            {/* Filters — stacked vertically */}
            <div className="flex flex-col gap-3">
              <MultiSelectDropdown
                id="filter-loan-type"
                label="Loan Type"
                options={loanTypes}
                selected={filters.loanType}
                onChange={v => onFilterChange('loanType', v)}
                placeholder="All Loan Types"
              />
              <SingleSelect
                id="filter-state"
                label="State"
                options={states}
                value={filters.state || 'All States'}
                onChange={v => onFilterChange('state', v)}
              />
              <MultiSelectDropdown
                id="filter-aum-size"
                label="AUM Size"
                options={ticketSizes}
                selected={filters.ticketSize}
                onChange={v => onFilterChange('ticketSize', v)}
                placeholder="All Sizes"
              />
              <MultiSelectDropdown
                id="filter-company-type"
                label="Company Type"
                options={companyTypes}
                selected={filters.companyType}
                onChange={v => onFilterChange('companyType', v)}
                placeholder="All Types"
              />
              <MultiSelectDropdown
                id="filter-sector"
                label="Sector"
                options={businessSectors}
                selected={filters.businessSector}
                onChange={v => onFilterChange('businessSector', v)}
                placeholder="All Sectors"
              />
              <MultiSelectDropdown
                id="filter-intensity"
                label="Coverage"
                options={operatingIntensities}
                selected={filters.operatingIntensity}
                onChange={v => onFilterChange('operatingIntensity', v)}
                placeholder="All Coverage"
              />
              <SingleSelect
                id="filter-listing"
                label="Listing Status"
                options={listingStatus}
                value={filters.listingStatus || 'All'}
                onChange={v => onFilterChange('listingStatus', v)}
              />
              <SingleSelect
                id="filter-est-year"
                label="Established Year"
                options={yearRanges}
                value={filters.establishedYearRange || 'All Years'}
                onChange={v => onFilterChange('establishedYearRange', v)}
              />
            </div>

            {sortRow}

            <ActiveFilterTags
              filters={filters}
              onFilterChange={onFilterChange}
            />

            {/* Results count pinned at bottom of sidebar */}
            <div className="mt-auto pt-4 border-t border-gray-100 text-center">
              <div className="inline-flex items-baseline gap-1">
                <span className="text-2xl font-extrabold text-transparent bg-clip-text"
                      style={{ backgroundImage: 'linear-gradient(135deg,#0F4848,#1A7070)' }}>
                  {resultsCount.toLocaleString('en-IN')}
                </span>
                <span className="text-sm text-gray-400 font-medium">
                  lender{resultsCount !== 1 ? 's' : ''}
                </span>
              </div>
            </div>

          </div>
        </aside>
      </>
    )
  }

  // ── Bar variant (default) ────────────────────────────────────

  return (
    <section className="py-6" style={{ background: '#F8F9FC' }}>
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">

        <div className="mb-6">{searchBar}</div>

        {/* Filter + Sort Card */}
        <div className="bg-white rounded-2xl p-5" style={{ boxShadow: '0 2px 8px rgba(26,43,107,0.06), 0 1px 2px rgba(0,0,0,0.04)', border: '1px solid rgba(198,197,210,0.35)' }}>

          <div className="mb-5">{filterHeader}</div>

          {/* Filter row — 8 columns on large screens */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-8 gap-4 mb-5">
            <MultiSelectDropdown
              id="filter-loan-type"
              label="Loan Type"
              options={loanTypes}
              selected={filters.loanType}
              onChange={v => onFilterChange('loanType', v)}
              placeholder="All Loan Types"
            />
            <SingleSelect
              id="filter-state"
              label="State"
              options={states}
              value={filters.state || 'All States'}
              onChange={v => onFilterChange('state', v)}
            />
            <MultiSelectDropdown
              id="filter-aum-size"
              label="AUM Size"
              options={ticketSizes}
              selected={filters.ticketSize}
              onChange={v => onFilterChange('ticketSize', v)}
              placeholder="All Sizes"
            />
            <MultiSelectDropdown
              id="filter-company-type"
              label="Company Type"
              options={companyTypes}
              selected={filters.companyType}
              onChange={v => onFilterChange('companyType', v)}
              placeholder="All Types"
            />
            <MultiSelectDropdown
              id="filter-sector"
              label="Sector"
              options={businessSectors}
              selected={filters.businessSector}
              onChange={v => onFilterChange('businessSector', v)}
              placeholder="All Sectors"
            />
            <MultiSelectDropdown
              id="filter-intensity"
              label="Coverage"
              options={operatingIntensities}
              selected={filters.operatingIntensity}
              onChange={v => onFilterChange('operatingIntensity', v)}
              placeholder="All Coverage"
            />
            <SingleSelect
              id="filter-listing"
              label="Listing Status"
              options={listingStatus}
              value={filters.listingStatus || 'All'}
              onChange={v => onFilterChange('listingStatus', v)}
            />
            <SingleSelect
              id="filter-est-year"
              label="Est. Year"
              options={yearRanges}
              value={filters.establishedYearRange || 'All Years'}
              onChange={v => onFilterChange('establishedYearRange', v)}
            />
          </div>

          {sortRow}

          <ActiveFilterTags
            filters={filters}
            onFilterChange={onFilterChange}
          />

        </div>
      </div>
    </section>
  )
}
