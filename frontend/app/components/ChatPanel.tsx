'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import {
  X, Send, RotateCcw, Sparkles,
  Globe, Phone, Mail, MapPin, Building2,
  Copy, Check, ExternalLink, ChevronRight,
  ThumbsUp, ThumbsDown,
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
  q?:                   string
  loan_type?:           string[]
  state?:               string
  company_type?:        string[]
  aum_category?:        string[]
  aum_min?:             number
  aum_max?:             number
  pan_india?:           boolean
  is_listed?:           boolean
  operating_intensity?: string[]
  business_sector?:     string[]
  sort_by?:             string
  sort_dir?:            'asc' | 'desc'
}

interface ChatMessage {
  role:               'user' | 'assistant'
  content:            string
  intent?:            'filter' | 'compare' | 'lender_detail' | 'concept' | 'qa' | 'greeting' | 'out_of_scope'
  lenders?:           LenderResult[]
  unmatched_names?:   string[]
  suggested_actions?: string[]
  message_id?:        number
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
    operatingIntensity:   f.operating_intensity ?? [],
    businessSector:       f.business_sector     ?? [],
    listingStatus,
    establishedYearRange: 'All Years',
    sortField:            (f.sort_by as MultiFilters['sortField']) ?? '',
    sortDirection:        f.sort_dir ?? 'desc',
    hasPolicies:          false,
    hasRevenue:           false,
  }
}

function Inline({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g)
  return (
    <>
      {parts.map((p, i) => {
        if (p.startsWith('**') && p.endsWith('**')) return <strong key={i}>{p.slice(2, -2)}</strong>
        if (p.startsWith('*') && p.endsWith('*'))   return <em key={i}>{p.slice(1, -1)}</em>
        return <span key={i}>{p}</span>
      })}
    </>
  )
}

function Markdown({ text }: { text: string }) {
  const lines = text.split('\n')
  const out: React.ReactNode[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (/^[-*]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^[-*]\s+/.test(lines[i]))
        items.push(lines[i++].replace(/^[-*]\s+/, ''))
      out.push(<ul key={out.length} className="list-disc list-inside space-y-0.5 my-1 pl-1">{items.map((it, j) => <li key={j}><Inline text={it} /></li>)}</ul>)
    } else if (/^\d+\.\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\d+\.\s+/.test(lines[i]))
        items.push(lines[i++].replace(/^\d+\.\s+/, ''))
      out.push(<ol key={out.length} className="list-decimal list-inside space-y-0.5 my-1 pl-1">{items.map((it, j) => <li key={j}><Inline text={it} /></li>)}</ol>)
    } else if (line.trim() === '') {
      out.push(<br key={out.length} />); i++
    } else {
      out.push(<p key={out.length} className="leading-relaxed"><Inline text={line} /></p>); i++
    }
  }
  return <>{out}</>
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
    { label: 'Quality',       render: l => l.quality_score != null ? `${Math.round(l.quality_score * 100)}%` : '—' },
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

