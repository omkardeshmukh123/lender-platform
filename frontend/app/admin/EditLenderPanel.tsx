'use client'

import { useEffect, useState } from 'react'
import { X, Save, Trash2, ShieldCheck } from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

interface Props {
  lenderId: number
  lenderName: string
  token: string
  onClose: () => void
  onSaved: () => void
}

interface LenderFields {
  website: string
  phone: string
  email: string
  hq_location: string
  hq_state: string
  rbi_category: string
  aum_crores: string
  pan_india: boolean
  primary_loan_segments: string
  operating_states: string
}

interface GROFields {
  name: string
  designation: string
  email: string
  phone: string
  source_url: string
  source_type: string
}

const INP = 'w-full text-sm border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-[#1A7070]'
const GRO_SOURCE_TYPES = ['website', 'rbi_circular', 'annual_report', 'manual']

export default function EditLenderPanel({ lenderId, lenderName, token, onClose, onSaved }: Props) {
  const [fields, setFields] = useState<LenderFields>({
    website: '', phone: '', email: '', hq_location: '', hq_state: '',
    rbi_category: '', aum_crores: '', pan_india: false,
    primary_loan_segments: '', operating_states: '',
  })
  const [gro, setGro] = useState<GROFields>({
    name: '', designation: '', email: '', phone: '', source_url: '', source_type: 'manual',
  })
  const [groExists, setGroExists] = useState(false)
  const [groLastVerified, setGroLastVerified] = useState<string | null>(null)

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [groSaving, setGroSaving] = useState(false)
  const [groDeleting, setGroDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [groError, setGroError] = useState<string | null>(null)
  const [groSuccess, setGroSuccess] = useState<string | null>(null)

  useEffect(() => {
    fetch(`${API_URL}/v1/admin/lenders/${lenderId}/detail`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json() })
      .then(d => {
        setFields({
          website: d.website ?? '',
          phone: d.phone ?? '',
          email: d.email ?? '',
          hq_location: d.hq_location ?? '',
          hq_state: d.hq_state ?? '',
          rbi_category: d.rbi_category ?? '',
          aum_crores: d.aum_crores != null ? String(d.aum_crores) : '',
          pan_india: !!d.pan_india,
          primary_loan_segments: Array.isArray(d.primary_loan_segments)
            ? d.primary_loan_segments.join(', ')
            : (d.primary_loan_segments ?? ''),
          operating_states: Array.isArray(d.operating_states)
            ? d.operating_states.join(', ')
            : (d.operating_states ?? ''),
        })
        const hasGro = !!d.gro_name || !!d.gro_email || !!d.gro_phone
        setGroExists(hasGro)
        setGroLastVerified(d.gro_last_verified_at ?? null)
        setGro({
          name: d.gro_name ?? '',
          designation: d.gro_designation ?? '',
          email: d.gro_email ?? '',
          phone: d.gro_phone ?? '',
          source_url: d.gro_source_url ?? '',
          source_type: d.gro_source_type ?? 'manual',
        })
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [lenderId, token])

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    const payload: Record<string, unknown> = {}
    if (fields.website) payload.website = fields.website
    if (fields.phone) payload.phone = fields.phone
    if (fields.email) payload.email = fields.email
    if (fields.hq_location) payload.hq_location = fields.hq_location
    if (fields.hq_state) payload.hq_state = fields.hq_state
    if (fields.rbi_category) payload.rbi_category = fields.rbi_category
    if (fields.aum_crores) payload.aum_crores = parseFloat(fields.aum_crores)
    payload.pan_india = fields.pan_india
    payload.primary_loan_segments = fields.primary_loan_segments
      ? fields.primary_loan_segments.split(',').map(s => s.trim()).filter(Boolean)
      : []
    payload.operating_states = fields.operating_states
      ? fields.operating_states.split(',').map(s => s.trim()).filter(Boolean)
      : []
    try {
      const res = await fetch(`${API_URL}/v1/admin/lenders/${lenderId}`, {
        method: 'PATCH',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(err.detail ?? res.statusText)
      }
      onSaved()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleGroSave = async () => {
    setGroSaving(true)
    setGroError(null)
    setGroSuccess(null)
    try {
      const res = await fetch(`${API_URL}/v1/admin/lenders/${lenderId}/grievance-officer`, {
        method: 'PATCH',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(gro),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(err.detail ?? res.statusText)
      }
      setGroExists(true)
      setGroLastVerified(new Date().toISOString())
      setGroSuccess('Grievance Officer saved')
      setTimeout(() => setGroSuccess(null), 3000)
    } catch (e: unknown) {
      setGroError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setGroSaving(false)
    }
  }

  const handleGroDelete = async () => {
    if (!window.confirm('Remove this Grievance Officer record?')) return
    setGroDeleting(true)
    setGroError(null)
    try {
      const res = await fetch(`${API_URL}/v1/admin/lenders/${lenderId}/grievance-officer`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(err.detail ?? res.statusText)
      }
      setGroExists(false)
      setGroLastVerified(null)
      setGro({ name: '', designation: '', email: '', phone: '', source_url: '', source_type: 'manual' })
    } catch (e: unknown) {
      setGroError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setGroDeleting(false)
    }
  }

  const setF = (key: keyof LenderFields) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setFields(f => ({ ...f, [key]: e.target.value }))

  const setG = (key: keyof GROFields) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setGro(g => ({ ...g, [key]: e.target.value }))

  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/30" onClick={onClose} />
      <div className="w-full max-w-md bg-white shadow-xl flex flex-col h-full">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 flex-shrink-0">
          <div>
            <h2 className="font-semibold text-gray-900 text-sm">Edit Lender</h2>
            <p className="text-xs text-gray-400 mt-0.5 truncate max-w-xs">{lenderName}</p>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        {loading ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[#1A7070]" />
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
            {error && <p className="text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">{error}</p>}

            {/* ── Lender fields ── */}
            <div>
              <label className="text-xs font-medium text-gray-600 block mb-1">Website</label>
              <input className={INP} value={fields.website} onChange={setF('website')} placeholder="https://..." />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-gray-600 block mb-1">Phone</label>
                <input className={INP} value={fields.phone} onChange={setF('phone')} />
              </div>
              <div>
                <label className="text-xs font-medium text-gray-600 block mb-1">Email</label>
                <input className={INP} value={fields.email} onChange={setF('email')} type="email" />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-gray-600 block mb-1">HQ Location</label>
                <input className={INP} value={fields.hq_location} onChange={setF('hq_location')} />
              </div>
              <div>
                <label className="text-xs font-medium text-gray-600 block mb-1">HQ State</label>
                <input className={INP} value={fields.hq_state} onChange={setF('hq_state')} />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-gray-600 block mb-1">RBI Category</label>
                <input className={INP} value={fields.rbi_category} onChange={setF('rbi_category')} />
              </div>
              <div>
                <label className="text-xs font-medium text-gray-600 block mb-1">AUM (crores)</label>
                <input className={INP} type="number" min="0" value={fields.aum_crores} onChange={setF('aum_crores')} />
              </div>
            </div>

            <div className="flex items-center gap-2">
              <input
                id="pan_india"
                type="checkbox"
                checked={fields.pan_india}
                onChange={e => setFields(f => ({ ...f, pan_india: e.target.checked }))}
                className="rounded border-gray-300 text-[#1A7070] focus:ring-[#1A7070]"
              />
              <label htmlFor="pan_india" className="text-sm text-gray-700">Pan India</label>
            </div>

            <div>
              <label className="text-xs font-medium text-gray-600 block mb-1">
                Primary Loan Segments <span className="text-gray-400 font-normal">(comma-separated)</span>
              </label>
              <textarea className={`${INP} resize-none`} rows={2} value={fields.primary_loan_segments} onChange={setF('primary_loan_segments')} />
            </div>

            <div>
              <label className="text-xs font-medium text-gray-600 block mb-1">
                Operating States <span className="text-gray-400 font-normal">(comma-separated)</span>
              </label>
              <textarea className={`${INP} resize-none`} rows={2} value={fields.operating_states} onChange={setF('operating_states')} />
            </div>

            {/* ── Grievance Officer sub-section ── */}
            <div className="border-t border-gray-100 pt-4">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <ShieldCheck className="w-4 h-4 text-[#1A7070]" />
                  <span className="text-xs font-semibold text-gray-700 uppercase tracking-wide">Grievance Officer</span>
                  {groExists && (
                    <span className="px-1.5 py-0.5 rounded text-xs bg-green-100 text-green-700">on record</span>
                  )}
                </div>
                {groExists && (
                  <button
                    onClick={handleGroDelete}
                    disabled={groDeleting}
                    className="inline-flex items-center gap-1 text-xs text-red-500 hover:text-red-700 disabled:opacity-50 transition-colors"
                  >
                    <Trash2 className="w-3 h-3" />
                    {groDeleting ? 'Removing…' : 'Remove'}
                  </button>
                )}
              </div>

              {groLastVerified && (
                <p className="text-xs text-gray-400 mb-3">
                  Last verified: {new Date(groLastVerified).toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}
                </p>
              )}

              {groError && <p className="text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2 mb-3">{groError}</p>}
              {groSuccess && <p className="text-xs text-green-700 bg-green-50 rounded-lg px-3 py-2 mb-3">{groSuccess}</p>}

              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs font-medium text-gray-600 block mb-1">Name</label>
                    <input className={INP} value={gro.name} onChange={setG('name')} />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-gray-600 block mb-1">Designation</label>
                    <input className={INP} value={gro.designation} onChange={setG('designation')} />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs font-medium text-gray-600 block mb-1">Email</label>
                    <input className={INP} type="email" value={gro.email} onChange={setG('email')} />
                  </div>
                  <div>
                    <label className="text-xs font-medium text-gray-600 block mb-1">Phone</label>
                    <input className={INP} value={gro.phone} onChange={setG('phone')} />
                  </div>
                </div>

                <div>
                  <label className="text-xs font-medium text-gray-600 block mb-1">Source URL</label>
                  <input className={INP} value={gro.source_url} onChange={setG('source_url')} placeholder="https://..." />
                </div>

                <div>
                  <label className="text-xs font-medium text-gray-600 block mb-1">Source Type</label>
                  <select className={INP} value={gro.source_type} onChange={setG('source_type')}>
                    {GRO_SOURCE_TYPES.map(t => (
                      <option key={t} value={t}>{t.replace('_', ' ')}</option>
                    ))}
                  </select>
                </div>

                <button
                  onClick={handleGroSave}
                  disabled={groSaving}
                  className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-[#1A7070] text-[#1A7070] text-sm font-medium hover:bg-[#E6F4F4] disabled:opacity-50 transition-colors"
                >
                  <Save className="w-3.5 h-3.5" />
                  {groSaving ? 'Saving GRO…' : 'Save GRO'}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="px-5 py-4 border-t border-gray-200 flex items-center justify-end gap-3 flex-shrink-0">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900 transition-colors">
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving || loading}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#1A7070] text-white text-sm font-medium hover:bg-[#145858] disabled:opacity-50 transition-colors"
          >
            <Save className="w-3.5 h-3.5" />
            {saving ? 'Saving…' : 'Save Lender'}
          </button>
        </div>
      </div>
    </div>
  )
}
