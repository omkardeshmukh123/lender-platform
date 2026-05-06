'use client'

import { useEffect, useState } from 'react'
import { X, Save } from 'lucide-react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

interface Props {
  policyId: number
  policyName: string
  token: string
  onClose: () => void
  onSaved: () => void
}

interface PolicyFields {
  product_name: string
  loan_type: string
  loan_amount_min: string
  loan_amount_max: string
  interest_rate_min: string
  interest_rate_max: string
  tenure_min: string
  tenure_max: string
  processing_fee: string
  prepayment_allowed: boolean
  collateral_required: boolean | null
  credit_score_min: string
  credit_score_max: string
  min_age: string
  max_age: string
  employment_types: string
  min_monthly_income: string
  eligible_states: string
  eligibility_notes: string
  kyc_pan_required: boolean
  kyc_aadhaar_required: boolean
  kyc_gstin_required: boolean
}

const INP = 'w-full text-sm border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-[#1A7070]'

const EMPTY: PolicyFields = {
  product_name: '', loan_type: '',
  loan_amount_min: '', loan_amount_max: '',
  interest_rate_min: '', interest_rate_max: '',
  tenure_min: '', tenure_max: '',
  processing_fee: '',
  prepayment_allowed: true, collateral_required: null,
  credit_score_min: '', credit_score_max: '',
  min_age: '', max_age: '',
  employment_types: '', min_monthly_income: '',
  eligible_states: '', eligibility_notes: '',
  kyc_pan_required: false, kyc_aadhaar_required: false, kyc_gstin_required: false,
}