function BotBubble({
  msg,
  onSuggestion,
  onFeedback,
}: {
  msg:          ChatMessage
  onSuggestion: (s: string) => void
  onFeedback?:  (rating: 'up' | 'down', messageId?: number) => void
}) {
  const [feedback, setFeedback] = useState<'up' | 'down' | null>(null)
  const hasUnmatched = msg.intent === 'compare' && msg.unmatched_names && msg.unmatched_names.length > 0

  const handleFeedback = (rating: 'up' | 'down') => {
    if (feedback) return
    setFeedback(rating)
    onFeedback?.(rating, msg.message_id)
  }

  return (
    <div className="flex gap-2.5 items-start animate-fade-in-up">
      <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5"
           style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)' }}>
        <Sparkles className="w-3.5 h-3.5 text-white" />
      </div>
      <div className="max-w-[88%]">
        <div className="rounded-2xl rounded-tl-none px-3.5 py-2.5 text-sm text-gray-800 leading-relaxed"
             style={{ background: '#F7FAFA', border: '1px solid #E6F4F4' }}>
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
        {msg.suggested_actions && msg.suggested_actions.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5 px-1">
            {msg.suggested_actions.map(s => (
              <button key={s} onClick={() => onSuggestion(s)}
                className="text-[11px] px-2.5 py-1 rounded-full border border-[#3B5CCC]/30 text-[#3B5CCC] bg-blue-50/60 hover:bg-blue-100 transition-colors">
                {s}
              </button>
            ))}
          </div>
        )}
        <div className="mt-1.5 flex items-center gap-0.5 px-1">
          <button
            onClick={() => handleFeedback('up')}
            title="Helpful"
            className={`p-1 rounded transition-colors ${feedback === 'up' ? 'text-green-500' : 'text-gray-300 hover:text-gray-500'}`}
          >
            <ThumbsUp className="w-3 h-3" />
          </button>
          <button
            onClick={() => handleFeedback('down')}
            title="Not helpful"
            className={`p-1 rounded transition-colors ${feedback === 'down' ? 'text-red-400' : 'text-gray-300 hover:text-gray-500'}`}
          >
            <ThumbsDown className="w-3 h-3" />
          </button>
        </div>
      </div>
    </div>
  )
}

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end animate-slide-in-right">
      <div className="max-w-[85%] text-white rounded-2xl rounded-tr-none px-3.5 py-2.5 text-sm leading-relaxed"
           style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)' }}>
        {content}
      </div>
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className="flex gap-2.5 items-start">
      <div className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0"
           style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)' }}>
        <Sparkles className="w-3.5 h-3.5 text-white" />
      </div>
      <div className="rounded-2xl rounded-tl-none px-4 py-3"
           style={{ background: '#F7FAFA', border: '1px solid #E6F4F4' }}>
        <span className="flex gap-1.5">
          {[0, 1, 2].map(i => (
            <span key={i} className="w-1.5 h-1.5 rounded-full animate-bounce"
                  style={{ background: 'rgba(26,112,112,0.4)', animationDelay: `${i * 0.15}s` }} />
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
  const [lastLenderNames,    setLastLenderNames]    = useState<string[]>([])
  // Ref to the scrollable messages container (NOT the sentinel div)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const inputRef           = useRef<HTMLTextAreaElement>(null)

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

  /** Scroll only within the chat panel — never touches the page scroll position */
  const scrollToBottom = useCallback((force = false) => {
    const el = scrollContainerRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120
    if (force || nearBottom) {
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    }
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
        // Restore last applied filters so multi-turn refinement survives a page reload
        const lastFilterMsg = [...data.messages].reverse().find(
          (m: { role: string; filters_used?: ApiFilters | null }) =>
            m.role === 'assistant' && m.filters_used
        )
        if (lastFilterMsg?.filters_used) {
          setLastAppliedFilters(lastFilterMsg.filters_used)
          onFiltersApplied(apiFiltersToMultiFilters(lastFilterMsg.filters_used))
        }
      }
      setHistoryLoaded(true)
    } catch { /* non-fatal */ }
  }, [apiUrl, user?.access_token, onFiltersApplied])

  const sendFeedback = useCallback(async (rating: 'up' | 'down', messageId?: number) => {
    if (!messageId || !user?.access_token) return
    try {
      await fetch(`${apiUrl}/v1/chat/feedback`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${user.access_token}` },
        body:    JSON.stringify({ session_id: sessionId, message_id: messageId, rating }),
      })
    } catch { /* non-fatal */ }
  }, [apiUrl, user?.access_token, sessionId])

  const sendMessage = useCallback(async (text?: string) => {
    const msg = (text ?? input).trim()
    if (!msg || loading || !user?.access_token) return

    setMessages(prev => [...prev, { role: 'user', content: msg }])
    setInput('')
    setLoading(true)
    setTimeout(() => scrollToBottom(true), 50)

    const historyPayload = messages.slice(-12).map(m => ({ role: m.role, content: m.content }))

    try {
      const res = await fetch(`${apiUrl}/v1/chat/stream`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${user.access_token}` },
        body:    JSON.stringify({
          message:           msg,
          session_id:        sessionId,
          history:           historyPayload,
          last_filters:      lastAppliedFilters,
          last_lender_names: lastLenderNames.length ? lastLenderNames : undefined,
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

      // Consume SSE stream
      const reader  = res.body!.getReader()
      const decoder = new TextDecoder()
      let buffer       = ''
      let assistantIdx = -1

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const events = buffer.split('\n\n')
        buffer = events.pop()!  // keep any incomplete trailing event

        for (const event of events) {
          const line = event.trim()
          if (!line.startsWith('data: ')) continue
          try {
            const data = JSON.parse(line.slice(6))

            if (data.type === 'meta') {
              const newMsg: ChatMessage = {
                role:              'assistant',
                content:           '',
                intent:            data.intent,
                lenders:           data.lenders           ?? [],
                unmatched_names:   data.unmatched_names   ?? [],
                suggested_actions: data.suggested_actions ?? [],
              }
              setMessages(prev => {
                assistantIdx = prev.length
                return [...prev, newMsg]
              })
              if (data.lenders?.length) {
                setLastLenderNames(data.lenders.slice(0, 3).map((l: LenderResult) => l.company_name))
              }
              if (data.intent === 'filter' && data.applied_filters) {
                setLastAppliedFilters(data.applied_filters)
                onFiltersApplied(apiFiltersToMultiFilters(data.applied_filters))
              }

            } else if (data.type === 'token' && assistantIdx >= 0) {
              setMessages(prev => {
                const updated = [...prev]
                const cur = updated[assistantIdx]
                if (cur) updated[assistantIdx] = { ...cur, content: cur.content + data.text }
                return updated
              })
              scrollToBottom()

            } else if (data.type === 'done' && assistantIdx >= 0 && data.message_id) {
              setMessages(prev => {
                const updated = [...prev]
                const cur = updated[assistantIdx]
                if (cur) updated[assistantIdx] = { ...cur, message_id: data.message_id }
                return updated
              })

            } else if (data.type === 'error' && assistantIdx >= 0) {
              setMessages(prev => {
                const updated = [...prev]
                const cur = updated[assistantIdx]
                if (cur) updated[assistantIdx] = { ...cur, content: 'Sorry, something went wrong. Please try again.' }
                return updated
              })
            }
          } catch { /* ignore malformed events */ }
        }
      }
    } catch {
      setMessages(prev => [...prev, {
        role:    'assistant',
        content: 'Unable to reach the server. Check your connection and try again.',
        intent:  'qa',
      }])
    } finally {
      setLoading(false)
      setTimeout(() => scrollToBottom(), 80)
    }
  }, [input, loading, user?.access_token, messages, sessionId, apiUrl, onFiltersApplied, lastAppliedFilters, lastLenderNames, scrollToBottom])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }
  }

  const startNewChat = () => {
    setMessages([])
    setSessionId(generateUUID())
    setHistoryLoaded(false)
    setLastAppliedFilters(null)
    setLastLenderNames([])
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
        <div className="flex items-center justify-between px-4 py-3 flex-shrink-0"
             style={{ borderBottom: '1px solid #D8EBEB', background: '#F7FAFA' }}>
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-full flex items-center justify-center"
                 style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)' }}>
              <Sparkles className="w-4 h-4 text-white" />
            </div>
            <div>
              <p className="font-bold text-sm leading-tight" style={{ color: '#0D3333' }}>Ask AI</p>
              <p className="text-[10px] text-gray-400">MITRAM360 Intelligence</p>
            </div>
          </div>
          <div className="flex items-center gap-1">
            <button onClick={startNewChat} title="New conversation"
              className="p-1.5 rounded-lg transition-colors"
              style={{ color: '#7A9E9E' }}
              onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.background = '#E6F4F4'; (e.currentTarget as HTMLButtonElement).style.color = '#1A7070' }}
              onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent'; (e.currentTarget as HTMLButtonElement).style.color = '#7A9E9E' }}
            >
              <RotateCcw className="w-4 h-4" />
            </button>
            <button onClick={onClose} title="Close"
              className="p-1.5 rounded-lg transition-colors"
              style={{ color: '#7A9E9E' }}
              onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.background = '#E6F4F4' }}
              onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent' }}
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Messages — scrollable container, isolated from page scroll */}
        <div
          ref={scrollContainerRef}
          className="flex-1 overflow-y-auto p-4 space-y-4"
          style={{ overscrollBehavior: 'contain' }}
        >
          {messages.length === 0 && !loading && (
            <div className="py-8 text-center">
              <div className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-4"
                   style={{ background: 'linear-gradient(135deg,#E6F4F4,#CCE9E9)' }}>
                <Sparkles className="w-7 h-7" style={{ color: '#1A7070' }} />
              </div>
              <p className="font-semibold text-sm mb-1" style={{ color: '#0D3333' }}>Ask about any lender</p>
              <p className="text-gray-400 text-xs mb-5 leading-relaxed max-w-[260px] mx-auto">
                Filter results, compare lenders, or get details — all in plain English.
              </p>
              <div className="space-y-2">
                {SUGGESTIONS.map(s => (
                  <button key={s} onClick={() => sendMessage(s)}
                    className="block w-full text-left text-xs font-medium px-3.5 py-2.5 rounded-xl
                               transition-colors"
                    style={{ background: '#E6F4F4', color: '#1A7070', border: '1px solid #A8DADA' }}
                    onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.background = '#CCE9E9' }}
                    onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = '#E6F4F4' }}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((msg, i) =>
            msg.role === 'user'
              ? <UserBubble key={i} content={msg.content} />
              : <BotBubble  key={i} msg={msg} onSuggestion={s => sendMessage(s)} onFeedback={sendFeedback} />
          )}
          {loading && <TypingIndicator />}
          {/* Sentinel — never used for scrollIntoView anymore */}
          <div aria-hidden="true" style={{ height: 1 }} />
        </div>

        {/* AI unavailable banner */}
        {aiAvailable === false && (
          <div className="mx-3 mb-2 px-3 py-2.5 rounded-xl text-xs text-amber-700"
               style={{ background: '#FFFBEB', border: '1px solid #FDE68A' }}>
            ⚠ AI is not configured on this server. Contact the admin to set up the Gemini API key.
          </div>
        )}

        {/* Input area */}
        <div className="p-3 flex-shrink-0 bg-white" style={{ borderTop: '1px solid #D8EBEB' }}>
          <div className="flex gap-2 items-end">
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => {
                setInput(e.target.value)
                e.target.style.height = 'auto'
                e.target.style.height = `${Math.min(e.target.scrollHeight, 112)}px`
              }}
              onKeyDown={handleKeyDown}
              placeholder="Ask about lenders…"
              rows={1}
              disabled={aiAvailable === false}
              className="flex-1 resize-none rounded-xl border px-3.5 py-2.5
                         text-sm text-gray-800 placeholder-gray-400
                         focus:outline-none max-h-28 overflow-y-auto
                         disabled:bg-gray-50 disabled:text-gray-400 transition-all duration-150"
              style={{
                lineHeight: '1.5',
                borderColor: '#D8EBEB',
                outline: 'none',
              } as React.CSSProperties}
              onFocus={e => { e.currentTarget.style.borderColor = '#1A7070'; e.currentTarget.style.boxShadow = '0 0 0 3px rgba(26,112,112,0.12)' }}
              onBlur={e =>  { e.currentTarget.style.borderColor = '#D8EBEB'; e.currentTarget.style.boxShadow = 'none' }}
            />
            <button onClick={() => sendMessage()}
              disabled={!input.trim() || loading || aiAvailable === false}
              className="p-2.5 rounded-xl text-white transition-all duration-200
                         disabled:opacity-40 disabled:cursor-not-allowed
                         hover:-translate-y-0.5 active:translate-y-0 flex-shrink-0"
              style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)', boxShadow: '0 2px 8px rgba(26,112,112,0.3)' }}>
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
