import { Metadata } from 'next'
import { notFound } from 'next/navigation'
import Link from 'next/link'
import { ArrowLeft, MapPin, CreditCard } from 'lucide-react'

const INDIA_STATES = [
  'Andhra Pradesh','Arunachal Pradesh','Assam','Bihar','Chhattisgarh',
  'Goa','Gujarat','Haryana','Himachal Pradesh','Jharkhand','Karnataka',
  'Kerala','Madhya Pradesh','Maharashtra','Manipur','Meghalaya','Mizoram',
  'Nagaland','Odisha','Punjab','Rajasthan','Sikkim','Tamil Nadu',
  'Telangana','Tripura','Uttar Pradesh','Uttarakhand','West Bengal',
  'Delhi','Jammu & Kashmir','Ladakh','Puducherry','Chandigarh',
]

const LOAN_TYPES = [
  'MSME Loan','Personal Loan','Home Loan','Business Loan','Vehicle Loan',
  'Gold Loan','Education Loan','Micro Loan','Loan Against Property',
  'Working Capital','Agriculture Loan','EV Loan','Two Wheeler Loan',
  'Rural Loan','Microfinance','Supply Chain Finance','Consumer Durable Loan','Credit Card',
]

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export async function generateStaticParams() {
  return [
    ...INDIA_STATES.map(s => ({ slug: encodeURIComponent(s) })),
    ...LOAN_TYPES.map(t => ({ slug: encodeURIComponent(t) })),
  ]
}

export async function generateMetadata({ params }: { params: { slug: string } }): Promise<Metadata> {
  const value = decodeURIComponent(params.slug)
  if (INDIA_STATES.includes(value)) {
    return {
      title: `NBFCs & Banks in ${value} — MITRAM360`,
      description: `Browse RBI-registered NBFCs and banks operating in ${value}. Find lenders by loan type, AUM, and company type. Free directory for DSAs and borrowers.`,
      keywords: `NBFC ${value}, bank ${value}, lender ${value}, MSME loan ${value}, RBI registered`,
    }
  }
  return {
    title: `Best NBFCs for ${value} in India — MITRAM360`,
    description: `Find RBI-registered NBFCs and banks offering ${value} across India. Compare lenders by state, AUM and company type. Free directory for DSAs and borrowers.`,
    keywords: `${value} NBFC, ${value} lender India, ${value} bank, RBI registered ${value}`,
  }
}

async function getLendersByState(state: string) {
  try {
    const res = await fetch(
      `${API_URL}/v1/lenders/search?state=${encodeURIComponent(state)}&limit=50`,
      { next: { revalidate: 3600 } }
    )
    if (!res.ok) return []
    const data = await res.json()
    return data.results ?? []
  } catch {
    return []
  }
}

async function getLendersByLoanType(loanType: string) {
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

export default async function LendersSlugPage({ params }: { params: { slug: string } }) {
  const value = decodeURIComponent(params.slug)

  const isState    = INDIA_STATES.includes(value)
  const isLoanType = LOAN_TYPES.includes(value)
  if (!isState && !isLoanType) notFound()

  const lenders = isState
    ? await getLendersByState(value)
    : await getLendersByLoanType(value)

  if (isState) {
    return (
      <div className="min-h-screen" style={{ background: '#F7FAFA', fontFamily: 'Inter, sans-serif' }}>
        <div className="max-w-5xl mx-auto px-6 py-10">
          <Link href="/dashboard" className="inline-flex items-center gap-2 text-sm mb-6 transition-colors"
                style={{ color: '#1A7070' }}>
            <ArrowLeft className="w-4 h-4" /> Back to all lenders
          </Link>

          <div className="flex items-center gap-3 mb-2">
            <MapPin className="w-5 h-5" style={{ color: '#1A7070' }} />
            <h1 className="text-2xl font-extrabold" style={{ color: '#0F4848' }}>
              NBFCs &amp; Banks in {value}
            </h1>
          </div>
          <p className="text-sm mb-8" style={{ color: '#7A9E9E' }}>
            {lenders.length} RBI-registered lenders operating in {value}
          </p>

          {lenders.length === 0 ? (
            <p className="text-sm" style={{ color: '#7A9E9E' }}>No lenders found for this state.</p>
          ) : (
            <div className="grid gap-3">
              {lenders.map((l: any) => (
                <Link key={l.id} href={`/lender/${l.id}`}
                      className="bg-white rounded-xl p-4 flex items-center justify-between border transition-all hover:-translate-y-0.5"
                      style={{ borderColor: '#E6F4F4', boxShadow: '0 1px 4px rgba(26,112,112,0.06)' }}>
                  <div>
                    <p className="font-semibold text-sm" style={{ color: '#0F4848' }}>{l.company_name}</p>
                    <p className="text-xs mt-0.5" style={{ color: '#7A9E9E' }}>
                      {l.company_type} · {l.hq_location ?? value}
                      {l.primary_loan_segments?.length ? ` · ${l.primary_loan_segments.slice(0, 2).join(', ')}` : ''}
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
              Browse other states
            </p>
            <div className="flex flex-wrap gap-2">
              {INDIA_STATES.filter(s => s !== value).slice(0, 12).map(s => (
                <Link key={s} href={`/lenders/${encodeURIComponent(s)}`}
                      className="text-xs px-3 py-1 rounded-full border transition-colors"
                      style={{ borderColor: '#D8EBEB', color: '#3D6363' }}>
                  {s}
                </Link>
              ))}
            </div>
          </div>
        </div>
      </div>
    )
  }

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
            Best NBFCs for {value} in India
          </h1>
        </div>
        <p className="text-sm mb-8" style={{ color: '#7A9E9E' }}>
          {lenders.length} RBI-registered lenders offering {value}
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
            {LOAN_TYPES.filter(t => t !== value).map(t => (
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
