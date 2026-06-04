import { useEffect, useState } from 'react'
import { ExternalLink } from 'lucide-react'
import { portalApi } from '../../lib/portal-api'

/**
 * Direct Stripe Checkout launcher (no SMS step-up).
 *
 * The portal JWT already authenticates the patient — re-prompting for a
 * texted PIN added friction without security benefit. We hit the checkout
 * endpoint, open Stripe in a new tab so the patient can return to the
 * dashboard with one click, and leave a small "we opened a new tab" panel
 * behind for context.
 *
 * Props:
 *   checkoutUrl — e.g. `/${sid}/payments/checkout` or `/${sid}/fmla/checkout`
 *   onCancel    — close handler
 */
export default function StepUpPayFlow({ checkoutUrl, onCancel }) {
  const [stage, setStage] = useState('opening')   // opening | opened | error
  const [stripeUrl, setStripeUrl] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let cancelled = false
    portalApi.post(checkoutUrl, {}).then(r => {
      if (cancelled) return
      setStripeUrl(r.data.checkout_url)
      const w = window.open(r.data.checkout_url, '_blank', 'noopener,noreferrer')
      setStage(w ? 'opened' : 'error')
      if (!w) setErr('Your browser blocked the popup. Use the link below.')
    }).catch(e => {
      if (cancelled) return
      setErr(e?.response?.data?.detail || 'Could not start payment.')
      setStage('error')
    })
    return () => { cancelled = true }
  }, [checkoutUrl])

  if (stage === 'opening') {
    return <div className="text-sm text-plum-700/70 mt-4">Opening Stripe…</div>
  }
  if (stage === 'error') {
    return (
      <div className="mt-4 bg-white rounded-2xl border border-rose-200 p-5 shadow-sm">
        <div className="text-sm text-rose-700">{err || 'Could not open Stripe.'}</div>
        {stripeUrl && (
          <a href={stripeUrl} target="_blank" rel="noopener noreferrer"
             className="text-plum-700 underline text-sm mt-2 inline-flex items-center gap-1">
            <ExternalLink size={12} /> Open Stripe manually
          </a>
        )}
        <div className="mt-3">
          <button onClick={onCancel} className="btn-secondary">Close</button>
        </div>
      </div>
    )
  }
  return (
    <div className="mt-4 bg-white rounded-2xl border border-plum-100 p-5 shadow-sm">
      <div className="text-[13px] text-plum-700/80">
        Stripe opened in a new tab. Finish paying there, then come back to
        this window — your balance will refresh automatically.
      </div>
      <div className="flex gap-2 mt-4 flex-wrap">
        {stripeUrl && (
          <a href={stripeUrl} target="_blank" rel="noopener noreferrer"
             className="btn-primary inline-flex items-center gap-1">
            <ExternalLink size={12} /> Re-open Stripe tab
          </a>
        )}
        <button onClick={onCancel} className="btn-secondary">Back to Portal</button>
      </div>
    </div>
  )
}
