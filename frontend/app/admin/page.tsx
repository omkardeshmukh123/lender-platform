'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { supabase } from '../lib/supabase'
import {
  CheckCircle, XCircle, Clock, AlertTriangle,
  RefreshCw, Building2, ChevronLeft, ChevronRight, FileText,
} from 'lucide-react'

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

interface PendingPolicy {
  id: number
  lender_id: number
  lender_name: string | null
  product_name: string | null
  loan_type: string | null
  loan_amount_min: number | null
  loan_amount_max: number | null
  interest_rate_min: number | null
  interest_rate_max: number | null
  completeness_score: number | null
  data_source: string | null
}

interface PipelineRun {
  id: number
  pipeline: string
  run_date: string
  started_at: string
  duration_seconds: number | null
  total_processed: number
  success_count: number
  failed_count: number
}

interface LenderRequest {
  id: number
  company_name: string
  cin: string | null
  requested_by: string | null
  notes: string | null
  created_at: string
  status: string
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

function badge(score: number | null) {
  if (score === null) return 'bg-gray-100 text-gray-500'
  if (score >= 0.7) return 'bg-green-100 text-green-700'
  if (score >= 0.4) return 'bg-yellow-100 text-yellow-700'
  return 'bg-red-100 text-red-700'
}

export default function AdminPage() {
  const router = useRouter()
  const [token, setToken] = useState<string | null>(null)
  const [authDone, setAuthDone] = useState(false)
  const [isAdmin, setIsAdmin] = useState(false)

  const [lenders, setLenders] = useState<PendingLender[]>([])
  const [lenderTotal, setLenderTotal] = useState(0)
  const [lenderPage, setLenderPage] = useState(1)

  const [policies, setPolicies] = useState<PendingPolicy[]>([])
  const [policyTotal, setPolicyTotal] = useState(0)
  const [policyPage, setPolicyPage] = useState(1)

  const [runs, setRuns] = useState<PipelineRun[]>([])
  const [requests, setRequests] = useState<LenderRequest[]>([])
  const [requestTotal, setRequestTotal] = useState(0)
  const [requestPage, setRequestPage] = useState(1)
  const [tab, setTab] = useState<'lenders' | 'policies' | 'pipeline' | 'requests'>('lenders')
  const [loading, setLoading] = useState(false)
  const [acting, setActing] = useState<number | null>(null)
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null)

