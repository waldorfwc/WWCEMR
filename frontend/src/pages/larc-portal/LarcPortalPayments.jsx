import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { CreditCard, CheckCircle2 } from 'lucide-react'
import { larcPortalApi } from '../../lib/larc-portal-api'
import { fmt } from '../../utils/api'

function money(n) {
  const v = Number(n)
  if (!Number.isFinite(v)) return '$0.00'
  return `$${v.toFixed(2)}`
}

function mutationErr(e, fallback) {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string') return detail
  return fallback
}

export default function LarcPortalPayments() {
  const [err, setErr] = useState('')

  const payQ = useQuery({
    queryKey: ['larc-portal-payments'],
    queryFn: () => larcPortalApi.get('/payments').then(r => r.data),
    staleTime: 30_000,
  })

  const checkout = useMutation({
    mutationFn: () => larcPortalApi.post('/payments/checkout').then(r => r.data),
    onSuccess: (d) => { if (d?.checkout_url) window.location.href = d.checkout_url },
    onError: (e) => setErr(mutationErr(e, 'We couldn’t start checkout. Please call our office at 240-252-2140.')),
  })

  if (payQ.isLoading) {
    return (
      <div className="bg-white rounded-2xl border border-plum-100 p-6 shadow-sm
                       text-[13px] text-plum-600/70">
        Loading…
      </div>
    )
  }

  if (payQ.isError) {
    return (
      <div className="bg-white rounded-2xl border border-rose-200 p-6 shadow-sm text-[13px] text-rose-700">
        We couldn't load your payment information right now. Please refresh, or
        call our office at <strong>240-252-2140</strong>.
      </div>
    )
  }

  const data = payQ.data || {}
  const responsibility = Number(data.responsibility) || 0
  const paid = !!data.paid
  const due = responsibility > 0 && !paid

  return (
    <div className="space-y-6">
      <header>
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mb-2">
          Device Tracking
        </div>
        <h1 className="font-serif text-[22px] md:text-[26px] text-plum-ink font-semibold tracking-tight leading-tight">
          Payments
        </h1>
        <p className="text-[13px] text-plum-700/80 mt-2 max-w-xl">
          Your estimated patient responsibility for your device.
        </p>
      </header>

      {paid && (
        <section className="bg-white rounded-2xl border border-emerald-200 shadow-sm p-6">
          <div className="flex items-start gap-4">
            <div className="w-11 h-11 rounded-xl bg-emerald-50 grid place-items-center text-emerald-700 shrink-0">
              <CheckCircle2 size={18} />
            </div>
            <div className="flex-1">
              <h2 className="font-serif text-[16px] text-plum-ink font-semibold leading-tight">
                Paid
              </h2>
              <p className="text-[13px] text-plum-700/80 mt-1">
                Thank you — your payment has been received
                {data.paid_at ? <> on <strong>{fmt.date(data.paid_at)}</strong></> : null}.
              </p>
              {responsibility > 0 && (
                <div className="font-serif text-[22px] text-plum-ink font-semibold mt-3">
                  {money(responsibility)}
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {due && (
        <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-6">
          <div className="flex items-start gap-4">
            <div className="w-11 h-11 rounded-xl bg-plum-50 grid place-items-center text-plum-700 shrink-0">
              <CreditCard size={18} />
            </div>
            <div className="flex-1">
              <h2 className="font-serif text-[16px] text-plum-ink font-semibold leading-tight">
                Amount Due
              </h2>
              <p className="text-[13px] text-plum-700/80 mt-1">
                Please pay your patient responsibility below.
              </p>
              <div className="font-serif text-[28px] text-plum-ink font-semibold mt-3">
                {money(responsibility)}
              </div>
              <button onClick={() => { setErr(''); checkout.mutate() }}
                      disabled={checkout.isPending}
                      className="btn-primary text-sm mt-4">
                {checkout.isPending ? 'Redirecting…' : 'Pay Now'}
              </button>
            </div>
          </div>
        </section>
      )}

      {!paid && !due && (
        <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-6">
          <h2 className="font-serif text-[16px] text-plum-ink font-semibold leading-tight">
            Nothing Due
          </h2>
          <p className="text-[13px] text-plum-700/80 mt-1">
            You have no balance due at this time.
          </p>
        </section>
      )}

      {err && <div className="text-sm text-rose-700">{err}</div>}

      <div className="text-[11px] text-plum-600/70 text-center pt-6 border-t border-plum-100">
        Need help? Call our office at <strong className="text-plum-700">240-252-2140</strong>.
      </div>
    </div>
  )
}