export default function EditPolicyPanel({ policyId, policyName, token, onClose, onSaved }: Props) {
  const [fields, setFields] = useState<PolicyFields>(EMPTY)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch(`${API_URL}/v1/admin/policies/${policyId}/detail`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json() })
      .then(d => {
        setFields({
          product_name: d.product_name ?? '',
          loan_type: d.loan_type ?? '',
          loan_amount_min: d.loan_amount_min != null ? String(d.loan_amount_min) : '',
          loan_amount_max: d.loan_amount_max != null ? String(d.loan_amount_max) : '',
          interest_rate_min: d.interest_rate_min != null ? String(d.interest_rate_min) : '',
          interest_rate_max: d.interest_rate_max != null ? String(d.interest_rate_max) : '',
          tenure_min: d.tenure_min != null ? String(d.tenure_min) : '',
          tenure_max: d.tenure_max != null ? String(d.tenure_max) : '',
          processing_fee: d.processing_fee != null ? String(d.processing_fee) : '',
          prepayment_allowed: d.prepayment_allowed ?? true,
          collateral_required: d.collateral_required ?? null,
          credit_score_min: d.credit_score_min != null ? String(d.credit_score_min) : '',
          credit_score_max: d.credit_score_max != null ? String(d.credit_score_max) : '',
          min_age: d.min_age != null ? String(d.min_age) : '',
          max_age: d.max_age != null ? String(d.max_age) : '',
          employment_types: Array.isArray(d.employment_types) ? d.employment_types.join(', ') : '',
          min_monthly_income: d.min_monthly_income != null ? String(d.min_monthly_income) : '',
          eligible_states: Array.isArray(d.eligible_states) ? d.eligible_states.join(', ') : '',
          eligibility_notes: d.eligibility_notes ?? '',
          kyc_pan_required: d.kyc_pan_required ?? false,
          kyc_aadhaar_required: d.kyc_aadhaar_required ?? false,
          kyc_gstin_required: d.kyc_gstin_required ?? false,
        })
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [policyId, token])

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    const payload: Record<string, unknown> = {}
    if (fields.product_name) payload.product_name = fields.product_name
    if (fields.loan_type) payload.loan_type = fields.loan_type
    if (fields.loan_amount_min !== '') payload.loan_amount_min = parseFloat(fields.loan_amount_min)
    if (fields.loan_amount_max !== '') payload.loan_amount_max = parseFloat(fields.loan_amount_max)
    if (fields.interest_rate_min !== '') payload.interest_rate_min = parseFloat(fields.interest_rate_min)
    if (fields.interest_rate_max !== '') payload.interest_rate_max = parseFloat(fields.interest_rate_max)
    if (fields.tenure_min !== '') payload.tenure_min = parseInt(fields.tenure_min)
    if (fields.tenure_max !== '') payload.tenure_max = parseInt(fields.tenure_max)
    if (fields.processing_fee !== '') payload.processing_fee = parseFloat(fields.processing_fee)
    payload.prepayment_allowed = fields.prepayment_allowed
    if (fields.collateral_required !== null) payload.collateral_required = fields.collateral_required
    if (fields.credit_score_min !== '') payload.credit_score_min = parseInt(fields.credit_score_min)
    if (fields.credit_score_max !== '') payload.credit_score_max = parseInt(fields.credit_score_max)
    if (fields.min_age !== '') payload.min_age = parseInt(fields.min_age)
    if (fields.max_age !== '') payload.max_age = parseInt(fields.max_age)
    payload.employment_types = fields.employment_types
      ? fields.employment_types.split(',').map(s => s.trim()).filter(Boolean)
      : []
    if (fields.min_monthly_income !== '') payload.min_monthly_income = parseFloat(fields.min_monthly_income)
    payload.eligible_states = fields.eligible_states
      ? fields.eligible_states.split(',').map(s => s.trim()).filter(Boolean)
      : []
    if (fields.eligibility_notes) payload.eligibility_notes = fields.eligibility_notes
    payload.kyc_pan_required = fields.kyc_pan_required
    payload.kyc_aadhaar_required = fields.kyc_aadhaar_required
    payload.kyc_gstin_required = fields.kyc_gstin_required

    try {
      const res = await fetch(`${API_URL}/v1/admin/policies/${policyId}`, {
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

  const setF = (key: keyof PolicyFields) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setFields(f => ({ ...f, [key]: e.target.value }))

  const setCheck = (key: keyof PolicyFields) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setFields(f => ({ ...f, [key]: e.target.checked }))

  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/30" onClick={onClose} />
      <div className="w-full max-w-md bg-white shadow-xl flex flex-col h-full">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 flex-shrink-0">
          <div>
            <h2 className="font-semibold text-gray-900 text-sm">Edit Policy</h2>
            <p className="text-xs text-gray-400 mt-0.5 truncate max-w-xs">{policyName}</p>
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

            {/* Product */}
            <div>
              <label className="text-xs font-medium text-gray-600 block mb-1">Product Name</label>
              <input className={INP} value={fields.product_name} onChange={setF('product_name')} />
            </div>
            <div>
              <label className="text-xs font-medium text-gray-600 block mb-1">Loan Type</label>
              <input className={INP} value={fields.loan_type} onChange={setF('loan_type')} />
            </div>

            {/* Loan amounts */}
            <div className="border-t border-gray-100 pt-4">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">Loan Terms</p>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium text-gray-600 block mb-1">Amount Min (L)</label>
                  <input className={INP} type="number" min="0" value={fields.loan_amount_min} onChange={setF('loan_amount_min')} />
                </div>
                <div>
                  <label className="text-xs font-medium text-gray-600 block mb-1">Amount Max (L)</label>
                  <input className={INP} type="number" min="0" value={fields.loan_amount_max} onChange={setF('loan_amount_max')} />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 mt-3">
                <div>
                  <label className="text-xs font-medium text-gray-600 block mb-1">Rate Min (%)</label>
                  <input className={INP} type="number" min="0" step="0.01" value={fields.interest_rate_min} onChange={setF('interest_rate_min')} />
                </div>
                <div>
                  <label className="text-xs font-medium text-gray-600 block mb-1">Rate Max (%)</label>
                  <input className={INP} type="number" min="0" step="0.01" value={fields.interest_rate_max} onChange={setF('interest_rate_max')} />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 mt-3">
                <div>
                  <label className="text-xs font-medium text-gray-600 block mb-1">Tenure Min (mo)</label>
                  <input className={INP} type="number" min="0" value={fields.tenure_min} onChange={setF('tenure_min')} />
                </div>
                <div>
                  <label className="text-xs font-medium text-gray-600 block mb-1">Tenure Max (mo)</label>
                  <input className={INP} type="number" min="0" value={fields.tenure_max} onChange={setF('tenure_max')} />
                </div>
              </div>
              <div className="mt-3">
                <label className="text-xs font-medium text-gray-600 block mb-1">Processing Fee (%)</label>
                <input className={INP} type="number" min="0" step="0.01" value={fields.processing_fee} onChange={setF('processing_fee')} />
              </div>
              <div className="flex items-center gap-4 mt-3">
                <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                  <input type="checkbox" checked={fields.prepayment_allowed} onChange={setCheck('prepayment_allowed')}
                    className="rounded border-gray-300 text-[#1A7070] focus:ring-[#1A7070]" />
                  Prepayment Allowed
                </label>
              </div>
              <div className="mt-3">
                <label className="text-xs font-medium text-gray-600 block mb-1">Collateral Required</label>
                <select className={INP} value={fields.collateral_required === null ? '' : String(fields.collateral_required)}
                  onChange={e => setFields(f => ({ ...f, collateral_required: e.target.value === '' ? null : e.target.value === 'true' }))}>
                  <option value="">Unknown</option>
                  <option value="true">Yes</option>
                  <option value="false">No</option>
                </select>
              </div>
            </div>

            {/* Eligibility */}
            <div className="border-t border-gray-100 pt-4">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">Eligibility</p>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium text-gray-600 block mb-1">Credit Score Min</label>
                  <input className={INP} type="number" min="0" max="900" value={fields.credit_score_min} onChange={setF('credit_score_min')} />
                </div>
                <div>
                  <label className="text-xs font-medium text-gray-600 block mb-1">Credit Score Max</label>
                  <input className={INP} type="number" min="0" max="900" value={fields.credit_score_max} onChange={setF('credit_score_max')} />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 mt-3">
                <div>
                  <label className="text-xs font-medium text-gray-600 block mb-1">Min Age</label>
                  <input className={INP} type="number" min="18" value={fields.min_age} onChange={setF('min_age')} />
                </div>
                <div>
                  <label className="text-xs font-medium text-gray-600 block mb-1">Max Age</label>
                  <input className={INP} type="number" min="18" value={fields.max_age} onChange={setF('max_age')} />
                </div>
              </div>
              <div className="mt-3">
                <label className="text-xs font-medium text-gray-600 block mb-1">
                  Employment Types <span className="text-gray-400 font-normal">(comma-separated)</span>
                </label>
                <textarea className={`${INP} resize-none`} rows={2} value={fields.employment_types} onChange={setF('employment_types')} />
              </div>
              <div className="mt-3">
                <label className="text-xs font-medium text-gray-600 block mb-1">Min Monthly Income (₹)</label>
                <input className={INP} type="number" min="0" value={fields.min_monthly_income} onChange={setF('min_monthly_income')} />
              </div>
              <div className="mt-3">
                <label className="text-xs font-medium text-gray-600 block mb-1">
                  Eligible States <span className="text-gray-400 font-normal">(comma-separated)</span>
                </label>
                <textarea className={`${INP} resize-none`} rows={2} value={fields.eligible_states} onChange={setF('eligible_states')} />
              </div>
              <div className="mt-3">
                <label className="text-xs font-medium text-gray-600 block mb-1">Eligibility Notes</label>
                <textarea className={`${INP} resize-none`} rows={3} value={fields.eligibility_notes} onChange={setF('eligibility_notes')} />
              </div>
            </div>

            {/* KYC */}
            <div className="border-t border-gray-100 pt-4">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">KYC Requirements</p>
              <div className="space-y-2">
                {([
                  ['kyc_pan_required', 'PAN Required'],
                  ['kyc_aadhaar_required', 'Aadhaar Required'],
                  ['kyc_gstin_required', 'GSTIN Required'],
                ] as const).map(([key, label]) => (
                  <label key={key} className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
                    <input type="checkbox" checked={fields[key] as boolean} onChange={setCheck(key)}
                      className="rounded border-gray-300 text-[#1A7070] focus:ring-[#1A7070]" />
                    {label}
                  </label>
                ))}
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
            {saving ? 'Saving…' : 'Save Policy'}
          </button>
        </div>
      </div>
    </div>
  )
}
