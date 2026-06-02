import { useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams, useSearchParams } from 'react-router-dom'
import { CreditCard, CheckCircle2 } from 'lucide-react'
import { portalApi, isStaffPreview } from '../../lib/portal-api'
import StepUpPayFlow from '../../components/portal/StepUpPayFlow'

function fmtMoney(n) {
  return `$${Number(n || 0).toFixed(2)}`
}


function BalanceCard({ data, onPayClick }) {
  const balance = Number(data.balance)
  if (balance <= 0 && Number(data.due) > 0) {
    return (
      <div className="bg-emerald-50 border border-emerald-200 rounded-2xl p-6 shadow-sm">
        <div className="flex items-center gap-2 text-emerald-700 text-[12px] font-semibold uppercase tracking-[0.16em]">
          <CheckCircle2 size={14} /> Paid in full
        </div>
        <div className="font-serif text-[40px] text-plum-ink font-semibold mt-2 leading-none">
          {fmtMoney(data.paid)}
        </div>
        <p className="text-[13px] text-plum-700/80 mt-3">
          Thank you for paying ahead of your procedure.
        </p>
      </div>
    )
  }
  if (Number(data.due) === 0) {
    return (
      <div className="bg-white rounded-2xl border border-plum-100 p-6 shadow-sm">
        <div className="text-[12px] font-semibold uppercase tracking-[0.16em] text-plum-600/80">
          Nothing to pay
        </div>
        <p className="text-[13px] text-plum-700/80 mt-2">
          Your insurance covers the full cost of this procedure.
        </p>
      </div>
    )
  }
  return (
    <div className="relative bg-white rounded-2xl border border-rose-200 p-6 shadow-sm overflow-hidden">
      <div className="absolute -right-10 -top-10 w-44 h-44 rounded-full bg-rose-50 opacity-60" />
      <div className="relative">
        <div className="flex items-center gap-2 text-[12px] font-semibold uppercase tracking-[0.16em] text-rose-700">
          <CreditCard size={14} /> Balance due
        </div>
        <div className="font-serif text-[44px] text-plum-ink font-semibold mt-2 leading-none">
          {fmtMoney(balance)}
        </div>
        <p className="text-[13px] text-plum-700/80 mt-3 max-w-md">
          Pay securely with your card, FSA, or HSA. Pre-payment is required
          before your surgery date is locked in.
        </p>
        {!isStaffPreview() && (
          <button onClick={onPayClick} className="btn-primary mt-5">
            Pay now
          </button>
        )}
      </div>
    </div>
  )
}


function History({ rows }) {
  if (!rows?.length) return null
  return (
    <section className="bg-white rounded-2xl border border-plum-100 p-6 shadow-sm">
      <h2 className="font-serif text-[18px] text-plum-ink font-semibold tracking-tight mb-4">
        Payment history
      </h2>
      <ul className="divide-y divide-plum-50">
        {rows.map(r => {
          const tone =
            r.status === 'paid'    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
            : r.status === 'failed' ? 'bg-rose-50 text-rose-700 border-rose-200'
            : 'bg-plum-50 text-plum-700 border-plum-100'
          return (
            <li key={r.id} className="py-3 flex items-center justify-between text-[13px]">
              <span className="text-plum-700/80">
                {(r.paid_at || r.requested_at || '').slice(0, 10)}
              </span>
              <span className="text-plum-ink font-mono font-medium">
                {fmtMoney(r.amount_paid)}
              </span>
              <span className={`text-[10px] uppercase tracking-wide px-2 py-1 rounded-full border ${tone}`}>
                {r.status}
              </span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}


export default function Payments() {
  const { sid } = useParams()
  const [sp] = useSearchParams()
  const qc = useQueryClient()
  const [showFlow, setShowFlow] = useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ['portal-payments', sid],
    queryFn: () => portalApi.get(`/${sid}/payments`).then(r => r.data),
    refetchInterval: sp.get('session_id') ? 2000 : false,
    staleTime: 10_000,
  })

  useEffect(() => {
    if (data && Number(data.balance) === 0 && sp.get('session_id')) {
      qc.invalidateQueries({ queryKey: ['portal-dashboard', sid] })
    }
  }, [data, sid, sp, qc])

  if (isLoading) {
    return (
      <div className="px-6 md:px-10 py-16 text-plum-600/70 text-sm">
        Loading…
      </div>
    )
  }

  return (
    <div className="px-6 md:px-10 py-8 md:py-10 max-w-5xl">
      <header className="mb-8">
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mb-2">
          Patient portal
        </div>
        <h1 className="font-serif text-[32px] md:text-[40px] text-plum-ink font-semibold tracking-tight leading-tight">
          Payments
        </h1>
        <p className="text-[13px] md:text-[14px] text-plum-700/80 mt-2 max-w-xl">
          Securely settle your balance ahead of your procedure. We accept all
          major cards, FSA, and HSA.
        </p>
      </header>

      <div className="space-y-5">
        <BalanceCard data={data} onPayClick={() => setShowFlow(true)} />
        {showFlow && (
          <StepUpPayFlow
            stepUpUrl={`/${sid}/payments/step-up`}
            checkoutUrl={`/${sid}/payments/checkout`}
            onCancel={() => setShowFlow(false)} />
        )}
        <History rows={data.history} />
      </div>
    </div>
  )
}
