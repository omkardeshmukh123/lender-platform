import './globals.css'
import type { Metadata } from 'next'
import { ReactNode } from 'react'
import { AuthProvider } from './components/AuthContext'

export const metadata: Metadata = {
  title: 'MITRAM360 — Lender Discovery Platform',
  description: 'Find the right NBFC or bank for your financing needs across India.',
  icons: { icon: '/logo.png', apple: '/logo.png' },
}

export default function RootLayout({
  children,
}: {
  children: ReactNode
}) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>
          {children}
        </AuthProvider>
      </body>
    </html>
  )
}