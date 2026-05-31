import { useCallback, useEffect, useState } from 'react'
import {
  portalApi, setPortalSession, clearPortalSession, getPortalSession,
} from '../lib/portal-api'

export function usePortalAuth() {
  const [session, setSession] = useState(getPortalSession)

  useEffect(() => {
    const onStorage = () => setSession(getPortalSession())
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  const login = useCallback(async (dob, phoneLast4) => {
    const { data } = await portalApi.post('/login', {
      dob, phone_last4: phoneLast4,
    })
    return data    // { challenge_token }
  }, [])

  const verify = useCallback(async (challengeToken, code) => {
    const { data } = await portalApi.post('/verify', {
      challenge_token: challengeToken, code,
    })
    setPortalSession(data)
    setSession({ token: data.token, surgery_id: data.surgery_id })
    return data
  }, [])

  const signOut = useCallback(() => {
    clearPortalSession()
    setSession({ token: null, surgery_id: null })
  }, [])

  return { session, login, verify, signOut }
}
