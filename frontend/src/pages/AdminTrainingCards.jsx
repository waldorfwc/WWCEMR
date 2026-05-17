import { useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  ArrowLeft, ExternalLink, Plus, X, Search, ShieldCheck, ShieldX,
  GraduationCap, AlertCircle, CheckSquare, Users, Trash2, Edit3,
} from 'lucide-react'
import api from '../utils/api'


export default function AdminTrainingCards() {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [filterMode, setFilterMode] = useState('all') // 'all' | 'gaps' | 'expiring'

  const { data, isLoading } = useQuery({
    queryKey: ['training-matrix'],
    queryFn: () => api.get('/training/matrix').then(r => r.data),
  })
  const { data: allGroups } = useQuery({
    queryKey: ['admin-groups'],
    queryFn: () => api.get('/admin/groups').then(r => r.data),
  })

  // Build per-template aggregates from the matrix payload
  const cardsRaw = useMemo(() => {
    if (!data) return []
    const cells = data.cells || []
    const userByEmail = Object.fromEntries((data.users || []).map(u => [u.email, u]))
    return (data.templates || []).map(t => {
      const tcells = cells.filter(c => c.template_id === t.id)
      const trainers = tcells.filter(c => c.is_trainer).map(c => c.user_email)
      const certified = []
      const pendingTrainer = []     // trainer signed, waiting on trainee
      const pendingTrainee = []     // (legacy synonym just in case)
      const revoked = []
      const expiring = []           // within 30 days
      for (const c of tcells) {
        if (!c.cert) continue
        const cert = c.cert
        if (cert.status === 'active') {
          certified.push(c.user_email)
          if (cert.expires_on && cert.days_to_expiry != null && cert.days_to_expiry <= 30) {
            expiring.push(c.user_email)
          }
        } else if (cert.status === 'pending_trainee') {
          pendingTrainee.push(c.user_email)
        } else if (cert.status === 'pending_trainer') {
          pendingTrainer.push(c.user_email)
        } else if (cert.status === 'revoked') {
          revoked.push(c.user_email)
        }
      }
      // All users known to the matrix (used for gap drill-in)
      const allUserEmails = (data.users || []).map(u => u.email)
      // Missing = known users not in any cert state above
      const involved = new Set([...certified, ...pendingTrainee, ...pendingTrainer])
      const missing = allUserEmails.filter(e => !involved.has(e))
      return {
        ...t,
        trainers,
        certified,
        pending_trainee: pendingTrainee,
        pending_trainer: pendingTrainer,
        revoked,
        expiring,
        missing,
        cells: tcells,
        userByEmail,
      }
    })
  }, [data])

  const cards = useMemo(() => {
    let list = cardsRaw
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      list = list.filter(c => (c.title || '').toLowerCase().includes(q))
    }
    if (filterMode === 'gaps') {
      list = list.filter(c => c.missing.length > 0)
    } else if (filterMode === 'expiring') {
      list = list.filter(c => c.expiring.length > 0)
    }
    return list
  }, [cardsRaw, search, filterMode])

  // Coverage summary across ALL cards (not filtered)
  const summary = useMemo(() => {
    if (!cardsRaw.length) return null
    const fullyCovered = cardsRaw.filter(c => c.missing.length === 0).length
    const withGaps = cardsRaw.filter(c => c.missing.length > 0).length
    const withExpiring = cardsRaw.filter(c => c.expiring.length > 0).length
    return {
      total: cardsRaw.length,
      fullyCovered, withGaps, withExpiring,
    }
  }, [cardsRaw])

  if (isLoading) return <div className="p-6 text-gray-400">Loading…</div>

  return (
    <div>
      <div className="flex items-baseline justify-between mb-3 flex-wrap gap-2">
        <div>
          <Link to="/admin" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-1">
            <ArrowLeft size={12} /> Back to Admin
          </Link>
          <h1 className="font-serif font-semibold text-ink text-[22px] m-0">Training — Per-task view</h1>
          <p className="text-muted text-[12px] mt-0.5">
            One card per training-gated task. Authorize trainers, certify employees one-at-a-time
            or by group, and see exactly who's missing coverage.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <a href="/admin/templates" target="_blank" rel="noreferrer"
              className="btn-secondary text-sm flex items-center gap-1"
              title="Open Checklist Templates in a new tab">
            <CheckSquare size={14} /> Checklist Templates
            <ExternalLink size={11} className="text-muted" />
          </a>
          <Link to="/admin/training" className="btn-secondary text-sm flex items-center gap-1">
            <GraduationCap size={14}/> Matrix view
          </Link>
        </div>
      </div>

      {/* Coverage banner */}
      {summary && (
        <div className="card mb-3 flex items-center gap-4 flex-wrap py-2 px-3">
          <CoverageStat label="Tasks total"     value={summary.total} onClick={() => setFilterMode('all')} active={filterMode === 'all'} />
          <CoverageStat label="Fully covered"   value={summary.fullyCovered}   accent="text-success" />
          <CoverageStat label="Has gaps"        value={summary.withGaps}       accent="text-warning"
                          onClick={() => setFilterMode('gaps')} active={filterMode === 'gaps'} />
          <CoverageStat label="Expiring ≤30d"   value={summary.withExpiring}   accent="text-amber-700"
                          onClick={() => setFilterMode('expiring')} active={filterMode === 'expiring'} />
          {filterMode !== 'all' && (
            <button className="text-[11px] text-muted hover:text-plum-700 ml-auto"
                     onClick={() => setFilterMode('all')}>
              Clear filter
            </button>
          )}
        </div>
      )}

      {/* Search */}
      <div className="relative mb-3 max-w-md">
        <Search size={12} className="absolute left-2 top-2.5 text-muted" />
        <input
          className="input text-sm pl-7 w-full"
          placeholder="Filter tasks…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>

      {/* Cards */}
      <div className="space-y-3">
        {cards.length === 0 && (
          <div className="card text-center py-8 text-muted text-[13px]">
            No matching tasks. {filterMode !== 'all' && 'Try clearing the coverage filter or search.'}
          </div>
        )}
        {cards.map(c => <TaskCard key={c.id} task={c}
                                       allGroups={allGroups || []}
                                       qc={qc} />)}
      </div>
    </div>
  )
}


