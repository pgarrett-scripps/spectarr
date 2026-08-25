/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, AUTH_EXPIRED_EVENT, clearAccessToken, getAccessToken, setAccessToken } from '../api/client'
import type { AuthMode, CurrentUser } from '../types'

interface AuthState {
  user: CurrentUser | null
  loading: boolean
  mode: AuthMode
  localUser?: string
  allowRemoteNoAuth: boolean
  login: (username: string, password: string) => Promise<void>
  bootstrap: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [loading, setLoading] = useState(true)
  const [mode, setMode] = useState<AuthMode>('password')
  const [localUser, setLocalUser] = useState<string | undefined>()
  const [allowRemoteNoAuth, setAllowRemoteNoAuth] = useState(false)

  useEffect(() => {
    let active = true
    const initialize = async () => {
      try {
        const configuration = await api.authConfiguration()
        if (!active) return
        setMode(configuration.mode)
        setLocalUser(configuration.localUser)
        setAllowRemoteNoAuth(configuration.allowRemoteNoAuth)
        if (configuration.mode === 'local') clearAccessToken()
        if (configuration.mode === 'local' || getAccessToken()) {
          const currentUser = await api.me()
          if (active) setUser(currentUser)
        }
      } catch {
        clearAccessToken()
      } finally {
        if (active) setLoading(false)
      }
    }
    void initialize()
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    const expired = () => setUser(null)
    window.addEventListener(AUTH_EXPIRED_EVENT, expired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, expired)
  }, [])

  const value = useMemo<AuthState>(() => ({
    user,
    loading,
    mode,
    localUser,
    allowRemoteNoAuth,
    login: async (username, password) => {
      const response = await api.login(username, password)
      setAccessToken(response.accessToken)
      setUser(response.user)
    },
    bootstrap: async (username, password) => {
      const response = await api.bootstrap(username, password)
      setAccessToken(response.accessToken)
      setUser(response.user)
    },
    logout: async () => {
      if (mode === 'local') return
      try {
        await api.logout()
      } finally {
        clearAccessToken()
        setUser(null)
      }
    }
  }), [allowRemoteNoAuth, loading, localUser, mode, user])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}
