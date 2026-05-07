'use client'

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from 'react'
import { supabase } from '../lib/supabase'

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

const SaveContext = createContext<SaveContextType>({
  saved:   [],
  count:   0,
  isSaved: () => false,
  toggle:  () => {},
  clear:   () => {},
})

const STORAGE_KEY = 'mitram360_saved_v1'
const MAX_SAVED   = 20

export function SaveProvider({ children }: { children: React.ReactNode }) {
  const [saved,   setSaved]   = useState<SavedLender[]>([])
  const [userId,  setUserId]  = useState<string | null>(null)

  // Read from localStorage once on mount
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (raw) setSaved(JSON.parse(raw))
    } catch {}
  }, [])

  // Track auth state and sync with DB when logged in
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      const uid = data.session?.user?.id ?? null
      setUserId(uid)
      if (uid) loadFromDb(uid)
    })

    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      const uid = session?.user?.id ?? null
      setUserId(uid)
      // Only reload shortlist on actual sign-in, not on every token refresh
      if (uid && event === 'SIGNED_IN') loadFromDb(uid)
    })
    return () => subscription.unsubscribe()
  }, [])

  async function loadFromDb(uid: string) {
    try {
      const { data, error } = await supabase
        .from('user_shortlists')
        .select('lender_id, lender_data')
        .eq('user_id', uid)
      if (error || !data) return
      const dbItems: SavedLender[] = data.map((r: { lender_id: number; lender_data: SavedLender }) => ({
        ...r.lender_data,
        id: String(r.lender_id),
      }))
      // Merge: DB is authoritative, but keep any local items not yet in DB
      setSaved(prev => {
        const dbIds = new Set(dbItems.map(l => l.id))
        const localOnly = prev.filter(l => !dbIds.has(l.id))
        const merged = [...dbItems, ...localOnly].slice(-MAX_SAVED)
        try { localStorage.setItem(STORAGE_KEY, JSON.stringify(merged)) } catch {}
        return merged
      })
    } catch {}
  }

  const isSaved = useCallback((id: string) => saved.some(s => s.id === id), [saved])

  const toggle = useCallback((lender: SavedLender) => {
    setSaved(prev => {
      const exists = prev.some(s => s.id === lender.id)
      const next = exists
        ? prev.filter(s => s.id !== lender.id)
        : [...prev.slice(-(MAX_SAVED - 1)), lender]
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)) } catch {}

      // Sync to DB for logged-in users — use cached userId, no extra network call
      if (userId) {
        const uid = userId
        if (exists) {
          supabase.from('user_shortlists')
            .delete()
            .eq('user_id', uid)
            .eq('lender_id', parseInt(lender.id, 10))
            .then(() => {})
        } else {
          supabase.from('user_shortlists')
            .upsert({
              user_id:     uid,
              lender_id:   parseInt(lender.id, 10),
              lender_data: lender,
            }, { onConflict: 'user_id,lender_id' })
            .then(() => {})
        }
      }

      return next
    })
  }, [userId])

  const clear = useCallback(() => {
    setSaved([])
    try { localStorage.removeItem(STORAGE_KEY) } catch {}
    if (userId) supabase.from('user_shortlists').delete().eq('user_id', userId).then(() => {})
  }, [userId])

  return (
    <SaveContext.Provider value={{ saved, count: saved.length, isSaved, toggle, clear }}>
      {children}
    </SaveContext.Provider>
  )
}

export const useSaved = () => useContext(SaveContext)
