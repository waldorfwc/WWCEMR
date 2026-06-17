import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { CreditCard, Package, RefreshCw, CheckCircle2 } from 'lucide-react'
import { pelletPortalApi } from '../../lib/pellet-portal-api'

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

// Given the package tiers and a selected count, find the discount that applies:
// the highest tier whose `count` is <= the selected count. No matching tier → 0% off.
function packagePrice(unitPrice, tiers, count) {
  const price = Number(unitPrice) || 0
  const applicable = (tiers || [])
    .filter(t => Number(t.count) <= count)
    .sort((a, b) => Number(b.count) - Number(a.count))[0]
  const pctOff = applicable ? Number(applicable.percent_off) || 0 : 0
  const full = price * count
  return full * (1 - pctOff / 100)
}

export default function PelletPayments() {
  const qc = useQueryClient()
  const [count, setCount] = useState(2)
  const [err, setErr] = useState('')

  const optionsQ = useQuery({
    queryKey: ['pellet-pay-options'],
    queryFn: () => pelletPortalApi.get('/payment/options').then(r => r.data),
    staleTime: 30_000,
  })
  const statusQ = useQuery({
    queryKey: ['pellet-pay-status'],
    queryFn: () => pelletPortalApi.get('/payment/status').then(r => r.data),
    staleTime: 30_000,
  })

  const single = useMutation({
    mutationFn: () => pelletPortalApi.post('/payment/single').then(r => r.data),
    onSuccess: (d) => { if (d?.checkout_url) window.location.href = d.checkout_url },
    onError: (e) => setErr(mutationErr(e, 'We couldn’t start checkout. Please call our office at 240-252-2140.')),
  })
  const pkg = useMutation({
    mutationFn: () => pelletPortalApi.post('/payment/package', { count }).then(r => r.data),
    onSuccess: (d) => { if (d?.checkout_url) window.location.href = d.checkout_url },
    onError: (e) => setErr(mutationErr(e, 'We couldn’t start checkout. Please call our office at 240-252-2140.')),
  })
  const subscribe = useMutation({
    mutationFn: () => pelletPortalApi.post('/payment/subscribe').then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-pay-status'] })
      qc.invalidateQueries({ queryKey: ['pellet-dashboard'] })
    },
    onError: (e) => setErr(mutationErr(e, 'We couldn’t start your subscription. Please call our office at 240-252-2140.')),
  })

  if (optionsQ.isLoading) {
    return <div className="py-16 text-center text-plum-600/70 text-sm">Loading payment options…</div>
  }
  if (optionsQ.error) {
    return (
      <div className="bg-rose-50 border border-rose-200 rounded-lg p-4 text-rose-800 text-sm">
        We couldn't load payment options right now. Please refresh, or call
        our office at <strong>240-252-2140</strong>.
      </div>
    )
  }

  const opt = optionsQ.data || {}
  const status = statusQ.data || {}
  const tiers = opt.package_tiers || []
  const availableInsertions =
    status.available_insertions ?? opt.available_insertions ?? 0
  const subscription = status.subscription || null
  const subActive = subscription?.status === 'active'

  return (
    <div>
      <header className="mb-6">
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mb-2">
          Pellet Portal
        </div>
        <h1 className="font-serif text-[24px] md:text-[30px] text-plum-ink font-semibold tracking-tight leading-tight">
          Payments
        </h1>
        <p className="text-[13px] md:text-[14px] text-plum-700/80 mt-2 max-w-xl">
          Pay for your pellet insertion. Choose a single insertion, a discounted
          package, or a monthly subscription.
        </p>
      </header>

      {/* Current balance / subscription summary */}
      <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-5 mb-4">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <div className="text-[11px] uppercase tracking-wide text-plum-600/70 font-medium">
              Available Insertions
            </div>
            <div className="font-serif text-[28px] text-plum-ink font-semibold leading-tight">
              {availableInsertions}
            </div>
          </div>
          {subActive && (
            <div className="text-right">
              <div className="inline-flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide
                                px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
                <CheckCircle2 size={12} /> Subscribed
              </div>
              <div className="text-[12px] text-plum-700/80 mt-1">
                {money(subscription.monthly_amount)}/mo · accrued {money(subscription.accrued_credit)}
              </div>
            </div>
          )}
        </div>
      </section>

      <div className="space-y-4">
        {/* Pay for One */}
        {opt.enable_single && (
          <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-6">
            <div className="flex items-start gap-4">
              <div className="w-11 h-11 rounded-xl bg-plum-50 grid place-items-center text-plum-700 shrink-0">
                <CreditCard size={18} />
              </div>
              <div className="flex-1">
                <h2 className="font-serif text-[16px] text-plum-ink font-semibold leading-tight">
                  Pay for One
                </h2>
                <p className="text-[13px] text-plum-700/80 mt-1">
                  A single pellet insertion.
                </p>
                <div className="font-serif text-[22px] text-plum-ink font-semibold mt-3">
                  {money(opt.insertion_price)}
                </div>
                <button onClick={() => { setErr(''); single.mutate() }}
                        disabled={single.isPending}
                        className="btn-primary text-sm mt-4">
                  {single.isPending ? 'Redirecting…' : 'Pay for One'}
                </button>
              </div>
            </div>
          </section>
        )}

        {/* Buy a Package */}
        {opt.enable_package && (
          <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-6">
            <div className="flex items-start gap-4">
              <div className="w-11 h-11 rounded-xl bg-plum-50 grid place-items-center text-plum-700 shrink-0">
                <Package size={18} />
              </div>
              <div className="flex-1">
                <h2 className="font-serif text-[16px] text-plum-ink font-semibold leading-tight">
                  Buy a Package
                </h2>
                <p className="text-[13px] text-plum-700/80 mt-1">
                  Buy multiple insertions up front and save.
                </p>

                <div className="flex items-center gap-2 mt-4">
                  {[2, 3, 4].map(n => (
                    <button key={n} onClick={() => setCount(n)}
                            className={`px-3 py-1.5 rounded-lg text-sm border transition ${
                              count === n
                                ? 'bg-plum-700 text-white border-plum-700'
                                : 'bg-white text-plum-700 border-plum-200 hover:border-plum-400'}`}>
                      {n}
                    </button>
                  ))}
                  <label className="flex items-center gap-2 text-[13px] text-plum-700/80 ml-2">
                    <span>Insertions</span>
                    <input type="number" min={2}
                           value={count}
                           onChange={e => setCount(Math.max(2, Number(e.target.value) || 2))}
                           className="input w-20" />
                  </label>
                </div>

                <div className="font-serif text-[22px] text-plum-ink font-semibold mt-3">
                  {money(packagePrice(opt.insertion_price, tiers, count))}
                  <span className="text-[13px] font-normal text-plum-600/70 ml-2">
                    for {count} insertions
                  </span>
                </div>

                <button onClick={() => { setErr(''); pkg.mutate() }}
                        disabled={pkg.isPending}
                        className="btn-primary text-sm mt-4">
                  {pkg.isPending ? 'Redirecting…' : 'Buy Package'}
                </button>
              </div>
            </div>
          </section>
        )}

        {/* Subscribe Monthly */}
        {opt.enable_subscription && (
          <section className="bg-white rounded-2xl border border-plum-100 shadow-sm p-6">
            <div className="flex items-start gap-4">
              <div className="w-11 h-11 rounded-xl bg-plum-50 grid place-items-center text-plum-700 shrink-0">
                <RefreshCw size={18} />
              </div>
              <div className="flex-1">
                <h2 className="font-serif text-[16px] text-plum-ink font-semibold leading-tight">
                  Subscribe Monthly
                </h2>
                <p className="text-[13px] text-plum-700/80 mt-1">
                  Spread the cost over monthly payments that build toward your insertion.
                </p>

                {subActive ? (
                  <div className="mt-3 flex items-center gap-2 text-[14px] text-emerald-800 bg-emerald-50
                                    border border-emerald-200 rounded-lg px-4 py-3">
                    <CheckCircle2 size={16} />
                    Subscribed — {money(subscription.monthly_amount)}/mo,
                    accrued {money(subscription.accrued_credit)}
                  </div>
                ) : (
                  <>
                    <div className="font-serif text-[22px] text-plum-ink font-semibold mt-3">
                      {money(opt.subscription_monthly_amount)}
                      <span className="text-[13px] font-normal text-plum-600/70 ml-2">
                        per month
                      </span>
                    </div>
                    {subscribe.isSuccess && (
                      <div className="mt-3 flex items-center gap-2 text-[14px] text-emerald-800 bg-emerald-50
                                        border border-emerald-200 rounded-lg px-4 py-3">
                        <CheckCircle2 size={16} /> Subscribed.
                      </div>
                    )}
                    <button onClick={() => { setErr(''); subscribe.mutate() }}
                            disabled={subscribe.isPending}
                            className="btn-primary text-sm mt-4">
                      {subscribe.isPending ? 'Subscribing…' : 'Subscribe Monthly'}
                    </button>
                  </>
                )}
              </div>
            </div>
          </section>
        )}
      </div>

      {err && <div className="text-sm text-rose-700 mt-4">{err}</div>}

      <div className="mt-6">
        <Link to="/pellet-portal/home"
              className="text-[12px] text-plum-700 hover:text-plum-900 underline">
          Back to Checklist
        </Link>
      </div>

      <div className="text-[11px] text-plum-600/70 text-center pt-6 mt-6 border-t border-plum-100">
        Need help? Call our office at <strong className="text-plum-700">240-252-2140</strong>.
      </div>
    </div>
  )
}
