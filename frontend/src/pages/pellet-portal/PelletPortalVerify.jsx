import { useState, useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { pelletPortalApi, setPelletSession } from '../../lib/pellet-portal-api'
import logoFull from '../../assets/wwc-logo-full.png'

export default function PelletPortalVerify() {
  const loc = useLocation()
  const nav = useNavigate()
  const challengeToken = loc.state?.challenge_token
  const [digits, setDigits] = useState(['', '', '', '', '', ''])
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const refs = useRef([])

  useEffect(() => {
    if (!challengeToken) nav('/pellet-portal/login', { replace: true })
  }, [challengeToken, nav])

  function setDigit(i, v) {
    const c = v.replace(/\D/g, '').slice(-1)
    const next = [...digits]
    next[i] = c
    setDigits(next)
    if (c && i < 5) refs.current[i + 1]?.focus()
  }

  function handleKeyDown(i, e) {
    if (e.key === 'Backspace' && !digits[i] && i > 0) {
      refs.current[i - 1]?.focus()
    }
  }

  async function submit(e) {
    e?.preventDefault?.()
    const code = digits.join('')
    if (code.length !== 6) return
    setErr(''); setBusy(true)
    try {
      const { data } = await pelletPortalApi.post('/verify', {
        challenge_token: challengeToken, code,
      })
      setPelletSession({ token: data.token, pellet_patient_id: data.pellet_patient_id })
      nav('/pellet-portal/home', { replace: true })
    } catch (e) {
      const detail = e?.response?.data?.detail
      setErr(typeof detail === 'string' ? detail : 'Invalid code.')
    } finally { setBusy(false) }
  }

  useEffect(() => {
    if (digits.every(d => d !== '')) submit()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [digits])

  return (
    <div className="min-h-screen bg-gradient-to-br from-plum-50 via-white to-plum-100/60
                       flex items-center justify-center p-4">
      <form onSubmit={submit}
            className="bg-white rounded-2xl border border-plum-100 shadow-sm
                          p-7 max-w-sm w-full space-y-5">
        <div className="flex flex-col items-center">
          <img src={logoFull} alt="Waldorf Women's Care — Pellet Portal"
               className="h-20 w-auto" />
          <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mt-3">
            Enter Your Code
          </div>
        </div>

        <p className="text-[13px] text-plum-700/80 text-center leading-relaxed">
          We texted a 6-digit code to the phone we have on file. It expires
          in 5 minutes.
        </p>

        <div className="flex justify-center gap-2">
          {digits.map((d, i) => (
            <input key={i}
                    ref={el => refs.current[i] = el}
                    type="text" inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={1} value={d}
                    onChange={e => setDigit(i, e.target.value)}
                    onKeyDown={e => handleKeyDown(i, e)}
                    className="w-11 h-12 text-center text-[20px] font-mono font-semibold
                                  rounded-lg border border-plum-300 bg-white
                                  text-plum-ink shadow-sm
                                  focus:border-plum-500 focus:ring-2 focus:ring-plum-200
                                  focus:outline-none" />
          ))}
        </div>

        {err && (
          <div className="text-[13px] text-rose-700 bg-rose-50 border border-rose-200
                             rounded-lg px-3 py-2">
            {err}
          </div>
        )}

        <button type="submit" disabled={busy || digits.join('').length !== 6}
                className="w-full rounded-lg bg-plum-700 text-white text-sm font-semibold
                              py-2.5 hover:bg-plum-800 disabled:opacity-50
                              transition">
          {busy ? 'Checking…' : 'Sign In'}
        </button>

        <p className="text-[11px] text-plum-700/60 text-center">
          Didn't get it?{' '}
          <a href="/pellet-portal/login" className="underline text-plum-700">Start Over</a>.
        </p>
      </form>
    </div>
  )
}
