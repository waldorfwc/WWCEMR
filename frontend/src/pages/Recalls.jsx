import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Phone, ChevronRight, Clock, AlertTriangle, X, Save, Eye,
  TrendingUp, Users, Calendar, CheckCircle2, RefreshCw,
  Upload, FileSpreadsheet, Lock, Star, Trash2,
} from 'lucide-react'
import { useCurrentUser } from '../hooks/useCurrentUser'
import api, { fmt } from '../utils/api'


const PRIORITY_BADGE = {
  1: 'bg-red-100 text-red-700 border-red-200',
  2: 'bg-blue-50 text-blue-700 border-blue-100',
  3: 'bg-gray-100 text-gray-600 border-gray-200',
}

const STATUS_TONE = {
  active:     'bg-green-50 text-green-700',
  suppressed: 'bg-gray-100 text-gray-500',
  completed:  'bg-blue-50 text-blue-700',
}


export default function Recalls() {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [recallType, setRecallType] = useState('')
  const [statusFilter, setStatusFilter] = useState('active')
  const [sort, setSort] = useState('recently_due_desc')
  const [page, setPage] = useState(1)
  const [includeCooldown, setIncludeCooldown] = useState(false)
  const [openId, setOpenId] = useState(null)

  // Saved filter presets (per-user)
  const [savingPreset, setSavingPreset] = useState(false)
  const [presetNameDraft, setPresetNameDraft] = useState('')
  const currentFilters = {
    search, recallType, statusFilter, sort, includeCooldown,
  }
  function applyFilters(f) {
    setSearch(f.search ?? '')
    setRecallType(f.recallType ?? '')
    setStatusFilter(f.statusFilter ?? 'active')
    setSort(f.sort ?? 'recently_due_desc')
    setIncludeCooldown(!!f.includeCooldown)
    setPage(1)
  }

  const { data: presets } = useQuery({
    queryKey: ['recall-filter-presets'],
    queryFn: () => api.get('/recall-filters').then(r => r.data),
  })

  // Auto-load the default preset once on first mount
  const [defaultApplied, setDefaultApplied] = useState(false)
  useEffect(() => {
    if (defaultApplied || !presets) return
    const def = presets.find(p => p.is_default)
    if (def) applyFilters(def.filters_json || {})
    setDefaultApplied(true)
  }, [presets, defaultApplied])

  const savePreset = useMutation({
    mutationFn: (body) => api.post('/recall-filters', body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['recall-filter-presets'] })
      setSavingPreset(false); setPresetNameDraft('')
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  const deletePreset = useMutation({
    mutationFn: (id) => api.delete(`/recall-filters/${id}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['recall-filter-presets'] }),
  })
  const setDefaultPreset = useMutation({
    mutationFn: (preset) => api.put(`/recall-filters/${preset.id}`,
                                      { name: preset.name,
                                        filters_json: preset.filters_json,
                                        is_default: true }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['recall-filter-presets'] }),
  })

  const { data: dash } = useQuery({
    queryKey: ['recalls-dash'],
    queryFn: () => api.get('/recalls/dashboard/stats').then(r => r.data),
    staleTime: 60_000,
  })

  const { data, isLoading } = useQuery({
    queryKey: ['recalls', search, recallType, statusFilter, sort, page, includeCooldown],
    queryFn: () => api.get('/recalls', { params: {
      search, recall_type: recallType, status: statusFilter,
      sort, page, per_page: 50, include_cooldown: includeCooldown,
    } }).then(r => r.data),
  })

  const recalls = data?.recalls || []
  const total = data?.total || 0

  return (
    <div>
      <div className="mb-4 flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Recalls</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Patients due for follow-up. Click the phone number to dial via RingCentral, then log the outcome.
          </p>
        </div>
        <ModMedImportButton />
      </div>

      <DashboardStrip dash={dash} />

      {/* Saved filter presets — chip bar */}
      {(presets?.length ?? 0) > 0 && (
        <div className="flex items-center gap-1.5 mb-2 flex-wrap">
          <span className="text-[10px] uppercase tracking-wide text-gray-500 mr-1">Saved:</span>
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

      {/* Filter bar */}
      <div className="card mb-3">
        <div className="flex flex-wrap gap-2 items-end">
          <div className="flex-1 min-w-[260px]">
            <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Search</label>
            <input
              className="input text-sm w-full"
              placeholder="Name, chart #, or phone…"
              value={search}
              onChange={e => { setSearch(e.target.value); setPage(1) }}
            />
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Status</label>
            <select className="input text-sm" aria-label="Status" value={statusFilter}
                    onChange={e => { setStatusFilter(e.target.value); setPage(1) }}>
              <option value="active">Active queue</option>
              <option value="completed">Completed (not due)</option>
              <option value="suppressed">Suppressed</option>
              <option value="all">All</option>
            </select>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Recall type</label>
            <input className="input text-sm w-44"
                   value={recallType}
                   onChange={e => { setRecallType(e.target.value); setPage(1) }}
                   placeholder="e.g. Est - Well-Woman Exam" />
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Sort</label>
            <select className="input text-sm" aria-label="Sort recalls by" value={sort}
                    onChange={e => setSort(e.target.value)}>
              <option value="recently_due_desc">Recently due first</option>
              <option value="overdue_desc">Most overdue first</option>
              <option value="recall_due">Recall due date</option>
              <option value="attempts_asc">Fewest attempts first</option>
              <option value="name">Name (A→Z)</option>
            </select>
          </div>
          <label className="flex items-center gap-1.5 text-xs text-gray-600 mb-1">
            <input type="checkbox" checked={includeCooldown}
                   onChange={e => setIncludeCooldown(e.target.checked)} />
            Include cooldown
          </label>
          <button type="button"
                   onClick={() => setSavingPreset(true)}
                   className="text-[11px] text-plum-700 hover:underline flex items-center gap-0.5 mb-1">
            <Save size={11}/> Save as preset
          </button>
        </div>
        {savingPreset && (
          <div className="mt-3 pt-3 border-t border-gray-100 flex items-end gap-2">
            <div className="flex-1 max-w-xs">
              <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Preset name</label>
              <input className="input text-sm w-full"
                     autoFocus
                     placeholder="e.g. Overdue 90+ WWE"
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
                    className="btn-primary text-xs flex items-center gap-1"
                    disabled={!presetNameDraft.trim() || savePreset.isPending}
                    onClick={() => savePreset.mutate({ name: presetNameDraft.trim(),
                                                        filters_json: currentFilters,
                                                        is_default: false })}>
              <Save size={11}/> Save
            </button>
            <button type="button"
                    className="text-xs text-muted hover:underline"
                    onClick={() => { setSavingPreset(false); setPresetNameDraft('') }}>
              Cancel
            </button>
          </div>
        )}
        <div className="text-[11px] text-gray-500 mt-2">
          {total.toLocaleString()} matching · page {page}
        </div>
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        <table className="w-full">
          <thead className="bg-plum-50">
            <tr>
              <th className="table-th">Chart</th>
              <th className="table-th">Patient</th>
              <th className="table-th">DOB</th>
              <th className="table-th">Phone</th>
              <th className="table-th">Last Visit</th>
              <th className="table-th">Recall Type</th>
              <th className="table-th text-center">Attempts</th>
              <th className="table-th">Last Outcome</th>
              <th className="table-th text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={9} className="table-td text-center text-muted py-8">Loading…</td></tr>
            )}
            {!isLoading && recalls.length === 0 && (
              <tr><td colSpan={9} className="table-td text-center text-muted py-8">No recalls match these filters.</td></tr>
            )}
            {recalls.map(r => (
              <RecallRow key={r.id} r={r} onOpen={() => setOpenId(r.id)} />
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {total > 50 && (
        <div className="flex items-center justify-between mt-3">
          <button className="btn-secondary text-sm" disabled={page === 1}
                  onClick={() => setPage(p => p - 1)}>Previous</button>
          <span className="text-xs text-gray-500">
            Showing {((page - 1) * 50) + 1}–{Math.min(page * 50, total)} of {total.toLocaleString()}
          </span>
          <button className="btn-secondary text-sm"
                  disabled={page * 50 >= total}
                  onClick={() => setPage(p => p + 1)}>Next</button>
        </div>
      )}

      {openId && <RecallDrawer recallId={openId} onClose={() => setOpenId(null)} />}
    </div>
  )
}


function ModMedImportButton() {
  const qc = useQueryClient()
  const { has } = useCurrentUser()
  const canImport = has?.('recall:manage')
  const [open, setOpen] = useState(false)

  if (!canImport) return null

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="btn-secondary text-sm flex items-center gap-1"
      >
        <Upload size={13} /> Import ModMed WWE report
      </button>
      {open && <ModMedImportDrawer onClose={() => setOpen(false)} />}
    </>
  )
}


function ModMedImportDrawer({ onClose }) {
  const qc = useQueryClient()
  const [file, setFile] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const { data: summary } = useQuery({
    queryKey: ['wwe-summary'],
    queryFn: () => api.get('/recalls/imports/wwe-summary').then(r => r.data),
  })

  const upload = useMutation({
    mutationFn: () => {
      const fd = new FormData()
      fd.append('file', file)
      return api.post('/recalls/imports/modmed-wwe', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      }).then(r => r.data)
    },
    onSuccess: (data) => {
      setResult(data); setError(null)
      qc.invalidateQueries({ queryKey: ['recalls'] })
      qc.invalidateQueries({ queryKey: ['recalls-dash'] })
      qc.invalidateQueries({ queryKey: ['wwe-summary'] })
    },
    onError: (e) => {
      setError(e?.response?.data?.detail || e.message)
      setResult(null)
    },
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-6 py-4 flex items-center justify-between z-10">
          <h2 className="font-serif font-semibold text-ink text-[18px]">Import ModMed WWE report</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        <div className="p-6 space-y-4">
          <div className="card !p-3 text-xs text-gray-700 bg-gray-50">
            Upload a fresh ModMed appointment report (XLSX). Re-running on the same data
            is safe — existing rows are upserted with the latest status, and the recall
            list is re-swept so anyone with a future appt or a recent visit drops off.
          </div>

          {summary && (
            <div className="card !p-3">
              <h3 className="text-sm font-semibold text-gray-800 mb-1">Currently Loaded</h3>
              <ul className="text-xs text-gray-700 space-y-0.5">
                {Object.entries(summary.totals_by_source || {}).map(([s, n]) => (
                  <li key={s}><strong>{s}</strong>: {n.toLocaleString()} rows</li>
                ))}
                <li><strong>Future scheduled</strong>: {(summary.scheduled_future || 0).toLocaleString()}</li>
                <li className="text-gray-500">
                  Last ModMed import: {summary.last_modmed_import?.slice(0, 16) || 'never'}
                </li>
              </ul>
            </div>
          )}

          <div className="card !p-3 space-y-2">
            <label className="flex items-center gap-2 text-sm">
              <FileSpreadsheet size={14} className="text-plum-700" />
              <span>Pick the .xlsx file</span>
            </label>
            <input
              type="file"
              accept=".xlsx,.xls"
              className="text-xs"
              onChange={e => { setFile(e.target.files?.[0] || null); setResult(null); setError(null) }}
            />
            <button
              className="btn-primary text-sm flex items-center gap-1 mt-1 disabled:opacity-60"
              disabled={!file || upload.isPending}
              onClick={() => upload.mutate()}
            >
              <Upload size={13} /> {upload.isPending ? 'Importing…' : 'Run import'}
            </button>
          </div>

          {error && (
            <div className="card !p-3 bg-red-50 border-red-200 text-xs text-red-800">
              ✗ {error}
            </div>
          )}

          {result && (
            <div className="card !p-3 bg-green-50 border-green-200 text-xs text-green-900 space-y-1">
              <div className="font-semibold">✓ {result.filename} imported</div>
              <div>Rows in file: <strong>{result.rows_in_file?.toLocaleString()}</strong></div>
              <div>Inserted: <strong>{result.inserted?.toLocaleString()}</strong></div>
              <div>Updated: <strong>{result.updated?.toLocaleString()}</strong></div>
              <div>Unchanged: <strong>{result.unchanged?.toLocaleString()}</strong></div>
              {result.skipped_non_wwe > 0 && <div>Skipped (non-WWE): {result.skipped_non_wwe}</div>}
              {result.skipped_bad_data > 0 && <div>Skipped (bad data): {result.skipped_bad_data}</div>}
              {result.recall_sweep && (
                <div className="mt-1 pt-1 border-t border-green-200">
                  Recall sweep: flipped <strong>{result.recall_sweep.flipped_completed}</strong> active recalls to completed
                  ({result.recall_sweep.future_count} have future appts · {result.recall_sweep.recent_count} were seen recently).
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}


function DashboardStrip({ dash }) {
  if (!dash) return null
  const q = dash.queue || {}
  const c = dash.calls || {}
  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
      <Stat icon={<Users size={14} />} label="Active queue" val={q.active?.toLocaleString()} tone="amber" />
      <Stat icon={<AlertTriangle size={14} />} label="Overdue ≥24mo" val={q.overdue_24mo?.toLocaleString()} tone="red" />
      <Stat icon={<Phone size={14} />} label="Calls today" val={c.today?.toLocaleString()} tone="blue" />
      <Stat icon={<TrendingUp size={14} />} label="Calls this week" val={c.this_week?.toLocaleString()} tone="green" />
      <Stat icon={<CheckCircle2 size={14} />} label="Suppressed" val={q.suppressed?.toLocaleString()} tone="gray" />
    </div>
  )
}


function Stat({ icon, label, val, tone }) {
  const tones = {
    amber: 'text-amber-700',
    red:   'text-red-600',
    blue:  'text-blue-700',
    green: 'text-green-700',
    gray:  'text-gray-600',
  }
  return (
    <div className="card !p-3">
      <div className="flex items-center gap-1.5 text-[10px] text-gray-500 uppercase tracking-wide">
        {icon} {label}
      </div>
      <div className={`text-2xl font-bold mt-1 ${tones[tone]}`}>{val ?? '—'}</div>
    </div>
  )
}


function RecallRow({ r, onOpen }) {
  const qc = useQueryClient()
  const { user } = useCurrentUser()
  const [dialState, setDialState] = useState(null)  // null | 'ringing' | 'connected' | 'error'
  const [dialMsg, setDialMsg] = useState(null)
  const phone = r.cell_phone || r.primary_phone
  // Soft-claim — another caller currently working this row?
  const claimedByOther = r.claimed_by && r.claimed_by !== (user?.email || '').toLowerCase()
  const overdueDays = r.last_visit
    ? Math.floor((Date.now() - new Date(r.last_visit).getTime()) / (1000 * 60 * 60 * 24))
    : null

  const dial = useMutation({
    mutationFn: () => api.post(`/recalls/${r.id}/dial`).then(res => res.data),
    onMutate: () => { setDialState('ringing'); setDialMsg('Calling your RC extension…') },
    onSuccess: (data) => {
      setDialState('connected')
      setDialMsg(data.message || 'Pick up your phone — RC is connecting you.')
      qc.invalidateQueries({ queryKey: ['recalls'] })
      setTimeout(() => { setDialState(null); setDialMsg(null) }, 8000)
    },
    onError: (err) => {
      setDialState('error')
      setDialMsg(err?.response?.data?.detail || 'Dial failed')
      setTimeout(() => { setDialState(null); setDialMsg(null) }, 6000)
    },
  })

  const stateColor = dialState === 'ringing' ? 'text-amber-700 bg-amber-50 border-amber-200'
    : dialState === 'connected' ? 'text-green-700 bg-green-50 border-green-200'
    : dialState === 'error' ? 'text-red-700 bg-red-50 border-red-200'
    : ''

  return (
    <tr className="table-row hover:bg-plum-50/30">
      <td className="table-td font-mono text-[11px]">{r.chart_number}</td>
      <td className="table-td">
        <div className="text-sm font-medium text-gray-900 flex items-center gap-1.5">
          {r.patient_name}
          {claimedByOther && (
            <span className="inline-flex items-center gap-0.5 text-[10px] bg-amber-100 text-amber-800 px-1.5 py-0.5 rounded border border-amber-200"
                  title={`Currently being worked by ${r.claimed_by}`}>
              <Lock size={9} /> {r.claimed_by.split('@')[0]}
            </span>
          )}
        </div>
      </td>
      <td className="table-td text-[11px] text-gray-600">
        {r.dob ? fmt.date(r.dob) : <span className="text-gray-400">—</span>}
      </td>
      <td className="table-td">
        {phone ? (
          <div className="space-y-1">
            <button
              onClick={() => dial.mutate()}
              disabled={dial.isPending}
              className="text-plum-700 hover:bg-plum-50 px-2 py-1 rounded flex items-center gap-1 text-sm font-mono disabled:opacity-60"
              title="Call via RingCentral — RC will ring your extension first, then bridge to patient"
            >
              <Phone size={12} /> {phone}
            </button>
            {dialMsg && (
              <div className={`text-[10px] px-1.5 py-0.5 rounded border ${stateColor}`}>
                {dialMsg}
              </div>
            )}
          </div>
        ) : <span className="text-gray-400 text-xs italic">no phone</span>}
      </td>
      <td className="table-td text-[11px]">
        {r.last_visit ? (
          <div>
            <div>{fmt.date(r.last_visit)}</div>
            <div className={`text-[10px] ${overdueDays > 730 ? 'text-red-600 font-medium' : 'text-gray-500'}`}>
              {Math.floor(overdueDays / 30)} months ago
            </div>
          </div>
        ) : <span className="text-amber-600 italic">no visit on file</span>}
      </td>
      <td className="table-td text-[11px]">{r.recall_type || '—'}</td>
      <td className="table-td text-center text-[11px] font-mono">{r.attempts || 0}</td>
      <td className="table-td text-[11px] text-gray-600 max-w-[180px] truncate">
        {r.last_outcome || <span className="text-gray-400">—</span>}
      </td>
      <td className="table-td text-right">
        <button className="text-xs text-plum-700 hover:underline flex items-center gap-1 ml-auto"
                onClick={onOpen}>
          Update <ChevronRight size={11} />
        </button>
      </td>
    </tr>
  )
}


function RecallDrawer({ recallId, onClose }) {
  const qc = useQueryClient()
  const { user } = useCurrentUser()
  const [outcome, setOutcome] = useState('')
  const [notes, setNotes] = useState('')
  const [claimError, setClaimError] = useState(null)

  const { data } = useQuery({
    queryKey: ['recalls', recallId],
    queryFn: () => api.get(`/recalls/${recallId}`).then(r => r.data),
  })

  // Take a soft claim when the drawer opens. Refresh every 4 minutes so a
  // long call doesn't lapse the lock. Release when the drawer closes
  // (best-effort — backend also auto-expires the claim after CLAIM_TTL).
  useEffect(() => {
    let cancelled = false
    let timer = null

    const claim = async () => {
      try {
        await api.post(`/recalls/${recallId}/claim`)
        if (cancelled) return
        setClaimError(null)
        timer = setTimeout(claim, 4 * 60 * 1000)
      } catch (err) {
        if (cancelled) return
        setClaimError(err?.response?.data?.detail || 'Could not claim this recall')
      }
    }
    claim()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
      // Best-effort release. Use sendBeacon-style fire-and-forget.
      api.delete(`/recalls/${recallId}/claim`).catch(() => {})
    }
  }, [recallId])
  const { data: catalog } = useQuery({
    queryKey: ['recall-outcomes'],
    queryFn: () => api.get('/recalls/outcomes/catalog').then(r => r.data),
  })

  const recall = data?.recall
  const history = data?.history || []
  const wweHistory = data?.wwe_history || []
  const wweTotal = data?.wwe_total_visits || 0
  const wweLatest = data?.wwe_latest_date
  const wweExpectedNext = data?.wwe_expected_next
  const wweNextScheduled = data?.wwe_next_scheduled || null
  const phone = recall?.cell_phone || recall?.primary_phone

  const submit = useMutation({
    mutationFn: () => api.post(`/recalls/${recallId}/outcome`, {
      outcome, notes: notes || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['recalls'] })
      qc.invalidateQueries({ queryKey: ['recalls-dash'] })
      qc.invalidateQueries({ queryKey: ['recalls', recallId] })
      onClose()
    },
  })

  const [dialState, setDialState] = useState(null)
  const [dialMsg, setDialMsg] = useState(null)
  const dial = useMutation({
    mutationFn: () => api.post(`/recalls/${recallId}/dial`).then(r => r.data),
    onMutate: () => { setDialState('ringing'); setDialMsg('Calling your RC extension…') },
    onSuccess: (data) => {
      setDialState('connected')
      setDialMsg(data.message || 'Pick up your phone.')
      qc.invalidateQueries({ queryKey: ['recalls'] })
      qc.invalidateQueries({ queryKey: ['recalls', recallId] })
      setTimeout(() => { setDialState(null); setDialMsg(null) }, 8000)
    },
    onError: (err) => {
      setDialState('error')
      setDialMsg(err?.response?.data?.detail || 'Dial failed')
      setTimeout(() => { setDialState(null); setDialMsg(null) }, 6000)
    },
  })

  const isPermanent = catalog?.outcomes?.find(o => o.value === outcome)?.permanent_suppression

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-2xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-6 py-4 flex items-center justify-between z-10">
          <h2 className="font-serif font-semibold text-ink text-[18px]">Update Recall</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        {claimError && (
          <div className="mx-6 mt-4 px-3 py-2 rounded border border-amber-300 bg-amber-50 text-[12px] text-amber-900 flex items-start gap-2">
            <Lock size={14} className="shrink-0 mt-0.5" />
            <div>
              <strong>Locked.</strong> {claimError} You can view this recall but
              dial + outcome will be blocked until they finish.
            </div>
          </div>
        )}

        {!recall && <div className="p-6 text-muted">Loading…</div>}

        {recall && (
          <div className="p-6 space-y-4">
            {/* Patient header */}
            <div className="card !p-3">
              <div className="text-sm font-semibold text-gray-900">{recall.patient_name}</div>
              <div className="text-[12px] text-gray-600 mt-0.5">
                Chart #{recall.chart_number}
                {recall.dob && <> · DOB {fmt.date(recall.dob)}</>}
              </div>
              {phone && (
                <div className="mt-2">
                  <button
                    type="button"
                    onClick={() => dial.mutate()}
                    disabled={dial.isPending}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-plum-100 hover:bg-plum-200 text-plum-700 rounded text-sm font-mono disabled:opacity-60"
                  >
                    <Phone size={13} /> {phone}
                  </button>
                  {dialMsg && (
                    <div className={`text-[11px] mt-1 px-2 py-1 rounded border ${
                      dialState === 'ringing'   ? 'text-amber-700 bg-amber-50 border-amber-200' :
                      dialState === 'connected' ? 'text-green-700 bg-green-50 border-green-200' :
                                                  'text-red-700 bg-red-50 border-red-200'
                    }`}>
                      {dialMsg}
                    </div>
                  )}
                </div>
              )}
              <div className="grid grid-cols-2 gap-2 mt-3 text-[11px]">
                <Field label="Last visit" val={recall.last_visit ? fmt.date(recall.last_visit) : 'none on file'} />
                <Field label="Recall type" val={recall.recall_type || '—'} />
                <Field label="Recall due" val={recall.recall_due ? fmt.date(recall.recall_due) : '—'} />
                <Field label="Attempts" val={recall.attempts || 0} />
                <Field label="Insurance" val={recall.primary_insurance || '—'} />
                <Field label="Email" val={recall.email || '—'} />
              </div>
            </div>

            {/* WWE history */}
            <WWEHistorySection
              total={wweTotal}
              latest={wweLatest}
              expectedNext={wweExpectedNext}
              nextScheduled={wweNextScheduled}
              visits={wweHistory}
            />

            {/* Caller script */}
            <CallerScript recall={recall} />

            {/* Outcome form */}
            <div className="card">
              <h3 className="text-sm font-semibold text-ink mb-2">Log Call Outcome</h3>
              <div className="space-y-2">
                <select className="input text-sm w-full"
                        value={outcome}
                        onChange={e => setOutcome(e.target.value)}>
                  <option value="">— Pick an outcome —</option>
                  {(catalog?.outcomes || []).map(o => (
                    <option key={o.value} value={o.value}>
                      {o.value}
                      {o.permanent_suppression ? ' (permanent suppression)' : ''}
                      {o.completes_recall ? ' (completes recall)' : ''}
                      {o.cooldown_days ? ` (${o.cooldown_days}d cooldown)` : ''}
                    </option>
                  ))}
                </select>
                <textarea className="input text-sm w-full" rows={3}
                          placeholder="Notes / next steps…"
                          value={notes}
                          onChange={e => setNotes(e.target.value)} />
                {isPermanent && (
                  <div className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
                    ⚠ This outcome will permanently suppress this patient. They cannot be re-added to the recall list.
                  </div>
                )}
                <div className="flex justify-end">
                  <button className="btn-primary text-sm"
                          onClick={() => submit.mutate()}
                          disabled={!outcome || submit.isPending}>
                    {submit.isPending ? 'Saving…' : 'Log outcome'}
                  </button>
                </div>
              </div>
            </div>

            {/* History */}
            <div className="card">
              <h3 className="text-sm font-semibold text-ink mb-2 flex items-center gap-1.5">
                <Clock size={13} /> History ({history.length})
              </h3>
              {history.length === 0 ? (
                <div className="text-xs text-gray-400 italic">No activity yet.</div>
              ) : (
                <ul className="space-y-2">
                  {history.map(h => (
                    <li key={h.id} className="border-l-2 border-plum-200 pl-3 py-0.5 text-[11px]">
                      <div className="flex items-baseline gap-2">
                        <span className="font-medium">{(h.user_email || 'system').split('@')[0]}</span>
                        <span className="text-gray-400">·</span>
                        <span className="text-gray-600">{h.event_type.replace(/_/g, ' ')}</span>
                        <span className="text-gray-400 ml-auto">
                          {new Date(h.occurred_at + 'Z').toLocaleString()}
                        </span>
                      </div>
                      {h.outcome && <div className="text-plum-700">{h.outcome}</div>}
                      {h.notes && <div className="text-gray-700 mt-1 whitespace-pre-wrap">{h.notes}</div>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}


function Field({ label, val }) {
  return (
    <div>
      <div className="text-[9px] text-gray-400 uppercase tracking-wide">{label}</div>
      <div className="text-gray-800 font-medium truncate">{val}</div>
    </div>
  )
}


function WWEHistorySection({ total, latest, expectedNext, nextScheduled, visits }) {
  const [open, setOpen] = useState(false)

  const expectedTone = (() => {
    if (!expectedNext) return 'text-gray-700'
    if (nextScheduled) return 'text-green-700'  // actually booked — good
    const days = (new Date(expectedNext) - new Date()) / 86400000
    if (days < -30) return 'text-red-700'
    if (days < 0)   return 'text-amber-700'
    if (days < 30)  return 'text-blue-700'
    return 'text-gray-700'
  })()

  // Visits are already newest-first from the backend
  const completedOnly = visits.filter(v => !v.is_future)

  return (
    <div className="card !p-3">
      <div className="flex items-baseline justify-between mb-1">
        <h3 className="text-sm font-semibold text-ink">Well-Woman Exam history</h3>
        {visits.length > 0 && (
          <button
            type="button"
            onClick={() => setOpen(o => !o)}
            className="text-[11px] text-plum-700 hover:underline"
          >
            {open ? 'Hide all' : `Show all ${visits.length} rows`}
          </button>
        )}
      </div>
      <div className="grid grid-cols-3 gap-2 text-[11px]">
        <Field label="Total WWE visits" val={total} />
        <Field label="Most recent WWE" val={latest ? fmt.date(latest) : 'none on file'} />
        <div>
          <div className="text-[9px] text-gray-400 uppercase tracking-wide">
            {nextScheduled ? 'Next scheduled' : 'Expected next'}
          </div>
          <div className={`font-medium truncate ${expectedTone} flex items-center gap-1`}>
            {expectedNext ? fmt.date(expectedNext) : '—'}
            {nextScheduled && (
              <span className="text-[9px] uppercase tracking-wide bg-green-100 text-green-800 px-1 py-0.5 rounded">booked</span>
            )}
          </div>
        </div>
      </div>
      {completedOnly.length === 0 && !nextScheduled && (
        <div className="mt-2 text-[11px] text-gray-500 italic">
          No historical preventive visits on file (Greenway 2014–present, ModMed 2026+).
        </div>
      )}
      {open && visits.length > 0 && (
        <ul className="mt-2 max-h-48 overflow-y-auto border-t border-gray-100 pt-2 text-[11px]">
          {visits.map((v, i) => (
            <li key={`${v.visit_date}-${v.procedure_code}-${i}`}
                className="flex items-baseline justify-between py-0.5">
              <span className="font-mono">{fmt.date(v.visit_date)}</span>
              <span className="text-gray-500 flex items-center gap-1">
                <span>{v.procedure_code}</span>
                <span className="text-gray-400">· {v.source}</span>
                {v.is_future && (
                  <span className="bg-green-100 text-green-800 px-1 rounded text-[9px]">scheduled</span>
                )}
                {v.status === 'cancelled' && (
                  <span className="bg-red-100 text-red-700 px-1 rounded text-[9px]">cancelled</span>
                )}
                {v.status === 'noshow' && (
                  <span className="bg-amber-100 text-amber-800 px-1 rounded text-[9px]">no-show</span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}


function CallerScript({ recall }) {
  const [open, setOpen] = useState(false)
  const isFirstAttempt = (recall.attempts || 0) === 0
  const lastOutcome = recall.last_outcome
  return (
    <div className="card !p-3 bg-plum-50/30 border-plum-100">
      <button onClick={() => setOpen(o => !o)}
              className="flex items-center justify-between w-full text-left">
        <span className="text-sm font-semibold text-plum-700">📞 Caller Script</span>
        <ChevronRight size={14} className={`text-plum-700 transition-transform ${open ? 'rotate-90' : ''}`} />
      </button>
      {open && (
        <div className="mt-2 text-[12px] text-gray-800 leading-relaxed whitespace-pre-line">
{`Hi, this is [your name] from Waldorf Women's Care. May I speak with ${recall.patient_name?.split(',')[1]?.trim() || 'the patient'}?

${isFirstAttempt ? `I'm calling because we haven't seen you for your annual exam in over a year — typically 13 months. We just wanted to check in and see if you'd like to schedule your well-woman visit.`
: lastOutcome === 'Left voicemail' ? `I left you a message a few days ago. I'm following up about scheduling your annual well-woman exam.`
: `I'm following up on your annual recall.`}

Would you like me to schedule that for you now? We have appointments at our Waldorf, Brandywine, and Arlington offices.

[If yes] Great! Let me check what we have available…
[If declining] Totally understand — would you prefer we not call again, or check in next year?
[If voicemail] Hi, this is [your name] from Waldorf Women's Care calling for ${recall.patient_name?.split(',')[1]?.trim() || 'the patient'}. We haven't seen you in over a year and wanted to check in about your annual exam. Please give us a call back at 240-252-2140 when you have a moment. Thank you!`}
        </div>
      )}
    </div>
  )
}


function PresetChip({ preset, onLoad, onSetDefault, onDelete }) {
  return (
    <div className={`group inline-flex items-center text-[11px] rounded-full border px-2 py-0.5 ${
      preset.is_default
        ? 'border-plum-300 bg-plum-50 text-plum-800'
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
                className="text-gray-400 hover:text-plum-700 px-1 opacity-0 group-hover:opacity-100">
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
