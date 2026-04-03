'use client'

/**
 * /admin — Admin panel for lender approval pipeline.
 *
 * Requires: authenticated Supabase user with app_metadata.role = "admin"
 * Backend:  GET/POST /v1/admin/lenders/pending  (paginated)
 *           POST /v1/admin/lenders/{id}/approve
 *           POST /v1/admin/lenders/{id}/reject
 *           GET  /v1/admin/pipeline/runs
 */

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { createClient } from '@supabase/supabase-js'
import {
  CheckCircle, XCircle, Clock, AlertTriangle,
  RefreshCw, Building2, ChevronLeft, ChevronRight,
} from 'lucide-react'

// ─────────────────────────────────────────────────────────────
// TYPES
// ─────────────────────────────────────────────────────────────

interface PendingLender {
  id: number
  company_name: string
  company_type: string
  hq_state: string | null
  website: string | null
  quality_score: number | null
  data_source: string | null
  created_at: string | null
  admin_notes: string | null
}

interface PipelineRun {
  id: number
  pipeline_name: string
  status: string
  records_processed: number | null
  records_failed: number | null
  started_at: string
  completed_at: string | null
  error_message: string | null
}

const API_URL    = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const SUPA_URL   = process.env.NEXT_PUBLIC_SUPABASE_URL!
const SUPA_KEY   = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!

// ─────────────────────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────────────────────

function badge(score: number | null) {
  if (score === null) return 'bg-gray-100 text-gray-500'
  if (score >= 0.7)  return 'bg-green-100 text-green-700'
  if (score >= 0.4)  return 'bg-yellow-100 text-yellow-700'
  return 'bg-red-100 text-red-700'
}

function statusDot(status: string) {
  const map: Record<string, string> = {
    running:   'bg-blue-400',
    success:   'bg-green-400',
    completed: 'bg-green-400',
    failed:    'bg-red-400',
    partial:   'bg-yellow-400',
  }
  return map[status.toLowerCase()] ?? 'bg-gray-300'
}

// ─────────────────────────────────────────────────────────────
// COMPONENT
// ─────────────────────────────────────────────────────────────

