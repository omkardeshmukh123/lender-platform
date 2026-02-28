'use client'

import { Search, ChevronDown } from 'lucide-react'

interface SearchFilterProps {
  filters: {
    search: string
    loanType: string
    state: string
    ticketSize: string
    companyType: string
    sortBy: string
  }
  onFilterChange: (key: string, value: string) => void
  resultsCount: number
  loanTypes: string[]
  states: string[]
  ticketSizes: string[]
  companyTypes: string[]
  listingStatus: string[]
}

export function SearchFilter({ 
  filters, 
  onFilterChange, 
  resultsCount,
  loanTypes,
  states,
  ticketSizes,
  companyTypes,
  listingStatus
}: SearchFilterProps) {
  const handleResetFilters = () => {
    onFilterChange('search', '')
    onFilterChange('loanType', 'All Loan Types')
    onFilterChange('state', 'All States')
    onFilterChange('ticketSize', 'All Sizes')
    onFilterChange('companyType', 'All Types')
    onFilterChange('sortBy', 'All')
  }

  return (
    <section className="relative bg-gradient-to-b from-gray-50 to-white py-8">
      <div className="max-w-7xl mx-auto px-8">
        {/* Search Bar */}
        <div className="mb-6">
          <div className="relative">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
            <input
              type="text"
              placeholder="Search by company name, city or product..."
              value={filters.search}
              onChange={(e) => onFilterChange('search', e.target.value)}
              className="w-full pl-12 pr-4 py-3.5 border border-[#E5E7EB] rounded-xl focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/30 focus:border-[#3B5CCC] focus:shadow-sm transition-all duration-200 bg-white"
            />
          </div>
        </div>

        {/* Filter Card */}
        <div className="bg-white rounded-2xl shadow-md shadow-gray-200/50 p-6">
          {/* Card Header */}
          <div className="mb-6">
            <h2 className="text-lg font-semibold text-gray-900">Filter Lenders</h2>
          </div>

          {/* Filter Dropdowns */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-5 mb-6">
            {/* Loan Type */}
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-2">
                Loan Type
              </label>
              <div className="relative">
                <select
                  value={filters.loanType}
                  onChange={(e) => onFilterChange('loanType', e.target.value)}
                  className="w-full px-4 py-2.5 text-sm appearance-none border border-[#E5E7EB] rounded-xl focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC] bg-white cursor-pointer transition-all duration-200 hover:border-gray-300"
                >
                  {loanTypes.map((type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ))}
                </select>
                <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
              </div>
            </div>

            {/* State */}
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-2">
                State
              </label>
              <div className="relative">
                <select
                  value={filters.state}
                  onChange={(e) => onFilterChange('state', e.target.value)}
                  className="w-full px-4 py-2.5 text-sm appearance-none border border-[#E5E7EB] rounded-xl focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC] bg-white cursor-pointer transition-all duration-200 hover:border-gray-300"
                >
                  {states.map((state) => (
                    <option key={state} value={state}>
                      {state}
                    </option>
                  ))}
                </select>
                <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
              </div>
            </div>

            {/* AUM */}
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-2">
                AUM (Size)
              </label>
              <div className="relative">
                <select
                  value={filters.ticketSize}
                  onChange={(e) => onFilterChange('ticketSize', e.target.value)}
                  className="w-full px-4 py-2.5 text-sm appearance-none border border-[#E5E7EB] rounded-xl focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC] bg-white cursor-pointer transition-all duration-200 hover:border-gray-300"
                >
                  {ticketSizes.map((size) => (
                    <option key={size} value={size}>
                      {size}
                    </option>
                  ))}
                </select>
                <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
              </div>
            </div>

            {/* Company Type */}
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-2">
                Company Type
              </label>
              <div className="relative">
                <select
                  value={filters.companyType}
                  onChange={(e) => onFilterChange('companyType', e.target.value)}
                  className="w-full px-4 py-2.5 text-sm appearance-none border border-[#E5E7EB] rounded-xl focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC] bg-white cursor-pointer transition-all duration-200 hover:border-gray-300"
                >
                  {companyTypes.map((type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ))}
                </select>
                <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
              </div>
            </div>

            {/* Listing Status */}
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-2">
                Listing Status
              </label>
              <div className="relative">
                <select
                  value={filters.sortBy}
                  onChange={(e) => onFilterChange('sortBy', e.target.value)}
                  className="w-full px-4 py-2.5 text-sm appearance-none border border-[#E5E7EB] rounded-xl focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/20 focus:border-[#3B5CCC] bg-white cursor-pointer transition-all duration-200 hover:border-gray-300"
                >
                  {listingStatus.map((status) => (
                    <option key={status} value={status}>
                      {status}
                    </option>
                  ))}
                </select>
                <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
              </div>
            </div>
          </div>

          {/* Results Count and Reset */}
          <div className="flex items-center justify-between pt-4 border-t border-gray-100">
            <p className="text-sm text-gray-600">
              Found <span className="font-semibold text-[#3B5CCC]">{resultsCount} lender(s)</span>
            </p>
            <button
              onClick={handleResetFilters}
              className="text-sm font-medium text-[#3B5CCC] hover:text-[#2d4aa8] transition-colors"
            >
              Reset Filters
            </button>
          </div>
        </div>
      </div>
    </section>
  )
}