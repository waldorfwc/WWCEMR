import { useState, useMemo, Fragment } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  ArrowLeft, ExternalLink, Check, X, AlertCircle, GraduationCap,
  ShieldCheck, ShieldX, Clock, Search, CheckSquare,
} from 'lucide-react'
import api from '../utils/api'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { MODULE, TIER } from '../routes.jsx'
import { useConfirm } from '../components/ui/ConfirmDialog'


const CATEGORY_BADGE = {
  clinical:      'bg-emerald-50 text-emerald-700 border-emerald-200',
  admin:         'bg-blue-50 text-blue-700 border-blue-200',
  billing:       'bg-purple-50 text-purple-700 border-purple-200',
  safety:        'bg-amber-50 text-amber-700 border-amber-200',
  compliance:    'bg-rose-50 text-rose-700 border-rose-200',
  communication: 'bg-indigo-50 text-indigo-700 border-indigo-200',
}


// "Sarah K." — uses display_name when present, else infers from email local part.
function shortName(u) {
  const raw = (u.display_name || u.email.split('@')[0] || '').trim()
  if (!raw) return '?'
  // Detect "first.last" or "first_last" in an email local part
  if (raw.includes('.') || raw.includes('_')) {
    const [first, ...rest] = raw.split(/[._]/)
    const last = rest[rest.length - 1] || ''
    if (last) return `${cap(first)} ${last[0].toUpperCase()}.`
    return cap(first).slice(0, 12)
  }
  const parts = raw.split(/\s+/)
  if (parts.length === 1) return cap(parts[0]).slice(0, 12)
  return `${cap(parts[0])} ${parts[parts.length - 1][0].toUpperCase()}.`
}
function cap(s) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''
}