  const PAGE_SIZE = 20

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      const session = data.session
      if (!session) { router.replace('/login?redirect=/admin'); return }
      const role = (session.user as any)?.app_metadata?.role
      if (role !== 'admin') { router.replace('/dashboard'); return }
      setToken(session.access_token)
      setIsAdmin(true)
      setAuthDone(true)
    })
  }, [router])

  const apiGet = useCallback(async (path: string) => {
    const res = await fetch(`${API_URL}${path}`, { headers: { Authorization: `Bearer ${token}` } })
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    return res.json()
  }, [token])

  const apiPost = useCallback(async (path: string, body?: object) => {
    const res = await fetch(`${API_URL}${path}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : JSON.stringify({}),
    })
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    return res.json()
  }, [token])

  const showToast = (msg: string, ok: boolean) => {
    setToast({ msg, ok })
    setTimeout(() => setToast(null), 3500)
  }

  const fetchLenders = useCallback(async () => {
    if (!token) return
    setLoading(true)
    try {
      const data = await apiGet(`/v1/admin/lenders/pending?page=${lenderPage}&limit=${PAGE_SIZE}`)
      setLenders(data.results ?? [])
      setLenderTotal(data.total ?? 0)
    } catch (e: any) {
      showToast(`Failed to load lenders: ${e.message}`, false)
    } finally { setLoading(false) }
  }, [token, lenderPage, apiGet])

  const fetchPolicies = useCallback(async () => {
    if (!token) return
    setLoading(true)
    try {
      const data = await apiGet(`/v1/admin/policies/pending?page=${policyPage}&limit=${PAGE_SIZE}`)
      setPolicies(data.results ?? [])
      setPolicyTotal(data.total ?? 0)
    } catch (e: any) {
      showToast(`Failed to load policies: ${e.message}`, false)
    } finally { setLoading(false) }
  }, [token, policyPage, apiGet])

  const fetchRuns = useCallback(async () => {
    if (!token) return
    setLoading(true)
    try {
      const data = await apiGet('/v1/admin/pipeline-runs?limit=30')
      setRuns(data ?? [])
    } catch (e: any) {
      showToast(`Failed to load pipeline runs: ${e.message}`, false)
    } finally { setLoading(false) }
  }, [token, apiGet])

  const fetchRequests = useCallback(async () => {
    if (!token) return
    setLoading(true)
    try {
      const data = await apiGet(`/v1/admin/lender-requests?page=${requestPage}&limit=${PAGE_SIZE}`)
      setRequests(data.results ?? [])
      setRequestTotal(data.total ?? 0)
    } catch (e: any) {
      showToast(`Failed to load requests: ${e.message}`, false)
    } finally { setLoading(false) }
  }, [token, requestPage, apiGet])

  // On initial auth, load all counts so the stats row is populated
  useEffect(() => {
    if (!authDone) return
    fetchLenders()
    fetchPolicies()
    fetchRuns()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authDone])

  // Reload current tab when tab/page changes
  useEffect(() => {
    if (!authDone) return
    if (tab === 'lenders') fetchLenders()
    else if (tab === 'policies') fetchPolicies()
    else if (tab === 'requests') fetchRequests()
    else fetchRuns()
  }, [tab, lenderPage, policyPage, requestPage, fetchLenders, fetchPolicies, fetchRequests, fetchRuns])

  const handleApproveLender = async (id: number) => {
    setActing(id)
    try {
      await apiPost(`/v1/admin/lenders/${id}/approve`, { notes: null })
      showToast('Lender approved — all its pending policies activated', true)
      fetchLenders()
    } catch (e: any) { showToast(`Approve failed: ${e.message}`, false) }
    finally { setActing(null) }
  }

  const handleRejectLender = async (id: number) => {
    const reason = window.prompt('Rejection reason (optional):') ?? ''
    setActing(id)
    try {
      await apiPost(`/v1/admin/lenders/${id}/reject`, { reason })
      showToast('Lender rejected', true)
      fetchLenders()
    } catch (e: any) { showToast(`Reject failed: ${e.message}`, false) }
    finally { setActing(null) }
  }

  const handleApprovePolicy = async (id: number) => {
    setActing(id)
    try {
      await apiPost(`/v1/admin/policies/${id}/approve`, { notes: null })
      showToast('Policy approved and published', true)
      fetchPolicies()
    } catch (e: any) { showToast(`Approve failed: ${e.message}`, false) }
    finally { setActing(null) }
  }

  const handleRejectPolicy = async (id: number) => {
    setActing(id)
    try {
      await apiPost(`/v1/admin/policies/${id}/reject`, { notes: null })
      showToast('Policy rejected', true)
      fetchPolicies()
    } catch (e: any) { showToast(`Reject failed: ${e.message}`, false) }
    finally { setActing(null) }
  }

  const handleApproveAllPolicies = async () => {
    if (!window.confirm(`Approve all ${policyTotal} pending policies?`)) return
    setLoading(true)
    let approved = 0
    for (const p of policies) {
      try {
        await apiPost(`/v1/admin/policies/${p.id}/approve`, { notes: 'bulk approved' })
        approved++
      } catch { /* skip */ }
    }
    showToast(`Approved ${approved} / ${policies.length} policies on this page`, true)
    fetchPolicies()
  }

  const handleUpdateRequestStatus = async (id: number, status: string) => {
    try {
      await apiPost(`/v1/admin/lender-requests/${id}/status`, { status })
      showToast(`Request marked ${status}`, true)
      fetchRequests()
    } catch (e: any) { showToast(`Update failed: ${e.message}`, false) }
  }

  if (!authDone) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-[#1A7070]" />
      </div>
    )
  }
  if (!isAdmin) return null

  const lenderPages = Math.ceil(lenderTotal / PAGE_SIZE)
  const policyPages = Math.ceil(policyTotal / PAGE_SIZE)

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Nav */}
      <nav className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Building2 className="w-5 h-5 text-[#1A7070]" />
          <span className="font-semibold text-lg">MITRAM360 Admin</span>
        </div>
        <button onClick={() => router.push('/dashboard')} className="text-sm text-gray-500 hover:text-gray-800 transition-colors">
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
        {/* Stats */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
          <div className="bg-white rounded-xl border border-gray-200 p-4 text-center">
            <div className="text-2xl font-bold text-[#1A7070]">{lenderTotal}</div>
            <div className="text-xs text-gray-500 mt-1">Pending Lenders</div>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-4 text-center">
            <div className="text-2xl font-bold text-orange-500">{policyTotal}</div>
            <div className="text-xs text-gray-500 mt-1">Pending Policies</div>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-4 text-center">
            <div className="text-2xl font-bold text-green-600">{runs.filter(r => r.success_count > 0).length}</div>
            <div className="text-xs text-gray-500 mt-1">Successful Runs</div>
          </div>
          <div className="bg-white rounded-xl border border-gray-200 p-4 text-center">
            <div className="text-2xl font-bold text-gray-700">{runs.reduce((s, r) => s + (r.total_processed ?? 0), 0).toLocaleString('en-IN')}</div>
            <div className="text-xs text-gray-500 mt-1">Records Processed</div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-6 bg-gray-100 rounded-xl p-1 w-fit">
          {(['lenders', 'policies', 'requests', 'pipeline'] as const).map(t => (
            <button
              key={t}
              onClick={() => { setTab(t); setLenderPage(1); setPolicyPage(1); setRequestPage(1) }}
              className={[
                'px-5 py-2 rounded-lg text-sm font-medium transition-all flex items-center gap-1.5',
                tab === t ? 'bg-white text-[#1A7070] shadow-sm' : 'text-gray-500 hover:text-gray-700',
              ].join(' ')}
            >
              {t === 'policies' && <FileText className="w-3.5 h-3.5" />}
              {t === 'lenders' ? 'Pending Lenders'
                : t === 'policies' ? `Pending Policies${policyTotal > 0 ? ` (${policyTotal})` : ''}`
                : t === 'requests' ? `Requests${requestTotal > 0 ? ` (${requestTotal})` : ''}`
                : 'Pipeline Runs'}
            </button>
          ))}
        </div>

        {/* Refresh */}
        <div className="flex items-center justify-between mb-4">
          <div />
          <div className="flex items-center gap-3">
            {tab === 'policies' && policyTotal > 0 && (
              <button
                onClick={handleApproveAllPolicies}
                disabled={loading}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-green-600 text-white text-sm font-medium hover:bg-green-700 disabled:opacity-50 transition-colors"
              >
                <CheckCircle className="w-4 h-4" />
                Approve All on Page
              </button>
            )}
            <button
              onClick={() => tab === 'lenders' ? fetchLenders() : tab === 'policies' ? fetchPolicies() : fetchRuns()}
              className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-800 transition-colors"
            >
              <RefreshCw className={['w-3.5 h-3.5', loading ? 'animate-spin' : ''].join(' ')} />
              Refresh
            </button>
          </div>
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
                <p className="text-sm text-gray-400 mt-1">All lenders have been reviewed.</p>
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
                      <th className="text-right px-4 py-3 font-medium text-gray-600">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {lenders.map(l => (
                      <tr key={l.id} className="hover:bg-gray-50 transition-colors">
                        <td className="px-4 py-3">
                          <div className="font-medium text-gray-900 max-w-xs truncate">{l.company_name}</div>
                          {l.website && (
                            <a href={l.website} target="_blank" rel="noopener noreferrer" className="text-xs text-[#1A7070] hover:underline truncate block max-w-xs">{l.website}</a>
                          )}
                          {l.admin_notes && (
                            <div className="flex items-center gap-1 mt-1">
                              <AlertTriangle className="w-3 h-3 text-yellow-500 flex-shrink-0" />
                              <span className="text-xs text-yellow-600 truncate max-w-xs">{l.admin_notes}</span>
                            </div>
                          )}
                        </td>
                        <td className="px-4 py-3 hidden md:table-cell">
                          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-[#E6F4F4] text-[#1A7070]">{l.company_type}</span>
                        </td>
                        <td className="px-4 py-3 text-gray-500 hidden lg:table-cell">{l.hq_state ?? '—'}</td>
                        <td className="px-4 py-3 hidden lg:table-cell">
                          {l.quality_score !== null ? (
                            <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${badge(l.quality_score)}`}>
                              {(l.quality_score * 100).toFixed(0)}%
                            </span>
                          ) : '—'}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <div className="inline-flex gap-2">
                            <button onClick={() => handleApproveLender(l.id)} disabled={acting === l.id}
                              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-green-50 text-green-700 hover:bg-green-100 disabled:opacity-50 transition-colors">
                              <CheckCircle className="w-3.5 h-3.5" />Approve
                            </button>
                            <button onClick={() => handleRejectLender(l.id)} disabled={acting === l.id}
                              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-red-50 text-red-700 hover:bg-red-100 disabled:opacity-50 transition-colors">
                              <XCircle className="w-3.5 h-3.5" />Reject
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {lenderPages > 1 && (
                  <div className="px-4 py-3 border-t border-gray-100 flex items-center justify-between">
                    <span className="text-xs text-gray-500">{((lenderPage - 1) * PAGE_SIZE) + 1}–{Math.min(lenderPage * PAGE_SIZE, lenderTotal)} of {lenderTotal}</span>
                    <div className="flex gap-2">
                      <button onClick={() => setLenderPage(p => Math.max(1, p - 1))} disabled={lenderPage === 1} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 disabled:opacity-30"><ChevronLeft className="w-4 h-4" /></button>
                      <span className="text-xs text-gray-500 flex items-center">{lenderPage} / {lenderPages}</span>
                      <button onClick={() => setLenderPage(p => Math.min(lenderPages, p + 1))} disabled={lenderPage === lenderPages} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 disabled:opacity-30"><ChevronRight className="w-4 h-4" /></button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {/* ── Policies tab ── */}
        {tab === 'policies' && (
          <>
            {loading && policies.length === 0 ? (
              <div className="text-center py-16 text-gray-400">Loading…</div>
            ) : policies.length === 0 ? (
              <div className="bg-white rounded-2xl border border-gray-200 p-12 text-center">
                <CheckCircle className="w-10 h-10 text-green-400 mx-auto mb-3" />
                <p className="font-medium text-gray-700">No pending policies</p>
                <p className="text-sm text-gray-400 mt-1">All policies have been reviewed.</p>
              </div>
            ) : (
              <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="text-left px-4 py-3 font-medium text-gray-600">Lender / Product</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-600 hidden md:table-cell">Type</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-600 hidden lg:table-cell">Amount (L)</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-600 hidden lg:table-cell">Rate %</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-600 hidden xl:table-cell">Score</th>
                      <th className="text-right px-4 py-3 font-medium text-gray-600">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {policies.map(p => (
                      <tr key={p.id} className="hover:bg-gray-50 transition-colors">
                        <td className="px-4 py-3">
                          <div className="font-medium text-gray-900 truncate max-w-xs">{p.lender_name ?? '—'}</div>
                          <div className="text-xs text-gray-400 truncate max-w-xs">{p.product_name ?? 'Unnamed product'}</div>
                        </td>
                        <td className="px-4 py-3 hidden md:table-cell">
                          {p.loan_type ? (
                            <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-purple-50 text-purple-700">{p.loan_type}</span>
                          ) : '—'}
                        </td>
                        <td className="px-4 py-3 text-gray-600 text-xs hidden lg:table-cell">
                          {p.loan_amount_min ?? '?'} – {p.loan_amount_max ?? '?'}
                        </td>
                        <td className="px-4 py-3 text-gray-600 text-xs hidden lg:table-cell">
                          {p.interest_rate_min ?? '?'} – {p.interest_rate_max ?? '?'}
                        </td>
                        <td className="px-4 py-3 hidden xl:table-cell">
                          {p.completeness_score !== null ? (
                            <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${badge(p.completeness_score)}`}>
                              {(p.completeness_score * 100).toFixed(0)}%
                            </span>
                          ) : '—'}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <div className="inline-flex gap-2">
                            <button onClick={() => handleApprovePolicy(p.id)} disabled={acting === p.id}
                              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-green-50 text-green-700 hover:bg-green-100 disabled:opacity-50 transition-colors">
                              <CheckCircle className="w-3.5 h-3.5" />Approve
                            </button>
                            <button onClick={() => handleRejectPolicy(p.id)} disabled={acting === p.id}
                              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-red-50 text-red-700 hover:bg-red-100 disabled:opacity-50 transition-colors">
                              <XCircle className="w-3.5 h-3.5" />Reject
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {policyPages > 1 && (
                  <div className="px-4 py-3 border-t border-gray-100 flex items-center justify-between">
                    <span className="text-xs text-gray-500">{((policyPage - 1) * PAGE_SIZE) + 1}–{Math.min(policyPage * PAGE_SIZE, policyTotal)} of {policyTotal}</span>
                    <div className="flex gap-2">
                      <button onClick={() => setPolicyPage(p => Math.max(1, p - 1))} disabled={policyPage === 1} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 disabled:opacity-30"><ChevronLeft className="w-4 h-4" /></button>
                      <span className="text-xs text-gray-500 flex items-center">{policyPage} / {policyPages}</span>
                      <button onClick={() => setPolicyPage(p => Math.min(policyPages, p + 1))} disabled={policyPage === policyPages} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 disabled:opacity-30"><ChevronRight className="w-4 h-4" /></button>
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
              </div>
            ) : (
              <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="text-left px-4 py-3 font-medium text-gray-600">Pipeline</th>
                      <th className="text-right px-4 py-3 font-medium text-gray-600 hidden sm:table-cell">Processed</th>
                      <th className="text-right px-4 py-3 font-medium text-gray-600 hidden sm:table-cell">Success</th>
                      <th className="text-right px-4 py-3 font-medium text-gray-600 hidden sm:table-cell">Failed</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-600 hidden lg:table-cell">Started</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {runs.map(run => (
                      <tr key={run.id} className="hover:bg-gray-50 transition-colors">
                        <td className="px-4 py-3 font-medium text-gray-800">{run.pipeline}</td>
                        <td className="px-4 py-3 text-right text-gray-600 hidden sm:table-cell">{run.total_processed?.toLocaleString('en-IN') ?? '—'}</td>
                        <td className="px-4 py-3 text-right text-green-600 hidden sm:table-cell">{run.success_count ?? '—'}</td>
                        <td className="px-4 py-3 text-right hidden sm:table-cell">
                          {run.failed_count > 0 ? <span className="text-red-500">{run.failed_count}</span> : <span className="text-gray-400">0</span>}
                        </td>
                        <td className="px-4 py-3 text-xs text-gray-400 hidden lg:table-cell">
                          {new Date(run.started_at).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}

        {/* ── Requests tab ── */}
        {tab === 'requests' && (
          <>
            {loading && requests.length === 0 ? (
              <div className="text-center py-16 text-gray-400">Loading…</div>
            ) : requests.length === 0 ? (
              <div className="bg-white rounded-2xl border border-gray-200 p-12 text-center">
                <CheckCircle className="w-10 h-10 text-green-400 mx-auto mb-3" />
                <p className="font-medium text-gray-700">No pending requests</p>
              </div>
            ) : (
              <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="text-left px-4 py-3 font-medium text-gray-600">Company</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-600 hidden md:table-cell">Requested By</th>
                      <th className="text-left px-4 py-3 font-medium text-gray-600 hidden lg:table-cell">Date</th>
                      <th className="text-right px-4 py-3 font-medium text-gray-600">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {requests.map(r => (
                      <tr key={r.id} className="hover:bg-gray-50 transition-colors">
                        <td className="px-4 py-3">
                          <div className="font-medium text-gray-900">{r.company_name}</div>
                          {r.cin && <div className="text-xs text-gray-400 font-mono">{r.cin}</div>}
                          {r.notes && <div className="text-xs text-gray-500 mt-0.5">{r.notes}</div>}
                        </td>
                        <td className="px-4 py-3 text-gray-500 text-xs hidden md:table-cell">{r.requested_by ?? '—'}</td>
                        <td className="px-4 py-3 text-xs text-gray-400 hidden lg:table-cell">
                          {new Date(r.created_at).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <select
                            value={r.status}
                            onChange={e => handleUpdateRequestStatus(r.id, e.target.value)}
                            className="text-xs border border-gray-200 rounded-lg px-2 py-1 bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-[#1A7070]"
                          >
                            <option value="pending">Pending</option>
                            <option value="in_progress">In Progress</option>
                            <option value="done">Done</option>
                            <option value="rejected">Rejected</option>
                          </select>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {Math.ceil(requestTotal / PAGE_SIZE) > 1 && (
                  <div className="px-4 py-3 border-t border-gray-100 flex items-center justify-between">
                    <span className="text-xs text-gray-500">{((requestPage - 1) * PAGE_SIZE) + 1}–{Math.min(requestPage * PAGE_SIZE, requestTotal)} of {requestTotal}</span>
                    <div className="flex gap-2">
                      <button onClick={() => setRequestPage(p => Math.max(1, p - 1))} disabled={requestPage === 1} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 disabled:opacity-30"><ChevronLeft className="w-4 h-4" /></button>
                      <span className="text-xs text-gray-500 flex items-center">{requestPage} / {Math.ceil(requestTotal / PAGE_SIZE)}</span>
                      <button onClick={() => setRequestPage(p => Math.min(Math.ceil(requestTotal / PAGE_SIZE), p + 1))} disabled={requestPage === Math.ceil(requestTotal / PAGE_SIZE)} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 disabled:opacity-30"><ChevronRight className="w-4 h-4" /></button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

