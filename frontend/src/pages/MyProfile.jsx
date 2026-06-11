import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Shield, Users, CheckCircle2, GraduationCap, ExternalLink, AlertCircle,
  Check, ShieldCheck, Clock,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import LoadingState from '../components/LoadingState'


export default function MyProfile() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['my-profile'],
    queryFn: () => api.get('/auth/me/profile').then(r => r.data),
  })

  if (isLoading) return <LoadingState />
  if (error) return <div className="p-6 text-red-600 text-sm">{error?.response?.data?.detail || error.message}</div>
  if (!data) return null

  return (
    <div className="space-y-4 max-w-3xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">My Profile</h1>
        <p className="text-sm text-gray-500 mt-0.5">
          What you can access in this system. To request changes, ask your administrator.
        </p>
      </div>

      <MyTrainingPanel />

      <div className="card">
        <h2 className="text-sm font-semibold text-gray-700 mb-2">Account</h2>
        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-y-2 gap-x-6 text-sm">
          <div>
            <dt className="text-[11px] uppercase tracking-wide text-gray-400">Email</dt>
            <dd className="font-mono text-gray-800">{data.email}</dd>
          </div>
          <div>
            <dt className="text-[11px] uppercase tracking-wide text-gray-400">Display name</dt>
            <dd className="text-gray-800">{data.display_name || '—'}</dd>
          </div>
          <div>
            <dt className="text-[11px] uppercase tracking-wide text-gray-400">Access level (legacy)</dt>
            <dd className="text-gray-800 capitalize">{data.legacy_group}</dd>
          </div>
        </dl>
      </div>

      <div className="card">
        <h2 className="text-sm font-semibold text-gray-700 mb-2 flex items-center gap-2">
          <Users size={14} className="text-plum-600" /> Groups ({data.groups.length})
        </h2>
        {data.groups.length === 0 ? (
          <div className="text-xs text-amber-700 italic">
            You're not in any groups yet — your administrator needs to add you.
          </div>
        ) : (
          <ul className="space-y-2">
            {data.groups.map(g => (
              <li key={g.id} className="border-l-2 border-plum-200 pl-3 py-1">
                <div className="text-sm font-medium text-gray-800">{g.name}</div>
                {g.description && (
                  <div className="text-xs text-gray-500 mt-0.5">{g.description}</div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="card">
        <h2 className="text-sm font-semibold text-gray-700 mb-2 flex items-center gap-2">
          <Shield size={14} className="text-plum-600" /> Module access
        </h2>
        <p className="text-[11px] text-gray-500 mb-3">
          Your effective tier on each module — the max of every group you
          belong to, or your per-user override if one is set.
          {data.is_super_admin && (
            <> You are a <strong>Super Admin</strong>, which grants access to every module.</>
          )}
        </p>
        <ul className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1.5">
          {(data.tiers || []).map(row => (
            <li key={row.module} className="flex items-baseline gap-2 text-xs">
              <CheckCircle2 size={12}
                            className={row.tier === 'none'
                              ? 'text-gray-300 shrink-0 translate-y-[1px]'
                              : 'text-green-600 shrink-0 translate-y-[1px]'} />
              <span className="font-medium text-gray-700 shrink-0">{row.label}</span>
              <span className="text-plum-700 text-[11px] font-mono">{row.tier}</span>
              {row.source_kind === 'group' && row.source_label && (
                <span className="text-gray-500 truncate">via {row.source_label}</span>
              )}
              {row.source_kind === 'override' && (
                <span className="text-amber-700 truncate">override</span>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}


function MyTrainingPanel() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['my-training'],
    queryFn: () => api.get('/training/mine').then(r => r.data),
  })

  if (isLoading || !data) return null

  const pending = data.pending_acknowledgments || []
  const certs = data.my_certifications || []
  const trainerFor = data.trainer_for || []

  // Sort active first, then expiring soon by date, then revoked/disputed at the bottom
  const today = new Date()
  const sortedCerts = [...certs].sort((a, b) => {
    const score = (c) => {
      if (c.status === 'revoked' || c.status === 'disputed') return 9
      if (!c.is_active) return 5
      if (c.expires_on) {
        const days = (new Date(c.expires_on) - today) / 86400000
        if (days <= 30) return 1
      }
      return 2
    }
    const sa = score(a), sb = score(b)
    if (sa !== sb) return sa - sb
    return (a.template?.title || '').localeCompare(b.template?.title || '')
  })

  return (
    <>
      {pending.length > 0 && (
        <div className="card border-amber-200 bg-amber-50">
          <div className="flex items-center gap-2 mb-2">
            <AlertCircle size={16} className="text-amber-700" />
            <h2 className="text-sm font-semibold text-amber-900">
              {pending.length} pending training acknowledgment{pending.length === 1 ? '' : 's'}
            </h2>
          </div>
          <p className="text-xs text-amber-800 mb-3">
            A trainer signed off that they trained you on the task(s) below. Confirm — or
            dispute if you weren't actually trained.
          </p>
          <div className="space-y-2">
            {pending.map(p => <PendingAckRow key={p.id} cert={p} qc={qc} />)}
          </div>
        </div>
      )}

      <div className="card">
        <h2 className="text-sm font-semibold text-gray-700 mb-2 flex items-center gap-2">
          <GraduationCap size={14} className="text-plum-600" /> My certifications ({sortedCerts.length})
        </h2>
        {sortedCerts.length === 0 ? (
          <div className="text-xs text-gray-500 italic">No certifications yet.</div>
        ) : (
          <ul className="divide-y divide-gray-100">
            {sortedCerts.map(c => <CertRow key={c.id} cert={c} />)}
          </ul>
        )}

        {trainerFor.length > 0 && (
          <div className="mt-4 pt-3 border-t border-border-subtle">
            <div className="flex items-center gap-1.5 text-xs text-blue-700 mb-1.5">
              <ShieldCheck size={12} /> You are an authorized trainer for:
            </div>
            <ul className="text-xs text-gray-700 space-y-0.5 pl-4">
              {trainerFor.map(a => (
                <li key={a.id}>
                  <strong>{a.template.title}</strong>
                  {a.template.training_material_url && (
                    <a href={a.template.training_material_url} target="_blank" rel="noopener noreferrer"
                       className="ml-2 text-plum-700 hover:underline">
                      <ExternalLink size={10} className="inline" /> material
                    </a>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </>
  )
}


function PendingAckRow({ cert, qc }) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)

  async function ack(confirm) {
    setBusy(true)
    try {
      await api.patch(`/training/certifications/${cert.id}/acknowledge`, {
        confirm,
        dispute_reason: confirm ? null : (reason || null),
      })
      qc.invalidateQueries({ queryKey: ['my-training'] })
    } finally { setBusy(false) }
  }

  return (
    <div className="bg-white border border-amber-200 rounded p-2.5">
      <div className="flex items-baseline gap-2 mb-1">
        <strong className="text-sm">{cert.template?.title}</strong>
        <span className="text-[10px] text-muted">signed by {cert.trainer_email} on {fmt.date(cert.trainer_signed_at)}</span>
      </div>
      {cert.template?.training_material_url && (
        <a href={cert.template.training_material_url} target="_blank" rel="noopener noreferrer"
           className="text-xs text-plum-700 hover:underline flex items-center gap-1 mb-2">
          <ExternalLink size={10} /> Open training material
        </a>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn-primary text-xs flex items-center gap-1"
                onClick={() => ack(true)} disabled={busy}>
          <Check size={11} /> Yes, I was trained
        </button>
        <input className="input text-xs flex-1 min-w-[180px]"
               placeholder="If disputing, briefly say why"
               value={reason}
               onChange={e => setReason(e.target.value)} />
        <button className="text-xs px-3 py-1.5 rounded border border-red-300 bg-white text-red-700 hover:bg-red-50 flex items-center gap-1"
                onClick={() => ack(false)} disabled={busy}>
          <AlertCircle size={11} /> Dispute
        </button>
      </div>
    </div>
  )
}


function CertRow({ cert }) {
  const tone = (() => {
    if (cert.status === 'revoked') return 'text-red-600'
    if (cert.status === 'disputed') return 'text-red-600'
    if (cert.expired) return 'text-gray-500'
    if (cert.expires_on) {
      const days = (new Date(cert.expires_on) - new Date()) / 86400000
      if (days <= 30) return 'text-amber-700'
    }
    return 'text-green-700'
  })()
  const label = cert.status === 'active'
    ? (cert.expired ? 'expired' : (cert.expires_on ? `expires ${cert.expires_on}` : 'no expiration'))
    : cert.status

  return (
    <li className="py-2 flex items-baseline gap-3">
      <CheckCircle2 size={12} className={`shrink-0 translate-y-[2px] ${tone}`} />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-gray-800">
          {cert.template?.title}
          {cert.template?.training_material_url && (
            <a href={cert.template.training_material_url} target="_blank" rel="noopener noreferrer"
               className="ml-2 text-[10px] text-plum-700 hover:underline">
              <ExternalLink size={9} className="inline" /> material
            </a>
          )}
        </div>
        <div className="text-[10px] text-gray-500">
          trained by {cert.trainer_email}
          {cert.trainee_signed_at && <> · acknowledged {fmt.date(cert.trainee_signed_at)}</>}
        </div>
      </div>
      <div className={`text-[11px] font-medium shrink-0 ${tone} flex items-center gap-1`}>
        {cert.expires_on && !cert.expired && <Clock size={10} />}
        {label}
      </div>
    </li>
  )
}
