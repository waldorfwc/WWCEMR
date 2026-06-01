import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useParams, Link } from 'react-router-dom'
import { portalApi, isStaffPreview } from '../../lib/portal-api'

function EmptyState({ sid }) {
  return (
    <div className="bg-white rounded-lg shadow p-4 text-sm text-gray-600">
      Once you've paid and picked a surgery date, your consent forms will
      appear here automatically.
      <div className="mt-3 flex gap-2">
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

function ResendCard({ onResend, busy, err }) {
  return (
    <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
      <div className="text-sm text-amber-700 font-medium">
        Consent forms not ready yet
      </div>
      <p className="text-sm text-gray-700 mt-1">
        Your forms should have been sent automatically. If you don't see
        them, click below to send them now.
      </p>
      {err && <p className="text-sm text-red-600 mt-2">{err}</p>}
      {!isStaffPreview() && (
        <button onClick={onResend} disabled={busy}
                 className="btn-primary mt-3">
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
      const r = await portalApi.get(
        `/${sid}/consent/signed-pdf/${env.id}`,
        { responseType: 'blob' },
      )
      const url = URL.createObjectURL(r.data)
      const a = document.createElement('a')
      a.href = url
      a.download = `${(env.template_name || 'consent').replace(/[^a-z0-9]/gi,'_')}.pdf`
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
    } finally { setBusy(false) }
  }
  return (
    <button onClick={go} disabled={busy} className="btn-secondary text-sm">
      {busy ? 'Loading…' : 'Download'}
    </button>
  )
}

function EnvelopeRow({ env, sid }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function signNow() {
    setBusy(true); setErr('')
    try {
      const { data } = await portalApi.get(
        `/${sid}/consent/sign-link/${env.id}`,
      )
      window.location.assign(data.sign_url)
    } catch (e) {
      setErr(e?.response?.data?.detail || 'Could not start signing.')
      setBusy(false)
    }
  }

  const statusBadge =
    env.status === 'signed' || env.status === 'completed'
      ? 'bg-green-100 text-green-700'
      : env.status === 'declined' || env.status === 'voided' || env.status === 'failed'
      ? 'bg-red-100 text-red-700'
      : 'bg-amber-100 text-amber-700'

  return (
    <li className="py-3 flex items-center justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-gray-900 truncate">
          {env.template_name || 'Consent form'}
        </div>
        <div className="text-xs text-gray-500 mt-0.5">
          <span className={`inline-block px-2 py-0.5 rounded ${statusBadge}`}>
            {env.status}
          </span>
          {env.sent_at && <span className="ml-2">sent {env.sent_at.slice(0, 10)}</span>}
        </div>
        {err && <div className="text-xs text-red-600 mt-1">{err}</div>}
      </div>
      <div className="flex gap-2">
        {env.can_sign && !isStaffPreview() && (
          <button onClick={signNow} disabled={busy}
                   className="btn-primary text-sm">
            {busy ? 'Opening…' : 'Sign now'}
          </button>
        )}
        {env.can_download && <DownloadButton sid={sid} env={env} />}
      </div>
    </li>
  )
}

export default function Consent() {
  const { sid } = useParams()
  const qc = useQueryClient()
  const [resendErr, setResendErr] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['portal-consent', sid],
    queryFn: () => portalApi.get(`/${sid}/consent`).then(r => r.data),
    // Poll every 5 seconds while any envelope is in flight, otherwise stop.
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
    onError: (e) => setResendErr(e?.response?.data?.detail || 'Could not send.'),
  })

  if (isLoading) return <div className="text-sm text-gray-500">Loading…</div>

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-gray-900">Consent forms</h1>

      {/* Unscheduled and unsent → empty state explaining the flow */}
      {data.envelopes.length === 0 && !data.scheduled_date && (
        <EmptyState sid={sid} />
      )}

      {/* Scheduled but no envelopes → auto-send must have failed; show resend */}
      {data.envelopes.length === 0 && data.scheduled_date && (
        <ResendCard
          onResend={() => { setResendErr(''); resend.mutate() }}
          busy={resend.isPending} err={resendErr} />
      )}

      {/* Envelopes exist → status list */}
      {data.envelopes.length > 0 && (
        <section className="bg-white rounded-lg shadow p-4">
          <ul className="divide-y divide-gray-100">
            {data.envelopes.map(env => (
              <EnvelopeRow key={env.id} env={env} sid={sid} />
            ))}
          </ul>
          {data.all_complete && (
            <div className="mt-3 text-sm text-green-700">
              ✓ All consent forms have been signed by all parties.
            </div>
          )}
        </section>
      )}
    </div>
  )
}
