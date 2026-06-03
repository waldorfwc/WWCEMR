import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { usePortalAuth } from '../../hooks/usePortalAuth'
import logoFull from '../../assets/wwc-logo-full.png'

export default function PortalLogin() {
  const [dob, setDob] = useState('')
  const [last4, setLast4] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const { login } = usePortalAuth()
  const nav = useNavigate()

  async function submit(e) {
    e.preventDefault()
    setErr(''); setBusy(true)
    try {
      const { challenge_token } = await login(dob, last4)
      nav('/portal/verify', { state: { challenge_token } })
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Sign-in failed.')
    } finally { setBusy(false) }
  }

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
      <form onSubmit={submit}
            className="bg-white rounded-lg shadow p-6 max-w-sm w-full space-y-4">
        <div className="flex justify-center mb-1">
          <img src={logoFull} alt="Waldorf Women's Care — Patient Portal"
               className="h-20 w-auto" />
        </div>
        <p className="text-sm text-gray-600">
          Sign in with your date of birth and the last 4 digits of the phone
          number we have on file. We'll text you a verification code.
        </p>
        <label className="block text-sm">
          <span className="text-gray-700">Date of birth</span>
          <input type="date" required value={dob}
                  onChange={e => setDob(e.target.value)}
                  className="mt-1 block w-full rounded border-gray-300" />
        </label>
        <label className="block text-sm">
          <span className="text-gray-700">Last 4 of cell phone</span>
          <input type="text" inputMode="numeric" pattern="\d{4}"
                  required maxLength={4} value={last4}
                  onChange={e => setLast4(e.target.value.replace(/\D/g, ''))}
                  className="mt-1 block w-full rounded border-gray-300"
                  placeholder="1234" />
        </label>
        {err && <div className="text-sm text-red-600">{err}</div>}
        <button type="submit" disabled={busy || !dob || last4.length !== 4}
                className="btn-primary w-full">
          {busy ? 'Sending code…' : 'Continue'}
        </button>
        <p className="text-xs text-gray-500 text-center">
          Lost access? Call our office at <a href="tel:2402522140"
            className="underline">240-252-2140</a>.
        </p>
      </form>
    </div>
  )
}
