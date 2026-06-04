import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useParams, Link } from 'react-router-dom'
import { ClipboardCheck, Download, CheckCircle2 } from 'lucide-react'
import { portalApi, isStaffPreview } from '../../lib/portal-api'
import { fmt } from '../../utils/api'


function EmptyState({ sid }) {
  return (
    <div className="bg-white rounded-2xl border border-plum-100 p-6 shadow-sm">
      <div className="w-12 h-12 rounded-xl bg-plum-50 grid place-items-center text-plum-700 mb-4">
        <ClipboardCheck size={20} />
      </div>
      <h3 className="font-serif text-[15px] text-plum-ink font-semibold">
        Forms will appear here
      </h3>
      <p className="text-[13px] text-plum-700/80 mt-2 max-w-md">
        Once you've paid your balance and picked a surgery date, your consent
        forms will be sent here automatically.
      </p>
      <div className="mt-5 flex gap-2">
        <Link to={`/portal/s/${sid}/payments`} className="btn-secondary text-sm">
          Go to Payments
        </Link>
        <Link to={`/portal/s/${sid}/schedule`} className="btn-secondary text-sm">
          Go to Schedule
        </Link>
      </div>
    </div>
  )
}


// Translate raw backend errors into patient-friendly copy. Staff-only
// hints (Settings paths, CPT/template internals, stack-trace-ish text)
// get folded into a generic "call the office" message. The original
// detail is preserved in the browser console so coordinators reproducing
// the patient's screen can still see what's wrong.
function patientFriendlyConsentError(detail) {
  const raw = typeof detail === 'string' ? detail : ''
  if (raw) console.warn('[portal/consent] raw backend detail:', raw)
  if (!raw) {
    return 'We couldn’t send your consent forms just now. Please call our office at 240-252-2140 and we’ll send them right over.'
  }
  const technicalPattern = /(template|CPT|cpt|procedure|Settings|Unmatched|Register)/i
  if (technicalPattern.test(raw)) {
    return 'Your consent forms aren’t ready yet — our coordinator needs to set them up on our end. Please call our office at 240-252-2140 and we’ll send them within the day.'
  }
  if (/no phone|cell|phone on file/i.test(raw)) {
    return 'We don’t have a current phone number on file. Please call our office at 240-252-2140 so we can update it and send your forms.'
  }
  return 'We couldn’t send your consent forms just now. Please call our office at 240-252-2140 and we’ll send them right over.'
}


function ResendCard({ onResend, busy, err }) {
  return (
    <div className="bg-amber-50 border border-amber-200 rounded-2xl p-6 shadow-sm">
      <div className="text-[12px] font-semibold uppercase tracking-[0.16em] text-amber-800">
        Consent forms not ready yet
      </div>
      <p className="text-[13px] text-plum-700/90 mt-3 max-w-md">
        Your forms should have been sent automatically. If you don't see them,
        click below to send them now.
      </p>
      {err && <p className="text-sm text-rose-700 mt-3">{err}</p>}
      {!isStaffPreview() && (
        <button onClick={onResend} disabled={busy} className="btn-primary mt-5">
          {busy ? 'Sending…' : 'Send consent forms'}
        </button>
      )}
    </div>
  )
}


function DownloadButton({ sid, env }) {
  const [busy, setBusy] = useState(false)
  async function go() {
    setBusy(true)
    try {
      const r = await portalApi.get(`/${sid}/consent/signed-pdf/${env.id}`,
                                       { responseType: 'blob' })
      const url = URL.createObjectURL(r.data)
      const a = document.createElement('a')
      a.href = url
      a.download = `${(env.template_name || 'consent').replace(/[^a-z0-9]/gi, '_')}.pdf`
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
    } finally { setBusy(false) }
  }
  return (
    <button onClick={go} disabled={busy}
            className="btn-secondary text-sm inline-flex items-center gap-1">
      <Download size={12} /> {busy ? 'Loading…' : 'Download'}
    </button>
  )
}


