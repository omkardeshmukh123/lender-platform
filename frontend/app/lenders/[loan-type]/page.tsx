import { Metadata } from 'next'
import { notFound } from 'next/navigation'
import Link from 'next/link'
import { ArrowLeft, CreditCard } from 'lucide-react'

const LOAN_TYPES = [
  'MSME Loan','Personal Loan','Home Loan','Business Loan','Vehicle Loan',
  'Gold Loan','Education Loan','Micro Loan','Loan Against Property',
  'Working Capital','Agriculture Loan','EV Loan','Two Wheeler Loan',
  'Rural Loan','Microfinance','Supply Chain Finance','Consumer Durable Loan','Credit Card',
]

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export async function generateStaticParams() {
  return LOAN_TYPES.map(t => ({ 'loan-type': encodeURIComponent(t) }))
}

export async function generateMetadata({ params }: { params: { 'loan-type': string } }): Promise<Metadata> {
  const loanType = decodeURIComponent(params['loan-type'])
  return {
    title: `Best NBFCs for ${loanType} in India — MITRAM360`,
    description: `Find RBI-registered NBFCs and banks offering ${loanType} across India. Compare lenders by state, AUM and company type. Free directory for DSAs and borrowers.`,
    keywords: `${loanType} NBFC, ${loanType} lender India, ${loanType} bank, RBI registered ${loanType}`,
  }
}

async function getLenders(loanType: string) {
  try {
    const res = await fetch(
      `${API_URL}/v1/lenders/search?loan_type=${encodeURIComponent(loanType)}&limit=50`,
      { next: { revalidate: 3600 } }
    )
    if (!res.ok) return []
    const data = await res.json()
    return data.results ?? []
  } catch {
    return []
  }
}

export default async function LoanTypeLendersPage({ params }: { params: { 'loan-type': string } }) {
  const loanType = decodeURIComponent(params['loan-type'])
  if (!LOAN_TYPES.includes(loanType)) notFound()

  const lenders = await getLenders(loanType)

  return (
    <div className="min-h-screen" style={{ background: '#F7FAFA', fontFamily: 'Inter, sans-serif' }}>
      <div className="max-w-5xl mx-auto px-6 py-10">
        <Link href="/dashboard" className="inline-flex items-center gap-2 text-sm mb-6 transition-colors"
              style={{ color: '#1A7070' }}>
          <ArrowLeft className="w-4 h-4" /> Back to all lenders
        </Link>

        <div className="flex items-center gap-3 mb-2">
          <CreditCard className="w-5 h-5" style={{ color: '#1A7070' }} />
          <h1 className="text-2xl font-extrabold" style={{ color: '#0F4848' }}>
            Best NBFCs for {loanType} in India
          </h1>
        </div>
        <p className="text-sm mb-8" style={{ color: '#7A9E9E' }}>
          {lenders.length} RBI-registered lenders offering {loanType}
        </p>

        {lenders.length === 0 ? (
          <p className="text-sm" style={{ color: '#7A9E9E' }}>No lenders found for this loan type.</p>
        ) : (
          <div className="grid gap-3">
            {lenders.map((l: any) => (
              <Link key={l.id} href={`/lender/${l.id}`}
                    className="bg-white rounded-xl p-4 flex items-center justify-between border transition-all hover:-translate-y-0.5"
                    style={{ borderColor: '#E6F4F4', boxShadow: '0 1px 4px rgba(26,112,112,0.06)' }}>
                <div>
                  <p className="font-semibold text-sm" style={{ color: '#0F4848' }}>{l.company_name}</p>
                  <p className="text-xs mt-0.5" style={{ color: '#7A9E9E' }}>
                    {l.company_type}
                    {l.hq_state ? ` · ${l.hq_state}` : ''}
                    {l.aum_category ? ` · AUM: ${l.aum_category}` : ''}
                  </p>
                </div>
                <span className="text-xs font-medium px-2.5 py-1 rounded-full"
                      style={{ background: '#E6F4F4', color: '#1A7070' }}>
                  View →
                </span>
              </Link>
            ))}
          </div>
        )}

        <div className="mt-10 pt-6 border-t" style={{ borderColor: '#E6F4F4' }}>
          <p className="text-xs mb-3 font-semibold uppercase tracking-wide" style={{ color: '#7A9E9E' }}>
            Other loan types
          </p>
          <div className="flex flex-wrap gap-2">
            {LOAN_TYPES.filter(t => t !== loanType).map(t => (
              <Link key={t} href={`/lenders/${encodeURIComponent(t)}`}
                    className="text-xs px-3 py-1 rounded-full border transition-colors"
                    style={{ borderColor: '#D8EBEB', color: '#3D6363' }}>
                {t}
              </Link>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
