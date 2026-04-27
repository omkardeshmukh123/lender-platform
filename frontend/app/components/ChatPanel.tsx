'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import {
  X, Send, RotateCcw, Sparkles,
  Globe, Phone, Mail, MapPin, Building2,
  Copy, Check, ExternalLink, ChevronRight,
} from 'lucide-react'
import { MultiFilters, DEFAULT_FILTERS } from './SearchFilter'

interface LenderResult {
  id:                    number
  company_name:          string
  company_type:          string
  rbi_category:          string | null
  aum_crores:            number | null
  aum_category:          string | null
  hq_state:              string | null
  hq_location:           string | null
  pan_india:             boolean
  primary_loan_segments: string[]
  operating_states:      string[]
  website:               string | null
  quality_score:         number | null
  employee_count:        number | null
  established_year:      number | null
  is_listed:             boolean
  phone:                 string | null
  email:                 string | null
  operating_intensity:   string | null
  business_sector:       string | null
}

interface ApiFilters {
  q?:            string
  loan_type?:    string[]
  state?:        string
  company_type?: string[]
  aum_category?: string[]
  aum_min?:      number
  aum_max?:      number
  pan_india?:    boolean
  is_listed?:    boolean
  sort_by?:      string
  sort_dir?:     'asc' | 'desc'
}

interface ChatMessage {
  role:             'user' | 'assistant'
  content:          string
  intent?:          'filter' | 'compare' | 'lender_detail' | 'concept' | 'qa' | 'greeting' | 'out_of_scope'
  lenders?:         LenderResult[]
  unmatched_names?: string[]
}

interface ChatPanelProps {
  open:             boolean
  onClose:          () => void
  onFiltersApplied: (filters: MultiFilters) => void
  apiUrl:           string
  user:             { access_token?: string } | null
}

const SUGGESTIONS = [
  'Show NBFCs in Maharashtra for MSME loans',
  'Compare Bajaj Finance vs Muthoot Finance',
  'Which PSU banks have gold loans?',
]

function generateUUID(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

function apiFiltersToMultiFilters(f: ApiFilters): MultiFilters {
  let listingStatus = 'All'
  if (f.is_listed === true)  listingStatus = 'Listed Only'
  if (f.is_listed === false) listingStatus = 'Unlisted Only'
  return {
    search:               f.q ?? '',
    loanType:             f.loan_type ?? [],
    state:                f.state ?? 'All States',
    ticketSize:           f.aum_category ?? [],
    companyType:          f.company_type ?? [],
    operatingIntensity:   [],
    businessSector:       [],
    listingStatus,
    establishedYearRange: 'All Years',
    sortField:            (f.sort_by as MultiFilters['sortField']) ?? '',
    sortDirection:        f.sort_dir ?? 'desc',
  }
}

function Markdown({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|\n)/g)
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**'))
          return <strong key={i}>{part.slice(2, -2)}</strong>
        if (part.startsWith('*') && part.endsWith('*'))
          return <em key={i}>{part.slice(1, -1)}</em>
        if (part === '\n') return <br key={i} />
        return <span key={i}>{part}</span>
      })}
    </>
  )
}

