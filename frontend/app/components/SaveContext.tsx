'use client'

/**
 * SaveContext.tsx
 * ================
 * Saved / shortlisted lenders — persisted in localStorage.
 *
 * Usage:
 *   const { isSaved, toggle, saved, count } = useSaved()
 *   toggle(lenderObj)   // add or remove
 *   isSaved('42')       // boolean
 *   count               // number of saved lenders
 *
 * Cap: 20 lenders maximum (oldest are dropped when limit hit).
 * Works without auth — purely client-side.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from 'react'

// ─────────────────────────────────────────────────────────────
// TYPES
// ─────────────────────────────────────────────────────────────

export interface SavedLender {
  id:          string
  name:        string
  companyType: string
  aum?:        string
  products?:   string[]
  website?:    string | null
  phone?:      string | null
  email?:      string | null
}

interface SaveContextType {
  saved:    SavedLender[]
  count:    number
  isSaved:  (id: string) => boolean
  toggle:   (lender: SavedLender) => void
  clear:    () => void
}

// ─────────────────────────────────────────────────────────────
// CONTEXT
// ─────────────────────────────────────────────────────────────

const SaveContext = createContext<SaveContextType>({
  saved:   [],
  count:   0,
  isSaved: () => false,
  toggle:  () => {},
  clear:   () => {},
})

const STORAGE_KEY = 'mitram360_saved_v1'
const MAX_SAVED   = 20

// ─────────────────────────────────────────────────────────────
// PROVIDER
// ─────────────────────────────────────────────────────────────

export function SaveProvider({ children }: { children: React.ReactNode }) {
  const [saved, setSaved] = useState<SavedLender[]>([])

  // Hydrate from localStorage once on mount (client only)
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (raw) setSaved(JSON.parse(raw))
    } catch {
      // localStorage unavailable or JSON parse error — start empty
    }
  }, [])

  const persist = (next: SavedLender[]) => {
    setSaved(next)
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
      // Quota exceeded or private browsing — silent fail
    }
  }

  const isSaved = useCallback(
    (id: string) => saved.some(s => s.id === id),
    [saved],
  )

  const toggle = useCallback((lender: SavedLender) => {
    setSaved(prev => {
      const exists = prev.some(s => s.id === lender.id)
      const next = exists
        ? prev.filter(s => s.id !== lender.id)
        : [...prev.slice(-(MAX_SAVED - 1)), lender]  // evict oldest if at cap
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      } catch {}
      return next
    })
  }, [])

  const clear = useCallback(() => {
    setSaved([])
    try { localStorage.removeItem(STORAGE_KEY) } catch {}
  }, [])

  return (
    <SaveContext.Provider value={{ saved, count: saved.length, isSaved, toggle, clear }}>
      {children}
    </SaveContext.Provider>
  )
}

// ─────────────────────────────────────────────────────────────
// HOOK
// ─────────────────────────────────────────────────────────────

export const useSaved = () => useContext(SaveContext)