function CoverageStat({ label, value, accent, onClick, active }) {
  const inner = (
    <div className={`px-2 ${onClick ? 'cursor-pointer hover:bg-plum-50 rounded' : ''} ${active ? 'bg-plum-50 rounded' : ''}`}
         onClick={onClick}>
      <div className="text-[9px] uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`text-[18px] font-semibold leading-tight ${accent || 'text-ink'}`}>{value}</div>
    </div>
  )
  return inner
}


function TaskCard({ task, allGroups, qc }) {
  const t = task
  const [pickedUser, setPickedUser] = useState('')
  const [pickedGroup, setPickedGroup] = useState('')
  const [adding, setAdding] = useState('user')  // 'user' | 'group'
  const [showMissing, setShowMissing] = useState(false)
  const allUsers = Object.values(t.userByEmail)

  function refresh() {
    qc.invalidateQueries({ queryKey: ['training-matrix'] })
  }

  // mutations
  const authorizeTrainer = useMutation({
    mutationFn: (email) => api.post('/training/trainers',
      { user_email: email, template_id: t.id }).then(r => r.data),
    onSuccess: refresh,
    onError: (e) => alert(e?.response?.data?.detail || 'Failed'),
  })
  const revokeTrainer = useMutation({
    mutationFn: (email) => api.delete('/training/trainers',
      { data: { user_email: email, template_id: t.id } }).then(r => r.data),
    onSuccess: refresh,
    onError: (e) => alert(e?.response?.data?.detail || 'Failed'),
  })
  const certifyOne = useMutation({
    mutationFn: (email) => api.post('/training/certifications',
      { trainee_email: email, template_id: t.id }).then(r => r.data),
    onSuccess: refresh,
    onError: (e) => alert(e?.response?.data?.detail || 'Failed'),
  })
  const revokeCert = useMutation({
    mutationFn: (cert_id) => api.delete(`/training/certifications/${cert_id}`,
      { data: { reason: 'manual revoke' } }).then(r => r.data),
    onSuccess: refresh,
    onError: (e) => alert(e?.response?.data?.detail || 'Failed'),
  })
  const certifyGroup = useMutation({
    mutationFn: (group_id) => api.post('/training/certify-group',
      { template_id: t.id, group_id }).then(r => r.data),
    onSuccess: (data) => {
      const msg = `Issued ${data.issued.length} certifications to ${data.group_name}` +
        (data.skipped.length ? `\n\nSkipped:\n${data.skipped.map(s => `• ${s.email} — ${s.reason}`).join('\n')}` : '')
      alert(msg)
      refresh()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Failed'),
  })
  const revokeGroup = useMutation({
    mutationFn: (group_id) => api.post('/training/revoke-group',
      { template_id: t.id, group_id, reason: 'Bulk revoke' }).then(r => r.data),
    onSuccess: (data) => {
      alert(`Revoked ${data.revoked.length} certifications in ${data.group_name}`)
      refresh()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Failed'),
  })

  function certIdFor(email) {
    const c = (t.cells || []).find(c => c.user_email === email && c.cert)
    return c?.cert?.id
  }

  return (
    <div className="card">
      {/* Title row */}
      <div className="flex items-baseline justify-between gap-3 mb-2 flex-wrap">
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <h3 className="text-[15px] font-semibold text-ink truncate">{t.title}</h3>
            {t.category && (
              <span className="text-[10px] uppercase text-plum-700 bg-plum-50 px-1.5 py-0.5 rounded">
                {t.category}
              </span>
            )}
            {t.expiring.length > 0 && (
              <span className="text-[10px] text-amber-800 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded">
                {t.expiring.length} cert{t.expiring.length === 1 ? '' : 's'} expiring ≤30d
              </span>
            )}
            <a href={`/admin/templates?edit=${t.id}`} target="_blank" rel="noreferrer"
                className="text-[10px] text-plum-700 hover:underline inline-flex items-center gap-0.5"
                title="Edit this task's template (opens in a new tab)">
              <Edit3 size={10}/> Edit task <ExternalLink size={9}/>
            </a>
          </div>
          {t.training_material_url && (
            <a href={t.training_material_url} target="_blank" rel="noreferrer"
                className="text-[11px] text-plum-700 hover:underline inline-flex items-center gap-0.5 mt-0.5">
              {t.training_material_url} <ExternalLink size={10}/>
            </a>
          )}
        </div>
        <div className="text-right text-[11px]">
          <div className="text-success">{t.certified.length} certified</div>
          {t.missing.length > 0 && (
            <button className="text-warning hover:underline"
                    onClick={() => setShowMissing(s => !s)}>
              {t.missing.length} missing {showMissing ? '▾' : '▸'}
            </button>
          )}
        </div>
      </div>

      {/* Missing drill-in */}
      {showMissing && t.missing.length > 0 && (
        <div className="border border-amber-200 bg-amber-50/40 rounded p-2 mb-2">
          <div className="text-[11px] text-amber-800 mb-1">
            Not certified ({t.missing.length}):
          </div>
          <div className="flex flex-wrap gap-1">
            {t.missing.map(email => (
              <button key={email}
                       className="inline-flex items-center gap-0.5 bg-white border border-amber-200 rounded px-1.5 py-0.5 text-[11px] hover:bg-amber-100"
                       title={`Issue cert to ${email}`}
                       onClick={() => certifyOne.mutate(email)}
                       disabled={certifyOne.isPending}>
                {email} <Plus size={10}/>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Trainers */}
      <ChipsSection
        icon={<ShieldCheck size={12} className="text-plum-700"/>}
        title="Trainers"
        emails={t.trainers}
        onRemove={(e) => {
          if (confirm(`Revoke trainer authorization for ${e}?`)) revokeTrainer.mutate(e)
        }}
      />

      {/* Certified */}
      <ChipsSection
        icon={<CheckSquare size={12} className="text-success"/>}
        title="Certified"
        emails={t.certified}
        emphasizeEmails={t.expiring}
        onRemove={(e) => {
          if (confirm(`Revoke certification for ${e}?`)) {
            const id = certIdFor(e)
            if (id) revokeCert.mutate(id)
          }
        }}
      />

      {/* Pending */}
      {(t.pending_trainee.length > 0 || t.pending_trainer.length > 0) && (
        <ChipsSection
          icon={<AlertCircle size={12} className="text-amber-700"/>}
          title="Pending"
          emails={[
            ...t.pending_trainee.map(e => `${e} — awaiting trainee confirm`),
            ...t.pending_trainer.map(e => `${e} — awaiting trainer signoff`),
          ]}
          dimmed
        />
      )}

      {/* Add picker */}
      <div className="border-t border-gray-100 pt-2 mt-2">
        <div className="text-[11px] text-muted mb-1.5 flex items-center gap-2">
          <Plus size={11}/> Add to this task:
          <label className="flex items-center gap-1 ml-2">
            <input type="radio" checked={adding === 'user'}
                    onChange={() => setAdding('user')} />
            one employee
          </label>
          <label className="flex items-center gap-1">
            <input type="radio" checked={adding === 'group'}
                    onChange={() => setAdding('group')} />
            whole group
          </label>
        </div>

        {adding === 'user' ? (
          <div className="flex items-center gap-2">
            <select className="input text-[12px] flex-1"
                     value={pickedUser}
                     onChange={e => setPickedUser(e.target.value)}>
              <option value="">— pick a user —</option>
              {allUsers
                .filter(u => !t.certified.includes(u.email) && !t.pending_trainee.includes(u.email))
                .sort((a, b) => (a.display_name || a.email).localeCompare(b.display_name || b.email))
                .map(u => (
                  <option key={u.email} value={u.email}>
                    {u.display_name ? `${u.display_name} (${u.email})` : u.email}
                  </option>
                ))}
            </select>
            <button className="btn-secondary text-[11px]"
                    onClick={() => pickedUser && authorizeTrainer.mutate(pickedUser)}
                    disabled={!pickedUser || authorizeTrainer.isPending}
                    title="Authorize as trainer">
              + Trainer
            </button>
            <button className="btn-primary text-[11px]"
                    onClick={() => {
                      if (!pickedUser) return
                      certifyOne.mutate(pickedUser)
                      setPickedUser('')
                    }}
                    disabled={!pickedUser || certifyOne.isPending}
                    title="Mark as certified (admin override)">
              + Certify
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2 flex-wrap">
            <select className="input text-[12px] flex-1 min-w-[180px]"
                     value={pickedGroup}
                     onChange={e => setPickedGroup(e.target.value)}>
              <option value="">— pick a group —</option>
              {allGroups.map(g => (
                <option key={g.id} value={g.id}>
                  {g.name} ({g.member_count} member{g.member_count === 1 ? '' : 's'})
                </option>
              ))}
            </select>
            <button className="btn-primary text-[11px]"
                    onClick={() => {
                      if (!pickedGroup) return
                      const g = allGroups.find(g => g.id === pickedGroup)
                      if (g && confirm(`Issue certifications to all ${g.member_count} members of "${g.name}" on "${t.title}"?\n\nAlready-certified members are skipped.`)) {
                        certifyGroup.mutate(pickedGroup)
                      }
                    }}
                    disabled={!pickedGroup || certifyGroup.isPending}>
              + Certify whole group
            </button>
            <button className="text-[11px] text-danger hover:underline"
                    onClick={() => {
                      if (!pickedGroup) return
                      const g = allGroups.find(g => g.id === pickedGroup)
                      if (g && confirm(`Revoke certifications for ALL members of "${g.name}" on "${t.title}"?\n\nUsed when an SOP changes and everyone needs to re-train.`)) {
                        revokeGroup.mutate(pickedGroup)
                      }
                    }}
                    disabled={!pickedGroup || revokeGroup.isPending}>
              <Trash2 size={11} className="inline"/> Revoke group
            </button>
          </div>
        )}
      </div>
    </div>
  )
}


function ChipsSection({ icon, title, emails, emphasizeEmails = [], dimmed, onRemove }) {
  if (!emails || emails.length === 0) {
    return (
      <div className="text-[11px] text-muted mt-2 flex items-center gap-1">
        {icon} <span>{title}: none</span>
      </div>
    )
  }
  const emphasize = new Set(emphasizeEmails)
  return (
    <div className="mt-2">
      <div className="text-[11px] text-muted mb-1 flex items-center gap-1">
        {icon} <span>{title} ({emails.length})</span>
      </div>
      <div className="flex flex-wrap gap-1">
        {emails.map(e => {
          const baseEmail = e.split(' — ')[0]
          const isEmph = emphasize.has(baseEmail)
          return (
            <span key={e}
                   className={`inline-flex items-center gap-0.5 border rounded px-1.5 py-0.5 text-[11px] ${
                     dimmed ? 'bg-amber-50 border-amber-100 text-amber-800' :
                     isEmph ? 'bg-amber-50 border-amber-200' :
                              'bg-plum-50 border-plum-100'
                   }`}>
              {e}
              {onRemove && (
                <button className="text-muted hover:text-danger"
                         title={`Remove ${baseEmail}`}
                         onClick={() => onRemove(baseEmail)}>
                  <X size={10}/>
                </button>
              )}
            </span>
          )
        })}
      </div>
    </div>
  )
}
