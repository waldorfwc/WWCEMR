import { useState, useEffect, useRef } from 'react'
import { portalApi } from '../../lib/portal-api'

/**
 * SMS-step-up + 6-digit code entry + Stripe Checkout redirect.
 * Used by both surgery balance payment (P2) and FMLA fee payment (P5b).
 *
 * Props:
 *   stepUpUrl   — e.g. `/${sid}/payments/step-up` or `/${sid}/fmla/step-up`
 *   checkoutUrl — e.g. `/${sid}/payments/checkout` or `/${sid}/fmla/checkout`
 *   onCancel    — called when the user backs out
 */
export default function StepUpPayFlow({ stepUpUrl, checkoutUrl, onCancel }) {
  const [stage, setStage] = useState('sending')   // sending | code | redirecting | error
  const [token, setToken] = useState(null)
  const [digits, setDigits] = useState(['','','','','',''])
  const [err, setErr] = useState('')
  const refs = useRef([])

  useEffect(() => {
    let cancelled = false
    portalApi.post(stepUpUrl).then(r => {
      if (cancelled) return
      setToken(r.data.step_up_token)
      setStage('code')
    }).catch(e => {
      if (cancelled) return
      setErr(e?.response?.data?.detail || 'Could not start payment.')
      setStage('error')
    })
    return () => { cancelled = true }
  }, [stepUpUrl])

  function setDigit(i, v) {
    const c = v.replace(/\D/g, '').slice(-1)
    const next = [...digits]; next[i] = c; setDigits(next)
    if (c && i < 5) refs.current[i+1]?.focus()
  }

  async function submit(e) {
    e?.preventDefault?.()
    const code = digits.join('')
    if (code.length !== 6) return
    setErr(''); setStage('redirecting')
    try {
      const { data } = await portalApi.post(checkoutUrl, {
        step_up_token: token, code,
      })
      window.location.assign(data.checkout_url)
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Invalid code.')
      setStage('code')
    }
  }

  useEffect(() => {
    if (stage === 'code' && digits.every(d => d !== '')) submit()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [digits, stage])

  if (stage === 'sending') {
    return <div className="text-sm text-gray-500 mt-4">Sending you a code…</div>
  }
  if (stage === 'redirecting') {
    return <div className="text-sm text-gray-500 mt-4">Redirecting to Stripe…</div>
  }
  if (stage === 'error') {
    return (
      <div className="mt-4">
        <div className="text-sm text-red-600">{err}</div>
        <button onClick={onCancel} className="btn-secondary mt-2">Back</button>
      </div>
    )
  }
  return (
    <form onSubmit={submit} className="mt-4 space-y-3">
      <div className="text-sm text-gray-600">
        Enter the 6-digit code we just texted you. (5 min expiry.)
      </div>
      <div className="flex gap-2">
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
      <div className="flex gap-2">
        <button type="submit" disabled={digits.join('').length !== 6}
                 className="btn-primary">Continue</button>
        <button type="button" onClick={onCancel}
                 className="btn-secondary">Cancel</button>
      </div>
    </form>
  )
}
