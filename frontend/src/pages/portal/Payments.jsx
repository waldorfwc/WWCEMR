import { useState, useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams, useSearchParams } from 'react-router-dom'
import { portalApi } from '../../lib/portal-api'

function fmtMoney(n) {
  return `$${Number(n).toFixed(2)}`
}

function BalanceCard({ data, onPayClick }) {
  const balance = Number(data.balance)
  if (balance <= 0 && Number(data.due) > 0) {
    return (
      <div className="bg-green-50 border border-green-200 rounded-lg p-4">
        <div className="text-sm text-green-700">Paid in full ✓</div>
        <div className="text-2xl font-semibold text-gray-900 mt-1">
          {fmtMoney(data.paid)}
        </div>
      </div>
    )
  }
  if (Number(data.due) === 0) {
    return (
      <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
        <div className="text-sm text-gray-600">Nothing to pay</div>
        <p className="text-xs text-gray-500 mt-1">
          Your insurance covers the full cost of this procedure.
        </p>
      </div>
    )
  }
  return (
    <div className="bg-plum-50 border border-plum-200 rounded-lg p-4">
      <div className="text-sm text-plum-700">You owe</div>
      <div className="text-3xl font-semibold text-gray-900 mt-1">
        {fmtMoney(balance)}
      </div>
      <button onClick={onPayClick} className="btn-primary mt-3">
        Pay now
      </button>
    </div>
  )
}

function PayFlow({ sid, onDone, onCancel }) {
  const [stage, setStage] = useState('sending')   // sending | code | redirecting | error
  const [token, setToken] = useState(null)
  const [digits, setDigits] = useState(['','','','','',''])
  const [err, setErr] = useState('')
  const refs = useRef([])

  // Send code on mount
  useEffect(() => {
    let cancelled = false
    portalApi.post(`/${sid}/payments/step-up`).then(r => {
      if (cancelled) return
      setToken(r.data.step_up_token)
      setStage('code')
    }).catch(e => {
      if (cancelled) return
      setErr(e?.response?.data?.detail || 'Could not start payment.')
      setStage('error')
    })
    return () => { cancelled = true }
  }, [sid])

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
      const { data } = await portalApi.post(`/${sid}/payments/checkout`, {
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

function History({ rows }) {
  if (!rows?.length) return null
  return (
    <section className="bg-white rounded-lg shadow p-4">
      <h2 className="text-sm font-semibold text-gray-700 mb-3">History</h2>
      <ul className="divide-y divide-gray-100">
        {rows.map(r => (
          <li key={r.id} className="py-2 flex items-center justify-between text-sm">
            <span>{(r.paid_at || r.requested_at || '').slice(0, 10)}</span>
            <span className="text-gray-900">{fmtMoney(r.amount_paid)}</span>
            <span className={`text-xs px-2 py-1 rounded ${
              r.status === 'paid' ? 'bg-green-100 text-green-700' :
              r.status === 'failed' ? 'bg-red-100 text-red-700' :
              'bg-gray-200 text-gray-700'
            }`}>{r.status}</span>
          </li>
        ))}
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

  // Stop polling when balance drops to 0 (webhook caught up)
  useEffect(() => {
    if (data && Number(data.balance) === 0 && sp.get('session_id')) {
      qc.invalidateQueries({ queryKey: ['portal-dashboard', sid] })
    }
  }, [data, sid, sp, qc])

  if (isLoading) return <div className="text-sm text-gray-500">Loading…</div>
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-gray-900">Payments</h1>
      <BalanceCard data={data} onPayClick={() => setShowFlow(true)} />
      {showFlow && (
        <PayFlow sid={sid} onCancel={() => setShowFlow(false)} onDone={() => setShowFlow(false)} />
      )}
      <History rows={data.history} />
    </div>
  )
}
