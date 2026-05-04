import Link from 'next/link'
import Image from 'next/image'

export default function NotFound() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-6"
         style={{ background: '#F7FAFA', fontFamily: 'Inter, sans-serif' }}>

      <div className="w-16 h-16 rounded-full p-0.5 mb-8"
           style={{ background: 'linear-gradient(135deg,#C9A227,#F5D97A)' }}>
        <div className="w-full h-full rounded-full overflow-hidden">
          <Image src="/logo.png" alt="MITRAM360" width={60} height={60}
                 className="w-full h-full object-cover" />
        </div>
      </div>

      <p className="text-7xl font-extrabold mb-4"
         style={{ color: '#E6F4F4', letterSpacing: '-2px' }}>404</p>

      <h1 className="text-2xl font-bold mb-2 text-center" style={{ color: '#0F4848' }}>
        Page not found
      </h1>
      <p className="text-sm mb-8 text-center max-w-xs" style={{ color: '#7A9E9E' }}>
        The page you&apos;re looking for doesn&apos;t exist or has been moved.
      </p>

      <div className="flex items-center gap-3">
        <Link href="/"
          className="px-5 py-2.5 rounded-xl text-sm font-semibold text-white transition-all hover:-translate-y-0.5"
          style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)', boxShadow: '0 2px 8px rgba(26,112,112,0.25)' }}>
          Go Home
        </Link>
        <Link href="/dashboard"
          className="px-5 py-2.5 rounded-xl text-sm font-semibold transition-all hover:-translate-y-0.5"
          style={{ background: 'white', color: '#1A7070', border: '1px solid #D8EBEB', boxShadow: '0 1px 3px rgba(26,112,112,0.08)' }}>
          Browse Lenders
        </Link>
      </div>
    </div>
  )
}
