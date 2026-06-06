import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ArrowLeft, Plus, Trash2, X, Shield, Users } from 'lucide-react'
import api from '../utils/api'


export default function AdminGroups() {
  const qc = useQueryClient()
  const [editingId, setEditingId] = useState(null)
  const [adding, setAdding] = useState(false)

  const { data: groups, isLoading } = useQuery({
    queryKey: ['admin-groups'],
    queryFn: () => api.get('/admin/groups').then(r => r.data),
  })

  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <Link to="/admin" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-1">
            <ArrowLeft size={12} /> Back to Admin
          </Link>
          <h1 className="font-serif font-semibold text-ink text-[22px] m-0">Groups & Permissions</h1>
          <p className="text-muted text-[12px] mt-0.5">
            Groups bundle permissions. Users get the union of all groups they belong to,
            plus per-user extras and minus per-user revokes.
          </p>
        </div>
        {!adding && (
          <button className="btn-primary text-sm flex items-center gap-1" onClick={() => setAdding(true)}>
            <Plus size={14} /> New Group
          </button>
        )}
      </div>

      {adding && <CreateGroupCard onClose={() => setAdding(false)} />}

      <div className="card p-0 overflow-hidden">
        <table className="w-full">
          <thead className="bg-plum-50">
            <tr>
              <th className="table-th">Name</th>
              <th className="table-th">Description</th>
              <th className="table-th text-right">Members</th>
              <th className="table-th text-right">Permissions</th>
              <th className="table-th"></th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={5} className="table-td text-center text-muted py-8">Loading…</td></tr>
            )}
            {!isLoading && groups?.map(g => (
              <tr key={g.id} className="table-row hover:bg-plum-50/40 cursor-pointer"
                  onClick={() => setEditingId(g.id)}>
                <td className="table-td">
                  <div className="flex items-center gap-2">
                    <Shield size={13} className={g.system_protected ? 'text-plum-700' : 'text-muted'} />
                    <span className="font-medium">{g.name}</span>
                    {g.system_protected && (
                      <span className="text-[9px] uppercase tracking-wide text-plum-600 bg-plum-100 px-1.5 py-0.5 rounded">
                        protected
                      </span>
                    )}
                  </div>
                </td>
                <td className="table-td text-[12px] text-muted">{g.description || '—'}</td>
                <td className="table-td text-right text-[12px] font-mono">{g.member_count}</td>
                <td className="table-td text-right text-[12px] font-mono">{g.permission_count}</td>
                <td className="table-td">
                  <Link
                    to={`/admin/groups/${g.id}/tiers`}
                    onClick={(e) => e.stopPropagation()}
                    className="text-[11px] text-plum-700 hover:underline whitespace-nowrap"
                    aria-label={`Edit tiers for ${g.name}`}
                  >
                    Tiers →
                  </Link>
                </td>
              </tr>
            ))}
            {!isLoading && groups?.length === 0 && (
              <tr><td colSpan={5} className="table-td text-center text-muted py-8">No groups yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {editingId && <EditGroupDrawer groupId={editingId} onClose={() => setEditingId(null)} />}
    </div>
  )
}


function CreateGroupCard({ onClose }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')

  const create = useMutation({
    mutationFn: () => api.post('/admin/groups', { name, description, permissions: [] })
                        .then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-groups'] })
      onClose()
    },
  })

  return (
    <div className="card mb-4 bg-plum-50/40">
      <div className="flex items-center justify-between mb-2">
        <div className="text-sm font-semibold text-ink">Create New Group</div>
        <button className="text-muted hover:text-ink" onClick={onClose}><X size={14} /></button>
      </div>
      <div className="space-y-2">
        <input
          className="input w-full text-sm"
          placeholder="Group name (e.g. Surgery Scheduler)"
          value={name}
          onChange={e => setName(e.target.value)}
          autoFocus
        />
        <input
          className="input w-full text-sm"
          placeholder="Short description (optional)"
          value={description}
          onChange={e => setDescription(e.target.value)}
        />
      </div>
      {create.isError && (
        <div className="text-danger text-[11px] mt-2">
          {create.error?.response?.data?.detail || 'error'}
        </div>
      )}
      <div className="flex gap-2 justify-end mt-3">
        <button className="btn-secondary text-sm" onClick={onClose}>Cancel</button>
        <button
          className="btn-primary text-sm"
          onClick={() => create.mutate()}
          disabled={!name.trim() || create.isPending}
        >
          {create.isPending ? 'Creating…' : 'Create'}
        </button>
      </div>
      <div className="text-[11px] text-muted mt-2">
        After creating, click the row to assign permissions.
      </div>
    </div>
  )
}


