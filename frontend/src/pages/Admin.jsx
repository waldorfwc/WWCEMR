import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { CheckSquare, ChevronRight, Edit3, Eye, FileSignature, MessageSquare, Phone, Plus, Settings, Shield, Star, Trash2, Trophy, Users, X } from 'lucide-react'
import api from '../utils/api'

function Flash({ kind, text }) {
  if (!text) return null
  const cls = kind === 'ok' ? 'text-success' : 'text-danger'
  return <span className={`ml-2 text-[11px] ${cls}`}>{text}</span>
}


function UserRow({ u, allGroups, onFlash, onViewPerms, flashKind, flashText }) {
  const queryClient = useQueryClient()
  const [nameDraft, setNameDraft] = useState(u.display_name || '')
  const [editingGroups, setEditingGroups] = useState(false)

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

  const setGroups = useMutation({
    mutationFn: (groupIds) =>
      api.put(`/admin/users/${encodeURIComponent(u.email)}/groups`,
              { group_ids: groupIds }).then(r => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      queryClient.invalidateQueries({ queryKey: ['admin-user-perms', u.email] })
      onFlash(u.email, 'ok', '✓ saved')
      setEditingGroups(false)
    },
    onError: (err) => {
      onFlash(u.email, 'err', `✗ ${err?.response?.data?.detail || 'error'}`)
    },
  })

  const deleteUser = useMutation({
    mutationFn: () => api.delete(`/admin/users/${encodeURIComponent(u.email)}`).then(r => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      queryClient.invalidateQueries({ queryKey: ['admin-groups'] })
      // group-member queries (used by the AdminGroups → /admin filter)
      queryClient.invalidateQueries({ queryKey: ['admin-group-members'] })
    },
    onError: (err) => {
      onFlash(u.email, 'err', `✗ ${err?.response?.data?.detail || 'delete failed'}`)
    },
  })

  return (
    <tr className="table-row">
      <td className="table-td font-mono text-[11px]">{u.email}</td>
      <td className="table-td">
        <input
          className="input w-full max-w-[180px] py-1 text-[12px]"
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
      <td className="table-td relative">
        <UserGroupsCell
          user={u}
          allGroups={allGroups}
          editing={editingGroups}
          onEdit={() => setEditingGroups(true)}
          onCancel={() => setEditingGroups(false)}
          onSave={(ids) => setGroups.mutate(ids)}
          saving={setGroups.isPending}
        />
      </td>
      <td className="table-td">
        <button
          className="text-[11px] text-plum-700 hover:underline flex items-center gap-1"
          onClick={() => onViewPerms(u.email)}
        >
          <Eye size={11} /> View
        </button>
      </td>
      <td className="table-td">
        <RingCentralCell user={u} />
      </td>
      <td className="table-td text-[11px] text-muted">
        {u.created_at ? new Date(u.created_at).toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' }) : '—'}
      </td>
      <td className="table-td">
        <div className="flex items-center gap-2">
          <Flash kind={flashKind} text={flashText} />
          <button
            className="text-danger hover:bg-red-50 p-1 rounded"
            title={`Delete ${u.email}`}
            onClick={() => {
              const msg = `Delete user ${u.email}?\n\n`
                + `Their group memberships will be removed and the row will be hard-deleted.\n`
                + `Audit log + historical task instances keep the email as a string reference.\n\n`
                + `This can't be undone — type the email below to confirm.`
              const typed = window.prompt(msg, '')
              if (typed && typed.trim().toLowerCase() === u.email.toLowerCase()) {
                deleteUser.mutate()
              } else if (typed !== null) {
                onFlash(u.email, 'err', '✗ email did not match — not deleted')
              }
            }}
            disabled={deleteUser.isPending}
          >
            <Trash2 size={13} />
          </button>
        </div>
      </td>
    </tr>
  )
}


function UserGroupsCell({ user, allGroups, editing, onEdit, onCancel, onSave, saving }) {
  // We don't have group ids on the user record from /admin/users, so we
  // derive them by name from allGroups when editing starts. The list is
  // passed via a separate per-user query (effective-permissions) for the
  // editor; for the chip display we use whatever we have.
  const triggerRef = useRef(null)
  const popoverRef = useRef(null)
  const [draft, setDraft] = useState(new Set())  // group ids
  const [popoverPos, setPopoverPos] = useState({ top: 0, left: 0 })

  // Lazily pull this user's current group memberships when editor opens
  const { data: perms } = useQuery({
    queryKey: ['admin-user-perms', user.email],
    queryFn: () => api.get(`/admin/users/${encodeURIComponent(user.email)}/effective-permissions`).then(r => r.data),
    enabled: editing,
  })

  useEffect(() => {
    if (perms && editing) {
      setDraft(new Set((perms.groups || []).map(g => g.id)))
    }
  }, [perms, editing])

  // Compute popover position from the button's bounding rect so it
  // escapes the table card's overflow:hidden clipping. Clamp to the
  // viewport so it never falls off-screen on the right, and flip above
  // the button when there isn't room below.
  useEffect(() => {
    if (!editing || !triggerRef.current) return
    const POPOVER_WIDTH = 288  // matches w-72
    const VIEWPORT_PAD = 8
    function place() {
      const rect = triggerRef.current.getBoundingClientRect()
      // Use measured popover height when available, otherwise estimate
      // based on the max-h-60 inner list + header/footer (~360px).
      const popoverHeight = popoverRef.current
        ? popoverRef.current.offsetHeight
        : 360
      const left = Math.min(
        rect.left,
        window.innerWidth - POPOVER_WIDTH - VIEWPORT_PAD,
      )
      // Prefer below; flip above if it would overflow the bottom edge.
      const spaceBelow = window.innerHeight - rect.bottom
      const spaceAbove = rect.top
      let top
      if (spaceBelow >= popoverHeight + VIEWPORT_PAD || spaceBelow >= spaceAbove) {
        top = Math.min(
          rect.bottom + 4,
          window.innerHeight - popoverHeight - VIEWPORT_PAD,
        )
      } else {
        top = Math.max(VIEWPORT_PAD, rect.top - popoverHeight - 4)
      }
      setPopoverPos({
        top: Math.max(VIEWPORT_PAD, top),
        left: Math.max(VIEWPORT_PAD, left),
      })
    }
    // First place uses the estimate; second place (after the popover has
    // rendered) uses the measured height for an accurate position.
    place()
    const id = requestAnimationFrame(place)
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      cancelAnimationFrame(id)
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [editing])

  // Close on outside click
  useEffect(() => {
    if (!editing) return
    function handleClick(e) {
      if (popoverRef.current && popoverRef.current.contains(e.target)) return
      if (triggerRef.current && triggerRef.current.contains(e.target)) return
      onCancel()
    }
    function handleEsc(e) { if (e.key === 'Escape') onCancel() }
    document.addEventListener('mousedown', handleClick)
    document.addEventListener('keydown', handleEsc)
    return () => {
      document.removeEventListener('mousedown', handleClick)
      document.removeEventListener('keydown', handleEsc)
    }
  }, [editing, onCancel])

  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      <UserGroupChips email={user.email} />
      <button
        ref={triggerRef}
        type="button"
        className="text-[11px] text-plum-700 hover:bg-plum-50 px-1.5 py-0.5 rounded flex items-center gap-1"
        onClick={onEdit}
      >
        <Edit3 size={10} /> Edit
      </button>

      {editing && (
        <div
          ref={popoverRef}
          className="w-72 bg-white border border-border-subtle rounded-md shadow-lg p-3"
          style={{
            position: 'fixed',
            top: popoverPos.top,
            left: popoverPos.left,
            zIndex: 50,
          }}
          onClick={e => e.stopPropagation()}
        >
          <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-2">
            Groups for {user.email.split('@')[0]}
          </div>
          {!perms && <div className="text-[12px] text-muted">Loading…</div>}
          {perms && (
            <div className="space-y-1 max-h-60 overflow-y-auto">
              {allGroups.map(g => {
                const checked = draft.has(g.id)
                return (
                  <label key={g.id}
                         className={`flex items-center gap-2 px-2 py-1 rounded text-[12px] cursor-pointer ${checked ? 'bg-plum-50' : 'hover:bg-gray-50'}`}>
                    <input type="checkbox" checked={checked}
                           onChange={() => {
                             const next = new Set(draft)
                             if (next.has(g.id)) next.delete(g.id)
                             else next.add(g.id)
                             setDraft(next)
                           }} />
                    <span className="flex-1">{g.name}</span>
                    {g.system_protected && (
                      <Shield size={10} className="text-plum-600" />
                    )}
                  </label>
                )
              })}
            </div>
          )}
          <div className="flex gap-2 justify-end mt-3">
            <button className="text-[11px] text-muted hover:underline" onClick={onCancel}>Cancel</button>
            <button
              className="btn-primary py-1 px-2 text-[11px]"
              onClick={() => onSave([...draft])}
              disabled={!perms || saving}
            >
              {saving ? '…' : 'Save'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}


function RingCentralCell({ user }) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [cb, setCb] = useState(user.ringcentral_callback_number || '')

  const patch = useMutation({
    mutationFn: (body) => api.patch(`/admin/users/${encodeURIComponent(user.email)}`, body)
                            .then(r => r.data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      setEditing(false)
    },
  })

  if (!user.ringcentral_extension && !user.ringcentral_callback_number) {
    return (
      <div className="text-[11px]">
        <span className="text-amber-700 italic">unmapped</span>
        <OverrideToggle user={user} patch={patch} />
      </div>
    )
  }

  return (
    <div className="text-[11px]">
      {user.ringcentral_extension && (
        <div className="text-gray-700">ext <span className="font-mono">{user.ringcentral_extension}</span></div>
      )}
      {editing ? (
        <div className="flex items-center gap-1 mt-0.5">
          <input
            className="input py-0.5 px-1 text-[11px] font-mono w-32"
            placeholder="+12405551234"
            value={cb}
            onChange={(e) => setCb(e.target.value)}
            autoFocus
          />
          <button className="text-[10px] text-success"
                  onClick={() => patch.mutate({ ringcentral_callback_number: cb || '' })}>save</button>
          <button className="text-[10px] text-muted" onClick={() => { setCb(user.ringcentral_callback_number || ''); setEditing(false) }}>×</button>
        </div>
      ) : (
        <button
          className="text-gray-600 font-mono hover:bg-plum-50 px-1 rounded"
          onClick={() => setEditing(true)}
          title="Click to edit RingCentral callback number"
        >
          {user.ringcentral_callback_number || <span className="text-amber-600 italic">no callback</span>}
        </button>
      )}
      <OverrideToggle user={user} patch={patch} />
      {patch.isError && (
        <div className="text-[10px] text-danger">{patch.error?.response?.data?.detail || 'error'}</div>
      )}
    </div>
  )
}


function OverrideToggle({ user, patch }) {
  return (
    <label className="flex items-center gap-1 mt-0.5 text-[10px] cursor-pointer"
            title="When on, the email-matching auto-sync skips this user (use when the RC seat is registered to a different email).">
      <input type="checkbox"
              checked={!!user.ringcentral_manual_override}
              onChange={e => patch.mutate({ ringcentral_manual_override: e.target.checked })} />
      <span className={user.ringcentral_manual_override ? 'text-plum-700 font-semibold' : 'text-gray-500'}>
        manual override
      </span>
    </label>
  )
}


function UserGroupChips({ email }) {
  const { data } = useQuery({
    queryKey: ['admin-user-perms', email],
    queryFn: () => api.get(`/admin/users/${encodeURIComponent(email)}/effective-permissions`).then(r => r.data),
    staleTime: 30_000,
  })
  const groups = data?.groups || []
  if (groups.length === 0) {
    return <span className="text-[11px] text-amber-700 italic">no groups</span>
  }
  return (
    <div className="flex flex-wrap gap-1">
      {groups.map(g => (
        <span key={g.id}
              className="text-[10px] bg-plum-100 text-plum-700 px-1.5 py-0.5 rounded">
          {g.name}
        </span>
      ))}
    </div>
  )
}


function PermissionsDrawer({ email, onClose }) {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['admin-user-perms', email],
    queryFn: () => api.get(`/admin/users/${encodeURIComponent(email)}/effective-permissions`).then(r => r.data),
  })
  const { data: catalog } = useQuery({
    queryKey: ['perm-catalog'],
    queryFn: () => api.get('/admin/permissions-catalog').then(r => r.data),
  })

  const [extras, setExtras] = useState(new Set())
  const [revoked, setRevoked] = useState(new Set())
  const [hydrated, setHydrated] = useState(false)

  useEffect(() => {
    if (data && !hydrated) {
      setExtras(new Set(data.permissions_extra || []))
      setRevoked(new Set(data.permissions_revoked || []))
      setHydrated(true)
    }
  }, [data, hydrated])

  const save = useMutation({
    mutationFn: () =>
      api.put(`/admin/users/${encodeURIComponent(email)}/permissions-override`, {
        permissions_extra: [...extras],
        permissions_revoked: [...revoked],
      }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-user-perms', email] })
    },
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-2xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-6 py-4 flex items-center justify-between">
          <div>
            <h2 className="font-serif font-semibold text-ink text-[18px]">Permissions</h2>
            <div className="text-muted text-[11px] font-mono">{email}</div>
          </div>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        {!data && <div className="p-6 text-muted">Loading…</div>}

        {data && (
          <div className="p-6 space-y-5">
            <section>
              <h3 className="text-sm font-semibold text-ink mb-2">Groups ({data.groups.length})</h3>
              {data.groups.length === 0 ? (
                <div className="text-[12px] text-amber-700 italic">No groups assigned.</div>
              ) : (
                <ul className="space-y-2">
                  {data.groups.map(g => (
                    <li key={g.id} className="border-l-2 border-plum-200 pl-3">
                      <div className="text-[13px] font-medium">{g.name}</div>
                      <div className="text-[10px] text-muted mt-0.5">
                        {(data.permissions_by_group[g.name] || []).length} permissions
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section>
              <h3 className="text-sm font-semibold text-ink mb-2">
                Effective permissions ({data.effective_permissions.length})
              </h3>
              <p className="text-[11px] text-muted mb-2">
                Union of all group permissions, plus extras, minus revoked.
              </p>
              {data.effective_permissions.length === 0 ? (
                <div className="text-[12px] text-amber-700 italic">No permissions in effect.</div>
              ) : (
                <ul className="grid grid-cols-2 gap-x-3 gap-y-1">
                  {data.effective_permissions.map(p => (
                    <li key={p} className="text-[11px] flex items-baseline gap-1.5">
                      <code className="text-plum-700 shrink-0">{p}</code>
                      <span className="text-muted truncate">{data.permission_descriptions[p] || ''}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="border-t border-gray-100 pt-5">
              <h3 className="text-sm font-semibold text-ink mb-1">
                Per-user overrides
              </h3>
              <p className="text-[11px] text-muted mb-3">
                Grant extras or revoke specific permissions on top of group memberships.
                Use sparingly — prefer changing groups.
              </p>

              <div className="space-y-2">
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">
                    Extras (granted just to this user)
                  </div>
                  <PermissionPicker
                    catalog={catalog?.permissions || []}
                    selected={extras}
                    onChange={(next) => setExtras(new Set(next))}
                    placeholder="Add a permission this user wouldn't otherwise have…"
                    excluded={revoked}
                  />
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">
                    Revoked (removed for this user)
                  </div>
                  <PermissionPicker
                    catalog={catalog?.permissions || []}
                    selected={revoked}
                    onChange={(next) => setRevoked(new Set(next))}
                    placeholder="Remove a permission this user inherits from a group…"
                    excluded={extras}
                  />
                </div>
              </div>

              {save.isError && (
                <div className="text-danger text-[11px] mt-2">
                  {save.error?.response?.data?.detail || 'error saving'}
                </div>
              )}
              {save.isSuccess && (
                <div className="text-success text-[11px] mt-2">✓ Overrides saved</div>
              )}

              <div className="flex justify-end mt-3">
                <button className="btn-primary text-sm" onClick={() => save.mutate()}
                        disabled={save.isPending}>
                  {save.isPending ? 'Saving…' : 'Save overrides'}
                </button>
              </div>
            </section>
          </div>
        )}
      </div>
    </div>
  )
}


function PermissionPicker({ catalog, selected, onChange, placeholder, excluded }) {
  const [text, setText] = useState('')
  const lower = text.toLowerCase().trim()
  const candidates = catalog
    .filter(p => !selected.has(p.key) && !excluded.has(p.key))
    .filter(p => !lower
                 || p.key.toLowerCase().includes(lower)
                 || (p.description || '').toLowerCase().includes(lower))
    .slice(0, 6)

  function add(key) {
    onChange([...selected, key])
    setText('')
  }
  function remove(key) {
    onChange([...selected].filter(k => k !== key))
  }

  return (
    <div>
      <div className="flex flex-wrap gap-1 mb-1">
        {[...selected].map(p => (
          <span key={p}
                className="text-[10px] bg-plum-100 text-plum-700 px-1.5 py-0.5 rounded flex items-center gap-1">
            <code>{p}</code>
            <button onClick={() => remove(p)} className="hover:text-red-600">
              <X size={10} />
            </button>
          </span>
        ))}
      </div>
      <input
        className="input text-[12px] py-1 w-full"
        placeholder={placeholder}
        value={text}
        onChange={e => setText(e.target.value)}
      />
      {text && candidates.length > 0 && (
        <div className="border border-gray-200 rounded mt-1 bg-white max-h-44 overflow-y-auto">
          {candidates.map(p => (
            <button
              key={p.key}
              type="button"
              className="block w-full text-left px-2 py-1.5 text-[11px] hover:bg-plum-50"
              onClick={() => add(p.key)}
            >
              <code className="text-plum-700">{p.key}</code>
              <span className="text-muted ml-2">{p.description}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}


function SyncRingCentralButton() {
  const queryClient = useQueryClient()
  const [result, setResult] = useState(null)
  const sync = useMutation({
    mutationFn: () => api.post('/admin/users/sync-ringcentral').then(r => r.data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      const txt = `✓ Updated ${data.updated}, unchanged ${data.unchanged}` +
                  (data.no_rc_match?.length ? `, ${data.no_rc_match.length} no RC match` : '') +
                  (data.manual_override_locked ? `, ${data.manual_override_locked} locked (manual)` : '')
      setResult({ kind: 'ok', text: txt })
      setTimeout(() => setResult(null), 5000)
    },
    onError: (err) => {
      setResult({ kind: 'err', text: err?.response?.data?.detail || err.message })
    },
  })
  return (
    <div className="flex items-center gap-1">
      <button className="btn-secondary text-sm flex items-center gap-1"
              onClick={() => sync.mutate()} disabled={sync.isPending}
              title="Pull each user's RC extension + callback number from RingCentral">
        <Phone size={13} /> {sync.isPending ? 'Syncing…' : 'Sync RC'}
      </button>
      {result && (
        <span className={`text-[11px] ${result.kind === 'ok' ? 'text-success' : 'text-danger'}`}>
          {result.text}
        </span>
      )}
    </div>
  )
}


function ChecklistTools() {
  const [result, setResult] = useState(null)

  const generate = useMutation({
    mutationFn: () => api.post('/checklist/generate-for-today').then(r => r.data),
    onSuccess: (data) => {
      const created = data?.created ?? data?.instances_created ?? 0
      const skipped = data?.skipped ?? data?.already_existed ?? 0
      setResult({ kind: 'ok', text: `✓ Generated ${created} new instance${created === 1 ? '' : 's'}${skipped ? `, ${skipped} already existed` : ''}` })
      setTimeout(() => setResult(null), 5000)
    },
    onError: (err) => {
      setResult({ kind: 'err', text: `✗ ${err?.response?.data?.detail || err.message}` })
    },
  })

  return (
    <div className="card mb-4">
      <div className="flex items-baseline justify-between">
        <div>
          <h2 className="font-serif font-semibold text-ink text-[16px] m-0">Checklist Tools</h2>
          <p className="text-muted text-[12px] mt-0.5">
            Daily task instances are normally spawned at 12:05 AM. Use this to generate
            today's tasks immediately for any users with active templates.
          </p>
        </div>
        <button
          className="btn-primary text-sm shrink-0"
          onClick={() => generate.mutate()}
          disabled={generate.isPending}
        >
          {generate.isPending ? 'Generating…' : "Generate today's tasks"}
        </button>
      </div>
      {result && (
        <div className={`mt-2 text-[12px] ${result.kind === 'ok' ? 'text-success' : 'text-danger'}`}>
          {result.text}
        </div>
      )}
    </div>
  )
}


function AddUserForm({ onClose, onFlash }) {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')

  // The legacy `group` enum (admin/billing/clinical) is still required by
  // the backend payload but isn't surfaced in the UI anymore. Default to
  // 'clinical' (lowest privilege) — admin assigns the real Groups after
  // creation. Phase 5+ migration may drop this field entirely.
  const create = useMutation({
    mutationFn: () => api.post('/admin/users', {
      email: email.trim().toLowerCase(),
      group: 'clinical',
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
      <td className="table-td text-[11px] text-muted italic">
        assign after create
      </td>
      <td className="table-td" />
      <td className="table-td" />
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
  const [searchParams, setSearchParams] = useSearchParams()
  const groupFilter = searchParams.get('group') || ''

  const { data: users, isLoading } = useQuery({
    queryKey: ['admin-users'],
    queryFn: () => api.get('/admin/users').then(r => r.data),
  })
  const { data: allGroups } = useQuery({
    queryKey: ['admin-groups'],
    queryFn: () => api.get('/admin/groups').then(r => r.data),
  })

  // Optional ?group=<id> filter — pulls the group's member emails and
  // narrows the users table to just those rows. Linked from the
  // "N members" badge on the Edit Group drawer.
  const { data: filterGroup } = useQuery({
    queryKey: ['admin-group-members', groupFilter],
    queryFn: () => api.get(`/admin/groups/${groupFilter}`).then(r => r.data),
    enabled: !!groupFilter,
  })
  const filterMemberSet = useMemo(() => {
    if (!filterGroup?.members) return null
    return new Set(filterGroup.members.map(e => (e || '').toLowerCase()))
  }, [filterGroup])
  const visibleUsers = useMemo(() => {
    if (!users) return users
    if (!filterMemberSet) return users
    return users.filter(u => filterMemberSet.has((u.email || '').toLowerCase()))
  }, [users, filterMemberSet])
  function clearGroupFilter() {
    const next = new URLSearchParams(searchParams)
    next.delete('group')
    setSearchParams(next, { replace: true })
  }

  const [adding, setAdding] = useState(false)
  const [flashes, setFlashes] = useState({})
  const [permsForEmail, setPermsForEmail] = useState(null)

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

  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <h1 className="font-serif font-semibold text-ink text-[22px] m-0">User management</h1>
          <div className="text-muted text-[12px] mt-0.5">
            {(users?.length || 0)} users · {(allGroups?.length || 0)} groups
          </div>
        </div>
        <div className="flex items-center gap-2">
          <SyncRingCentralButton />
          <Link to="/admin/templates"
                className="btn-secondary text-sm flex items-center gap-1">
            <CheckSquare size={13} /> Templates
          </Link>
          <Link to="/admin/consent-templates"
                className="btn-secondary text-sm flex items-center gap-1">
            <FileSignature size={13} /> Consent Templates
          </Link>
          <Link to="/admin/message-templates"
                className="btn-secondary text-sm flex items-center gap-1">
            <MessageSquare size={13} /> Message Templates
          </Link>
          <Link to="/admin/reputation/profiles"
                className="btn-secondary text-sm flex items-center gap-1">
            <Star size={13} /> Reputation Profiles
          </Link>
          <Link to="/admin/reputation/leaderboard"
                className="btn-secondary text-sm flex items-center gap-1">
            <Trophy size={13} /> Leaderboard
          </Link>
          <Link to="/admin/reputation/reviews"
                className="btn-secondary text-sm flex items-center gap-1">
            <Star size={13} /> Reviews
          </Link>
          <Link to="/admin/training/cards"
                className="btn-secondary text-sm flex items-center gap-1">
            <CheckSquare size={13} /> Training
          </Link>
          <Link to="/admin/google-sync"
                className="btn-secondary text-sm flex items-center gap-1">
            <Settings size={13} /> Google Sync
          </Link>
          <Link to="/admin/groups"
                className="btn-secondary text-sm flex items-center gap-1">
            <Settings size={13} /> Groups & Permissions
          </Link>
          {!adding && (
            <button className="btn-primary text-sm" onClick={() => setAdding(true)}>
              <Plus size={13} className="inline -mt-0.5" /> Add user
            </button>
          )}
        </div>
      </div>

      <ChecklistTools />

      {groupFilter && filterGroup && (
        <div className="mb-3 flex items-center justify-between bg-plum-50 border border-plum-100 rounded px-3 py-2">
          <div className="text-[12px] flex items-center gap-2">
            <Users size={13} className="text-plum-700" />
            <span>Showing members of <strong>{filterGroup.name}</strong> ({filterGroup.members?.length || 0})</span>
          </div>
          <button className="text-[11px] text-plum-700 hover:underline flex items-center gap-1"
                   onClick={clearGroupFilter}>
            Show all users <X size={11} />
          </button>
        </div>
      )}

      <div className="card p-0 overflow-hidden">
        <table className="w-full">
          <thead className="bg-plum-50">
            <tr>
              <th className="table-th">Email</th>
              <th className="table-th">Display name</th>
              <th className="table-th">Groups</th>
              <th className="table-th">Permissions</th>
              <th className="table-th">RingCentral</th>
              <th className="table-th">Created</th>
              <th className="table-th"></th>
            </tr>
          </thead>
          <tbody>
            {adding && <AddUserForm onClose={() => setAdding(false)} onFlash={onFlash} />}
            {isLoading && (
              <tr><td colSpan={7} className="table-td text-center text-muted py-8">Loading...</td></tr>
            )}
            {!isLoading && visibleUsers?.map(u => (
              <UserRow key={u.email} u={u}
                       allGroups={allGroups || []}
                       onFlash={onFlash}
                       onViewPerms={setPermsForEmail}
                       flashKind={flashes[u.email]?.kind}
                       flashText={flashes[u.email]?.text} />
            ))}
            {!isLoading && visibleUsers?.length === 0 && (
              <tr><td colSpan={7} className="table-td text-center text-muted py-8">
                {groupFilter ? 'No users in this group yet.' : 'No users yet.'}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {permsForEmail && (
        <PermissionsDrawer email={permsForEmail} onClose={() => setPermsForEmail(null)} />
      )}
    </div>
  )
}
