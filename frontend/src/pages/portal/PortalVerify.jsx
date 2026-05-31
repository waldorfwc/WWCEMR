import { useState, useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { usePortalAuth } from '../../hooks/usePortalAuth'

export default function PortalVerify() {
  const loc = useLocation()
  const nav = useNavigate()
  const { verify } = usePortalAuth()
  const challengeToken = loc.state?.challenge_token
  const [digits, setDigits] = useState(['','','','','',''])
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const refs = useRef([])

  useEffect(() => {
    if (!challengeToken) nav('/portal/login', { replace: true })
  }, [challengeToken, nav])

  function setDigit(i, v) {
    const c = v.replace(/\D/g, '').slice(-1)
    const next = [...digits]
    next[i] = c
    setDigits(next)
    if (c && i < 5) refs.current[i+1]?.focus()
  }

  async function submit(e) {
    e?.preventDefault?.()
    const code = digits.join('')
    if (code.length !== 6) return
    setErr(''); setBusy(true)
    try {
      await verify(challengeToken, code)
      // verify() updated localStorage; pull surgery_id back to route.
      const sid = localStorage.getItem('wwc.portal.sid')
      nav(`/portal/s/${sid}`, { replace: true })
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Invalid code.')
    } finally { setBusy(false) }
  }

  // Auto-submit on 6th digit
  useEffect(() => {
    if (digits.every(d => d !== '')) submit()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [digits])

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
      <form onSubmit={submit}
            className="bg-white rounded-lg shadow p-6 max-w-sm w-full space-y-4">
        <h1 className="text-xl font-semibold text-plum-700">Enter your code</h1>
        <p className="text-sm text-gray-600">
          We texted a 6-digit code to the phone we have on file. It expires
          in 5 minutes.
        </p>
        <div className="flex justify-between gap-2">
          {digits.map((d, i) => (
            <input key={i}
                    ref={el => refs.current[i] = el}
                    type="text" inputMode="numeric"
                    maxLength={1} value={d}
                    onChange={e => setDigit(i, e.target.value)}
                    className="w-10 h-12 text-center text-lg rounded border-gray-300" />
          ))}
        </div>
        {err && <div className="text-sm text-red-600">{err}</div>}
        <button type="submit" disabled={busy || digits.join('').length !== 6}
                className="btn-primary w-full">
          {busy ? 'Checking…' : 'Sign in'}
        </button>
        <p className="text-xs text-gray-500 text-center">
          Didn't get it? <a href="/portal/login" className="underline">Start over</a>.
        </p>
      </form>
    </div>
  )
}
