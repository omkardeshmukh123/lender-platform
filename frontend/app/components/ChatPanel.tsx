'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { X, Send, RotateCcw, Bot } from 'lucide-react'
import { MultiFilters, DEFAULT_FILTERS } from './SearchFilter'

interface LenderResult {
  id:                    number
  company_name:          string
  company_type:          string
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
  role:    'user' | 'assistant'
  content: string
  intent?: 'filter' | 'compare' | 'qa'
  lenders?: LenderResult[]
}

interface ChatPanelProps {
  open:             boolean
  onClose:          () => void
  onFiltersApplied: (filters: MultiFilters) => void
  apiUrl:           string
  user:             { access_token?: string } | null
}

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

function CompareTable({ lenders }: { lenders: LenderResult[] }) {
  if (lenders.length < 2) return null
  const fields: { label: string; key: keyof LenderResult }[] = [
    { label: 'Type',      key: 'company_type' },
    { label: 'AUM (Cr)', key: 'aum_crores' },
    { label: 'HQ',        key: 'hq_state' },
    { label: 'Est. Year', key: 'established_year' },
    { label: 'Employees', key: 'employee_count' },
    { label: 'Pan India', key: 'pan_india' },
    { label: 'Listed',    key: 'is_listed' },
  ]
  return (
    <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200 text-xs">
      <table className="min-w-full">
        <thead>
          <tr className="bg-gray-50">
            <th className="px-2 py-1.5 text-left font-semibold text-gray-500">Field</th>
            {lenders.map(l => (
              <th key={l.id} className="px-2 py-1.5 text-left font-semibold text-gray-800 max-w-[100px] truncate">
                {l.company_name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {fields.map(f => (
            <tr key={f.key}>
              <td className="px-2 py-1 text-gray-500">{f.label}</td>
              {lenders.map(l => {
                const val = l[f.key]
                const display = val === null || val === undefined
                  ? '—'
                  : typeof val === 'boolean'
                  ? (val ? 'Yes' : 'No')
                  : typeof val === 'number' && f.key === 'aum_crores'
                  ? `₹${val.toLocaleString('en-IN')}`
                  : String(val)
                return <td key={l.id} className="px-2 py-1 text-gray-700">{display}</td>
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function BotBubble({ msg }: { msg: ChatMessage }) {
  return (
    <div className="flex gap-2 items-start">
      <div className="w-7 h-7 rounded-full bg-[#3B5CCC] flex items-center justify-center flex-shrink-0 mt-0.5">
        <Bot className="w-4 h-4 text-white" />
      </div>
      <div className="max-w-[85%]">
        <div className="bg-gray-100 rounded-2xl rounded-tl-none px-3 py-2 text-sm text-gray-800 whitespace-pre-wrap">
          {msg.content}
        </div>
        {msg.intent === 'compare' && msg.lenders && msg.lenders.length >= 2 && (
          <CompareTable lenders={msg.lenders} />
        )}
        {msg.intent === 'filter' && msg.lenders !== undefined && (
          <div className="mt-1.5 text-xs text-[#3B5CCC] font-medium">
            Showing {msg.lenders.length} lender{msg.lenders.length !== 1 ? 's' : ''} in grid
          </div>
        )}
      </div>
    </div>
  )
}

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] bg-[#3B5CCC] text-white rounded-2xl rounded-tr-none px-3 py-2 text-sm whitespace-pre-wrap">
        {content}
      </div>
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className="flex gap-2 items-start">
      <div className="w-7 h-7 rounded-full bg-[#3B5CCC] flex items-center justify-center flex-shrink-0">
        <Bot className="w-4 h-4 text-white" />
      </div>
      <div className="bg-gray-100 rounded-2xl rounded-tl-none px-3 py-2">
        <span className="flex gap-1">
          {[0, 1, 2].map(i => (
            <span key={i} className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
              style={{ animationDelay: `${i * 0.15}s` }} />
          ))}
        </span>
      </div>
    </div>
  )
}

export function ChatPanel({ open, onClose, onFiltersApplied, apiUrl, user }: ChatPanelProps) {
  const [messages,      setMessages]      = useState<ChatMessage[]>([])
  const [input,         setInput]         = useState('')
  const [loading,       setLoading]       = useState(false)
  const [sessionId,     setSessionId]     = useState<string>(() => generateUUID())
  const [historyLoaded, setHistoryLoaded] = useState(false)
  const [aiAvailable,   setAiAvailable]   = useState<boolean | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef  = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (!open || !user?.access_token) return
    // Check if AI is configured on first open
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

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  useEffect(() => {
    if (open) inputRef.current?.focus()
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
    } catch {
      // non-fatal
    }
  }, [apiUrl, user?.access_token])

  const sendMessage = useCallback(async () => {
    const text = input.trim()
    if (!text || loading || !user?.access_token) return

    const userMsg: ChatMessage = { role: 'user', content: text }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)

    const historyPayload = messages.slice(-12).map(m => ({ role: m.role, content: m.content }))

    try {
      const res = await fetch(`${apiUrl}/v1/chat`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${user.access_token}` },
        body: JSON.stringify({ message: text, session_id: sessionId, history: historyPayload }),
      })
      if (!res.ok) {
        const body = await res.text()
        let errMsg = 'Sorry, something went wrong. Please try again.'
        if (res.status === 503) {
          if (body.includes('AI_NOT_CONFIGURED')) {
            errMsg = 'The AI service is not configured on the server. Please contact support.'
            setAiAvailable(false)
          } else if (body.includes('AI_UNAVAILABLE')) {
            errMsg = 'The AI service is temporarily unavailable. Please try again in a moment.'
          }
        } else if (res.status === 401) {
          errMsg = 'Your session has expired. Please refresh the page and log in again.'
        } else if (res.status === 429) {
          errMsg = 'You\'re sending messages too fast. Please wait a moment.'
        }
        setMessages(prev => [...prev, { role: 'assistant', content: errMsg, intent: 'qa' }])
        return
      }
      const data = await res.json()
      const botMsg: ChatMessage = {
        role: 'assistant', content: data.answer, intent: data.intent, lenders: data.lenders,
      }
      setMessages(prev => [...prev, botMsg])
      if (data.intent === 'filter' && data.applied_filters) {
        onFiltersApplied(apiFiltersToMultiFilters(data.applied_filters))
      }
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', content: 'Unable to reach the server. Check your connection and try again.', intent: 'qa' }])
    } finally {
      setLoading(false)
    }
  }, [input, loading, user?.access_token, messages, sessionId, apiUrl, onFiltersApplied])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }
  }

  const startNewChat = () => {
    setMessages([])
    setSessionId(generateUUID())
    setHistoryLoaded(false)
    inputRef.current?.focus()
  }

  if (!open) return null

  return (
    <>
      <div className="fixed inset-0 bg-black/20 z-20 md:hidden" onClick={onClose} />
      <aside className="fixed right-0 top-0 h-full w-80 z-30 bg-white border-l border-gray-200 shadow-xl flex flex-col md:relative md:shadow-none md:z-auto">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 flex-shrink-0">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-full bg-[#3B5CCC] flex items-center justify-center">
              <Bot className="w-4 h-4 text-white" />
            </div>
            <span className="font-semibold text-gray-800 text-sm">Ask AI</span>
          </div>
          <div className="flex items-center gap-1">
            <button onClick={startNewChat} title="New chat" className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors">
              <RotateCcw className="w-4 h-4" />
            </button>
            <button onClick={onClose} title="Close" className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.length === 0 && !loading && (
            <div className="text-center py-8">
              <p className="text-gray-400 text-sm">Ask me about lenders, loan types, or compare specific lenders.</p>
              <div className="mt-4 space-y-2">
                {[
                  'Show NBFCs in Maharashtra for MSME loans',
                  'Compare Bajaj Finance vs Muthoot Finance',
                  'What is an NBFC-MFI?',
                ].map(s => (
                  <button key={s} onClick={() => { setInput(s); inputRef.current?.focus() }}
                    className="block w-full text-left text-xs text-[#3B5CCC] bg-blue-50 hover:bg-blue-100 px-3 py-2 rounded-lg transition-colors">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((msg, i) =>
            msg.role === 'user'
              ? <UserBubble key={i} content={msg.content} />
              : <BotBubble key={i} msg={msg} />
          )}
          {loading && <TypingIndicator />}
          <div ref={bottomRef} />
        </div>

        {aiAvailable === false && (
          <div className="mx-3 mb-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700">
            AI is not configured on this server. Contact the admin to set up the Gemini API key.
          </div>
        )}

        <div className="border-t border-gray-200 p-3 flex-shrink-0">
          <div className="flex gap-2 items-end">
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about lenders…"
              rows={1}
              disabled={aiAvailable === false}
              className="flex-1 resize-none rounded-xl border border-gray-200 px-3 py-2 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-[#3B5CCC]/30 focus:border-[#3B5CCC] max-h-28 overflow-y-auto disabled:bg-gray-50 disabled:text-gray-400"
              style={{ lineHeight: '1.4' }}
            />
            <button onClick={sendMessage} disabled={!input.trim() || loading || aiAvailable === false}
              className="p-2 rounded-xl bg-[#3B5CCC] text-white hover:bg-[#2d4aa8] transition-colors disabled:opacity-40 disabled:cursor-not-allowed flex-shrink-0">
              <Send className="w-4 h-4" />
            </button>
          </div>
          <p className="text-[10px] text-gray-400 mt-1.5 text-center">Enter to send · Shift+Enter for new line</p>
        </div>
      </aside>
    </>
  )
}
