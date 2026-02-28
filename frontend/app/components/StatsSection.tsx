'use client'

import { Building2, MapPinned, TrendingUp } from 'lucide-react'

interface StatsSectionProps {
  totalLenders?: number
}

export function StatsSection({ totalLenders = 928 }: StatsSectionProps) {
  const stats = [
    {
      icon: Building2,
      value: totalLenders.toLocaleString(),
      label: 'Verified Lenders',
      description: 'RBI-registered NBFCs & Banks'
    },
    {
      icon: MapPinned,
      value: '28+',
      label: 'States Covered',
      description: 'Pan-India presence'
    },
    {
      icon: TrendingUp,
      value: '₹12.5L Cr',
      label: 'Total AUM',
      description: 'Combined assets'
    }
  ]

  return (
    <section className="relative py-16 bg-gradient-to-b from-blue-50/30 to-gray-50/50">
      <div className="max-w-7xl mx-auto px-8">
        <div className="grid md:grid-cols-3 gap-8">
          {stats.map((stat, index) => {
            const Icon = stat.icon
            return (
              <div
                key={index}
                className="bg-white border border-[#E5E7EB] rounded-xl p-8 text-center"
              >
                <div className="inline-flex items-center justify-center w-12 h-12 bg-blue-50 rounded-lg mb-4">
                  <Icon className="w-6 h-6 text-[#3B5CCC]" />
                </div>
                <div className="text-4xl font-semibold text-gray-900 mb-2">
                  {stat.value}
                </div>
                <div className="text-lg font-medium text-gray-900 mb-1">
                  {stat.label}
                </div>
                <div className="text-sm text-gray-500">
                  {stat.description}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}