function EnvelopeRow({ env, sid }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function signNow() {
    setBusy(true); setErr('')
    try {
      const { data } = await portalApi.get(`/${sid}/consent/sign-link/${env.id}`)
      window.location.assign(data.sign_url)
    } catch (e) {
      setErr(patientFriendlyConsentError(e?.response?.data?.detail))
      setBusy(false)
    }
  }

  // Show "you're done · waiting for the practice" when the patient signed
  // but the overall envelope isn't completed yet (surgeon/witness pending).
  const patientDone = !!env.patient_signed_at || env.awaiting_countersignature
  const fullyDone = env.status === 'signed' || env.status === 'completed'

  const tone = fullyDone
    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
    : env.status === 'declined' || env.status === 'voided' || env.status === 'failed'
      ? 'bg-rose-50 text-rose-700 border-rose-200'
      : patientDone
        ? 'bg-sky-50 text-sky-700 border-sky-200'
        : 'bg-amber-50 text-amber-800 border-amber-200'

  const label = fullyDone
    ? 'signed'
    : env.status === 'declined' ? 'declined'
    : env.status === 'voided'   ? 'voided'
    : env.status === 'failed'   ? 'failed'
    : patientDone               ? '✓ Your part is done · awaiting practice countersignature'
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
            {env.template_name || 'Consent form'}
          </div>
          <div className="text-[11px] text-plum-700/80 mt-2 flex items-center gap-2 flex-wrap">
            <span className={`uppercase tracking-wide px-2 py-0.5 rounded-full border ${tone}`}>
              {label}
            </span>
            {env.sent_at && <span>sent {fmt.date(env.sent_at.slice(0, 10))}</span>}
            {env.patient_signed_at && (
              <span>you signed {fmt.date(env.patient_signed_at.slice(0, 10))}</span>
            )}
          </div>
          {err && <div className="text-xs text-rose-700 mt-2">{err}</div>}
        </div>
      </div>
      <div className="flex gap-2 shrink-0">
        {env.can_sign && !isStaffPreview() && (
          <button onClick={signNow} disabled={busy}
                  className="btn-primary text-sm">
            {busy ? 'Opening…' : 'Sign now'}
          </button>
        )}
        {env.can_download && <DownloadButton sid={sid} env={env} />}
      </div>
    </div>
  )
}


export default function Consent() {
  const { sid } = useParams()
  const qc = useQueryClient()
  const [resendErr, setResendErr] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['portal-consent', sid],
    queryFn: () => portalApi.get(`/${sid}/consent`).then(r => r.data),
    refetchInterval: (q) => {
      const d = q.state.data
      if (!d) return false
      const anyInFlight = (d.envelopes || []).some(e =>
        ['sent', 'delivered', 'pending', 'in_progress'].includes(e.status),
      )
      return anyInFlight ? 5000 : false
    },
    staleTime: 5_000,
  })

  const resend = useMutation({
    mutationFn: () => portalApi.post(`/${sid}/consent/resend`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['portal-consent', sid] }),
    onError: (e) => setResendErr(patientFriendlyConsentError(e?.response?.data?.detail)),
  })

  if (isLoading) {
    return <div className="px-6 md:px-10 py-16 text-plum-600/70 text-sm">Loading…</div>
  }

  return (
    <div className="px-6 md:px-10 py-8 md:py-10 max-w-5xl">
      <header className="mb-8">
        <div className="text-[11px] uppercase tracking-[0.22em] text-plum-600/70 font-medium mb-2">
          Surgery portal
        </div>
        <h1 className="font-serif text-[24px] md:text-[30px] text-plum-ink font-semibold tracking-tight leading-tight">
          Consent forms
        </h1>
        <p className="text-[13px] md:text-[14px] text-plum-700/80 mt-2 max-w-xl">
          Review and electronically sign the documents below. Each takes about
          three minutes.
        </p>
      </header>

      {data.envelopes.length === 0 && !data.scheduled_date && (
        <EmptyState sid={sid} />
      )}

      {data.envelopes.length === 0 && data.scheduled_date && (
        <ResendCard
          onResend={() => { setResendErr(''); resend.mutate() }}
          busy={resend.isPending} err={resendErr} />
      )}

      {data.envelopes.length > 0 && (
        <div className="space-y-3">
          {data.envelopes.map(env => (
            <EnvelopeRow key={env.id} env={env} sid={sid} />
          ))}
          {data.all_complete && (
            <div className="mt-4 bg-emerald-50 border border-emerald-200 rounded-xl px-4 py-3
                              flex items-center gap-2 text-[13px] text-emerald-800">
              <CheckCircle2 size={14} />
              All consent forms have been signed by all parties.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
