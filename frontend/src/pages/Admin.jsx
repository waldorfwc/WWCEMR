import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../utils/api'

const GROUPS = [
  { value: 'admin',    label: 'Admin' },
  { value: 'billing',  label: 'Billing' },
  { value: 'clinical', label: 'Clinical' },
]

function Flash({ kind, text }) {
  if (!text) return null
  const cls = kind === 'ok'
    ? 'text-success'
    : 'text-danger'
  return <span className={`ml-2 text-[11px] ${cls}`}>{text}</span>
}

function UserRow({ u, onFlash, flashKind, flashText }) {
  const queryClient = useQueryClient()
  const [nameDraft, setNameDraft] = useState(u.display_name || '')

  const patch = useMutation({
    mutationFn: (body) => api.patch(`/admin/users/${encodeURIComponent(u.email)}`, body).then(r => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      onFlash(u.email, 'ok', '✓ saved')
    },
    onError: (err) => {
      onFlash(u.email, 'err', `✗ ${err?.response?.data?.detail || 'error'}`)
    },
  })

  return (
    <tr className="table-row">
      <td className="table-td font-mono text-[11px]">{u.email}</td>
      <td className="table-td">
        <input
          className="input w-full max-w-[200px] py-1 text-[12px]"
          value={nameDraft}
          onChange={(e) => setNameDraft(e.target.value)}
          onBlur={() => {
            if ((nameDraft || '') !== (u.display_name || '')) {
              patch.mutate({ display_name: nameDraft })
            }
          }}
          placeholder="—"
        />
      </td>
      <td className="table-td">
        <select
          className="input w-[120px] py-1 text-[12px]"
          value={u.group}
          onChange={(e) => patch.mutate({ group: e.target.value })}
        >
          {GROUPS.map(g => <option key={g.value} value={g.value}>{g.label}</option>)}
        </select>
      </td>
      <td className="table-td text-[11px] text-muted">
        {u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}
      </td>
      <td className="table-td">
        <Flash kind={flashKind} text={flashText} />
      </td>
    </tr>
  )
}

function AddUserForm({ onClose, onFlash }) {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [group, setGroup] = useState('billing')
  const [displayName, setDisplayName] = useState('')

  const create = useMutation({
    mutationFn: () => api.post('/admin/users', {
      email: email.trim().toLowerCase(),
      group,
      display_name: displayName || null,
    }).then(r => r.data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      onFlash(data.email, 'ok', '✓ created')
      onClose()
    },
  })

  return (
    <tr className="bg-plum-50">
      <td className="table-td">
        <input
          className="input w-full py-1 text-[12px] font-mono"
          placeholder="email@waldorfwomenscare.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoFocus
        />
      </td>
      <td className="table-td">
        <input
          className="input w-full py-1 text-[12px]"
          placeholder="Display name (optional)"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />
      </td>
      <td className="table-td">
        <select className="input w-[120px] py-1 text-[12px]"
                value={group} onChange={(e) => setGroup(e.target.value)}>
          {GROUPS.map(g => <option key={g.value} value={g.value}>{g.label}</option>)}
        </select>
      </td>
      <td className="table-td" />
      <td className="table-td">
        <div className="flex gap-2 items-center">
          <button className="btn-primary py-1 px-2 text-[11px]"
                  onClick={() => create.mutate()}
                  disabled={!email || create.isPending}>
            {create.isPending ? '...' : 'Create'}
          </button>
          <button className="text-[11px] text-muted underline" onClick={onClose}>Cancel</button>
          {create.isError && (
            <span className="text-[11px] text-danger">
              ✗ {create.error?.response?.data?.detail || 'error'}
            </span>
          )}
        </div>
      </td>
    </tr>
  )
}

export default function Admin() {
  const { data: users, isLoading } = useQuery({
    queryKey: ['admin-users'],
    queryFn: () => api.get('/admin/users').then(r => r.data),
  })

  const [adding, setAdding] = useState(false)
  const [flashes, setFlashes] = useState({})

  function onFlash(email, kind, text) {
    setFlashes(prev => ({ ...prev, [email]: { kind, text } }))
    const timeout = kind === 'err' ? 3000 : 1500
    setTimeout(() => {
      setFlashes(prev => {
        const next = { ...prev }
        delete next[email]
        return next
      })
    }, timeout)
  }

  const counts = (users || []).reduce((acc, u) => {
    acc[u.group] = (acc[u.group] || 0) + 1
    return acc
  }, {})

  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <h1 className="font-serif font-semibold text-ink text-[22px] m-0">User management</h1>
          <div className="text-muted text-[12px] mt-0.5">
            {(users?.length || 0)} users ·{' '}
            {counts.admin || 0} admin ·{' '}
            {counts.billing || 0} billing ·{' '}
            {counts.clinical || 0} clinical
          </div>
        </div>
        {!adding && (
          <button className="btn-primary" onClick={() => setAdding(true)}>
            + Add user
          </button>
        )}
      </div>

      <div className="card p-0 overflow-hidden">
        <table className="w-full">
          <thead className="bg-plum-50">
            <tr>
              <th className="table-th">Email</th>
              <th className="table-th">Display name</th>
              <th className="table-th">Group</th>
              <th className="table-th">Created</th>
              <th className="table-th"></th>
            </tr>
          </thead>
          <tbody>
            {adding && <AddUserForm onClose={() => setAdding(false)} onFlash={onFlash} />}
            {isLoading && (
              <tr><td colSpan={5} className="table-td text-center text-muted py-8">Loading...</td></tr>
            )}
            {!isLoading && users?.map(u => (
              <UserRow key={u.email} u={u}
                       onFlash={onFlash}
                       flashKind={flashes[u.email]?.kind}
                       flashText={flashes[u.email]?.text} />
            ))}
            {!isLoading && users?.length === 0 && (
              <tr><td colSpan={5} className="table-td text-center text-muted py-8">No users yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