function LenderDetailCard({ lender }: { lender: LenderResult }) {
  return (
    <div className="mt-3 rounded-xl overflow-hidden border border-gray-100 text-xs bg-white">
      <div className="px-3 py-2.5 flex items-start justify-between gap-2"
           style={{ background: 'linear-gradient(135deg,#EEF2FF,#F0F4FF)' }}>
        <div>
          <p className="font-bold text-[#001454] text-sm leading-tight">{lender.company_name}</p>
          <p className="text-gray-500 mt-0.5">
            {lender.company_type}{lender.rbi_category ? ` · ${lender.rbi_category}` : ''}
          </p>
        </div>
        <a href={`/lender/${lender.id}`}
           className="flex-shrink-0 flex items-center gap-1 text-[#3B5CCC] hover:underline text-[11px] font-semibold">
          <ExternalLink className="w-3 h-3" /> Profile
        </a>
      </div>
      <div className="divide-y divide-gray-50">
        {(lender.hq_location || lender.hq_state) && (
          <div className="px-3 py-1.5 flex gap-2 text-gray-600">
            <MapPin className="w-3 h-3 text-gray-300 mt-0.5 flex-shrink-0" />
            <span>{lender.hq_location ?? lender.hq_state}</span>
          </div>
        )}
        {lender.aum_crores != null && (
          <div className="px-3 py-1.5 flex gap-2 text-gray-600">
            <Building2 className="w-3 h-3 text-gray-300 mt-0.5 flex-shrink-0" />
            <span>₹{lender.aum_crores.toLocaleString('en-IN')} Cr {lender.aum_category ? `(${lender.aum_category})` : ''}</span>
          </div>
        )}
        {lender.primary_loan_segments.length > 0 && (
          <div className="px-3 py-1.5 text-gray-500 leading-relaxed">
            {lender.primary_loan_segments.slice(0, 5).join(' · ')}
          </div>
        )}
        {lender.website && (
          <div className="px-3 py-1.5 flex gap-2">
            <Globe className="w-3 h-3 text-gray-300 mt-0.5 flex-shrink-0" />
            <a href={lender.website} target="_blank" rel="noopener noreferrer"
               className="text-[#3B5CCC] underline underline-offset-2 break-all">
              {lender.website.replace(/^https?:\/\//, '')}
            </a>
          </div>
        )}
        {lender.phone && (
          <div className="px-3 py-1.5 flex gap-2 text-gray-600">
            <Phone className="w-3 h-3 text-gray-300 mt-0.5 flex-shrink-0" />
            <span>{lender.phone}</span>
          </div>
        )}
        {lender.email && (
          <div className="px-3 py-1.5 flex gap-2">
            <Mail className="w-3 h-3 text-gray-300 mt-0.5 flex-shrink-0" />
            <a href={`mailto:${lender.email}`} className="text-[#3B5CCC] underline underline-offset-2">
              {lender.email}
            </a>
          </div>
        )}
      </div>
    </div>
  )
}

function CompareTable({ lenders }: { lenders: LenderResult[] }) {
  const [copied, setCopied] = useState(false)
  if (lenders.length < 2) return null

  type FieldDef = { label: string; render: (l: LenderResult) => string }
  const fields: FieldDef[] = [
    { label: 'Type',          render: l => l.company_type },
    { label: 'RBI Category',  render: l => l.rbi_category ?? '—' },
    { label: 'AUM (Cr)',      render: l => l.aum_crores != null ? `₹${l.aum_crores.toLocaleString('en-IN')}` : '—' },
    { label: 'AUM Band',      render: l => l.aum_category ?? '—' },
    { label: 'HQ',            render: l => l.hq_location ?? l.hq_state ?? '—' },
    { label: 'Est. Year',     render: l => l.established_year != null ? String(l.established_year) : '—' },
    { label: 'Employees',     render: l => l.employee_count != null ? l.employee_count.toLocaleString('en-IN') : '—' },
    { label: 'Quality',       render: l => l.quality_score != null ? `${l.quality_score.toFixed(1)}/10` : '—' },
    { label: 'Loan Products', render: l => l.primary_loan_segments.length ? l.primary_loan_segments.slice(0,3).join(', ') : '—' },
    { label: 'Pan India',     render: l => l.pan_india ? 'Yes' : 'No' },
    { label: 'Listed',        render: l => l.is_listed ? 'Yes' : 'No' },
  ]

  const copyAsCSV = () => {
    const header = ['Field', ...lenders.map(l => l.company_name)].join('\t')
    const rows = fields.map(f => [f.label, ...lenders.map(l => f.render(l))].join('\t'))
    navigator.clipboard.writeText([header, ...rows].join('\n')).then(() => {
      setCopied(true); setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="mt-3 rounded-xl border border-gray-100 text-xs overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 bg-gray-50">
        <span className="font-semibold text-gray-500">Side-by-Side Comparison</span>
        <button onClick={copyAsCSV} className="flex items-center gap-1 text-gray-400 hover:text-gray-600 transition-colors">
          {copied ? <Check className="w-3 h-3 text-green-500" /> : <Copy className="w-3 h-3" />}
          <span>{copied ? 'Copied!' : 'Copy CSV'}</span>
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full">
          <thead>
            <tr style={{ background: '#F8F9FC' }}>
              <th className="px-3 py-2 text-left font-semibold text-gray-400 whitespace-nowrap text-[11px] uppercase tracking-wide">Field</th>
              {lenders.map(l => (
                <th key={l.id} className="px-3 py-2 text-left font-bold text-[#1A2B6B]">
                  <a href={`/lender/${l.id}`} className="hover:text-[#3B5CCC] hover:underline">{l.company_name}</a>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {fields.map(f => (
              <tr key={f.label} className="hover:bg-[#F8F9FC] transition-colors">
                <td className="px-3 py-2 text-gray-400 whitespace-nowrap font-semibold">{f.label}</td>
                {lenders.map(l => (
                  <td key={l.id} className="px-3 py-2 text-gray-700">{f.render(l)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function FilterMiniCards({ lenders }: { lenders: LenderResult[] }) {
  return (
    <div className="mt-2 space-y-1.5">
      {lenders.slice(0, 3).map(l => (
        <a key={l.id} href={`/lender/${l.id}`}
           className="flex items-center gap-3 rounded-xl border border-gray-100 px-3 py-2 bg-white
                      hover:border-[#3B5CCC]/30 hover:bg-[#EEF2FF]/30 transition-all duration-200 text-xs">
          <div className="flex-1 min-w-0">
            <p className="font-bold text-[#1A2B6B] truncate">{l.company_name}</p>
            <p className="text-gray-400 mt-0.5 flex gap-2">
              <span>{l.company_type}</span>
              {l.aum_crores != null && <span>₹{l.aum_crores.toLocaleString('en-IN')} Cr</span>}
            </p>
          </div>
          <ChevronRight className="w-3.5 h-3.5 text-gray-300 flex-shrink-0" />
        </a>
      ))}
      {lenders.length > 3 && (
        <p className="text-[11px] text-[#3B5CCC] font-semibold px-1">
          +{lenders.length - 3} more shown in the grid above
        </p>
      )}
    </div>
  )
}

function BotBubble({ msg }: { msg: ChatMessage }) {
  const hasUnmatched = msg.intent === 'compare' && msg.unmatched_names && msg.unmatched_names.length > 0
  return (
    <div className="flex gap-2.5 items-start animate-fade-in-up">
      {/* Avatar */}
      <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5"
           style={{ background: 'linear-gradient(135deg,#001454,#3B5CCC)' }}>
        <Sparkles className="w-3.5 h-3.5 text-white" />
      </div>
      <div className="max-w-[88%]">
        <div className="bg-gray-50 border border-gray-100 rounded-2xl rounded-tl-none px-3.5 py-2.5 text-sm text-gray-800 leading-relaxed">
          <Markdown text={msg.content} />
        </div>
        {hasUnmatched && (
          <p className="mt-1.5 text-xs text-amber-600 px-1">
            Could not find: <strong>{msg.unmatched_names!.join(', ')}</strong>
          </p>
        )}
        {msg.intent === 'lender_detail' && msg.lenders && msg.lenders.length > 0 && (
          <LenderDetailCard lender={msg.lenders[0]} />
        )}
        {msg.intent === 'compare' && msg.lenders && msg.lenders.length >= 2 && (
          <CompareTable lenders={msg.lenders} />
        )}
        {msg.intent === 'filter' && msg.lenders && msg.lenders.length > 0 && (
          <FilterMiniCards lenders={msg.lenders} />
        )}
      </div>
    </div>
  )
}

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end animate-slide-in-right">
      <div className="max-w-[85%] text-white rounded-2xl rounded-tr-none px-3.5 py-2.5 text-sm leading-relaxed"
           style={{ background: 'linear-gradient(135deg,#001454,#3B5CCC)' }}>
        {content}
      </div>
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className="flex gap-2.5 items-start">
      <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0"
           style={{ background: 'linear-gradient(135deg,#001454,#3B5CCC)' }}>
        <Sparkles className="w-3.5 h-3.5 text-white" />
      </div>
      <div className="bg-gray-50 border border-gray-100 rounded-2xl rounded-tl-none px-4 py-3">
        <span className="flex gap-1.5">
          {[0, 1, 2].map(i => (
            <span key={i} className="w-1.5 h-1.5 rounded-full bg-[#3B5CCC]/40 animate-bounce"
                  style={{ animationDelay: `${i * 0.15}s` }} />
          ))}
        </span>
      </div>
    </div>
  )
}

// ─── Main Export ─────────────────────────────────────────────
export function ChatPanel({ open, onClose, onFiltersApplied, apiUrl, user }: ChatPanelProps) {
  const [messages,           setMessages]           = useState<ChatMessage[]>([])
  const [input,              setInput]              = useState('')
  const [loading,            setLoading]            = useState(false)
  const [sessionId,          setSessionId]          = useState<string>(() => generateUUID())
  const [historyLoaded,      setHistoryLoaded]      = useState(false)
  const [aiAvailable,        setAiAvailable]        = useState<boolean | null>(null)
  const [lastAppliedFilters, setLastAppliedFilters] = useState<ApiFilters | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef  = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (!open || !user?.access_token) return
    if (aiAvailable === null) {
      fetch(`${apiUrl}/v1/chat/ping`, {
        headers: { Authorization: `Bearer ${user.access_token}` },
      })
        .then(r => r.ok ? r.json() : null)
        .then(d => setAiAvailable(d?.ai_available ?? false))
        .catch(() => setAiAvailable(false))
    }
    if (!historyLoaded) loadHistory()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const scrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 100)
  }, [open])

  const loadHistory = useCallback(async () => {
    if (!user?.access_token) return
    try {
      const res = await fetch(`${apiUrl}/v1/chat/history`, {
        headers: { Authorization: `Bearer ${user.access_token}` },
      })
      if (!res.ok) return
      const data = await res.json()
      if (data.session_id) setSessionId(data.session_id)
      if (data.messages?.length) {
        setMessages(data.messages.map((m: { role: string; content: string; intent?: string }) => ({
          role:    m.role as 'user' | 'assistant',
          content: m.content,
          intent:  m.intent as ChatMessage['intent'],
        })))
      }
      setHistoryLoaded(true)
    } catch { /* non-fatal */ }
  }, [apiUrl, user?.access_token])

  const sendMessage = useCallback(async (text?: string) => {
    const msg = (text ?? input).trim()
    if (!msg || loading || !user?.access_token) return

    setMessages(prev => [...prev, { role: 'user', content: msg }])
    setInput('')
    setLoading(true)
    setTimeout(scrollToBottom, 50)

    const historyPayload = messages.slice(-12).map(m => ({ role: m.role, content: m.content }))

    try {
      const res = await fetch(`${apiUrl}/v1/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${user.access_token}` },
        body: JSON.stringify({
          message: msg, session_id: sessionId,
          history: historyPayload, last_filters: lastAppliedFilters,
        }),
      })
      if (!res.ok) {
        const body = await res.text()
        let errMsg = 'Sorry, something went wrong. Please try again.'
        if (res.status === 503) {
          if (body.includes('AI_NOT_CONFIGURED')) {
            errMsg = 'The AI service is not configured on this server. Please contact support.'
            setAiAvailable(false)
          } else if (body.includes('AI_UNAVAILABLE')) {
            errMsg = 'The AI service is temporarily unavailable. Please try again in a moment.'
          }
        } else if (res.status === 401) {
          errMsg = 'Your session has expired. Please refresh the page and sign in again.'
        } else if (res.status === 429) {
          errMsg = "You're sending messages too fast. Please wait a moment."
        }
        setMessages(prev => [...prev, { role: 'assistant', content: errMsg, intent: 'qa' }])
        return
      }
      const data = await res.json()
      setMessages(prev => [...prev, {
        role: 'assistant', content: data.answer, intent: data.intent,
        lenders: data.lenders, unmatched_names: data.unmatched_names,
      }])
      if (data.intent === 'filter' && data.applied_filters) {
        setLastAppliedFilters(data.applied_filters)
        onFiltersApplied(apiFiltersToMultiFilters(data.applied_filters))
      }
    } catch {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: 'Unable to reach the server. Check your connection and try again.',
        intent: 'qa',
      }])
    } finally {
      setLoading(false)
    }
  }, [input, loading, user?.access_token, messages, sessionId, apiUrl, onFiltersApplied, lastAppliedFilters])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }
  }

  const startNewChat = () => {
    setMessages([])
    setSessionId(generateUUID())
    setHistoryLoaded(false)
    setLastAppliedFilters(null)
    inputRef.current?.focus()
  }

  if (!open) return null

  return (
    <>
      {/* Mobile backdrop */}
      <div className="fixed inset-0 bg-black/30 backdrop-blur-sm z-20 md:hidden" onClick={onClose} />

      <aside className="fixed right-0 top-0 h-full z-30 flex flex-col
                        md:relative md:shadow-none md:z-auto"
             style={{
               width: '400px',
               background: 'white',
               borderLeft: '1px solid #E5E7EB',
               boxShadow: '0 0 40px rgba(26,43,107,0.12)',
             }}>

        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 flex-shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-full flex items-center justify-center"
                 style={{ background: 'linear-gradient(135deg,#001454,#3B5CCC)' }}>
              <Sparkles className="w-4 h-4 text-white" />
            </div>
            <div>
              <p className="font-bold text-[#001454] text-sm leading-tight">Ask AI</p>
              <p className="text-[10px] text-gray-400">MITRAM360 Intelligence</p>
            </div>
          </div>
          <div className="flex items-center gap-1">
            <button onClick={startNewChat} title="New conversation"
              className="p-1.5 rounded-lg text-gray-400 hover:text-[#3B5CCC] hover:bg-[#EEF2FF] transition-colors">
              <RotateCcw className="w-4 h-4" />
            </button>
            <button onClick={onClose} title="Close"
              className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.length === 0 && !loading && (
            <div className="py-8 text-center">
              <div className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-4"
                   style={{ background: 'linear-gradient(135deg,#EEF2FF,#dce1ff)' }}>
                <Sparkles className="w-7 h-7 text-[#3B5CCC]" />
              </div>
              <p className="font-semibold text-[#1A2B6B] text-sm mb-1">Ask about any lender</p>
              <p className="text-gray-400 text-xs mb-5 leading-relaxed max-w-[260px] mx-auto">
                Filter results, compare lenders, or get details — all in plain English.
              </p>
              <div className="space-y-2">
                {SUGGESTIONS.map(s => (
                  <button key={s} onClick={() => sendMessage(s)}
                    className="block w-full text-left text-xs font-medium text-[#3B5CCC]
                               bg-[#EEF2FF] hover:bg-[#dde3ff] px-3.5 py-2.5 rounded-xl
                               transition-colors border border-[#C7D2FE]/50">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((msg, i) =>
            msg.role === 'user'
              ? <UserBubble key={i} content={msg.content} />
              : <BotBubble  key={i} msg={msg} />
          )}
          {loading && <TypingIndicator />}
          <div ref={bottomRef} />
        </div>

        {/* AI unavailable banner */}
        {aiAvailable === false && (
          <div className="mx-3 mb-2 px-3 py-2.5 rounded-xl text-xs text-amber-700"
               style={{ background: '#FFFBEB', border: '1px solid #FDE68A' }}>
            ⚠ AI is not configured on this server. Contact the admin to set up the Gemini API key.
          </div>
        )}

        {/* Input area */}
        <div className="border-t border-gray-100 p-3 flex-shrink-0 bg-white">
          <div className="flex gap-2 items-end">
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about lenders…"
              rows={1}
              disabled={aiAvailable === false}
              className="flex-1 resize-none rounded-xl border border-gray-200 px-3.5 py-2.5
                         text-sm text-gray-800 placeholder-gray-400
                         focus:outline-none focus:ring-2 focus:border-[#3B5CCC] max-h-28 overflow-y-auto
                         disabled:bg-gray-50 disabled:text-gray-400 transition-all duration-150"
              style={{ lineHeight: '1.5', focusRingColor: 'rgba(59,92,204,0.25)' } as React.CSSProperties}
            />
            <button onClick={() => sendMessage()}
              disabled={!input.trim() || loading || aiAvailable === false}
              className="p-2.5 rounded-xl text-white transition-all duration-200
                         disabled:opacity-40 disabled:cursor-not-allowed
                         hover:shadow-md hover:shadow-[#1A2B6B]/20 hover:-translate-y-0.5
                         active:translate-y-0 flex-shrink-0"
              style={{ background: 'linear-gradient(135deg,#001454,#3B5CCC)' }}>
              <Send className="w-4 h-4" />
            </button>
          </div>
          <p className="text-[10px] text-gray-300 mt-1.5 text-center">
            Enter to send · Shift+Enter for new line
          </p>
        </div>
      </aside>
    </>
  )
}