export default function AdminPage() {
  const router = useRouter()

  const [token,    setToken]    = useState<string | null>(null)
  const [authDone, setAuthDone] = useState(false)
  const [isAdmin,  setIsAdmin]  = useState(false)

  const [lenders,   setLenders]   = useState<PendingLender[]>([])
  const [total,     setTotal]     = useState(0)
  const [page,      setPage]      = useState(1)
  const PAGE_SIZE = 20

  const [runs,     setRuns]     = useState<PipelineRun[]>([])
  const [tab,      setTab]      = useState<'lenders' | 'pipeline'>('lenders')
  const [loading,  setLoading]  = useState(false)
  const [acting,   setActing]   = useState<number | null>(null)
  const [toast,    setToast]    = useState<{ msg: string; ok: boolean } | null>(null)

  // ── Auth ────────────────────────────────────────────────────
  useEffect(() => {
    const supa = createClient(SUPA_URL, SUPA_KEY)
    supa.auth.getSession().then(({ data }) => {
      const session = data.session
      if (!session) {
        router.replace('/login?redirect=/admin')
        return
      }
      const role = (session.user as any)?.app_metadata?.role
      if (role !== 'admin') {
        router.replace('/dashboard')
        return
      }
      setToken(session.access_token)
      setIsAdmin(true)
      setAuthDone(true)
    })
  }, [router])

  // ── API helpers ─────────────────────────────────────────────
  const apiGet = useCallback(async (path: string) => {
    const res = await fetch(`${API_URL}${path}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    return res.json()
  }, [token])

  const apiPost = useCallback(async (path: string, body?: object) => {
    const res = await fetch(`${API_URL}${path}`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: body ? JSON.stringify(body) : undefined,
    })
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    return res.json()
  }, [token])

  const showToast = (msg: string, ok: boolean) => {
    setToast({ msg, ok })
    setTimeout(() => setToast(null), 3000)
  }

  // ── Fetch pending lenders ───────────────────────────────────
  const fetchLenders = useCallback(async () => {
    if (!token) return
    setLoading(true)
    try {
      const data = await apiGet(
        `/v1/admin/lenders/pending?page=${page}&limit=${PAGE_SIZE}`
      )
      setLenders(data.results ?? [])
      setTotal(data.total ?? 0)
    } catch (e: any) {
      showToast(`Failed to load lenders: ${e.message}`, false)
    } finally {
      setLoading(false)
    }
  }, [token, page, apiGet])

  // ── Fetch pipeline runs ─────────────────────────────────────
  const fetchRuns = useCallback(async () => {
    if (!token) return
    setLoading(true)
    try {
      const data = await apiGet('/v1/admin/pipeline/runs?limit=30')
      setRuns(data.results ?? [])
    } catch (e: any) {
      showToast(`Failed to load pipeline runs: ${e.message}`, false)
    } finally {
      setLoading(false)
    }
  }, [token, apiGet])

  useEffect(() => {
    if (!authDone) return
    if (tab === 'lenders') fetchLenders()
    else fetchRuns()
  }, [authDone, tab, page, fetchLenders, fetchRuns])

  // ── Approve / Reject ────────────────────────────────────────
  const handleApprove = async (id: number) => {
    setActing(id)
    try {
      await apiPost(`/v1/admin/lenders/${id}/approve`)
      showToast('Lender approved and published', true)
      fetchLenders()
    } catch (e: any) {
      showToast(`Approve failed: ${e.message}`, false)
    } finally {
      setActing(null)
    }
  }

  const handleReject = async (id: number) => {
    const reason = window.prompt('Rejection reason (optional):')
    if (reason === null) return   // cancelled
    setActing(id)
    try {
      await apiPost(`/v1/admin/lenders/${id}/reject`, { reason })
      showToast('Lender rejected', true)
      fetchLenders()
    } catch (e: any) {
      showToast(`Reject failed: ${e.message}`, false)
    } finally {
      setActing(null)
    }
  }

  // ── Render guards ────────────────────────────────────────────
  if (!authDone) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-[#3B5CCC]" />
      </div>
    )
  }
  if (!isAdmin) return null

  const totalPages = Math.ceil(total / PAGE_SIZE)

  // ── Render ───────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-gray-50">
      {/* Nav */}
      <nav className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Building2 className="w-5 h-5 text-[#3B5CCC]" />
          <span className="font-semibold text-lg">MITRAM360 Admin</span>
        </div>
        <button
          onClick={() => router.push('/dashboard')}
          className="text-sm text-gray-500 hover:text-gray-800 transition-colors"
        >
          ← Back to Dashboard
        </button>
      </nav>

      {/* Toast */}
      {toast && (
        <div className={[
          'fixed top-5 right-5 z-50 px-4 py-3 rounded-xl shadow-lg text-sm font-medium flex items-center gap-2',
          toast.ok ? 'bg-green-600 text-white' : 'bg-red-600 text-white',
        ].join(' ')}>
          {toast.ok ? <CheckCircle className="w-4 h-4" /> : <XCircle className="w-4 h-4" />}
          {toast.msg}
        </div>
      )}

      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Stats bar */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
          <div className="bg-white rounded-xl border border-gray-200 p-4 text-center">
            <div className="text-2xl font-bold text-[#3B5CCC]">{total}</div>
            <div className="text-xs text-gray-500 mt-1">Pending Review</div>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-4 text-center">
            <div className="text-2xl font-bold text-green-600">{runs.filter(r => r.status === 'success' || r.status === 'completed').length}</div>
            <div className="text-xs text-gray-500 mt-1">Successful Runs</div>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-4 text-center">
            <div className="text-2xl font-bold text-red-500">{runs.filter(r => r.status === 'failed').length}</div>
            <div className="text-xs text-gray-500 mt-1">Failed Runs</div>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-4 text-center">
            <div className="text-2xl font-bold text-gray-700">{runs.reduce((s, r) => s + (r.records_processed ?? 0), 0).toLocaleString('en-IN')}</div>
            <div className="text-xs text-gray-500 mt-1">Records Processed</div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-6 bg-gray-100 rounded-xl p-1 w-fit">
          {(['lenders', 'pipeline'] as const).map(t => (
            <button
              key={t}
              onClick={() => { setTab(t); setPage(1) }}
              className={[
                'px-5 py-2 rounded-lg text-sm font-medium transition-all',
                tab === t
                  ? 'bg-white text-[#3B5CCC] shadow-sm'
                  : 'text-gray-500 hover:text-gray-700',
              ].join(' ')}
            >
              {t === 'lenders' ? 'Pending Lenders' : 'Pipeline Runs'}
            </button>
          ))}
        </div>

        {/* Refresh */}
        <div className="flex justify-end mb-4">
          <button
            onClick={() => tab === 'lenders' ? fetchLenders() : fetchRuns()}
            className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition-colors"
          >
            <RefreshCw className={['w-3.5 h-3.5', loading ? 'animate-spin' : ''].join(' ')} />
            Refresh
          </button>
        </div>

        {/* ── Lenders tab ── */}
        {tab === 'lenders' && (
          <>
            {loading && lenders.length === 0 ? (
              <div className="text-center py-16 text-gray-400">Loading…</div>
            ) : lenders.length === 0 ? (
              <div className="bg-white rounded-2xl border border-gray-200 p-12 text-center">
                <CheckCircle className="w-10 h-10 text-green-400 mx-auto mb-3" />
                <p className="font-medium text-gray-700">No pending lenders</p>
                <p className="text-sm text-gray-400 mt-1">All extractions have been reviewed.</p>
              </div>
            ) : (
              <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="text-left px-4 py-3 font-medium text-gray-600">Company</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-600 hidden md:table-cell">Type</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-600 hidden lg:table-cell">State</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-600 hidden lg:table-cell">Quality</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-600 hidden xl:table-cell">Source</th>
                      <th className="text-right px-4 py-3 font-medium text-gray-600">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {lenders.map(l => (
                      <tr key={l.id} className="hover:bg-gray-50 transition-colors">
                        <td className="px-4 py-3">
                          <div className="font-medium text-gray-900 max-w-xs truncate">
                            {l.company_name}
                          </div>
                          {l.website && (
                            <a
                              href={l.website}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-xs text-[#3B5CCC] hover:underline truncate block max-w-xs"
                            >
                              {l.website}
                            </a>
                          )}
                          {l.admin_notes && (
                            <div className="flex items-center gap-1 mt-1">
                              <AlertTriangle className="w-3 h-3 text-yellow-500 flex-shrink-0" />
                              <span className="text-xs text-yellow-600 truncate max-w-xs">{l.admin_notes}</span>
                            </div>
                          )}
                        </td>
                        <td className="px-4 py-3 hidden md:table-cell">
                          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-700">
                            {l.company_type}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-gray-500 hidden lg:table-cell">
                          {l.hq_state ?? '—'}
                        </td>
                        <td className="px-4 py-3 hidden lg:table-cell">
                          {l.quality_score !== null ? (
                            <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${badge(l.quality_score)}`}>
                              {(l.quality_score * 100).toFixed(0)}%
                            </span>
                          ) : '—'}
                        </td>
                        <td className="px-4 py-3 text-gray-400 text-xs hidden xl:table-cell">
                          {l.data_source ?? '—'}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <div className="inline-flex gap-2">
                            <button
                              onClick={() => handleApprove(l.id)}
                              disabled={acting === l.id}
                              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium
                                         bg-green-50 text-green-700 hover:bg-green-100 disabled:opacity-50 transition-colors"
                            >
                              <CheckCircle className="w-3.5 h-3.5" />
                              Approve
                            </button>
                            <button
                              onClick={() => handleReject(l.id)}
                              disabled={acting === l.id}
                              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium
                                         bg-red-50 text-red-700 hover:bg-red-100 disabled:opacity-50 transition-colors"
                            >
                              <XCircle className="w-3.5 h-3.5" />
                              Reject
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                {/* Pagination */}
                {totalPages > 1 && (
                  <div className="px-4 py-3 border-t border-gray-100 flex items-center justify-between">
                    <span className="text-xs text-gray-500">
                      {((page - 1) * PAGE_SIZE) + 1}–{Math.min(page * PAGE_SIZE, total)} of {total}
                    </span>
                    <div className="flex gap-2">
                      <button
                        onClick={() => setPage(p => Math.max(1, p - 1))}
                        disabled={page === 1}
                        className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 disabled:opacity-30 transition-colors"
                      >
                        <ChevronLeft className="w-4 h-4" />
                      </button>
                      <span className="text-xs text-gray-500 flex items-center">
                        {page} / {totalPages}
                      </span>
                      <button
                        onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                        disabled={page === totalPages}
                        className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 disabled:opacity-30 transition-colors"
                      >
                        <ChevronRight className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {/* ── Pipeline tab ── */}
        {tab === 'pipeline' && (
          <>
            {loading && runs.length === 0 ? (
              <div className="text-center py-16 text-gray-400">Loading…</div>
            ) : runs.length === 0 ? (
              <div className="bg-white rounded-2xl border border-gray-200 p-12 text-center">
                <Clock className="w-10 h-10 text-gray-300 mx-auto mb-3" />
                <p className="font-medium text-gray-700">No pipeline runs yet</p>
                <p className="text-sm text-gray-400 mt-1">Run an extraction script to see results here.</p>
              </div>
            ) : (
              <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="text-left px-4 py-3 font-medium text-gray-600">Pipeline</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-600">Status</th>
                      <th className="text-right px-4 py-3 font-medium text-gray-600 hidden sm:table-cell">Processed</th>
                      <th className="text-right px-4 py-3 font-medium text-gray-600 hidden sm:table-cell">Failed</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-600 hidden lg:table-cell">Started</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-600 hidden xl:table-cell">Error</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {runs.map(run => (
                      <tr key={run.id} className="hover:bg-gray-50 transition-colors">
                        <td className="px-4 py-3 font-medium text-gray-800">{run.pipeline_name}</td>
                        <td className="px-4 py-3">
                          <span className="inline-flex items-center gap-1.5">
                            <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusDot(run.status)}`} />
                            <span className="capitalize text-gray-600">{run.status}</span>
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right text-gray-600 hidden sm:table-cell">
                          {run.records_processed?.toLocaleString('en-IN') ?? '—'}
                        </td>
                        <td className="px-4 py-3 text-right hidden sm:table-cell">
                          {run.records_failed != null && run.records_failed > 0 ? (
                            <span className="text-red-500">{run.records_failed}</span>
                          ) : (
                            <span className="text-gray-400">0</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-xs text-gray-400 hidden lg:table-cell">
                          {new Date(run.started_at).toLocaleString('en-IN', {
                            day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
                          })}
                        </td>
                        <td className="px-4 py-3 text-xs text-red-400 max-w-xs truncate hidden xl:table-cell">
                          {run.error_message ?? '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
