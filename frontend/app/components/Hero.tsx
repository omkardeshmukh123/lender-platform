'use client'

import Image from 'next/image'

export function Hero() {
  return (
    <section
      className="relative overflow-hidden py-10 sm:py-14"
      style={{ background: 'linear-gradient(160deg, #082E2E 0%, #0F4848 50%, #1A7070 100%)' }}
    >
      {/* Grid overlay */}
      <div className="absolute inset-0 opacity-[0.04] pointer-events-none"
           style={{
             backgroundImage: 'linear-gradient(rgba(255,255,255,0.4) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.4) 1px, transparent 1px)',
             backgroundSize: '40px 40px',
           }} />
      {/* Gold glow */}
      <div className="absolute top-0 right-0 w-64 h-64 rounded-full pointer-events-none opacity-10"
           style={{ background: 'radial-gradient(circle, #C9A227 0%, transparent 70%)', transform: 'translate(25%, -25%)' }} />

      <div className="relative z-10 max-w-7xl mx-auto px-6 sm:px-8">
        <div className="flex flex-col md:flex-row items-center gap-8">

          {/* Logo + branding */}
          <div className="flex items-center gap-4 sm:gap-5 flex-shrink-0">
            <div className="w-16 h-16 sm:w-20 sm:h-20 rounded-full p-0.5 flex-shrink-0"
                 style={{ background: 'linear-gradient(135deg,#C9A227,#F5D97A,#A07E1A)', boxShadow: '0 0 30px rgba(201,162,39,0.35)' }}>
              <div className="w-full h-full rounded-full overflow-hidden">
                <Image src="/logo.png" alt="MITRAM360" width={76} height={76}
                       className="w-full h-full object-cover" priority />
              </div>
            </div>
            <div>
              <h1 className="text-xl sm:text-2xl font-extrabold text-white leading-tight tracking-tight">
                Lender Directory
              </h1>
              <p className="text-xs sm:text-sm mt-0.5" style={{ color: 'rgba(255,255,255,0.55)' }}>
                India&apos;s most complete NBFC &amp; Bank database
              </p>
              <div className="flex items-center gap-1.5 sm:gap-2 mt-2 flex-wrap">
                <span className="px-2 sm:px-2.5 py-0.5 rounded-full text-[9px] sm:text-[10px] font-semibold border"
                      style={{ background: 'rgba(201,162,39,0.15)', borderColor: 'rgba(201,162,39,0.3)', color: '#F5D97A' }}>
                  1,000+ Lenders
                </span>
                <span className="px-2 sm:px-2.5 py-0.5 rounded-full text-[9px] sm:text-[10px] font-semibold border"
                      style={{ background: 'rgba(201,162,39,0.15)', borderColor: 'rgba(201,162,39,0.3)', color: '#F5D97A' }}>
                  28 States
                </span>
                <span className="px-2 sm:px-2.5 py-0.5 rounded-full text-[9px] sm:text-[10px] font-semibold border"
                      style={{ background: 'rgba(201,162,39,0.15)', borderColor: 'rgba(201,162,39,0.3)', color: '#F5D97A' }}>
                  RBI Verified
                </span>
              </div>
            </div>
          </div>

        </div>
      </div>

      {/* Wave divider */}
      <div className="absolute bottom-0 left-0 right-0">
        <svg viewBox="0 0 1440 40" preserveAspectRatio="none" className="w-full h-6 sm:h-8 block"
             fill="#F7FAFA">
          <path d="M0,30 C360,0 1080,40 1440,15 L1440,40 L0,40 Z" />
        </svg>
      </div>
    </section>
  )
}