function EditGroupDrawer({ groupId, onClose }) {
  const qc = useQueryClient()
  const { data: group, isLoading } = useQuery({
    queryKey: ['admin-group', groupId],
    queryFn: () => api.get(`/admin/groups/${groupId}`).then(r => r.data),
  })
  const { data: catalog } = useQuery({
    queryKey: ['perm-catalog'],
    queryFn: () => api.get('/admin/permissions-catalog').then(r => r.data),
  })

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [perms, setPerms] = useState(new Set())
  const [dirty, setDirty] = useState(false)
  const [saved, setSaved] = useState(null)
  const [hydrated, setHydrated] = useState(false)

  // Sync once when the group payload arrives. Runs for every group,
  // including ones with zero permissions or empty name/description.
  useEffect(() => {
    if (!group || hydrated || dirty) return
    setName(group.name || '')
    setDescription(group.description || '')
    setPerms(new Set(group.permissions || []))
    setHydrated(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group])

  const savePerms = useMutation({
    mutationFn: () => api.put(`/admin/groups/${groupId}/permissions`,
                              { permissions: [...perms] }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-groups'] })
      qc.invalidateQueries({ queryKey: ['admin-group', groupId] })
      setSaved('Permissions saved.')
      setDirty(false)
      setTimeout(() => setSaved(null), 1500)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed — see console'),
  })

  const saveMeta = useMutation({
    mutationFn: () => api.patch(`/admin/groups/${groupId}`,
                                 { name, description }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-groups'] })
      qc.invalidateQueries({ queryKey: ['admin-group', groupId] })
      setSaved('Saved.')
      setTimeout(() => setSaved(null), 1500)
    },
  })

  const remove = useMutation({
    mutationFn: () => api.delete(`/admin/groups/${groupId}`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-groups'] })
      onClose()
    },
  })

  // Members — full users list for the add picker
  const { data: allUsers } = useQuery({
    queryKey: ['admin-users'],
    queryFn: () => api.get('/admin/users').then(r => r.data),
  })
  const [pickedEmail, setPickedEmail] = useState('')
  const [memberFlash, setMemberFlash] = useState(null)
  function flashMembers(msg) {
    setMemberFlash(msg)
    setTimeout(() => setMemberFlash(null), 1500)
  }
  const addMember = useMutation({
    mutationFn: (email) => api.post(`/admin/groups/${groupId}/members`,
                                       { email }).then(r => r.data),
    onSuccess: (_data, email) => {
      qc.invalidateQueries({ queryKey: ['admin-groups'] })
      qc.invalidateQueries({ queryKey: ['admin-group', groupId] })
      qc.invalidateQueries({ queryKey: ['admin-group-members'] })
      qc.invalidateQueries({ queryKey: ['admin-users'] })
      setPickedEmail('')
      flashMembers(`✓ Added ${email}`)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Add failed'),
  })
  const removeMember = useMutation({
    mutationFn: (email) => api.delete(`/admin/groups/${groupId}/members/${encodeURIComponent(email)}`)
                                .then(r => r.data),
    onSuccess: (_data, email) => {
      qc.invalidateQueries({ queryKey: ['admin-groups'] })
      qc.invalidateQueries({ queryKey: ['admin-group', groupId] })
      qc.invalidateQueries({ queryKey: ['admin-group-members'] })
      qc.invalidateQueries({ queryKey: ['admin-users'] })
      flashMembers(`✓ Removed ${email}`)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Remove failed'),
  })

  function togglePerm(p) {
    const next = new Set(perms)
    if (next.has(p)) next.delete(p)
    else next.add(p)
    setPerms(next)
    setDirty(true)
  }

  // Group catalog rows by domain prefix (the part before the colon)
  const grouped = {}
  for (const item of (catalog?.permissions || [])) {
    const dom = item.key.split(':')[0]
    if (!grouped[dom]) grouped[dom] = []
    grouped[dom].push(item)
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div
        className="relative w-full max-w-2xl bg-white shadow-xl overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-white border-b border-border-subtle px-6 py-4 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[18px]">Edit Group</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        {isLoading && <div className="p-6 text-muted">Loading…</div>}

        {group && (
          <div className="p-6 space-y-5">
            <div>
              <label className="text-[10px] uppercase tracking-wide text-gray-500">Name</label>
              <div className="flex gap-2 mt-1">
                <input
                  className="input text-sm flex-1"
                  value={name}
                  onChange={e => setName(e.target.value)}
                />
                <button
                  className="btn-secondary text-xs"
                  onClick={() => saveMeta.mutate()}
                  disabled={saveMeta.isPending || (name === group.name && description === (group.description || ''))}
                >
                  Save name/desc
                </button>
              </div>
              <label className="text-[10px] uppercase tracking-wide text-gray-500 mt-3 block">Description</label>
              <input
                className="input text-sm w-full mt-1"
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="Optional"
              />
              {group.system_protected && (
                <div className="text-[11px] text-plum-700 bg-plum-50 border border-plum-200 rounded px-2 py-1 mt-2">
                  System-protected group — cannot be deleted, but membership and permissions can be edited.
                </div>
              )}
            </div>

            {/* ─── Members ─── */}
            <div className="border-t border-gray-100 pt-4">
              <div className="flex items-baseline justify-between mb-2">
                <h3 className="text-sm font-semibold text-ink flex items-center gap-2">
                  <Users size={13} /> Members
                  <span className="text-[11px] text-muted font-normal">
                    ({group.members?.length || 0})
                  </span>
                </h3>
                <Link to={`/admin?group=${groupId}`}
                       className="text-[11px] text-plum-700 hover:underline">
                  Open in Admin →
                </Link>
              </div>
              <p className="text-[11px] text-muted mb-3">
                Members of this group inherit every permission checked below.
                <span className="text-success ml-1">Member changes save instantly</span> —
                the <strong>Save Permissions</strong> button at the bottom only saves
                permission edits.
              </p>
              {memberFlash && (
                <div className="text-[11px] text-success mb-2">{memberFlash}</div>
              )}

              <div className="flex flex-wrap gap-1.5 mb-3">
                {(group.members || []).length === 0 && (
                  <span className="text-[11px] text-muted italic">No members yet.</span>
                )}
                {(group.members || []).map(email => (
                  <span key={email}
                         className="inline-flex items-center gap-1 bg-plum-50 border border-plum-100 rounded px-1.5 py-0.5 text-[11px]">
                    {email}
                    <button className="text-muted hover:text-danger"
                             title={`Remove ${email}`}
                             onClick={() => {
                               if (confirm(`Remove ${email} from ${group.name}?`)) {
                                 removeMember.mutate(email)
                               }
                             }}
                             disabled={removeMember.isPending}>
                      <X size={10} />
                    </button>
                  </span>
                ))}
              </div>

              {/* Add picker */}
              <div className="flex items-center gap-2">
                <select className="input text-[12px] flex-1"
                         value={pickedEmail}
                         onChange={e => setPickedEmail(e.target.value)}>
                  <option value="">— pick a user to add —</option>
                  {(allUsers || [])
                    .filter(u => !(group.members || []).includes(u.email))
                    .sort((a, b) => (a.display_name || a.email).localeCompare(b.display_name || b.email))
                    .map(u => (
                      <option key={u.email} value={u.email}>
                        {u.display_name ? `${u.display_name} (${u.email})` : u.email}
                      </option>
                    ))}
                </select>
                <button className="btn-secondary text-[12px] flex items-center gap-1"
                        onClick={() => pickedEmail && addMember.mutate(pickedEmail)}
                        disabled={!pickedEmail || addMember.isPending}>
                  <Plus size={11}/> Add
                </button>
              </div>
            </div>

            {/* ─── Permissions ─── */}
            <div className="border-t border-gray-100 pt-4">
              <div className="flex items-baseline justify-between mb-2">
                <h3 className="text-sm font-semibold text-ink">
                  Permissions ({perms.size}/{catalog?.permissions?.length || '?'})
                </h3>
              </div>
              <p className="text-[11px] text-muted mb-3">
                Click to toggle. Members of this group inherit every checked permission.
              </p>
              <div className="space-y-3">
                {Object.entries(grouped).map(([domain, items]) => (
                  <div key={domain}>
                    <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">{domain}</div>
                    <div className="space-y-1">
                      {items.map(item => {
                        const checked = perms.has(item.key)
                        return (
                          <label key={item.key}
                                 className={`flex items-start gap-2 py-1 px-2 rounded text-[12px] cursor-pointer ${checked ? 'bg-plum-50' : 'hover:bg-gray-50'}`}>
                            <input type="checkbox" checked={checked}
                                   onChange={() => togglePerm(item.key)}
                                   className="mt-0.5" />
                            <div className="flex-1 min-w-0">
                              <div className="flex items-baseline gap-2">
                                <code className="text-plum-700 text-[11px]">{item.key}</code>
                              </div>
                              <div className="text-muted text-[11px]">{item.description}</div>
                            </div>
                          </label>
                        )
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="sticky bottom-0 bg-white border-t border-border-subtle pt-3 -mx-6 px-6 flex items-center gap-2 justify-between">
              <div>
                {!group.system_protected && (
                  <button
                    className="text-danger text-[12px] flex items-center gap-1 hover:underline"
                    onClick={() => {
                      if (confirm(`Delete group "${group.name}"?`)) remove.mutate()
                    }}
                    disabled={remove.isPending || group.member_count > 0}
                    title={group.member_count > 0 ? 'Remove all members first' : 'Delete this group'}
                  >
                    <Trash2 size={12} /> Delete Group
                  </button>
                )}
              </div>
              <div className="flex items-center gap-3">
                {saved && <span className="text-success text-[12px]">{saved}</span>}
                <button className="btn-secondary text-sm" onClick={onClose}>Close</button>
                <button
                  className="btn-primary text-sm"
                  onClick={() => savePerms.mutate()}
                  disabled={!dirty || savePerms.isPending}
                >
                  {savePerms.isPending ? 'Saving…' : 'Save Permissions'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
