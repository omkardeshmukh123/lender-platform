import { Metadata } from 'next'
import { notFound } from 'next/navigation'
import Link from 'next/link'
import { ArrowLeft, MapPin } from 'lucide-react'

const INDIA_STATES = [
  'Andhra Pradesh','Arunachal Pradesh','Assam','Bihar','Chhattisgarh',
  'Goa','Gujarat','Haryana','Himachal Pradesh','Jharkhand','Karnataka',
  'Kerala','Madhya Pradesh','Maharashtra','Manipur','Meghalaya','Mizoram',
  'Nagaland','Odisha','Punjab','Rajasthan','Sikkim','Tamil Nadu',
  'Telangana','Tripura','Uttar Pradesh','Uttarakhand','West Bengal',
  'Delhi','Jammu & Kashmir','Ladakh','Puducherry','Chandigarh',
]

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

export async function generateStaticParams() {
  return INDIA_STATES.map(s => ({ state: encodeURIComponent(s) }))
}

export async function generateMetadata({ params }: { params: { state: string } }): Promise<Metadata> {
  const state = decodeURIComponent(params.state)
  return {
    title: `NBFCs & Banks in ${state} — MITRAM360`,
    description: `Browse RBI-registered NBFCs and banks operating in ${state}. Find lenders by loan type, AUM, and company type. Free directory for DSAs and borrowers.`,
    keywords: `NBFC ${state}, bank ${state}, lender ${state}, MSME loan ${state}, RBI registered`,
  }
}

async function getLenders(state: string) {
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

export default async function StateLendersPage({ params }: { params: { state: string } }) {
  const state = decodeURIComponent(params.state)
  if (!INDIA_STATES.includes(state)) notFound()

  const lenders = await getLenders(state)

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
            NBFCs &amp; Banks in {state}
          </h1>
        </div>
        <p className="text-sm mb-8" style={{ color: '#7A9E9E' }}>
          {lenders.length} RBI-registered lenders operating in {state}
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
                    {l.company_type} · {l.hq_location ?? state}
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
            {INDIA_STATES.filter(s => s !== state).slice(0, 12).map(s => (
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
