'use client'

import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { createClient } from '@supabase/supabase-js'

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  throw new Error(
    'Missing required environment variables: NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY must be set.'
  )
}

const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY)

type User = {
  id: string
  email: string
  access_token?: string
}

type AuthContextType = {
  user: User | null
  loading: boolean
  phoneRequired: boolean
  profileChecking: boolean
  signUp: (email: string, password: string) => Promise<{ data: any; error: any }>
  signIn: (email: string, password: string) => Promise<{ data: any; error: any }>
  signInWithGoogle: () => Promise<void>
  signOut: () => Promise<void>
  savePhone: (phone: string) => Promise<{ error: string | null }>
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  loading: true,
  phoneRequired: false,
  profileChecking: false,
  signUp: async () => ({ data: null, error: null }),
  signIn: async () => ({ data: null, error: null }),
  signInWithGoogle: async () => {},
  signOut: async () => {},
  savePhone: async () => ({ error: null }),
})

async function checkPhoneRequired(userId: string): Promise<boolean> {
  const { data } = await supabase
    .from('user_profiles')
    .select('phone')
    .eq('user_id', userId)
    .maybeSingle()
  return !data?.phone
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [phoneRequired, setPhoneRequired] = useState(false)
  const [profileChecking, setProfileChecking] = useState(false)
  // Tracks which user ID has already had a phone check initiated, preventing
  // duplicate checks when both getSession() and onAuthStateChange(SIGNED_IN)
  // fire on the same page load for Google OAuth users.
  const phoneCheckedForRef = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false

    // Page load: restore existing session and check phone for Google users
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      if (cancelled) return
      if (session?.user) {
        setUser({
          id: session.user.id,
          email: session.user.email || '',
          access_token: session.access_token,
        })
        if (session.user.app_metadata?.provider === 'google' && phoneCheckedForRef.current !== session.user.id) {
          phoneCheckedForRef.current = session.user.id
          setProfileChecking(true)
          const required = await checkPhoneRequired(session.user.id)
          if (!cancelled) {
            setPhoneRequired(required)
            setProfileChecking(false)
          }
        }
      } else {
        setUser(null)
      }
      if (!cancelled) setLoading(false)
    })

    // Auth state changes (login, logout, token refresh)
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      async (event, session) => {
        if (session?.user) {
          setUser({
            id: session.user.id,
            email: session.user.email || '',
            access_token: session.access_token,
          })
          // Check phone only on explicit sign-in, not on every token refresh,
          // and only if getSession() didn't already initiate the check.
          if (event === 'SIGNED_IN' && session.user.app_metadata?.provider === 'google' && phoneCheckedForRef.current !== session.user.id) {
            phoneCheckedForRef.current = session.user.id
            setProfileChecking(true)
            const required = await checkPhoneRequired(session.user.id)
            setPhoneRequired(required)
            setProfileChecking(false)
          }
        } else {
          setUser(null)
          setPhoneRequired(false)
          setProfileChecking(false)
          phoneCheckedForRef.current = null
        }
      }
    )

    return () => {
      cancelled = true
      subscription.unsubscribe()
    }
  }, [])

  const signUp = async (email: string, password: string) => {
    const result = await supabase.auth.signUp({ email, password })
    return { data: result.data, error: result.error }
  }

  const signIn = async (email: string, password: string) => {
    const result = await supabase.auth.signInWithPassword({ email, password })
    return { data: result.data, error: result.error }
  }

  const signInWithGoogle = async () => {
    await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: {
        redirectTo: `${window.location.origin}/dashboard`,
      },
    })
  }

  const signOut = async () => {
    await supabase.auth.signOut()
  }

  const savePhone = async (phone: string): Promise<{ error: string | null }> => {
    if (!user) return { error: 'Not authenticated' }
    const { error } = await supabase
      .from('user_profiles')
      .upsert({ user_id: user.id, email: user.email, phone }, { onConflict: 'user_id' })
    if (error) return { error: error.message }
    setPhoneRequired(false)
    return { error: null }
  }

  return (
    <AuthContext.Provider
      value={{ user, loading, phoneRequired, profileChecking, signUp, signIn, signInWithGoogle, signOut, savePhone }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)
