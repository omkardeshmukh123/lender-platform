'use client'

import Image from 'next/image'

export function Hero() {
  return (
    <section className="relative bg-gradient-to-b from-blue-50/50 to-white py-16 md:py-24 overflow-hidden">
      <div className="max-w-7xl mx-auto px-8">
        <div className="grid md:grid-cols-2 gap-12 items-center">
          {/* Left side - Content */}
          <div className="space-y-6">
            <h1 className="text-4xl md:text-5xl font-semibold text-gray-900 leading-tight">
              Discover Verified NBFC Lending Partners
            </h1>
            <p className="text-lg text-gray-600 leading-relaxed">
              Search and filter RBI-registered lenders by type, geography and size.
            </p>
          </div>
          
          {/* Right side - Image */}
          <div className="relative flex justify-center md:justify-end">
            <div className="w-[92%]">
              <div className="rounded-2xl overflow-hidden shadow-lg border border-[#E5E7EB] bg-white">
                {/* Option 1: Use your own image */}
                <div className="relative w-full h-[400px]">
                  <Image
                    src="/hero-image.jpg"
                    alt="Financial consultation"
                    fill
                    className="object-cover"
                    priority
                  />
                </div>
                
                {/* Option 2: Use Unsplash image (free stock photos) */}
                {/* <div className="relative w-full h-[400px]">
                  <Image
                    src="https://images.unsplash.com/photo-1556742049-0cfed4f6a45d?w=800&h=600&fit=crop"
                    alt="Financial consultation"
                    fill
                    className="object-cover"
                    priority
                  />
                </div> */}
                
                {/* Option 3: Keep placeholder gradient */}
                {/* <div className="w-full h-[400px] bg-gradient-to-br from-blue-100 via-blue-50 to-indigo-100 flex items-center justify-center">
                  <div className="text-center">
                    <div className="w-16 h-16 mx-auto mb-4 bg-[#3B5CCC] rounded-full flex items-center justify-center">
                      <svg className="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                    </div>
                    <p className="text-gray-600 font-medium">Verified Lenders</p>
                  </div>
                </div> */}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}