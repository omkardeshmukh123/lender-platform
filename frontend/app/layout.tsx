import './globals.css'
import type { Metadata } from 'next'
import { ReactNode, Suspense } from 'react'
import { AuthProvider } from './components/AuthContext'
import { SaveProvider } from './components/SaveContext'
import GoogleAnalytics from './components/GoogleAnalytics'

export const metadata: Metadata = {
  title: 'MITRAM360 — India\'s Lender Intelligence Platform',
  description:
    'Find and compare 1,000+ RBI-registered NBFCs and banks by loan type, state, AUM size, and eligibility criteria. Free to use.',
  keywords: 'NBFC, bank, lender, MSME loan, India, RBI, loan comparison, lender directory',
  openGraph: {
    title: 'MITRAM360 — India\'s Lender Intelligence Platform',
    description:
      'Find and compare 1,000+ RBI-registered NBFCs and banks by loan type, state, AUM size, and eligibility criteria.',
    type: 'website',
    locale: 'en_IN',
  },
  icons: { icon: '/logo.png', apple: '/logo.png' },
  viewport: 'width=device-width, initial-scale=1',
  themeColor: '#1A2B6B',
}

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
      </head>
      <body>
        <Suspense fallback={null}>
          <GoogleAnalytics />
        </Suspense>
        <AuthProvider>
          <SaveProvider>
            {children}
          </SaveProvider>
        </AuthProvider>
      </body>
    </html>
  )
}