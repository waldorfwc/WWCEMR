import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueries, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Plus, Shield, Users, X, Trash2 } from 'lucide-react'
import api from '../../utils/api'


// ─── Tier columns ──────────────────────────────────────────────────────────
// "Denied" is user-only (overrides a group grant down to none for one user);
// groups can't be denied because the absence of a grant already means no
// access — a group can only ADD permissions, never subtract.
const USER_TIERS = [
  { value: 'view',   label: 'View'   },
  { value: 'work',   label: 'Work'   },
  { value: 'manage', label: 'Manage' },
  { value: 'admin',  label: 'Admin'  },
  { value: 'denied', label: 'Denied' },
]
const GROUP_TIERS = USER_TIERS.slice(0, 4)

const STORAGE_KEY = 'wwc.admin.permissions.module'

// Mirrors backend MODULE_REGISTRY — used as a fallback dropdown source so
// the module picker is populated immediately on first paint, before per-
// subject tier queries resolve.
const MODULE_FALLBACK = [
  { value: 'chart',                 label: 'Chart' },
  { value: 'active_ar',             label: 'Active AR' },
  { value: 'billing_bank_recon',    label: 'Billing – Bank Recon' },
  { value: 'billing_missing_charges', label: 'Billing – Missing Charges' },
  { value: 'billing_insurance_docs',  label: 'Billing – Insurance Documents' },
  { value: 'billing_insurance_contacts', label: 'Billing – Insurance Contacts' },
  { value: 'recall',                label: 'Recall' },
  { value: 'surgery',               label: 'Surgery' },
  { value: 'device_larc',           label: 'Device Tracking – LARC' },
  { value: 'device_office_procedures', label: 'Device Tracking – Office Procedures' },
  { value: 'pellets',               label: 'Pellets' },
  { value: 'reputation',            label: 'Reputation Management' },
  { value: 'training',              label: 'Training' },
  { value: 'my_checklist',          label: 'My Checklist' },
  { value: 'audit_log',             label: 'Audit Log' },
]


// ─── Page ──────────────────────────────────────────────────────────────────

