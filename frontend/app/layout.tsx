import './globals.css'
import type { Metadata } from 'next'
import { ReactNode } from 'react'
import { AuthProvider } from './components/AuthContext'

export const metadata: Metadata = {
  title: 'Lender Discovery Platform',
  description: 'Find the right lender for your needs - NBFCs, Banks, and more',
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