export default function AdminTraining({ embedded = false }) {
  const qc = useQueryClient()
  // 'training:authorize' wasn't in the legacy table, so it effectively
  // resolved to super-admin only. Training MANAGE per the backend
  // catalog covers "mark complete on behalf of others", which is what
  // "authorize" means here; super-admin still passes via tier()'s
  // short-circuit.
  const { user, tier } = useCurrentUser()
  const canAuthorize = tier(MODULE.TRAINING, TIER.MANAGE)

  const [search, setSearch] = useState('')
  const [activeCell, setActiveCell] = useState(null)

  const { data, isLoading } = useQuery({
    queryKey: ['training-matrix'],
    queryFn: () => api.get('/training/matrix').then(r => r.data),
  })

  const templates = data?.templates || []
  const users = data?.users || []
  const cells = data?.cells || []

  const cellMap = useMemo(() => {
    const m = new Map()
    for (const c of cells) m.set(`${c.user_email}::${c.template_id}`, c)
    return m
  }, [cells])

  // Per-user coverage (active certs / requires-training templates)
  const userCoverage = useMemo(() => {
    const out = {}
    for (const u of users) {
      let active = 0
      for (const t of templates) {
        const c = cellMap.get(`${u.email}::${t.id}`)
        if (c?.cert?.is_active) active++
      }
      out[u.email] = { active, total: templates.length }
    }
    return out
  }, [users, templates, cellMap])

  // Per-template coverage
  const templateCoverage = useMemo(() => {
    const out = {}
    for (const t of templates) {
      let active = 0
      for (const u of users) {
        const c = cellMap.get(`${u.email}::${t.id}`)
        if (c?.cert?.is_active) active++
      }
      out[t.id] = { active, total: users.length }
    }
    return out
  }, [users, templates, cellMap])

  // Filter tasks by search; group by category preserving the backend's order
  const filteredTemplates = useMemo(() => {
    const lower = search.toLowerCase().trim()
    if (!lower) return templates
    return templates.filter(t =>
      t.title.toLowerCase().includes(lower)
      || t.category.toLowerCase().includes(lower))
  }, [templates, search])

  const grouped = useMemo(() => {
    const out = []
    let cur = null
    for (const t of filteredTemplates) {
      if (!cur || cur.category !== t.category) {
        cur = { category: t.category, templates: [] }
        out.push(cur)
      }
      cur.templates.push(t)
    }
    return out
  }, [filteredTemplates])

  if (isLoading) return <div className="p-6 text-gray-400">Loading…</div>

  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        <div>
          {!embedded && (
            <Link to="/admin" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-1">
              <ArrowLeft size={12} /> Back to Admin
            </Link>
          )}
          <h1 className="font-serif font-semibold text-ink text-[22px] m-0">Training Matrix</h1>
          <p className="text-muted text-[12px] mt-0.5">
            Each row is a task; each column is a user. Click any cell to authorize a
            trainer or certify a trainee. Tasks only generate for users with active
            certifications.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link to="/training/cards"
                 className="btn-primary text-sm flex items-center gap-1"
                 title="Per-task card view — better for authorizing trainers and bulk-certifying groups">
            <GraduationCap size={14}/> Card view
          </Link>
          <a href="/admin/templates" target="_blank" rel="noreferrer"
              className="btn-secondary text-sm flex items-center gap-1"
              title="Open Checklist Templates in a new tab">
            <CheckSquare size={14} /> Checklist Templates
            <ExternalLink size={11} className="text-muted" />
          </a>
          <div className="relative">
            <Search size={12} className="absolute left-2 top-2.5 text-muted" />
            <input
              className="input text-sm pl-7 w-64"
              placeholder="Filter tasks…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
          </div>
        </div>
      </div>

      {/* Legend */}
      <div className="card flex flex-wrap items-center gap-4 text-[11px] mb-3">
        <span className="flex items-center gap-1.5">
          <Cell status={{ kind: 'active' }} compact /> Active cert
        </span>
        <span className="flex items-center gap-1.5">
          <Cell status={{ kind: 'expiring' }} compact /> Expiring soon (≤30 days)
        </span>
        <span className="flex items-center gap-1.5">
          <Cell status={{ kind: 'pending' }} compact /> Trainer signed, awaiting trainee
        </span>
        <span className="flex items-center gap-1.5">
          <Cell status={{ kind: 'disputed' }} compact /> Disputed
        </span>
        <span className="flex items-center gap-1.5">
          <Cell status={{ kind: 'expired' }} compact /> Expired / revoked
        </span>
        <span className="flex items-center gap-1.5">
          <Cell status={{ kind: 'none' }} compact /> No cert
        </span>
        <span className="flex items-center gap-1.5">
          <ShieldCheck size={11} className="text-blue-600" /> Trainer
        </span>
      </div>

      {templates.length === 0 ? (
        <div className="card text-sm text-gray-500 italic">
          No active templates require training. Open a template, enable "Require training",
          and trainers/trainees will need to certify before tasks generate.
        </div>
      ) : (
        <div className="card p-0 overflow-auto max-h-[78vh]">
          <table className="border-collapse text-[11px]">
            <thead>
              <tr>
                <th
                  className="table-th text-left bg-plum-50 sticky top-0 left-0 z-30 border-b border-r border-plum-100"
                  style={{ minWidth: 280, maxWidth: 280 }}
                >
                  Task
                </th>
                {users.map(u => {
                  const cov = userCoverage[u.email]
                  const display = shortName(u)
                  return (
                    <th key={u.email}
                        className="table-th text-center bg-plum-50 sticky top-0 z-20 border-b border-plum-100 align-middle"
                        style={{ minWidth: 70, maxWidth: 70 }}>
                      <div className="flex flex-col items-center px-1 py-1.5">
                        <div className="text-[11px] font-medium leading-tight"
                             title={`${u.display_name || ''}\n${u.email}`}>
                          {display}
                        </div>
                        <div className="text-[11px] text-muted mt-0.5">
                          {cov.active}/{cov.total}
                        </div>
                      </div>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {grouped.map(group => (
                <Fragment key={group.category}>
                  {/* Category subheader row */}
                  <tr>
                    <td
                      colSpan={users.length + 1}
                      className={`px-3 py-1 text-[11px] uppercase tracking-wider font-semibold border-y ${CATEGORY_BADGE[group.category] || 'bg-gray-50 text-gray-600 border-gray-200'} sticky left-0`}
                    >
                      {group.category} · {group.templates.length} task{group.templates.length === 1 ? '' : 's'}
                    </td>
                  </tr>
                  {group.templates.map(t => {
                    const cov = templateCoverage[t.id]
                    return (
                      <tr key={t.id} className="hover:bg-plum-50/30">
                        <td className="table-td sticky left-0 bg-white z-10 border-r border-border-subtle"
                            style={{ minWidth: 280, maxWidth: 280 }}>
                          <div className="text-[12px] font-medium leading-tight">{t.title}</div>
                          <div className="text-[10px] text-muted mt-0.5 flex items-center gap-2 flex-wrap">
                            <span>{cov.active}/{cov.total} certified</span>
                            <a href={`/admin/templates?edit=${t.id}`}
                                target="_blank" rel="noopener noreferrer"
                                className="text-plum-700 hover:underline flex items-center gap-0.5"
                                onClick={e => e.stopPropagation()}
                                title="Edit this task's template (opens in a new tab)">
                              <ExternalLink size={9} /> edit task
                            </a>
                            {t.training_material_url && (
                              <a href={t.training_material_url}
                                 target="_blank" rel="noopener noreferrer"
                                 className="text-plum-700 hover:underline flex items-center gap-0.5"
                                 onClick={e => e.stopPropagation()}>
                                <ExternalLink size={9} /> material
                              </a>
                            )}
                          </div>
                        </td>
                        {users.map(u => {
                          const c = cellMap.get(`${u.email}::${t.id}`)
                          return (
                            <td key={u.email}
                                className="table-td p-1 text-center cursor-pointer"
                                onClick={() => setActiveCell({ user: u, template: t, cell: c })}>
                              <CellSummary cell={c} />
                            </td>
                          )
                        })}
                      </tr>
                    )
                  })}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {activeCell && (
        <CellEditor
          user={activeCell.user}
          template={activeCell.template}
          cell={activeCell.cell}
          canAuthorize={canAuthorize}
          currentUser={user}
          onClose={() => setActiveCell(null)}
          onChange={() => {
            qc.invalidateQueries({ queryKey: ['training-matrix'] })
            setActiveCell(null)
          }}
        />
      )}
    </div>
  )
}


function classifyCell(cell) {
  if (!cell) return { kind: 'none' }
  if (!cell.cert) {
    return cell.is_trainer ? { kind: 'trainer-only' } : { kind: 'none' }
  }
  const c = cell.cert
  if (c.expired || c.status === 'revoked') return { kind: 'expired' }
  if (c.status === 'disputed') return { kind: 'disputed' }
  if (c.status === 'pending_trainee') return { kind: 'pending' }
  if (c.status === 'active') {
    if (c.expires_on) {
      const days = (new Date(c.expires_on) - new Date()) / 86400000
      if (days <= 30) return { kind: 'expiring', days: Math.round(days) }
    }
    return { kind: 'active' }
  }
  return { kind: 'none' }
}


function CellSummary({ cell }) {
  const status = classifyCell(cell)
  return (
    <div className="flex items-center justify-center gap-1">
      <Cell status={status} />
      {cell?.is_trainer && (
        <ShieldCheck size={10} className="text-blue-600" title="Authorized trainer" />
      )}
    </div>
  )
}


function Cell({ status, compact = false }) {
  const tone = {
    active:       'bg-green-500 text-white',
    expiring:     'bg-amber-300 text-amber-900',
    pending:      'bg-blue-200 text-blue-700',
    disputed:     'bg-red-300 text-red-900',
    expired:      'bg-gray-400 text-white',
    'trainer-only': 'bg-white border border-blue-300',
    none:         'bg-gray-100 border border-gray-200',
  }[status.kind]
  const ico = {
    active:   <Check size={11} />,
    expiring: <Clock size={9} />,
    pending:  <span className="text-[8px]">↗</span>,
    disputed: <AlertCircle size={9} />,
    expired:  <X size={11} />,
    'trainer-only': <ShieldCheck size={10} className="text-blue-600" />,
    none:     null,
  }[status.kind]
  return (
    <div className={`${compact ? 'w-4 h-4' : 'w-6 h-6'} rounded ${tone} flex items-center justify-center font-semibold`}
         title={status.kind === 'expiring' ? `Expires in ${status.days} days` : status.kind}>
      {ico}
    </div>
  )
}


function CellEditor({ user, template, cell, canAuthorize, currentUser, onClose, onChange }) {
  const me = currentUser?.email || ''
  const isMe = user.email === me
  const [notes, setNotes] = useState('')
  const [error, setError] = useState(null)
  const confirm = useConfirm()

  const authorizeAsTrainer = useMutation({
    mutationFn: () => api.post('/training/trainers', {
      user_email: user.email,
      template_id: template.id,
      notes: notes || null,
    }).then(r => r.data),
    onSuccess: onChange,
    onError: (e) => setError(e?.response?.data?.detail || 'authorize failed'),
  })

  const revokeTrainer = useMutation({
    mutationFn: () => api.delete('/training/trainers', {
      data: { user_email: user.email, template_id: template.id, reason: notes || null },
    }).then(r => r.data),
    onSuccess: onChange,
    onError: (e) => setError(e?.response?.data?.detail || 'revoke failed'),
  })

  const certifyTrainee = useMutation({
    mutationFn: () => api.post('/training/certifications', {
      trainee_email: user.email,
      template_id: template.id,
      notes: notes || null,
    }).then(r => r.data),
    onSuccess: onChange,
    onError: (e) => setError(e?.response?.data?.detail || 'certify failed'),
  })

  const revokeCert = useMutation({
    mutationFn: () => api.delete(`/training/certifications/${cell.cert.id}`, {
      data: { reason: notes || null },
    }).then(r => r.data),
    onSuccess: onChange,
    onError: (e) => setError(e?.response?.data?.detail || 'revoke failed'),
  })

  const acknowledge = useMutation({
    mutationFn: (confirm) => api.patch(
      `/training/certifications/${cell.cert.id}/acknowledge`,
      { confirm, dispute_reason: confirm ? null : (notes || null) },
    ).then(r => r.data),
    onSuccess: onChange,
    onError: (e) => setError(e?.response?.data?.detail || 'ack failed'),
  })

  const c = cell?.cert
  const isTrainer = cell?.is_trainer
  const status = classifyCell(cell)

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-4 flex items-center justify-between z-10">
          <div>
            <h2 className="font-serif font-semibold text-ink text-[16px]">{template.title}</h2>
            <div className="text-[11px] text-muted">
              for <strong>{user.display_name || user.email.split('@')[0]}</strong>
              <span className="text-gray-400 ml-1">{user.email}</span>
            </div>
          </div>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        <div className="p-5 space-y-4">
          {template.training_material_url && (
            <a href={template.training_material_url} target="_blank" rel="noopener noreferrer"
               className="text-sm text-plum-700 hover:underline flex items-center gap-1">
              <ExternalLink size={12} /> Open training material
            </a>
          )}

          <div className="border border-border-subtle rounded p-3 space-y-2 text-sm">
            <div className="flex items-center gap-2">
              <Cell status={status} />
              <span className="capitalize">{status.kind === 'none' ? 'Not certified' : status.kind.replace('-', ' ')}</span>
            </div>
            {c && (
              <div className="text-xs text-muted space-y-0.5 pl-1">
                <div>Trainer: <strong>{c.trainer_email}</strong> on {c.trainer_signed_at?.slice(0, 16)}</div>
                {c.trainee_signed_at && <div>Trainee: signed {c.trainee_signed_at.slice(0, 16)}</div>}
                {c.expires_on && <div>Expires: <strong>{c.expires_on}</strong></div>}
                {c.revoked_at && <div className="text-red-600">Revoked: {c.revoked_at.slice(0, 16)} by {c.revoked_by}</div>}
                {c.notes && <div>Notes: <em>{c.notes}</em></div>}
              </div>
            )}
            {isTrainer && (
              <div className="flex items-center gap-1 text-xs text-blue-700">
                <ShieldCheck size={11} /> Authorized trainer for this task
              </div>
            )}
          </div>

          {isMe && c?.status === 'pending_trainee' && (
            <div className="border border-amber-200 bg-amber-50 rounded p-3 space-y-2">
              <div className="text-sm">
                <strong>{c.trainer_email}</strong> certified you were trained on <strong>{template.title}</strong>.
                Confirm this is accurate?
              </div>
              <textarea className="input text-xs" rows={2}
                        placeholder="(if disputing, briefly say why)"
                        value={notes} onChange={e => setNotes(e.target.value)} />
              <div className="flex gap-2">
                <button className="btn-primary text-xs flex items-center gap-1"
                        onClick={() => acknowledge.mutate(true)} disabled={acknowledge.isPending}>
                  <Check size={11} /> Yes, I was trained
                </button>
                <button className="text-xs px-3 py-1.5 rounded border border-red-300 bg-white text-red-700 hover:bg-red-50 flex items-center gap-1"
                        onClick={() => acknowledge.mutate(false)} disabled={acknowledge.isPending}>
                  <AlertCircle size={11} /> Dispute
                </button>
              </div>
            </div>
          )}

          {canAuthorize && !isMe && (
            <div className="border border-border-subtle rounded p-3 space-y-2">
              <div className="text-xs text-muted">Trainer authorization (managers only)</div>
              {!isTrainer ? (
                <button className="btn-secondary text-xs flex items-center gap-1"
                        onClick={() => authorizeAsTrainer.mutate()}
                        disabled={authorizeAsTrainer.isPending}>
                  <ShieldCheck size={11} /> Authorize as trainer
                </button>
              ) : (
                <button className="text-xs px-3 py-1.5 rounded border border-red-300 bg-white text-red-700 hover:bg-red-50 flex items-center gap-1"
                        onClick={async () => {
                          if (await confirm({
                            title: 'Revoke trainer authorization?',
                            message: `${user.display_name || user.email} will no longer be able to certify others for "${template.title}".`,
                            confirmLabel: 'Revoke',
                          })) revokeTrainer.mutate()
                        }}
                        disabled={revokeTrainer.isPending}>
                  <ShieldX size={11} /> Revoke trainer authorization
                </button>
              )}
            </div>
          )}

          {!isMe && (
            <div className="border border-border-subtle rounded p-3 space-y-2">
              <div className="text-xs text-muted">
                Certify {user.display_name || user.email.split('@')[0]} as trained
              </div>
              <textarea className="input text-xs" rows={2}
                        placeholder="Optional notes (training session date, scenarios covered…)"
                        value={notes} onChange={e => setNotes(e.target.value)} />
              <button className="btn-primary text-xs flex items-center gap-1"
                      onClick={() => certifyTrainee.mutate()}
                      disabled={certifyTrainee.isPending}>
                <GraduationCap size={11} /> {c ? 'Re-certify (resets to pending ack)' : 'Mark as trained'}
              </button>
              <p className="text-[10px] text-muted">
                You must be an authorized trainer for this task. After signing, the trainee
                must acknowledge before the cert becomes active.
              </p>
            </div>
          )}

          {canAuthorize && c && c.status !== 'revoked' && !isMe && (
            <div className="border border-red-200 rounded p-3 space-y-2">
              <div className="text-xs text-red-700">Revoke this certification</div>
              <input className="input text-xs" placeholder="Reason (optional)"
                     value={notes} onChange={e => setNotes(e.target.value)} />
              <button className="text-xs px-3 py-1.5 rounded border border-red-300 bg-white text-red-700 hover:bg-red-50 flex items-center gap-1"
                      onClick={async () => {
                        if (await confirm({
                          title: 'Revoke certification?',
                          message: `${user.display_name || user.email}'s certification for "${template.title}" will be revoked.`,
                          confirmLabel: 'Revoke',
                        })) revokeCert.mutate()
                      }}
                      disabled={revokeCert.isPending}>
                <ShieldX size={11} /> Revoke certification
              </button>
            </div>
          )}

          {error && <div className="text-xs text-red-600">{error}</div>}
        </div>
      </div>
    </div>
  )
}
