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
    <div className="min-h-screen bg-gradient-to-br from-plum-50 via-white to-plum-100/60
                       flex items-center justify-center p-4">
      <form onSubmit={submit}
            className="bg-white rounded-2xl border border-plum-100 shadow-sm
                          p-7 max-w-sm w-full space-y-5">
        <div className="flex flex-col items-center">
          <img src={logoFull} alt="Waldorf Women's Care — Surgery Portal"
               className="h-20 w-auto" />
          <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mt-3">
            Surgery portal
          </div>
        </div>

        <p className="text-[13px] text-plum-700/80 text-center leading-relaxed">
          Sign in with your date of birth and the last 4 digits of the
          phone we have on file. We'll text you a verification code.
        </p>

        <label className="block">
          <span className="text-[11px] uppercase tracking-wide text-plum-700/70 font-medium">
            Date of birth
          </span>
          <input type="date" required value={dob}
                 onChange={e => setDob(e.target.value)}
                 className="mt-1 block w-full rounded-lg border border-plum-200
                              bg-white px-3 py-2 text-sm text-plum-ink
                              focus:border-plum-500 focus:ring-2 focus:ring-plum-200
                              focus:outline-none" />
        </label>

        <label className="block">
          <span className="text-[11px] uppercase tracking-wide text-plum-700/70 font-medium">
            Last 4 of cell phone
          </span>
          <input type="text" inputMode="numeric" pattern="\d{4}"
                 required maxLength={4} value={last4}
                 onChange={e => setLast4(e.target.value.replace(/\D/g, ''))}
                 placeholder="1234"
                 className="mt-1 block w-full rounded-lg border border-plum-200
                              bg-white px-3 py-2 text-sm font-mono text-plum-ink
                              tracking-widest
                              focus:border-plum-500 focus:ring-2 focus:ring-plum-200
                              focus:outline-none" />
        </label>

        {err && (
          <div className="text-[13px] text-rose-700 bg-rose-50 border border-rose-200
                             rounded-lg px-3 py-2">
            {err}
          </div>
        )}

        <button type="submit" disabled={busy || !dob || last4.length !== 4}
                className="w-full rounded-lg bg-plum-700 text-white text-sm font-semibold
                              py-2.5 hover:bg-plum-800 disabled:opacity-50
                              transition">
          {busy ? 'Sending code…' : 'Continue'}
        </button>

        <p className="text-[11px] text-plum-700/60 text-center">
          Lost access? Call our office at{' '}
          <a href="tel:2402522140" className="underline text-plum-700">240-252-2140</a>.
        </p>
        <p className="text-[10px] text-plum-700/60 text-center italic">
          Surgery portal access ends <strong>30 days after your surgery date</strong>.
        </p>
      </form>
    </div>
  )
}