export default function AdminPermissions() {
  const qc = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const [drawer, setDrawer] = useState(null)  // {kind: 'user'|'group', id: email|groupId}
  const [creatingGroup, setCreatingGroup] = useState(false)

  // ─── Module dropdown ────────────────────────────────────────────────
  const moduleFromUrl = searchParams.get('module')
  const moduleFromStorage = (typeof window !== 'undefined')
    ? localStorage.getItem(STORAGE_KEY) : null
  const [module, setModule] = useState(
    moduleFromUrl || moduleFromStorage || 'chart'
  )
  function changeModule(slug) {
    setModule(slug)
    try { localStorage.setItem(STORAGE_KEY, slug) } catch {}
    const next = new URLSearchParams(searchParams)
    next.set('module', slug)
    setSearchParams(next, { replace: true })
  }

  // Honor ?focus=<email-or-group-id> on initial load. A value containing @
  // is treated as a user email; anything else as a group id. We strip the
  // param after consuming it so a later close+reopen doesn't re-trigger.
  useEffect(() => {
    const f = searchParams.get('focus')
    if (!f || drawer) return
    setDrawer({ kind: f.includes('@') ? 'user' : 'group', id: f })
    const next = new URLSearchParams(searchParams)
    next.delete('focus')
    setSearchParams(next, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ─── Top-level lists ────────────────────────────────────────────────
  const groupsQ = useQuery({
    queryKey: ['admin-groups'],
    queryFn: () => api.get('/admin/groups').then(r => r.data),
  })
  const usersQ = useQuery({
    queryKey: ['admin-users'],
    queryFn: () => api.get('/admin/users').then(r => r.data),
  })

  // ─── Per-subject tier fetches (parallel) ────────────────────────────
  // We fetch all 15-module tier maps once per subject; module switching
  // is instant after that, and the drawer reuses the same cache. With
  // ~5 groups + 20 users that's <30 requests on initial load.
  const groupTierQueries = useQueries({
    queries: (groupsQ.data || []).map(g => ({
      queryKey: ['group-tiers', g.id],
      queryFn: () => api.get(`/admin/groups/${g.id}/tiers`).then(r => r.data),
      staleTime: 30_000,
    })),
  })
  const userTierQueries = useQueries({
    queries: (usersQ.data || []).map(u => ({
      queryKey: ['user-tiers', u.email],
      queryFn: () =>
        api.get(`/admin/users/${encodeURIComponent(u.email)}/tiers`)
           .then(r => r.data),
      staleTime: 30_000,
    })),
  })

  // Banner for the worst possible UX failure mode on a permission grid:
  // a silent failure where the office manager believes she granted access
  // and the MA still can't see the page. (Fable UX critique.)
  const [mutError, setMutError] = useState(null)

  // ─── Mutations ──────────────────────────────────────────────────────
  const setUserTier = useMutation({
    mutationFn: ({ email, moduleSlug, tier }) =>
      api.put(`/admin/users/${encodeURIComponent(email)}/overrides/${moduleSlug}`,
              { tier }).then(r => r.data),
    onSuccess: (_d, vars) => {
      qc.invalidateQueries({ queryKey: ['user-tiers', vars.email] })
      setMutError(null)
    },
    onError: (e, vars) => setMutError({
      kind: 'user', email: vars.email,
      msg: e?.response?.data?.detail || e.message || 'Save failed',
    }),
  })
  const setGroupTier = useMutation({
    mutationFn: ({ groupId, moduleSlug, tier }) =>
      api.put(`/admin/groups/${groupId}/tiers/${moduleSlug}`,
              { tier }).then(r => r.data),
    onSuccess: (_d, vars) => {
      qc.invalidateQueries({ queryKey: ['group-tiers', vars.groupId] })
      // user tiers may resolve through this group, invalidate them all
      qc.invalidateQueries({ queryKey: ['user-tiers'] })
      setMutError(null)
    },
    onError: (e, vars) => setMutError({
      kind: 'group', groupId: vars.groupId,
      msg: e?.response?.data?.detail || e.message || 'Save failed',
    }),
  })

  // ─── Derived: list of {subject, tierForModule} for the selected mod ─
  const groupRows = useMemo(() => {
    if (!groupsQ.data) return []
    return groupsQ.data.map((g, i) => {
      const q = groupTierQueries[i]
      const entry = q?.data?.tiers?.find(t => t.module === module)
      return {
        kind: 'group',
        id: g.id,
        name: g.name,
        description: g.description,
        system_protected: g.system_protected,
        memberCount: g.member_count,
        tier: entry?.tier || null,
        source_kind: entry?.tier ? 'group' : 'none',
        source_label: null,
        loading: q?.isLoading,
      }
    })
  }, [groupsQ.data, groupTierQueries, module])

  const userRows = useMemo(() => {
    if (!usersQ.data) return []
    return usersQ.data.map((u, i) => {
      const q = userTierQueries[i]
      const entry = q?.data?.tiers?.find(t => t.module === module)
      // Detect super-admin by looking at any module entry (all modules
      // will say source_kind=super_admin if the user is SA).
      const anyEntry = q?.data?.tiers?.[0]
      const isSA = anyEntry?.source_kind === 'super_admin'
      return {
        kind: 'user',
        id: u.email,
        name: u.display_name || u.email,
        subtitle: u.display_name ? u.email : null,
        tier: entry?.tier || null,
        source_kind: entry?.source_kind || 'none',
        source_label: entry?.source_label || null,
        is_super_admin: isSA,
        active: u.is_active,
        loading: q?.isLoading,
      }
    })
  }, [usersQ.data, userTierQueries, module])

  // ─── Filter ─────────────────────────────────────────────────────────
  const [filter, setFilter] = useState('')
  const [showKind, setShowKind] = useState('all')  // 'all' | 'groups' | 'users'
  const filterLower = filter.trim().toLowerCase()

  const matchedGroups = groupRows.filter(r => {
    if (showKind === 'users') return false
    if (!filterLower) return true
    return r.name.toLowerCase().includes(filterLower)
        || (r.description || '').toLowerCase().includes(filterLower)
  })
  const matchedUsers = userRows.filter(r => {
    if (showKind === 'groups') return false
    if (!filterLower) return true
    return r.name.toLowerCase().includes(filterLower)
        || r.id.toLowerCase().includes(filterLower)
  })

  // ─── Module options (from any loaded tiers query, fall back to mirror) ─
  const moduleOptions = useMemo(() => {
    const loaded = groupTierQueries.find(q => q.data) || userTierQueries.find(q => q.data)
    if (!loaded?.data?.tiers) return MODULE_FALLBACK
    return loaded.data.tiers.map(t => ({ value: t.module, label: t.label }))
  }, [groupTierQueries, userTierQueries])

  // ─── Render ─────────────────────────────────────────────────────────
  const isLoading = groupsQ.isLoading || usersQ.isLoading
  const tiers = showKind === 'groups' ? GROUP_TIERS : USER_TIERS

  return (
    <div>
      <div className="flex items-baseline justify-between mb-3">
        <div>
          <h1 className="font-serif font-semibold text-ink text-[22px] m-0">
            Permissions
          </h1>
          <p className="text-muted text-[12px] mt-0.5">
            Pick a module to see who has access. Click a tier dot to grant,
            click the active dot to clear. Click a name to open its profile.
          </p>
        </div>
        <button className="btn-primary text-sm flex items-center gap-1"
                onClick={() => setCreatingGroup(true)}>
          <Plus size={13} /> New Group
        </button>
      </div>

      {/* Toolbar */}
      <div className="card mb-3 flex flex-wrap items-center gap-3">
        <label className="text-[12px] text-gray-600 flex items-center gap-2">
          Module
          <select
            className="input text-sm py-1"
            value={module}
            onChange={e => changeModule(e.target.value)}
          >
            {moduleOptions.length === 0 && <option value={module}>{module}</option>}
            {moduleOptions.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
        <div className="h-5 w-px bg-gray-200" />
        <input
          className="input text-sm py-1 flex-1 max-w-xs"
          placeholder="Filter subjects…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
        />
        <div className="flex items-center gap-1 text-[12px]">
          {['all', 'groups', 'users'].map(k => (
            <button key={k}
                    onClick={() => setShowKind(k)}
                    className={
                      'px-2 py-1 rounded ' +
                      (showKind === k
                         ? 'bg-plum-100 text-plum-700 font-medium'
                         : 'text-gray-600 hover:bg-gray-100')
                    }>
              {k === 'all' ? 'Groups + Users' : k[0].toUpperCase() + k.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {creatingGroup && (
        <CreateGroupCard onClose={() => setCreatingGroup(false)} />
      )}

      {mutError && (
        <div className="card bg-red-50 border-red-200 text-sm flex items-start gap-3">
          <div className="text-red-700 font-medium flex-1">
            Couldn't save permission change
            <div className="text-xs text-red-600 mt-1 font-normal">
              {mutError.kind === 'user'
                ? <>For user <span className="font-mono">{mutError.email}</span>: {mutError.msg}</>
                : <>For a group: {mutError.msg}</>}
            </div>
            <div className="text-xs text-red-600 mt-1 font-normal">
              The cell may have reverted — try again, and if it keeps failing, ping engineering.
            </div>
          </div>
          <button className="text-red-400 hover:text-red-700"
                  onClick={() => setMutError(null)}>×</button>
        </div>
      )}

      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-plum-50 border-b border-plum-200 text-left">
            <tr>
              <th className="px-3 py-2 font-medium">Subject</th>
              {tiers.map(t => (
                <th key={t.value}
                    className="px-2 py-2 font-medium text-center w-16">
                  {t.label}
                </th>
              ))}
              <th className="px-3 py-2 font-medium">Source</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={tiers.length + 2}
                      className="px-3 py-6 text-center text-muted">Loading…</td></tr>
            )}

            {!isLoading && matchedGroups.length > 0 && (
              <tr className="bg-plum-50 border-t border-plum-100">
                <td colSpan={tiers.length + 2}
                    className="px-3 py-1 text-[11px] uppercase tracking-wide text-gray-500 font-semibold">
                  Groups ({matchedGroups.length})
                </td>
              </tr>
            )}
            {matchedGroups.map(row => (
              <SubjectRow
                key={`g:${row.id}`}
                row={row}
                tiers={GROUP_TIERS}
                showDeniedCell={showKind !== 'groups'}
                onCellClick={(tierValue) => {
                  const next = row.tier === tierValue ? null : tierValue
                  setGroupTier.mutate({
                    groupId: row.id, moduleSlug: module, tier: next,
                  })
                }}
                onOpen={() => setDrawer({ kind: 'group', id: row.id })}
                disabled={setGroupTier.isPending}
              />
            ))}

            {!isLoading && matchedUsers.length > 0 && (
              <tr className="bg-plum-50 border-t border-plum-100">
                <td colSpan={tiers.length + 2}
                    className="px-3 py-1 text-[11px] uppercase tracking-wide text-gray-500 font-semibold">
                  Users ({matchedUsers.length})
                </td>
              </tr>
            )}
            {matchedUsers.map(row => (
              <SubjectRow
                key={`u:${row.id}`}
                row={row}
                tiers={USER_TIERS}
                showDeniedCell={true}
                onCellClick={(tierValue) => {
                  // Click the active dot to clear an override (fall back
                  // to group). Click any other dot to set the override.
                  const next = (row.tier === tierValue
                                 && row.source_kind === 'override')
                    ? null : tierValue
                  setUserTier.mutate({
                    email: row.id, moduleSlug: module, tier: next,
                  })
                }}
                onOpen={() => setDrawer({ kind: 'user', id: row.id })}
                disabled={setUserTier.isPending}
              />
            ))}

            {!isLoading
              && matchedGroups.length === 0
              && matchedUsers.length === 0 && (
              <tr><td colSpan={tiers.length + 2}
                      className="px-3 py-6 text-center text-muted">
                No matches.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="text-[11px] text-muted mt-2">
        <span className="inline-block w-2 h-2 rounded-full bg-plum-700 mr-1 align-middle" />
        active &nbsp;·&nbsp;
        <strong>Source:</strong> <em>← Group</em> means the user inherits from
        that group. <em>Override</em> means a per-user override is set —
        click the active dot to clear. <em>Super Admin</em> always wins; you
        cannot change SA tiers from this grid.
      </div>

      {drawer && (
        <SubjectDrawer
          kind={drawer.kind}
          id={drawer.id}
          onClose={() => setDrawer(null)}
          onJumpToModule={(slug) => changeModule(slug)}
        />
      )}
    </div>
  )
}


// ─── Row ───────────────────────────────────────────────────────────────────

function SubjectRow({ row, tiers, showDeniedCell, onCellClick, onOpen, disabled }) {
  const isSA = row.is_super_admin
  return (
    <tr className="border-t border-plum-100 hover:bg-plum-50">
      <td className="px-3 py-2">
        <button
          type="button"
          className="text-left hover:underline flex items-baseline gap-1.5"
          onClick={onOpen}
        >
          {row.kind === 'group' && (
            <Shield size={11} className={row.system_protected ? 'text-plum-700' : 'text-gray-400'} />
          )}
          <span className="font-medium">{row.name}</span>
          {isSA && (
            <span className="inline-flex items-center gap-1 text-[11px] uppercase tracking-wide
                             text-plum-700 bg-plum-100 px-1.5 py-0.5 rounded">
              <Shield size={9} /> Super Admin
            </span>
          )}
          {row.subtitle && (
            <span className="text-[11px] text-gray-500 font-mono">{row.subtitle}</span>
          )}
          {row.kind === 'group' && typeof row.memberCount === 'number' && (
            <span className="text-[10px] text-gray-500">({row.memberCount})</span>
          )}
        </button>
      </td>
      {tiers.map(t => {
        // Hide the Denied dot for group rows since a group can't deny.
        if (t.value === 'denied' && row.kind === 'group') {
          return <td key={t.value} className="px-2 py-2 text-center" />
        }
        // Super Admin implies Admin on every module — light the Admin cell
        // so the row reads as a complete grant at a glance.
        const active = isSA
          ? t.value === 'admin'
          : row.tier === t.value
        return (
          <td key={t.value} className="px-2 py-2 text-center">
            <button
              type="button"
              onClick={() => onCellClick(t.value)}
              disabled={disabled || isSA}
              aria-label={`Set ${row.name} → ${t.label}`}
              title={isSA ? 'Super Admin — tier is implicit Admin' : `Set ${t.label}`}
              className={
                active
                  ? (row.kind === 'user' && row.source_kind === 'override'
                       ? 'inline-block w-3 h-3 rounded-full bg-amber-600'
                       : 'inline-block w-3 h-3 rounded-full bg-plum-700')
                  : 'inline-block w-3 h-3 rounded-full border border-plum-300 hover:bg-plum-100 disabled:opacity-40'
              }
            />
          </td>
        )
      })}
      <td className="px-3 py-2 text-xs text-muted">
        {isSA && (
          <span className="inline-flex items-center gap-1 text-plum-700">
            <Shield size={11} /> Super Admin
          </span>
        )}
        {!isSA && row.kind === 'user' && row.source_kind === 'override' && (
          <span className="text-amber-700">Override</span>
        )}
        {!isSA && row.kind === 'user' && row.source_kind === 'group'
          && (<>← {row.source_label}</>)}
        {!isSA && row.kind === 'user' && row.source_kind === 'none' && '—'}
        {!isSA && row.kind === 'group' && (row.tier ? 'Group grant' : '—')}
      </td>
    </tr>
  )
}


// ─── Create-group helper (inline, no modal) ────────────────────────────────

function CreateGroupCard({ onClose }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const create = useMutation({
    mutationFn: () => api.post('/admin/groups', { name, description })
                        .then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-groups'] })
      onClose()
    },
  })
  return (
    <div className="card mb-3 bg-plum-50/40">
      <div className="flex items-center justify-between mb-2">
        <div className="text-sm font-semibold text-ink">Create New Group</div>
        <button className="text-muted hover:text-ink" onClick={onClose}><X size={14} /></button>
      </div>
      <div className="space-y-2">
        <input
          className="input w-full text-sm"
          placeholder="Group name (e.g. Surgery Coordinator)"
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
        <button className="btn-primary text-sm"
                onClick={() => create.mutate()}
                disabled={!name.trim() || create.isPending}>
          {create.isPending ? 'Creating…' : 'Create'}
        </button>
      </div>
    </div>
  )
}


// ─── Subject Drawer ────────────────────────────────────────────────────────

function SubjectDrawer({ kind, id, onClose, onJumpToModule }) {
  useEffect(() => {
    function handleEsc(e) { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handleEsc)
    return () => document.removeEventListener('keydown', handleEsc)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-2xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-6 py-4 flex items-center justify-between z-10">
          <h2 className="font-serif font-semibold text-ink text-[18px]">
            {kind === 'user' ? 'User Profile' : 'Group Profile'}
          </h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        {kind === 'user'
          ? <UserDrawerBody email={id} onJumpToModule={onJumpToModule} />
          : <GroupDrawerBody groupId={id} onJumpToModule={onJumpToModule} onClose={onClose} />}
      </div>
    </div>
  )
}


function UserDrawerBody({ email, onJumpToModule }) {
  const qc = useQueryClient()
  const decoded = decodeURIComponent(email)

  const tiersQ = useQuery({
    queryKey: ['user-tiers', decoded],
    queryFn: () =>
      api.get(`/admin/users/${encodeURIComponent(decoded)}/tiers`)
         .then(r => r.data),
  })
  const groupsQ = useQuery({
    queryKey: ['admin-user-perms', decoded],
    queryFn: () =>
      api.get(`/admin/users/${encodeURIComponent(decoded)}/groups`)
         .then(r => r.data),
  })
  const allGroupsQ = useQuery({
    queryKey: ['admin-groups'],
    queryFn: () => api.get('/admin/groups').then(r => r.data),
  })

  const setGroups = useMutation({
    mutationFn: (group_ids) =>
      api.put(`/admin/users/${encodeURIComponent(decoded)}/groups`,
              { group_ids }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-user-perms', decoded] })
      qc.invalidateQueries({ queryKey: ['user-tiers', decoded] })
      qc.invalidateQueries({ queryKey: ['admin-users'] })
      qc.invalidateQueries({ queryKey: ['admin-groups'] })
    },
  })
  const setSuperAdmin = useMutation({
    mutationFn: (is_super_admin) =>
      api.put(`/admin/users/${encodeURIComponent(decoded)}/super_admin`,
              { is_super_admin }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['user-tiers', decoded] })
    },
  })
  const clearOverride = useMutation({
    mutationFn: (moduleSlug) =>
      api.put(`/admin/users/${encodeURIComponent(decoded)}/overrides/${moduleSlug}`,
              { tier: null }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['user-tiers', decoded] }),
  })

  if (tiersQ.isLoading) return <div className="p-6 text-muted">Loading…</div>
  if (tiersQ.error) return (
    <div className="p-6 text-sm text-red-700">
      {tiersQ.error?.response?.data?.detail || tiersQ.error.message}
    </div>
  )

  const tiers = tiersQ.data?.tiers || []
  const isSA = tiers[0]?.source_kind === 'super_admin'
  const userGroups = groupsQ.data?.groups || []
  const memberSet = new Set(userGroups.map(g => g.id))
  const allGroups = allGroupsQ.data || []
  const overrideCount = tiers.filter(t => t.source_kind === 'override').length

  return (
    <div className="p-6 space-y-5">
      <div>
        <div className="text-[11px] uppercase tracking-wide text-gray-500">Email</div>
        <div className="text-sm font-mono text-gray-800">{decoded}</div>
      </div>

      {/* Super Admin toggle */}
      <div className="border-t border-border-subtle pt-4">
        <label className="flex items-baseline gap-2 cursor-pointer">
          <input type="checkbox"
                 checked={isSA}
                 disabled={setSuperAdmin.isPending}
                 onChange={e => {
                   const next = e.target.checked
                   const msg = next
                     ? `Grant Super Admin to ${decoded}? They will get Admin on every module and can grant Super Admin to others.`
                     : `Revoke Super Admin from ${decoded}?`
                   if (window.confirm(msg)) setSuperAdmin.mutate(next)
                 }} />
          <span className="text-sm font-medium text-gray-800 flex items-center gap-1.5">
            <Shield size={13} className="text-plum-700" /> Super Admin
          </span>
        </label>
        {setSuperAdmin.isError && (
          <div className="text-danger text-[11px] mt-1">
            {setSuperAdmin.error?.response?.data?.detail || 'error'}
          </div>
        )}
        <p className="text-[11px] text-muted mt-1">
          A Super Admin has implicit Admin on every module. The system
          refuses to revoke the last Super Admin.
        </p>
      </div>

      {/* Groups */}
      <div className="border-t border-border-subtle pt-4">
        <div className="flex items-baseline justify-between mb-2">
          <h3 className="text-sm font-semibold text-ink flex items-center gap-2">
            <Users size={13} /> Groups
            <span className="text-[11px] text-muted font-normal">
              ({userGroups.length})
            </span>
          </h3>
        </div>
        <p className="text-[11px] text-muted mb-2">
          The user gets the max tier across every group they belong to.
        </p>
        <div className="space-y-1">
          {allGroups.map(g => {
            const checked = memberSet.has(g.id)
            return (
              <label key={g.id}
                     className={`flex items-center gap-2 px-2 py-1.5 rounded text-[12px] cursor-pointer
                                 ${checked ? 'bg-plum-50' : 'hover:bg-gray-50'}`}>
                <input type="checkbox" checked={checked}
                       disabled={setGroups.isPending}
                       onChange={() => {
                         const next = new Set(memberSet)
                         if (next.has(g.id)) next.delete(g.id)
                         else next.add(g.id)
                         setGroups.mutate([...next])
                       }} />
                <span className="flex-1">{g.name}</span>
                {g.system_protected && (
                  <Shield size={10} className="text-plum-600" />
                )}
                <span className="text-[10px] text-gray-500">{g.member_count}</span>
              </label>
            )
          })}
        </div>
      </div>

      {/* Full 15-module profile */}
      <div className="border-t border-border-subtle pt-4">
        <h3 className="text-sm font-semibold text-ink mb-2">
          Module access ({overrideCount > 0
            ? `${overrideCount} override${overrideCount === 1 ? '' : 's'}`
            : 'no overrides'})
        </h3>
        <ul className="space-y-1">
          {tiers.map(t => (
            <li key={t.module}
                className="flex items-baseline gap-2 text-xs border-b border-gray-50 py-1">
              <button
                className="font-medium text-gray-700 hover:underline shrink-0 w-40 text-left"
                onClick={() => onJumpToModule(t.module)}
                title="Jump to this module in the grid"
              >
                {t.label}
              </button>
              <span className={
                t.tier === 'none'
                  ? 'text-gray-400 font-mono w-16 shrink-0'
                  : 'text-plum-700 font-mono w-16 shrink-0'
              }>
                {t.tier === 'none' ? '—'
                  : t.tier === 'super_admin' ? 'admin' : t.tier}
              </span>
              <span className="text-gray-500 flex-1 truncate">
                {t.source_kind === 'super_admin' && (
                  <span className="text-plum-700 inline-flex items-center gap-1">
                    <Shield size={10} /> Super Admin
                  </span>
                )}
                {t.source_kind === 'group' && t.source_label && (
                  <>← {t.source_label}</>
                )}
                {t.source_kind === 'override' && (
                  <span className="text-amber-700">Override</span>
                )}
              </span>
              {t.source_kind === 'override' && (
                <button
                  className="text-[11px] text-amber-700 hover:underline shrink-0"
                  onClick={() => clearOverride.mutate(t.module)}
                  disabled={clearOverride.isPending}
                >
                  clear
                </button>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}


function GroupDrawerBody({ groupId, onJumpToModule, onClose }) {
  const qc = useQueryClient()

  const groupQ = useQuery({
    queryKey: ['admin-group', groupId],
    queryFn: () => api.get(`/admin/groups/${groupId}`).then(r => r.data),
  })
  const tiersQ = useQuery({
    queryKey: ['group-tiers', groupId],
    queryFn: () => api.get(`/admin/groups/${groupId}/tiers`).then(r => r.data),
  })
  const allUsersQ = useQuery({
    queryKey: ['admin-users'],
    queryFn: () => api.get('/admin/users').then(r => r.data),
  })

  const [name, setName] = useState(null)
  const [description, setDescription] = useState(null)
  const [pickedEmail, setPickedEmail] = useState('')
  const hydratedName = name ?? (groupQ.data?.name || '')
  const hydratedDesc = description ?? (groupQ.data?.description || '')

  const saveMeta = useMutation({
    mutationFn: () => api.patch(`/admin/groups/${groupId}`,
                                 { name: hydratedName, description: hydratedDesc })
                          .then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-groups'] })
      qc.invalidateQueries({ queryKey: ['admin-group', groupId] })
      setName(null); setDescription(null)
    },
  })
  const remove = useMutation({
    mutationFn: () => api.delete(`/admin/groups/${groupId}`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-groups'] })
      onClose()
    },
  })
  const addMember = useMutation({
    mutationFn: (email) => api.post(`/admin/groups/${groupId}/members`,
                                      { email }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-group', groupId] })
      qc.invalidateQueries({ queryKey: ['admin-groups'] })
      qc.invalidateQueries({ queryKey: ['user-tiers'] })
    },
  })
  const removeMember = useMutation({
    mutationFn: (email) =>
      api.delete(`/admin/groups/${groupId}/members/${encodeURIComponent(email)}`)
         .then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-group', groupId] })
      qc.invalidateQueries({ queryKey: ['admin-groups'] })
      qc.invalidateQueries({ queryKey: ['user-tiers'] })
    },
  })

  if (groupQ.isLoading) return <div className="p-6 text-muted">Loading…</div>
  const g = groupQ.data
  if (!g) return null

  const tiers = tiersQ.data?.tiers || []
  const grantedCount = tiers.filter(t => t.tier).length
  const members = g.members || []
  const memberSet = new Set(members.map(e => (e || '').toLowerCase()))
  const candidates = (allUsersQ.data || [])
    .filter(u => !memberSet.has((u.email || '').toLowerCase()))
    .sort((a, b) => (a.display_name || a.email).localeCompare(b.display_name || b.email))

  return (
    <div className="p-6 space-y-5">
      <div>
        <div className="text-[11px] uppercase tracking-wide text-gray-500">Name</div>
        <input className="input text-sm w-full mt-1"
               value={hydratedName}
               onChange={e => setName(e.target.value)} />
        <div className="text-[11px] uppercase tracking-wide text-gray-500 mt-3">Description</div>
        <input className="input text-sm w-full mt-1"
               value={hydratedDesc}
               onChange={e => setDescription(e.target.value)}
               placeholder="Optional" />
        <div className="flex items-center gap-2 mt-2">
          <button className="btn-secondary text-xs"
                  disabled={saveMeta.isPending
                              || (hydratedName === g.name
                                  && hydratedDesc === (g.description || ''))}
                  onClick={() => saveMeta.mutate()}>
            Save name/description
          </button>
          {g.system_protected && (
            <span className="text-[11px] text-plum-700 inline-flex items-center gap-1">
              <Shield size={11} /> System-protected (can't be deleted)
            </span>
          )}
        </div>
      </div>

      {/* Members */}
      <div className="border-t border-border-subtle pt-4">
        <div className="flex items-baseline justify-between mb-2">
          <h3 className="text-sm font-semibold text-ink flex items-center gap-2">
            <Users size={13} /> Members
            <span className="text-[11px] text-muted font-normal">({members.length})</span>
          </h3>
        </div>
        {members.length === 0 && (
          <div className="text-[11px] text-muted italic mb-2">No members yet.</div>
        )}
        <div className="flex flex-wrap gap-1.5 mb-3">
          {members.map(email => (
            <span key={email}
                   className="inline-flex items-center gap-1 bg-plum-50 border border-plum-100 rounded px-1.5 py-0.5 text-[11px]">
              {email}
              <button className="text-muted hover:text-danger"
                       title={`Remove ${email}`}
                       onClick={() => {
                         if (window.confirm(`Remove ${email} from ${g.name}?`)) {
                           removeMember.mutate(email)
                         }
                       }}
                       disabled={removeMember.isPending}>
                <X size={10} />
              </button>
            </span>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <select className="input text-[12px] flex-1"
                   value={pickedEmail}
                   onChange={e => setPickedEmail(e.target.value)}>
            <option value="">— pick a user to add —</option>
            {candidates.map(u => (
              <option key={u.email} value={u.email}>
                {u.display_name ? `${u.display_name} (${u.email})` : u.email}
              </option>
            ))}
          </select>
          <button className="btn-secondary text-[12px] flex items-center gap-1"
                  onClick={() => {
                    if (pickedEmail) {
                      addMember.mutate(pickedEmail)
                      setPickedEmail('')
                    }
                  }}
                  disabled={!pickedEmail || addMember.isPending}>
            <Plus size={11}/> Add
          </button>
        </div>
      </div>

      {/* Full 15-module grants */}
      <div className="border-t border-border-subtle pt-4">
        <h3 className="text-sm font-semibold text-ink mb-2">
          Module grants ({grantedCount > 0
            ? `${grantedCount} granted`
            : 'no grants'})
        </h3>
        <ul className="space-y-1">
          {tiers.map(t => (
            <li key={t.module}
                className="flex items-baseline gap-2 text-xs border-b border-gray-50 py-1">
              <button
                className="font-medium text-gray-700 hover:underline shrink-0 w-40 text-left"
                onClick={() => onJumpToModule(t.module)}
              >
                {t.label}
              </button>
              <span className={
                !t.tier
                  ? 'text-gray-400 font-mono w-16 shrink-0'
                  : 'text-plum-700 font-mono w-16 shrink-0'
              }>
                {t.tier || '—'}
              </span>
            </li>
          ))}
        </ul>
      </div>

      {/* Danger zone */}
      {!g.system_protected && (
        <div className="border-t border-border-subtle pt-4">
          <button className="text-danger text-[12px] flex items-center gap-1 hover:underline"
                  onClick={() => {
                    if (window.confirm(`Delete group "${g.name}"?`)) remove.mutate()
                  }}
                  disabled={remove.isPending || members.length > 0}
                  title={members.length > 0 ? 'Remove all members first' : 'Delete this group'}>
            <Trash2 size={12} /> Delete Group
          </button>
        </div>
      )}
    </div>
  )
}
