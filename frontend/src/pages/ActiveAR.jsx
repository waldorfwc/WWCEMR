import React, { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  Search, Upload, Plus, X, RefreshCw, MoreHorizontal, ChevronDown,
  ChevronRight, SlidersHorizontal, Layers, MessageSquare,
  Save, Star, Trash2, SearchX,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import EmptyState from '../components/EmptyState'

// ─────────────────────────────────────────────────────────────────────
// Constants

const PRIORITIES = ['', 'Primary', 'Secondary', 'Tertiary']
const AGE_BUCKETS = ['', '0-30', '31-60', '61-90', '90+']
const WORKFLOW_STATES = ['', 'new', 'in_progress', 'waiting_payer', 'waiting_patient',
                        'denied', 'appealed', 'paid', 'rebilled_modmed', 'written_off', 'closed']
const SORTS = [
  { key: 'balance_desc', label: 'Balance (high→low)' },
  { key: 'age_desc',     label: 'Age (oldest first)' },
  { key: 'dos_desc',     label: 'DOS (newest first)' },
  { key: 'tf_asc',       label: 'TF deadline (soonest first)' },
]

const QUICK_TABS = [
  { key: '',                label: 'All' },
  { key: 'new',             label: 'New' },
  { key: 'in_progress',     label: 'In Progress' },
  { key: 'denied',          label: 'Denials' },
  { key: 'appealed',        label: 'Appeals' },
  { key: 'paid',            label: 'Paid' },
  { key: 'rebilled_modmed', label: 'Rebilled in ModMed' },
]

const TF_STATUSES = [
  { key: '',       label: 'All TF status' },
  { key: 'urgent', label: 'Nearing TF (≤14 days)' },
  { key: 'soon',   label: 'TF Soon (15–30 days)' },
  { key: 'safe',   label: 'TF Safe (>30 days)' },
  { key: 'past',   label: 'TF Past — likely uncollectible' },
]

const PRIORITY_BADGE = {
  Primary:   { label: 'P', cls: 'bg-emerald-100 text-emerald-700' },
  Secondary: { label: 'S', cls: 'bg-amber-100 text-amber-700' },
  Tertiary:  { label: 'T', cls: 'bg-gray-100 text-gray-600' },
}

const STATE_BADGE = {
  new:              'bg-blue-50 text-blue-700 border-blue-200',
  in_progress:      'bg-indigo-50 text-indigo-700 border-indigo-200',
  waiting_payer:    'bg-amber-50 text-amber-700 border-amber-200',
  waiting_patient:  'bg-orange-50 text-orange-700 border-orange-200',
  denied:           'bg-red-50 text-red-700 border-red-200',
  appealed:         'bg-purple-50 text-purple-700 border-purple-200',
  paid:             'bg-green-50 text-green-700 border-green-200',
  rebilled_modmed:  'bg-teal-50 text-teal-700 border-teal-200',
  written_off:      'bg-gray-100 text-gray-600 border-gray-200',
  closed:           'bg-gray-100 text-gray-500 border-gray-200',
}

const TF_DOT = {
  past:    'bg-red-500',
  urgent:  'bg-red-400',
  soon:    'bg-amber-400',
  safe:    'bg-green-400',
  unknown: 'bg-gray-300',
}


function getMyEmail() {
  try { return JSON.parse(localStorage.getItem('user') || '{}').email || '' }
  catch { return '' }
}


/**
 * Like useState but persists in localStorage. Survives page reload + navigation.
 * Filter values stay where the user left them when they bounce to a claim
 * detail and back.
 *
 * Bump the storage key (`ar.filter.v1` → `v2`) if you ever need to invalidate
 * everyone's saved filter values after a schema change.
 */
function useStickyState(key, initial) {
  const [val, setVal] = useState(() => {
    try {
      const raw = localStorage.getItem(key)
      return raw !== null ? JSON.parse(raw) : initial
    } catch { return initial }
  })
  useEffect(() => {
    try { localStorage.setItem(key, JSON.stringify(val)) } catch {}
  }, [key, val])
  return [val, setVal]
}

function isFresh(c) {
  // "Fresh" = imported / status-checked / last seen in export within 24h
  const now = Date.now()
  const recent = [c.last_status_check_at, c.last_seen_in_export_at, c.imported_at]
    .filter(Boolean)
    .map(s => new Date(s.replace(' ', 'T') + 'Z').getTime())
    .filter(n => !isNaN(n))
  return recent.some(t => now - t < 24 * 60 * 60 * 1000)
}

// ─────────────────────────────────────────────────────────────────────
// Page

export default function ActiveAR() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const me = getMyEmail()

  // Filter state persists in localStorage (sticky across nav + page reload).
  // Namespaced per user so different staff on the same browser don't collide.
  const k = (suffix) => `ar.filter.v1.${me || 'anon'}.${suffix}`
  const [search, setSearch] = useStickyState(k('search'), '')
  const [priority, setPriority] = useStickyState(k('priority'), '')
  const [ageBucket, setAgeBucket] = useStickyState(k('ageBucket'), '')
  const [workflowState, setWorkflowState] = useStickyState(k('workflowState'), '')
  const [payer, setPayer] = useStickyState(k('payer'), '')
  const [plan, setPlan] = useStickyState(k('plan'), '')
  const [tfStatus, setTfStatus] = useStickyState(k('tfStatus'), '')
  const [assignedTo, setAssignedTo] = useStickyState(k('assignedTo'), '')
  const [sort, setSort] = useStickyState(k('sort'), 'balance_desc')
  const [includeAged, setIncludeAged] = useStickyState(k('includeAged'), false)
  const [page, setPage] = useStickyState(k('page'), 1)
  const [view, setView] = useStickyState(k('view'), 'table')   // 'table' | 'dos'

  const [showUpload, setShowUpload] = useState(false)
  const [showEnrich, setShowEnrich] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [showPayers, setShowPayers] = useState(false)
  const [actionsOpen, setActionsOpen] = useState(false)
  const [savingPreset, setSavingPreset] = useState(false)
  const [presetNameDraft, setPresetNameDraft] = useState('')

  // Saved filter presets (per-user)
  const currentFilters = {
    search, priority, ageBucket, workflowState, payer, plan, tfStatus,
    assignedTo, sort, includeAged,
  }
  function applyFilters(f) {
    setSearch(f.search ?? '')
    setPriority(f.priority ?? '')
    setAgeBucket(f.ageBucket ?? '')
    setWorkflowState(f.workflowState ?? '')
    setPayer(f.payer ?? '')
    setPlan(f.plan ?? '')
    setTfStatus(f.tfStatus ?? '')
    setAssignedTo(f.assignedTo ?? '')
    setSort(f.sort ?? 'balance_desc')
    setIncludeAged(!!f.includeAged)
    setPage(1)
  }

  const { data: presets } = useQuery({
    queryKey: ['active-ar-filter-presets'],
    queryFn: () => api.get('/active-ar-filters').then(r => r.data),
  })

  // Auto-load the default preset once on first mount
  const [defaultApplied, setDefaultApplied] = useState(false)
  useEffect(() => {
    if (defaultApplied || !presets) return
    const def = presets.find(p => p.is_default)
    if (def) applyFilters(def.filters_json || {})
    setDefaultApplied(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presets, defaultApplied])

  const savePreset = useMutation({
    mutationFn: (body) => api.post('/active-ar-filters', body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['active-ar-filter-presets'] })
      setSavingPreset(false); setPresetNameDraft('')
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  const deletePreset = useMutation({
    mutationFn: (id) => api.delete(`/active-ar-filters/${id}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['active-ar-filter-presets'] }),
  })
  const setDefaultPreset = useMutation({
    mutationFn: (preset) => api.put(`/active-ar-filters/${preset.id}`,
                                      { name: preset.name,
                                        filters_json: preset.filters_json,
                                        is_default: true }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['active-ar-filter-presets'] }),
  })

  const [syncToast, setSyncToast] = useState(null)
  const [syncing, setSyncing] = useState(false)

  // Quick filter "Mine" / "Unassigned" use the assignedTo field as a sentinel
  const isMine = assignedTo === me && me
  const isUnassigned = assignedTo === '__none__'

  function setMine() {
    if (!me) return
    if (isMine) setAssignedTo('')
    else setAssignedTo(me)
    setPage(1)
  }
  function setUnassigned() {
    if (isUnassigned) setAssignedTo('')
    else setAssignedTo('__none__')
    setPage(1)
  }

  const { data: summary } = useQuery({
    queryKey: ['active-ar-summary'],
    queryFn: () => api.get('/active-ar/summary').then(r => r.data),
  })

  const { data, isLoading } = useQuery({
    queryKey: ['active-ar-claims', search, priority, ageBucket, workflowState,
               payer, plan, tfStatus, assignedTo, sort, includeAged, page],
    queryFn: () => api.get('/active-ar/claims', {
      params: {
        search, insurance_priority: priority, age_bucket: ageBucket,
        workflow_state: workflowState, payer, plan, tf_status: tfStatus,
        // map sentinels: '__none__' = no assignee
        assigned_to: assignedTo === '__none__' ? '__null__' : assignedTo,
        sort, include_aged: includeAged, page, per_page: 50,
      },
    }).then(r => r.data),
    enabled: view === 'table',
  })

  const { data: grouped, isLoading: groupedLoading } = useQuery({
    queryKey: ['active-ar-by-dos', workflowState],
    queryFn: () => api.get('/active-ar/claims/by-dos', {
      params: workflowState ? { workflow_state: workflowState } : {},
    }).then(r => r.data),
    enabled: view === 'dos',
  })

  const { data: assigneesData } = useQuery({
    queryKey: ['active-ar-assignees'],
    queryFn: () => api.get('/active-ar/assignees').then(r => r.data),
    staleTime: 5 * 60 * 1000,
  })

  function clearFilters() {
    setSearch(''); setPriority(''); setAgeBucket('')
    setWorkflowState(''); setPayer(''); setPlan(''); setTfStatus('')
    setAssignedTo(''); setIncludeAged(false); setPage(1)
  }

  const activeFilterCount = [search, priority, ageBucket, payer, plan, tfStatus, assignedTo, includeAged]
    .filter(v => v).length

  async function runBatchSync() {
    if (syncing) return
    if (!window.confirm('Sync Waystar status for up to 50 claims? Takes 1–2 min.')) return
    setSyncing(true); setSyncToast(null)
    try {
      const params = new URLSearchParams({ only_unchecked: 'true', max_count: '50' })
      if (workflowState) params.set('workflow_state', workflowState)
      if (payer) params.set('payer', payer)
      if (ageBucket) params.set('age_bucket', ageBucket)
      const res = await api.post(`/active-ar/sync-status-batch?${params}`)
      setSyncToast({ ok: true, ...res.data })
      qc.invalidateQueries()
    } catch (e) {
      setSyncToast({ ok: false, error: e?.response?.data?.detail || e.message })
    } finally {
      setSyncing(false)
      setActionsOpen(false)
    }
  }

  // Auto-dismiss sync toast after 6s
  useEffect(() => {
    if (!syncToast) return
    const t = setTimeout(() => setSyncToast(null), 6000)
    return () => clearTimeout(t)
  }, [syncToast])

  return (
    <div className="space-y-3">
      {/* HEADER */}
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="page-title">Active AR</h1>
          {summary && (
            <p className="text-sm text-gray-500 mt-0.5">
              {summary.open_count.toLocaleString()} open · {fmt.currency(summary.total_balance)} outstanding
              {data?.total != null && data.total !== summary.open_count && (
                <span className="text-gray-400"> · {data.total.toLocaleString()} in current view</span>
              )}
            </p>
          )}
        </div>
        <div className="flex gap-2 items-center relative">
          <button
            className="btn-secondary flex items-center gap-1 text-sm"
            onClick={() => setActionsOpen(o => !o)}
            onBlur={() => setTimeout(() => setActionsOpen(false), 200)}
          >
            <MoreHorizontal size={14} /> Actions <ChevronDown size={12} />
          </button>
          {actionsOpen && (
            <div className="absolute right-[180px] top-9 z-30 bg-white border border-border-subtle rounded-lg shadow-lg w-56 py-1">
              <ActionItem onClick={() => { setShowUpload(true); setActionsOpen(false) }}>
                <Upload size={14} /> Upload Unpaid Claims
              </ActionItem>
              <ActionItem onClick={() => { setShowEnrich(true); setActionsOpen(false) }}>
                <Upload size={14} /> Enrich from Charge Analysis
              </ActionItem>
              <ActionItem onClick={runBatchSync} disabled={syncing}>
                <RefreshCw size={14} className={syncing ? 'animate-spin' : ''} />
                {syncing ? 'Syncing…' : 'Sync Waystar (50)'}
              </ActionItem>
            </div>
          )}
          <button className="btn-primary flex items-center gap-1 text-sm" onClick={() => navigate('/active-ar/post-payment')}>
            <Plus size={14} /> Post Payment
          </button>
        </div>
      </div>

      {/* SYNC TOAST (auto-dismisses) */}
      {syncToast && (
        <div className={`fixed top-20 right-6 z-50 max-w-md text-xs rounded-lg shadow-lg px-3 py-2 border
                         ${syncToast.ok ? 'bg-green-50 border-green-200 text-green-800' : 'bg-red-50 border-red-200 text-red-700'}`}>
          {syncToast.ok ? (
            <>✓ Synced {syncToast.synced_count} · {syncToast.era_attached_count} ERA{syncToast.era_attached_count === 1 ? '' : 's'} attached
              {syncToast.errors?.length > 0 && <> · {syncToast.errors.length} errors</>}</>
          ) : (
            <>Sync failed: {syncToast.error}</>
          )}
        </div>
      )}

      {/* SUMMARY CHIPS — 6 high-value */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
          <SummaryChip
            label="Open" tone="blue"
            value={summary.open_count.toLocaleString()}
            sub={fmt.currency(summary.total_balance)}
            active={!tfStatus && !assignedTo}
            onClick={clearFilters}
          />
          <SummaryChip
            label="TF Past" tone="red"
            value={summary.tf_buckets?.past.count.toLocaleString() || '0'}
            sub={fmt.currency(summary.tf_buckets?.past.balance || 0)}
            active={tfStatus === 'past'}
            onClick={() => { setTfStatus('past'); setPage(1) }}
          />
          <SummaryChip
            label="TF Urgent ≤14d" tone="red"
            value={summary.tf_buckets?.urgent.count.toLocaleString() || '0'}
            sub={fmt.currency(summary.tf_buckets?.urgent.balance || 0)}
            active={tfStatus === 'urgent'}
            onClick={() => { setTfStatus('urgent'); setSort('tf_asc'); setPage(1) }}
          />
          <SummaryChip
            label="TF Soon 15–30d" tone="amber"
            value={summary.tf_buckets?.soon.count.toLocaleString() || '0'}
            sub={fmt.currency(summary.tf_buckets?.soon.balance || 0)}
            active={tfStatus === 'soon'}
            onClick={() => { setTfStatus('soon'); setSort('tf_asc'); setPage(1) }}
          />
          <SummaryChip
            label="Mine" tone="indigo"
            value={isMine ? (data?.total ?? '—').toLocaleString() : '—'}
            sub={me ? me.split('@')[0] : 'sign in to use'}
            active={isMine}
            onClick={setMine}
          />
          <SummaryChip
            label="Unassigned" tone="gray"
            value={isUnassigned ? (data?.total ?? '—').toLocaleString() : '—'}
            sub="needs assignment"
            active={isUnassigned}
            onClick={setUnassigned}
          />
        </div>
      )}

      {/* WORKFLOW TABS */}
      <div className="flex gap-1 border-b border-border-subtle">
        {QUICK_TABS.map(t => (
          <button
            key={t.key}
            onClick={() => { setWorkflowState(t.key); setPage(1) }}
            className={`px-3 py-2 text-sm border-b-2 -mb-px transition-colors ${
              workflowState === t.key
                ? 'border-plum-700 text-plum-700 font-medium'
                : 'border-transparent text-gray-500 hover:text-gray-800'
            }`}
          >
            {t.label}
            {summary?.by_workflow_state?.[t.key] != null && (
              <span className="ml-1 text-[10px] text-gray-400">({summary.by_workflow_state[t.key]})</span>
            )}
          </button>
        ))}
      </div>

      {/* Saved filter presets — chip bar */}
      {(presets?.length ?? 0) > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[11px] uppercase tracking-wide text-gray-500 mr-1">Saved:</span>
          {presets.map(p => (
            <PresetChip key={p.id} preset={p}
                        onLoad={() => applyFilters(p.filters_json || {})}
                        onSetDefault={() => setDefaultPreset.mutate(p)}
                        onDelete={() => {
                          if (window.confirm(`Delete preset "${p.name}"?`)) deletePreset.mutate(p.id)
                        }} />
          ))}
        </div>
      )}

      {/* COMPACT FILTER BAR */}
      <div className="card flex gap-2 items-center flex-wrap py-2 px-3">
        <div className="relative flex-1 min-w-[220px]">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            className="input pl-9 text-sm py-1.5"
            placeholder="Search claim #, patient, chart #, policy #…"
            value={search}
            onChange={e => { setSearch(e.target.value); setPage(1) }}
          />
        </div>
        <select className="input text-sm py-1.5 w-44" aria-label="Assignee filter" value={assignedTo} onChange={e => { setAssignedTo(e.target.value); setPage(1) }}>
          <option value="">All assignees</option>
          {me && <option value={me}>👤 Mine ({me.split('@')[0]})</option>}
          <option value="__none__">— Unassigned —</option>
          {assigneesData?.assignees?.filter(a => a.email !== me).map(a => (
            <option key={a.email} value={a.email}>{(a.display_name || a.email).slice(0, 28)}</option>
          ))}
        </select>
        <select className="input text-sm py-1.5 w-44" aria-label="Sort claims by" value={sort} onChange={e => setSort(e.target.value)} disabled={view === 'dos'}>
          {SORTS.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select>
        <button
          className={`btn-secondary text-sm py-1.5 px-2 flex items-center gap-1 ${showAdvanced ? 'bg-gray-100' : ''}`}
          onClick={() => setShowAdvanced(o => !o)}
        >
          <SlidersHorizontal size={13} />
          More filters
          {activeFilterCount > 0 && (
            <span className="ml-1 bg-plum-700 text-white text-[10px] rounded-full px-1.5 py-0">
              {activeFilterCount}
            </span>
          )}
        </button>
        <button
          type="button"
          className="text-xs text-plum-600 hover:underline flex items-center gap-1 px-1"
          onClick={() => setSavingPreset(true)}
          title="Save current filters as a named preset"
        >
          <Save size={11} /> Save Preset
        </button>
        <div className="flex border border-border-subtle rounded overflow-hidden">
          <button
            className={`text-xs px-2 py-1.5 ${view === 'table' ? 'bg-gray-100 text-gray-900' : 'text-gray-500 hover:bg-gray-50'}`}
            onClick={() => setView('table')}
            title="Flat table"
          >
            Table
          </button>
          <button
            className={`text-xs px-2 py-1.5 flex items-center gap-1 ${view === 'dos' ? 'bg-gray-100 text-gray-900' : 'text-gray-500 hover:bg-gray-50'}`}
            onClick={() => setView('dos')}
            title="Group by patient + DOS"
          >
            <Layers size={12} /> By DOS
          </button>
        </div>
      </div>

      {/* ADVANCED FILTERS DRAWER */}
      {showAdvanced && (
        <div className="card flex gap-2 items-center flex-wrap py-2 px-3 bg-gray-50/50">
          <select className="input text-sm py-1.5 w-32" value={priority} onChange={e => { setPriority(e.target.value); setPage(1) }}>
            {PRIORITIES.map(p => <option key={p} value={p}>{p || 'All Priority'}</option>)}
          </select>
          <select className="input text-sm py-1.5 w-32" value={ageBucket} onChange={e => { setAgeBucket(e.target.value); setPage(1) }}>
            {AGE_BUCKETS.map(b => <option key={b} value={b}>{b ? `Age ${b}d` : 'All ages'}</option>)}
          </select>
          <select className="input text-sm py-1.5 w-44" value={workflowState} onChange={e => { setWorkflowState(e.target.value); setPage(1) }}>
            {WORKFLOW_STATES.map(s => <option key={s} value={s}>{s ? s.replace(/_/g, ' ') : 'All workflow states'}</option>)}
          </select>
          <select className="input text-sm py-1.5 w-52" value={payer} onChange={e => { setPayer(e.target.value); setPage(1) }}>
            <option value="">All payers</option>
            {summary?.top_payers?.map(p => (
              <option key={p.payer} value={p.payer}>{p.payer.length > 32 ? p.payer.slice(0, 32) + '…' : p.payer} ({p.count})</option>
            ))}
          </select>
          <select className="input text-sm py-1.5 w-52" value={plan} onChange={e => { setPlan(e.target.value); setPage(1) }}>
            <option value="">All plans</option>
            {summary?.top_plans?.map(p => (
              <option key={p.plan} value={p.plan}>{p.plan.length > 32 ? p.plan.slice(0, 32) + '…' : p.plan} ({p.count})</option>
            ))}
          </select>
          <select className="input text-sm py-1.5 w-56" value={tfStatus} onChange={e => { setTfStatus(e.target.value); setPage(1) }}>
            {TF_STATUSES.map(t => <option key={t.key} value={t.key}>{t.label}</option>)}
          </select>
          <label className="flex items-center gap-1 text-xs text-gray-700 cursor-pointer ml-2">
            <input type="checkbox" checked={includeAged}
                   onChange={e => { setIncludeAged(e.target.checked); setPage(1) }} />
            Include claims &gt; 2 years old
          </label>
        </div>
      )}

      {/* SAVE-PRESET inline form */}
      {savingPreset && (
        <div className="card flex items-end gap-2 py-2 px-3 bg-gray-50/50">
          <div className="flex-1 max-w-xs">
            <label className="text-[11px] uppercase text-gray-500 tracking-wide block mb-1">Preset name</label>
            <input className="input text-sm w-full py-1.5"
                   autoFocus
                   placeholder="e.g. My past-TF BCBS claims"
                   value={presetNameDraft}
                   onChange={e => setPresetNameDraft(e.target.value)}
                   onKeyDown={e => {
                     if (e.key === 'Enter' && presetNameDraft.trim())
                       savePreset.mutate({ name: presetNameDraft.trim(),
                                           filters_json: currentFilters,
                                           is_default: false })
                     else if (e.key === 'Escape') {
                       setSavingPreset(false); setPresetNameDraft('')
                     }
                   }} />
          </div>
          <button type="button"
                  className="btn-primary text-xs py-1.5 px-3 flex items-center gap-1"
                  disabled={!presetNameDraft.trim() || savePreset.isPending}
                  onClick={() => savePreset.mutate({ name: presetNameDraft.trim(),
                                                      filters_json: currentFilters,
                                                      is_default: false })}>
            <Save size={11}/> Save
          </button>
          <button type="button"
                  className="text-xs text-gray-500 hover:underline"
                  onClick={() => { setSavingPreset(false); setPresetNameDraft('') }}>
            Cancel
          </button>
        </div>
      )}

      {/* ACTIVE FILTER CHIPS (removable) */}
      {activeFilterCount > 0 && (
        <div className="flex flex-wrap gap-1.5 items-center text-xs">
          <span className="text-gray-500">Filters:</span>
          {search && <FilterChip label={`Search: ${search}`} onRemove={() => { setSearch(''); setPage(1) }} />}
          {priority && <FilterChip label={`Priority: ${priority}`} onRemove={() => { setPriority(''); setPage(1) }} />}
          {ageBucket && <FilterChip label={`Age: ${ageBucket}d`} onRemove={() => { setAgeBucket(''); setPage(1) }} />}
          {payer && <FilterChip label={`Payer: ${payer.length > 25 ? payer.slice(0, 25) + '…' : payer}`} onRemove={() => { setPayer(''); setPage(1) }} />}
          {plan && <FilterChip label={`Plan: ${plan.length > 25 ? plan.slice(0, 25) + '…' : plan}`} onRemove={() => { setPlan(''); setPage(1) }} />}
          {tfStatus && <FilterChip label={`TF: ${TF_STATUSES.find(t => t.key === tfStatus)?.label || tfStatus}`} onRemove={() => { setTfStatus(''); setPage(1) }} />}
          {assignedTo && <FilterChip label={`Assigned: ${isMine ? 'Mine' : isUnassigned ? 'Unassigned' : assignedTo.split('@')[0]}`} onRemove={() => { setAssignedTo(''); setPage(1) }} />}
          {includeAged && <FilterChip label="Including > 2-year claims" onRemove={() => { setIncludeAged(false); setPage(1) }} />}
          <button className="text-gray-500 hover:text-gray-800 underline ml-1" onClick={clearFilters}>Clear all</button>
        </div>
      )}

      {/* TOP PAYERS — collapsed by default */}
      {summary?.top_payers?.length > 0 && (
        <div className="card py-2 px-3">
          <button
            className="w-full flex items-center justify-between text-left"
            onClick={() => setShowPayers(o => !o)}
          >
            <span className="text-xs uppercase text-gray-500 flex items-center gap-1">
              {showPayers ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              Top Payers by Open Balance
            </span>
            <span className="text-[10px] text-gray-400">{summary.top_payers.length} payers</span>
          </button>
          {showPayers && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {summary.top_payers.slice(0, 12).map(p => (
                <button
                  key={p.payer}
                  onClick={() => { setPayer(p.payer); setPage(1) }}
                  className={`text-xs px-2 py-1 rounded border ${payer === p.payer ? 'bg-plum-700 text-white border-plum-700' : 'bg-gray-50 hover:bg-gray-100'}`}
                  title={`${p.count} claims · ${fmt.currency(p.balance)}`}
                >
                  <span className="font-medium">{p.payer.length > 30 ? p.payer.slice(0, 30) + '…' : p.payer}</span>
                  <span className="ml-1.5 text-gray-500">{p.count} · {fmt.currency(p.balance)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* WORKFLOW SPLIT + NEEDS YOUR ATTENTION
          Click any row to apply that filter to the table below. */}
      {summary && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
          {/* Workflow split */}
          <div className="card py-2 px-3">
            <h2 className="text-xs uppercase text-gray-500 mb-2">Workflow Split</h2>
            {summary.by_workflow_state ? (
              <div className="text-[12px] text-ink">
                {Object.entries(summary.by_workflow_state).map(([state, count]) => (
                  <div key={state}
                       className="py-1 border-b border-border-subtle last:border-b-0 flex justify-between items-center cursor-pointer hover:text-plum-700"
                       onClick={() => { setWorkflowState(state); setPage(1) }}>
                    <span className="capitalize">{state.replace(/_/g, ' ')}</span>
                    <span className="font-medium font-mono">{count.toLocaleString()}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-[12px] text-muted py-3 text-center">—</div>
            )}
          </div>

          {/* Needs your attention */}
          <div className="card py-2 px-3">
            <h2 className="text-xs uppercase text-gray-500 mb-2">Needs your attention</h2>
            <div className="text-[12px] text-ink">
              <div className="py-1 border-b border-border-subtle flex justify-between items-center cursor-pointer hover:text-plum-700"
                   onClick={() => { setTfStatus('urgent'); setPage(1) }}>
                <span>Claims with TF deadline ≤14 days</span>
                <span className={`font-medium font-mono ${(summary.tf_buckets?.urgent?.count || 0) > 0 ? 'text-red-600' : ''}`}>
                  {summary.tf_buckets?.urgent?.count || 0}
                </span>
              </div>
              <div className="py-1 border-b border-border-subtle flex justify-between items-center cursor-pointer hover:text-plum-700"
                   onClick={() => { setTfStatus('soon'); setPage(1) }}>
                <span>Claims with TF deadline 15–30 days</span>
                <span className={`font-medium font-mono ${(summary.tf_buckets?.soon?.count || 0) > 0 ? 'text-amber-600' : ''}`}>
                  {summary.tf_buckets?.soon?.count || 0}
                </span>
              </div>
              <div className="py-1 border-b border-border-subtle flex justify-between items-center cursor-pointer hover:text-plum-700"
                   onClick={() => { setWorkflowState('denied'); setPage(1) }}>
                <span>Denied (in active workflow)</span>
                <span className="font-medium font-mono">{summary.by_workflow_state?.denied || 0}</span>
              </div>
              <div className="py-1 flex justify-between items-center cursor-pointer hover:text-plum-700"
                   onClick={() => { setWorkflowState('appealed'); setPage(1) }}>
                <span>Appealed (pending response)</span>
                <span className="font-medium font-mono">{summary.by_workflow_state?.appealed || 0}</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* DOS-GROUPED VIEW */}
      {view === 'dos' && (
        <div className="card p-0 overflow-hidden">
          <div className="px-3 py-2 border-b border-border-subtle text-xs text-gray-500">
            {groupedLoading ? 'Loading…' : `${grouped?.group_count?.toLocaleString() || 0} unique DOS groups across ${grouped?.groups?.reduce((s,g)=>s+g.claims.length,0).toLocaleString() || 0} claims`}
          </div>
          <div className="divide-y divide-gray-100 max-h-[70vh] overflow-y-auto">
            {grouped?.groups?.map(g => (
              <div key={`${g.patient_external_id}-${g.dos}`} className="px-3 py-2 hover:bg-gray-50">
                <div className="flex items-baseline gap-2 mb-1">
                  <span className="text-[11px] uppercase text-gray-400">DOS</span>
                  <span className="font-semibold text-gray-800 text-sm">{fmt.date(g.dos)}</span>
                  <span className="text-xs text-gray-500">·</span>
                  <span className="font-medium text-gray-700 text-sm">{g.patient_name}</span>
                  <span className="text-xs text-gray-400 font-mono">#{g.patient_external_id}</span>
                  <span className="ml-auto text-xs text-gray-500">
                    Bal <span className={`font-mono font-semibold ${g.total_balance > 0 ? 'text-red-600' : 'text-gray-500'}`}>{fmt.currency(g.total_balance)}</span>
                  </span>
                </div>
                <div className="ml-5 space-y-0.5">
                  {g.claims.map(c => {
                    const pri = PRIORITY_BADGE[c.insurance_priority] || PRIORITY_BADGE.Primary
                    return (
                      <div key={c.id} className="flex items-center gap-2 text-xs cursor-pointer hover:underline"
                           onClick={() => navigate(`/active-ar/${c.id}`)}>
                        <span className={`px-1 py-0.5 text-[11px] font-bold rounded ${pri.cls}`}>{pri.label}</span>
                        <span className="font-mono text-plum-700">{c.claim_number}</span>
                        <span className="text-gray-600 truncate max-w-[260px]">{c.insurance_company}</span>
                        <span className={`font-mono font-semibold ml-auto ${c.insurance_balance > 0 ? 'text-red-600' : 'text-gray-500'}`}>
                          {fmt.currency(c.insurance_balance)}
                        </span>
                        <span className={`text-[11px] px-1.5 py-0.5 rounded border ${STATE_BADGE[c.workflow_state] || 'bg-gray-50 border-gray-200'}`}>
                          {c.workflow_state.replace(/_/g, ' ')}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* TABLE VIEW */}
      {view === 'table' && (
        <div className="card p-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200 sticky top-0 z-10">
                <tr>
                  <th className="table-th px-2 py-1.5 w-2"></th>{/* fresh dot */}
                  <th className="table-th px-2 py-1.5">Claim #</th>
                  <th className="table-th px-1 py-1.5">P</th>
                  <th className="table-th px-2 py-1.5">Patient</th>
                  <th className="table-th px-2 py-1.5">DOS</th>
                  <th className="table-th px-2 py-1.5 text-right">Age</th>
                  <th className="table-th px-1 py-1.5 text-center">TF</th>
                  <th className="table-th px-2 py-1.5">Payer / Policy</th>
                  <th className="table-th px-2 py-1.5 text-right">Billed</th>
                  <th className="table-th px-2 py-1.5 text-right">Paid</th>
                  <th className="table-th px-2 py-1.5 text-right">Balance</th>
                  <th className="table-th px-2 py-1.5">Workflow</th>
                  <th className="table-th px-2 py-1.5">Assigned</th>
                </tr>
              </thead>
              <tbody>
                {isLoading && (
                  <tr><td colSpan={13} className="table-td text-center text-gray-400 py-6">Loading…</td></tr>
                )}
                {!isLoading && data?.claims?.length === 0 && (
                  <tr>
                    <td colSpan={13} className="table-td">
                      <EmptyState
                        icon={SearchX}
                        title="No claims match these filters"
                        body="Try clearing a filter or widening the age bucket."
                        compact
                      />
                    </td>
                  </tr>
                )}
                {data?.claims?.map(c => {
                  const pri = PRIORITY_BADGE[c.insurance_priority] || PRIORITY_BADGE.Primary
                  return (
                    <React.Fragment key={c.id}>
                    <tr className="border-t border-gray-100 cursor-pointer hover:bg-gray-50" onClick={() => navigate(`/active-ar/${c.id}`)}>
                      <td className="px-2 py-1">
                        {isFresh(c) && <span className="block w-1.5 h-1.5 rounded-full bg-blue-500" title="Updated in last 24h"></span>}
                      </td>
                      <td className="px-2 py-1 font-mono text-xs font-medium text-plum-700">{c.claim_number}</td>
                      <td className="px-1 py-1">
                        <span className={`px-1 py-0 text-[11px] font-bold rounded ${pri.cls}`}>{pri.label}</span>
                      </td>
                      <td className="px-2 py-1 text-xs">
                        <div className="font-medium text-gray-900">{c.patient_name || '—'}</div>
                        <div className="text-[10px] text-gray-500 font-mono">
                          #{c.patient_external_id}
                          {c.patient_dob && <> · {fmt.date(c.patient_dob)}</>}
                        </div>
                      </td>
                      <td className="px-2 py-1 text-xs whitespace-nowrap">{fmt.date(c.dos)}</td>
                      <td className={`px-2 py-1 text-right text-xs ${c.age_days > 90 ? 'text-red-600 font-semibold' : c.age_days > 60 ? 'text-amber-600' : 'text-gray-600'}`}>
                        {c.age_days != null ? `${c.age_days}d` : '—'}
                      </td>
                      <td className="px-1 py-1 text-center" title={c.tf_status === 'past' ? 'Past TF deadline' : c.tf_status === 'urgent' ? `${c.days_until_tf_deadline}d remaining` : c.tf_status === 'soon' ? `${c.days_until_tf_deadline}d remaining` : `${c.days_until_tf_deadline}d remaining`}>
                        <span className={`inline-block w-2 h-2 rounded-full ${TF_DOT[c.tf_status] || TF_DOT.unknown}`}></span>
                      </td>
                      <td className="px-2 py-1 text-xs max-w-[200px]">
                        <div className="truncate" title={c.insurance_company}>{c.insurance_company || '—'}</div>
                        <div className="text-[10px] text-gray-400 truncate font-mono" title={c.policy_number}>{c.policy_number || ''}</div>
                      </td>
                      <td className="px-2 py-1 text-right font-mono text-xs">{fmt.currency(c.total_charges || c.claim_amount)}</td>
                      <td className="px-2 py-1 text-right font-mono text-xs text-green-700">{fmt.currency(c.paid_amount)}</td>
                      <td className={`px-2 py-1 text-right font-mono text-xs font-semibold ${c.insurance_balance > 0 ? 'text-red-600' : 'text-gray-500'}`}>
                        {fmt.currency(c.insurance_balance)}
                      </td>
                      <td className="px-2 py-1">
                        <span className={`text-[11px] px-1.5 py-0.5 rounded border ${STATE_BADGE[c.workflow_state] || 'bg-gray-50 border-gray-200'}`}>
                          {c.workflow_state.replace(/_/g, ' ')}
                        </span>
                      </td>
                      <td className="px-2 py-1 text-xs text-gray-600" onClick={e => e.stopPropagation()}>
                        <InlineAssigneePicker
                          claimId={c.id}
                          currentAssignee={c.assigned_to}
                          assignees={assigneesData?.assignees || []}
                          onChanged={() => qc.invalidateQueries()}
                        />
                      </td>
                    </tr>
                    {c.latest_note && (
                      <tr className="border-t-0 cursor-pointer hover:bg-amber-50/40 bg-amber-50/20"
                          onClick={() => navigate(`/active-ar/${c.id}`)}>
                        <td colSpan={13} className="px-3 py-1.5">
                          <div className="text-[11px] text-gray-700 flex items-baseline gap-1.5">
                            <MessageSquare size={10} className="text-amber-700 shrink-0 mt-0.5" />
                            <span className="text-[10px] text-gray-500 shrink-0">
                              {c.latest_note.user?.split('@')[0] || 'unknown'}
                              {' · '}
                              {fmt.date(c.latest_note.created_at)}
                              :
                            </span>
                            <span className="italic whitespace-pre-wrap line-clamp-2">
                              {c.latest_note.note}
                            </span>
                          </div>
                        </td>
                      </tr>
                    )}
                    </React.Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>

          {data && data.total > 50 && (
            <div className="border-t border-border-subtle px-3 py-2 flex items-center justify-between text-xs text-gray-500">
              <span>Page {page} of {Math.ceil(data.total / 50)} · {data.total.toLocaleString()} total</span>
              <div className="flex gap-2">
                <button className="btn-secondary py-1 px-2 text-xs" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}>Prev</button>
                <button className="btn-secondary py-1 px-2 text-xs" onClick={() => setPage(p => p + 1)} disabled={page >= Math.ceil(data.total / 50)}>Next</button>
              </div>
            </div>
          )}
        </div>
      )}

      {showUpload && <UploadModal onClose={() => setShowUpload(false)} onDone={() => qc.invalidateQueries()} />}
      {showEnrich && <EnrichModal onClose={() => setShowEnrich(false)} onDone={() => qc.invalidateQueries()} />}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────
// Sub-components

function ActionItem({ children, ...props }) {
  return (
    <button
      type="button"
      className="w-full px-3 py-2 text-sm flex items-center gap-2 hover:bg-gray-50 disabled:opacity-50 text-left"
      {...props}
    >
      {children}
    </button>
  )
}

function FilterChip({ label, onRemove }) {
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-100">
      {label}
      <button onClick={onRemove} className="hover:bg-blue-100 rounded p-0.5">
        <X size={10} />
      </button>
    </span>
  )
}

function SummaryChip({ label, value, sub, tone, onClick, active }) {
  const tones = {
    blue:    'bg-blue-50 border-blue-200 text-blue-700',
    red:     'bg-red-50 border-red-200 text-red-700',
    amber:   'bg-amber-50 border-amber-200 text-amber-700',
    indigo:  'bg-indigo-50 border-indigo-200 text-indigo-700',
    gray:    'bg-gray-50 border-gray-200 text-gray-700',
  }
  const ringActive = active ? 'ring-2 ring-offset-1 ring-current' : ''
  return (
    <button
      onClick={onClick}
      disabled={!onClick}
      className={`text-left rounded-lg border p-2 ${tones[tone] || tones.gray} ${onClick ? 'hover:shadow' : 'cursor-default'} ${ringActive}`}
    >
      <div className="text-[11px] uppercase tracking-wide opacity-75">{label}</div>
      <div className="text-xl font-bold leading-tight">{value}</div>
      <div className="text-[10px] opacity-65 mt-0.5 truncate">{sub}</div>
    </button>
  )
}

function InlineAssigneePicker({ claimId, currentAssignee, assignees, onChanged }) {
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)

  if (!editing) {
    return (
      <button
        className="text-xs text-gray-600 hover:text-plum-700 hover:underline"
        onClick={() => setEditing(true)}
        title="Click to reassign"
      >
        {currentAssignee ? currentAssignee.split('@')[0] : <span className="text-gray-400">— assign —</span>}
      </button>
    )
  }
  return (
    <select
      autoFocus
      className="input text-xs py-0.5 w-full"
      value={currentAssignee || ''}
      disabled={busy}
      onBlur={() => setEditing(false)}
      onChange={async e => {
        const v = e.target.value
        setBusy(true)
        try {
          await api.patch(`/active-ar/claims/${claimId}`, { assigned_to: v })
          onChanged?.()
        } finally { setBusy(false); setEditing(false) }
      }}
    >
      <option value="">— Unassigned —</option>
      {assignees.map(a => (
        <option key={a.email} value={a.email}>{a.display_name || a.email}</option>
      ))}
    </select>
  )
}

// ─────────────────────────────────────────────────────────────────────
// Modals (unchanged)

function EnrichModal({ onClose, onDone }) {
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  async function handleUpload() {
    if (!file) return
    setBusy(true); setError(null)
    const fd = new FormData()
    fd.append('file', file)
    try {
      const res = await api.post('/active-ar/enrich-from-charge-analysis', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setResult(res.data)
      onDone?.()
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Enrich from Charge Analysis</h2>
          <button onClick={onClose}><X size={18} className="text-gray-500" /></button>
        </div>

        {!result && (
          <>
            <p className="text-sm text-gray-600 mb-4">
              Upload a Greenway "Charge Analysis" XLS. Existing Active AR claims will be enriched
              with procedure codes, dx codes, provider NPIs, secondary insurance, and DOB.
              <strong className="block mt-2">Only matching claims (by patient + DOS) are updated. New claims won't be created.</strong>
            </p>
            <input
              type="file" accept=".xls,.xlsx"
              onChange={e => setFile(e.target.files?.[0] || null)}
              className="block w-full text-sm mb-3"
            />
            {error && <div className="text-red-600 text-xs mb-3">Error: {error}</div>}
            <div className="flex gap-2 justify-end">
              <button className="btn-secondary" onClick={onClose}>Cancel</button>
              <button className="btn-primary" onClick={handleUpload} disabled={!file || busy}>
                {busy ? 'Processing…' : 'Enrich'}
              </button>
            </div>
          </>
        )}

        {result && (
          <div className="space-y-2 text-sm">
            <div className="font-medium">Enrichment complete:</div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              <div>Total source rows:</div>            <div className="font-mono">{result.total_rows.toLocaleString()}</div>
              <div>Distinct visits in file:</div>      <div className="font-mono">{result.visits_in_file.toLocaleString()}</div>
              <div>Active claim records updated:</div> <div className="font-mono text-green-700">{result.matched_claim_records.toLocaleString()}</div>
              <div>Visits not in active AR:</div>      <div className="font-mono text-gray-500">{result.unmatched_visits.toLocaleString()}</div>
            </div>
            <div className="flex justify-end pt-3">
              <button className="btn-primary" onClick={onClose}>Done</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function UploadModal({ onClose, onDone }) {
  const [file, setFile] = useState(null)
  const [markMissing, setMarkMissing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  async function handleUpload() {
    if (!file) return
    setBusy(true); setError(null)
    const fd = new FormData()
    fd.append('file', file)
    try {
      const res = await api.post(`/active-ar/upload?mark_missing_as_closed=${markMissing}`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setResult(res.data)
      onDone?.()
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Upload Unpaid Claims</h2>
          <button onClick={onClose}><X size={18} className="text-gray-500" /></button>
        </div>

        {!result && (
          <>
            <p className="text-sm text-gray-600 mb-4">
              Upload the Greenway "Unpaid Claims" XLS export. Existing claims will have their balance/status updated; locally-managed fields (workflow state, assignment, notes) are preserved.
            </p>
            <input
              type="file" accept=".xls,.xlsx"
              onChange={e => setFile(e.target.files?.[0] || null)}
              className="block w-full text-sm mb-3"
            />
            <label className="flex items-center gap-2 text-xs mb-4">
              <input type="checkbox" checked={markMissing} onChange={e => setMarkMissing(e.target.checked)} />
              <span>Auto-close claims that aren't in this export (use only for full snapshots)</span>
            </label>
            {error && <div className="text-red-600 text-xs mb-3">Error: {error}</div>}
            <div className="flex gap-2 justify-end">
              <button className="btn-secondary" onClick={onClose}>Cancel</button>
              <button className="btn-primary" onClick={handleUpload} disabled={!file || busy}>
                {busy ? 'Uploading…' : 'Upload'}
              </button>
            </div>
          </>
        )}

        {result && (
          <div className="space-y-2 text-sm">
            <div className="font-medium">Import complete:</div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              <div>Total rows in file:</div>           <div className="font-mono">{result.total_rows.toLocaleString()}</div>
              <div>New claims created:</div>           <div className="font-mono text-green-700">{result.new_claims.toLocaleString()}</div>
              <div>Existing claims updated:</div>      <div className="font-mono">{result.updated_claims.toLocaleString()}</div>
              <div>Unchanged:</div>                    <div className="font-mono text-gray-500">{result.unchanged.toLocaleString()}</div>
              <div>Auto-closed (not in export):</div>  <div className="font-mono">{result.closed_claims.toLocaleString()}</div>
              <div>Errors:</div>                       <div className="font-mono text-red-600">{result.error_count}</div>
            </div>
            <div className="flex justify-end pt-3">
              <button className="btn-primary" onClick={onClose}>Done</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}


function PresetChip({ preset, onLoad, onSetDefault, onDelete }) {
  return (
    <div className={`group inline-flex items-center text-[11px] rounded-full border px-2 py-0.5 ${
      preset.is_default
        ? 'border-plum-300 bg-plum-50 text-plum-700'
        : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50'
    }`}>
      <button type="button"
              onClick={onLoad}
              title="Load this preset"
              className="flex items-center gap-1 pr-1">
        {preset.is_default && <Star size={10} className="text-plum-600 fill-plum-600" />}
        {preset.name}
      </button>
      {!preset.is_default && (
        <button type="button"
                onClick={onSetDefault}
                title="Set as default (auto-loads on next visit)"
                className="text-gray-400 hover:text-plum-600 px-1 opacity-0 group-hover:opacity-100">
          <Star size={10} />
        </button>
      )}
      <button type="button"
              onClick={onDelete}
              title="Delete preset"
              className="text-gray-400 hover:text-red-600 pl-1 opacity-0 group-hover:opacity-100">
        <Trash2 size={10} />
      </button>
    </div>
  )
}
