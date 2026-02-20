'use client'

import { createContext, useContext, useEffect, useState } from 'react'
import { createClient } from '@supabase/supabase-js'

const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL || '',
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || ''
)

type User = {
  id: string
  email: string
}

type AuthContextType = {
  user: User | null
  loading: boolean
  signUp: (email: string, password: string) => Promise<{ data: any; error: any }>
  signIn: (email: string, password: string) => Promise<{ data: any; error: any }>
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  loading: true,
  signUp: async () => ({ data: null, error: null }),
  signIn: async () => ({ data: null, error: null }),
  signOut: async () => {},
})

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
  // Check active session
  supabase.auth.getSession().then(({ data: { session } }) => {
    if (session?.user) {
      // Check if email is confirmed
      const isConfirmed = session.user.confirmed_at != null
      
      if (!isConfirmed) {
        console.log('⚠️ Email not confirmed yet')
      }
      
      setUser({ 
        id: session.user.id, 
        email: session.user.email || '' 
      })
    } else {
      setUser(null)
    }
    setLoading(false)
  })

  // Listen for auth changes
  const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
    console.log('Auth event:', event) // Will show 'SIGNED_IN' when confirmed
    
    if (session?.user) {
      setUser({ 
        id: session.user.id, 
        email: session.user.email || '' 
      })
    } else {
      setUser(null)
    }
  })

  return () => subscription.unsubscribe()
}, [])

  const signUp = async (email: string, password: string) => {
    const result = await supabase.auth.signUp({
      email,
      password,
    })
    return { data: result.data, error: result.error }
  }

  const signIn = async (email: string, password: string) => {
    const result = await supabase.auth.signInWithPassword({
      email,
      password,
    })
    return { data: result.data, error: result.error }
  }

  const signOut = async () => {
    await supabase.auth.signOut()
  }

  return (
    <AuthContext.Provider value={{ user, loading, signUp, signIn, signOut }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)