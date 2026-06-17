import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Receipt, ExternalLink } from 'lucide-react'
import { pelletPortalApi } from '../../lib/pellet-portal-api'

function money(n) {
  const v = Number(n)
  if (!Number.isFinite(v)) return '$0.00'
  return `$${v.toFixed(2)}`
}

// Render a YYYY-MM-DD (or ISO) date as MM/DD/YYYY in local time without the
// negative-UTC-offset day-slip bug.
function fmtDate(val) {
  if (!val) return ''
  const m = String(val).match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (m) {
    const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
    return d.toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' })
  }
  const d = new Date(val)
  return Number.isNaN(d.getTime()) ? String(val) : d.toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' })
}

export default function PelletReceipts() {
  const receiptsQ = useQuery({
    queryKey: ['pellet-receipts'],
    queryFn: () => pelletPortalApi.get('/receipts').then(r => r.data),
    staleTime: 30_000,
  })
  const [errorId, setErrorId] = useState(null)
  const [loadingId, setLoadingId] = useState(null)

  const items = receiptsQ.data?.items || []

  async function viewReceipt(id) {
    setErrorId(null)
    setLoadingId(id)
    try {
      const res = await pelletPortalApi.get(`/receipts/${id}/receipt-url`)
      if (res.data?.url) {
        window.open(res.data.url, '_blank', 'noopener')
      } else {
        setErrorId(id)
      }
    } catch {
      setErrorId(id)
    } finally {
      setLoadingId(null)
    }
  }

  return (
    <div>
      <header className="mb-6">
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mb-2">
          Pellet Portal
        </div>
        <h1 className="font-serif text-[24px] md:text-[30px] text-plum-ink font-semibold tracking-tight leading-tight">
          Receipts
        </h1>
        <p className="text-[13px] md:text-[14px] text-plum-700/80 mt-2 max-w-xl">
          Your payment history and receipts.
        </p>
      </header>

      {receiptsQ.isLoading ? (
        <div className="py-16 text-center text-plum-600/70 text-sm">Loading receipts…</div>
      ) : receiptsQ.error ? (
        <div className="bg-rose-50 border border-rose-200 rounded-lg p-4 text-rose-800 text-sm">
          We couldn't load your receipts right now. Please refresh, or call
          our office at <strong>240-252-2140</strong>.
        </div>
      ) : items.length === 0 ? (
        <div className="bg-white rounded-2xl border border-plum-100 shadow-sm p-8 text-center text-plum-600/70 text-sm">
          No receipts yet.
        </div>
      ) : (
        <div className="space-y-3">
          {items.map(r => (
            <section key={r.id}
                     className="bg-white rounded-2xl border border-plum-100 shadow-sm p-5
                                flex items-center gap-3 flex-wrap">
              <div className="w-9 h-9 rounded-lg bg-plum-50 grid place-items-center text-plum-700 shrink-0">
                <Receipt size={16} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[14px] text-plum-ink font-semibold leading-tight">
                  {r.kind_label || r.kind}
                </div>
                <div className="text-[12px] text-plum-700/70 mt-0.5">
                  {fmtDate(r.paid_at)}{r.status ? ` · ${r.status}` : ''}
                </div>
              </div>
              <div className="font-serif text-[18px] text-plum-ink font-semibold shrink-0">
                {money(r.amount)}
              </div>
              <div className="shrink-0 text-right">
                {r.has_receipt && (
                  <button onClick={() => viewReceipt(r.id)}
                          disabled={loadingId === r.id}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px]
                                     font-semibold border border-plum-200 text-plum-700
                                     hover:border-plum-400 hover:bg-plum-50 disabled:opacity-50 transition">
                    <ExternalLink size={13} />
                    {loadingId === r.id ? 'Opening…' : 'View Receipt'}
                  </button>
                )}
                {errorId === r.id && (
                  <div className="text-[11px] text-rose-700 mt-1">Receipt unavailable</div>
                )}
              </div>
            </section>
          ))}
        </div>
      )}

      <div className="text-[11px] text-plum-600/70 text-center pt-6 mt-6 border-t border-plum-100">
        Need help? Call our office at <strong className="text-plum-700">240-252-2140</strong>.
      </div>
    </div>
  )
}
