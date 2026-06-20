import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ClipboardCheck, CheckCircle2 } from 'lucide-react'
import { larcPortalApi } from '../../lib/larc-portal-api'

const SIGNED_STATUSES = ['signed', 'completed']

function EnrollmentRow({ item }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const status = item.status || 'pending'
  const isSigned = SIGNED_STATUSES.includes(status)
  const isFailed = ['declined', 'voided', 'failed'].includes(status)

  async function signNow() {
    setBusy(true); setErr('')
    try {
      const { data } = await larcPortalApi.get(`/enrollment/sign-link/${item.id}`)
      if (data?.sign_url) {
        window.location.href = data.sign_url
      } else {
        setErr('We couldn’t open your enrollment form. Please call our office at 240-252-2140.')
        setBusy(false)
      }
    } catch (e) {
      setErr(e?.response?.data?.detail
             || 'We couldn’t open your enrollment form. Please call our office at 240-252-2140.')
      setBusy(false)
    }
  }

  const tone = isSigned
    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
    : isFailed
      ? 'bg-rose-50 text-rose-700 border-rose-200'
      : 'bg-amber-50 text-amber-800 border-amber-200'

  const label = isSigned
    ? 'signed'
    : isFailed
      ? status
      : 'awaiting your signature'

  return (
    <div className="bg-white rounded-2xl border border-plum-100 p-5 shadow-sm
                      hover:shadow-md transition flex items-start justify-between gap-4">
      <div className="flex items-start gap-4 min-w-0 flex-1">
        <div className="w-12 h-12 rounded-xl bg-plum-50 grid place-items-center text-plum-700 shrink-0">
          <ClipboardCheck size={20} />
        </div>
        <div className="min-w-0">
          <div className="font-serif text-[16px] text-plum-ink font-semibold leading-tight">
            {item.label || item.template_name || 'Enrollment Form'}
          </div>
          <div className="text-[11px] text-plum-700/80 mt-2 flex items-center gap-2 flex-wrap">
            <span className={`uppercase tracking-wide px-2 py-0.5 rounded-full border ${tone}`}>
              {label}
            </span>
          </div>
          {err && <div className="text-xs text-rose-700 mt-2">{err}</div>}
        </div>
      </div>
      <div className="flex gap-2 shrink-0">
        {!isSigned && !isFailed && (
          <button onClick={signNow} disabled={busy} className="btn-primary text-sm">
            {busy ? 'Opening…' : 'Sign Now'}
          </button>
        )}
      </div>
    </div>
  )
}

export default function LarcPortalEnrollment() {
  const enrollQ = useQuery({
    queryKey: ['larc-portal-enrollment'],
    queryFn: () => larcPortalApi.get('/enrollment').then(r => r.data),
    staleTime: 30_000,
  })

  if (enrollQ.isLoading) {
    return (
      <div className="bg-white rounded-2xl border border-plum-100 p-6 shadow-sm
                       text-[13px] text-plum-600/70">
        Loading…
      </div>
    )
  }

  if (enrollQ.isError) {
    return (
      <div className="bg-white rounded-2xl border border-rose-200 p-6 shadow-sm text-[13px] text-rose-700">
        We couldn't load your enrollment forms right now. Please refresh, or
        call our office at <strong>240-252-2140</strong>.
      </div>
    )
  }

  const items = enrollQ.data?.items || []
  const allSigned = items.length > 0 && items.every(i => SIGNED_STATUSES.includes(i.status))

  return (
    <div className="space-y-6">
      <header>
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mb-2">
          Device Tracking
        </div>
        <h1 className="font-serif text-[22px] md:text-[26px] text-plum-ink font-semibold tracking-tight leading-tight">
          Enrollment
        </h1>
        <p className="text-[13px] text-plum-700/80 mt-2 max-w-xl">
          Review and electronically sign the pharmacy enrollment forms below.
        </p>
      </header>

      {items.length === 0 ? (
        <div className="bg-white rounded-2xl border border-plum-100 p-6 shadow-sm">
          <h2 className="font-serif text-[16px] text-plum-ink font-semibold leading-tight">
            No Enrollment Form Needed
          </h2>
          <p className="text-[13px] text-plum-700/80 mt-1">
            There are no enrollment forms for you to sign.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map(item => (
            <EnrollmentRow key={item.id} item={item} />
          ))}
          {allSigned && (
            <div className="mt-2 bg-emerald-50 border border-emerald-200 rounded-xl px-4 py-3
                              flex items-center gap-2 text-[13px] text-emerald-800">
              <CheckCircle2 size={14} />
              All enrollment forms have been signed.
            </div>
          )}
        </div>
      )}

      <div className="text-[11px] text-plum-600/70 text-center pt-6 border-t border-plum-100">
        Need help? Call our office at <strong className="text-plum-700">240-252-2140</strong>.
      </div>
    </div>
  )
}
