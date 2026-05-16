import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''
const CRON_SECRET = process.env.CRON_SECRET ?? ''

export const runtime = 'edge'

export async function GET(request: NextRequest) {
  if (CRON_SECRET) {
    const auth = request.headers.get('authorization')
    if (auth !== `Bearer ${CRON_SECRET}`) {
      return NextResponse.json({ ok: false }, { status: 401 })
    }
  }

  if (!API_URL) return NextResponse.json({ ok: false, reason: 'no API_URL' })

  try {
    const res = await fetch(`${API_URL}/health`, {
      signal: AbortSignal.timeout(10_000),
    })
    return NextResponse.json({ ok: res.ok, status: res.status })
  } catch {
    return NextResponse.json({ ok: false })
  }
}
