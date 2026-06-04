import { useState, useMemo, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import {
  Activity, AlertTriangle, BookOpen, Calendar, CheckCircle2, Clock, Hospital,
  Search, Stethoscope, TrendingUp, Users, Building2, Upload, X, FileText, Settings,
  Check, Phone, Save, Star, ChevronDown, ChevronUp, Trash2, MessageSquare,
  DollarSign,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { WeeklyCalendar } from './SurgeryCalendar'
import { useFacilities } from '../hooks/useFacilities'


const STATUS_TONE = {
  incomplete:    'bg-gray-100 text-gray-700',
  new:           'bg-gray-100 text-gray-700',
  in_progress:   'bg-amber-50 text-amber-800',
  confirmed:     'bg-blue-50 text-blue-800',
  completed:     'bg-green-50 text-green-800',
  hold:          'bg-violet-50 text-violet-800',
  cancelled:     'bg-red-50 text-red-700',
  unresponsive:  'bg-gray-100 text-gray-500',
}

// Internal status value → human label shown to the user.
export const STATUS_LABEL = {
  incomplete:    'Incomplete',
  new:           'New',
  in_progress:   'Benefits Check',
  confirmed:     'Pre-Surgery',
  completed:     'Post-Surgery',
  hold:          'Hold',
  cancelled:     'Canceled',
  unresponsive:  'Unresponsive',
}


// Dashboard bucket definitions — matches backend ALL_BUCKETS order.
// `tone` controls the tile color; `descr` shows in a tooltip.
const BUCKET_DEFS = [
  { k: 'outstanding',         l: 'Outstanding',         tone: 'gray',    descr: 'All active surgeries (anything not cancelled or completed)' },
  { k: 'incomplete',          l: 'Incomplete',          tone: 'amber',   descr: 'Missing required information from the order' },
  { k: 'needs_benefits',      l: 'Needs Benefits',      tone: 'amber',   descr: 'Insurance benefits not yet determined' },
  { k: 'needs_prior_auth',    l: 'Needs Prior Auth',    tone: 'amber',   descr: 'Authorization not yet granted' },
  { k: 'unresponsive',        l: 'Unresponsive',        tone: 'red',     descr: 'Pre-op visit was 30+ days ago and patient still has not picked a surgery date' },
  { k: 'date_picked',         l: 'Date Picked',         tone: 'blue',    descr: 'Patient has picked a surgery date' },
  { k: 'needs_consent',       l: 'Needs Consent',       tone: 'amber',   descr: 'Date is picked but consent is not signed' },
  { k: 'needs_clearance',     l: 'Needs Clearance',     tone: 'amber',   descr: 'Medical clearance pending' },
  { k: 'needs_assistant_surgeon', l: 'Needs Asst Surgeon', tone: 'amber', descr: 'Assistant surgeon required — office not notified or patient appt not confirmed' },
  { k: 'needs_labs',          l: 'Needs Labs',          tone: 'red',     descr: 'Hospital surgery within 7 days, no labs sent' },
  { k: 'needs_repeat_preop',  l: 'Needs Repeat Pre-op', tone: 'red',     descr: 'Pre-op was >180 days before surgery — must be repeated' },
  { k: 'needs_followup_appt', l: 'Needs F/U Appt',      tone: 'amber',   descr: 'Date picked but post-op appointment not scheduled' },
  { k: 'needs_post_op_call',  l: 'Needs Post-Op Call',  tone: 'red',     descr: 'Surgery date passed and we have not spoken to patient' },
  { k: 'needs_post_op_docs',  l: 'Needs Post-Op Docs',  tone: 'red',     descr: '5+ days post-op, op notes not received' },
  { k: 'needs_billed',        l: 'Needs to be Billed',  tone: 'violet',  descr: 'Op notes received, not yet billed' },
]


const MILESTONE_TITLE = {
  benefits_determined:        'Benefits',
  prior_auth:                 'Prior Auth',
  patient_picks_date:         'Patient Picks Date',
  post_op_appts_scheduled:    'Post-Op Appts',
  device_assigned:            'Device',
  assistant_surgeon:          'Assistant Surgeon',
  consent:                    'Consent',
  surgery_confirmed_hospital: 'Hospital Confirm',
  labs_to_hospital:           'Labs',
  post_op_call:               'Post-Op Call',
  op_notes:                   'Op Notes',
  path_report:                'Path Report',
  surgery_billed:             'Billed',
}


const EMPTY_FILTERS = {
  search: '',
  status: '',
  facility: '',
  bucket: '',
  behind_only: false,
  urgent_only: false,
  procedure_classification: '',
  surgeon: '',
  primary_insurance: '',
  is_robotic: '',                   // '' | 'true' | 'false'
  has_date: '',                     // '' | 'true' | 'false'
  date_from: '',
  date_to: '',
  reschedule_count_min: '',         // number as string
  preop_needs_repeat: '',           // '' | 'true' | 'false'
  clearance_required: '',           // '' | 'true' | 'false'
  auth_status: '',
  age_min: '',
  age_max: '',
}

function filtersToParams(f) {
  const out = {}
  for (const [k, v] of Object.entries(f)) {
    if (v === '' || v === false || v == null) continue
    if (k === 'reschedule_count_min' || k === 'age_min' || k === 'age_max') {
      const n = Number(v); if (!Number.isFinite(n)) continue
      out[k] = n
    } else if (['is_robotic', 'has_date', 'preop_needs_repeat', 'clearance_required'].includes(k)) {
      out[k] = v === 'true' ? true : v === 'false' ? false : undefined
    } else {
      out[k] = v
    }
  }
  return out
}

function activeFilterCount(f) {
  let n = 0
  for (const [k, v] of Object.entries(f)) {
    if (k === 'search' || k === 'bucket') continue
    if (v === '' || v === false || v == null) continue
    n++
  }
  return n
}


export default function Surgery() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [savingPreset, setSavingPreset] = useState(false)
  const [presetNameDraft, setPresetNameDraft] = useState('')

  const setF = (patch) => setFilters(prev => ({ ...prev, ...patch }))

  const { data: dash } = useQuery({
    queryKey: ['surgery-dashboard'],
    queryFn: () => api.get('/surgery/dashboard').then(r => r.data),
  })

  // Saved filter presets (per-user)
  const { data: presets } = useQuery({
    queryKey: ['surgery-filter-presets'],
    queryFn: () => api.get('/surgery-filters').then(r => r.data),
  })

  // Load the default preset once on first visit (only if no filters are set)
  const [defaultApplied, setDefaultApplied] = useState(false)
  useEffect(() => {
    if (defaultApplied || !presets) return
    const def = presets.find(p => p.is_default)
    if (def) {
      setFilters({ ...EMPTY_FILTERS, ...def.filters_json })
    }
    setDefaultApplied(true)
  }, [presets, defaultApplied])

  const savePreset = useMutation({
    mutationFn: (body) => api.post('/surgery-filters', body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery-filter-presets'] })
      setSavingPreset(false); setPresetNameDraft('')
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  const deletePreset = useMutation({
    mutationFn: (id) => api.delete(`/surgery-filters/${id}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery-filter-presets'] }),
  })
  const setDefaultPreset = useMutation({
    mutationFn: (preset) => api.put(`/surgery-filters/${preset.id}`,
                                       { name: preset.name,
                                         filters_json: preset.filters_json,
                                         is_default: true }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery-filter-presets'] }),
  })
  const resolveConflict = useMutation({
    mutationFn: (surgery_id) =>
      api.post(`/surgery/${surgery_id}/blocked-conflict/resolve`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery-dashboard'] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Failed to resolve'),
  })

  const { data, isLoading } = useQuery({
    queryKey: ['surgery-list', filters],
    queryFn: () => api.get('/surgery', {
      params: { ...filtersToParams(filters), per_page: 200 },
    }).then(r => r.data),
  })

  const surgeries = data?.surgeries || []

  // Group by primary bucket for dashboard view. Buckets follow BUCKET_DEFS
  // order so the action-oriented progression reads top-down. We assign each
  // surgery to its FIRST matching bucket (skipping the catch-all 'outstanding'
  // and 'date_picked' since the more specific buckets surface the actionable
  // need). Surgeries that fall through go to 'all_clear'.
  const grouped = useMemo(() => {
    const order = BUCKET_DEFS.map(b => b.k).filter(k => k !== 'outstanding' && k !== 'date_picked')
    const out = {}
    for (const s of surgeries) {
      const sBuckets = new Set(s.buckets || [])
      let primary = order.find(k => sBuckets.has(k))
      if (!primary) primary = 'all_clear'
      if (!out[primary]) out[primary] = []
      out[primary].push(s)
    }
    return [...order, 'all_clear']
      .filter(k => out[k]?.length)
      .map(k => ({
        kind: k,
        title: BUCKET_DEFS.find(b => b.k === k)?.l || (k === 'all_clear' ? 'All clear (no open buckets)' : k),
        tone: BUCKET_DEFS.find(b => b.k === k)?.tone || 'gray',
        items: out[k],
      }))
  }, [surgeries])

  if (isLoading && !data) return <div className="p-6 text-gray-400">Loading…</div>

  return (
    <div>
      <div className="mb-4 flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Surgery scheduling</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            {data?.total || 0} surgeries currently in the system. Click a row to open milestones.
          </p>
        </div>
        <div className="flex gap-2">
          <Link to="/surgery/rules"
                className="btn-secondary text-sm flex items-center gap-1"
                title="Reference guide for schedulers — block rules, capacity, consents, etc.">
            <BookOpen size={13} /> Rules
          </Link>
          <Link to="/surgery/calendar"
                className="btn-secondary text-sm flex items-center gap-1">
            <Calendar size={13} /> Calendar
          </Link>
          <Link to="/surgery/block-schedule"
                className="btn-secondary text-sm flex items-center gap-1">
            <Settings size={13} /> Block schedule
          </Link>
          <Link to="/surgery/fee-schedule"
                className="btn-secondary text-sm flex items-center gap-1">
            <DollarSign size={13} /> Fee schedule
          </Link>
          <Link to="/surgery/waitlist"
                className="btn-secondary text-sm flex items-center gap-1">
            <Users size={13} /> Waitlist
          </Link>
          <MessagesLink />
          <ManualCreateButton />
          <UploadDemographicsButton />
          <UploadOrderButton />
        </div>
      </div>

      {/* Status row — Unresponsive + Needs Repeat Pre-op */}
      <div className="flex flex-wrap gap-1.5 mb-2">
        <div className="text-[10px] uppercase tracking-[0.18em] text-gray-500 font-medium
                          self-center mr-1">
          Status
        </div>
        {STATUS_BUCKETS.map(b => (
          <BucketChip
            key={b.k}
            label={b.l}
            val={dash?.buckets?.[b.k]}
            tone={b.tone}
            descr={b.descr}
            active={filters.bucket === b.k}
            onClick={() => setF({ bucket: filters.bucket === b.k ? '' : b.k })}
          />
        ))}
      </div>

      {/* Step chips — numbered circle + title + count, ordered by step */}
      <div className="flex flex-wrap items-stretch gap-1.5 mb-4">
        <div className="text-[10px] uppercase tracking-[0.18em] text-gray-500 font-medium
                          self-center mr-1">
          Steps
        </div>
        {STEP_BUCKETS.map(b => (
          <StepBucketChip
            key={b.k}
            n={b.n}
            label={b.l}
            val={dash?.buckets?.[b.k]}
            descr={b.descr}
            active={filters.bucket === b.k}
            onClick={() => setF({ bucket: filters.bucket === b.k ? '' : b.k })}
          />
        ))}
      </div>

      {/* Next available date per facility */}
      <NextAvailableBar next={dash?.next_slots || {}} horizon={dash?.booked_through || {}} />

      {/* Critical alerts + To-do (release alerts now folded into to-do) */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
        <CriticalAlertsPanel alerts={dash?.critical_alerts || []} onOpen={(id) => navigate(`/surgery/${id}`)} />
        <ToDoPanel todos={dash?.todo || []}
                   hospitalUnbooked={dash?.hospital_unbooked || []}
                   officeUnderbooked={dash?.office_underbooked || []}
                   blockedConflicts={dash?.blocked_conflicts || []}
                   onResolveConflict={(id) => resolveConflict.mutate(id)}
                   onOpen={(id) => navigate(`/surgery/${id}`)} />
      </div>

      {/* Always-visible weekly calendar */}
      <div className="mb-4">
        <WeeklyCalendar compact />
      </div>

      {filters.bucket && (
        <div className="mb-2 text-xs text-gray-700">
          Filtered to: <strong>{(() => {
            const step = STEP_BUCKETS.find(b => b.k === filters.bucket)
            if (step) return `Step ${step.n} · ${step.l}`
            return STATUS_BUCKETS.find(b => b.k === filters.bucket)?.l
                || BUCKET_DEFS.find(b => b.k === filters.bucket)?.l
                || filters.bucket
          })()}</strong>
          <button onClick={() => setF({ bucket: '' })}
                  className="ml-2 text-plum-700 hover:underline">clear filter</button>
        </div>
      )}

      {/* Saved filter presets — chip bar */}
      {(presets?.length ?? 0) > 0 && (
        <div className="flex items-center gap-1.5 mb-2 flex-wrap">
          <span className="text-[10px] uppercase tracking-wide text-gray-500 mr-1">Saved:</span>
          {presets.map(p => (
            <PresetChip key={p.id} preset={p}
                        onLoad={() => setFilters({ ...EMPTY_FILTERS, ...p.filters_json })}
                        onSetDefault={() => setDefaultPreset.mutate(p)}
                        onDelete={() => {
                          if (confirm(`Delete preset "${p.name}"?`)) deletePreset.mutate(p.id)
                        }} />
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="card mb-3">
        <div className="flex flex-wrap gap-2 items-end">
          <div className="flex-1 min-w-[260px]">
            <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Search</label>
            <div className="relative">
              <Search size={12} className="absolute left-2 top-2.5 text-muted" />
              <input
                className="input text-sm pl-7 w-full"
                placeholder="Patient name, chart #, or surgery #…"
                value={filters.search}
                onChange={e => setF({ search: e.target.value })}
              />
            </div>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Status</label>
            <select className="input text-sm" value={filters.status}
                    onChange={e => setF({ status: e.target.value })}>
              <option value="">All</option>
              <option value="incomplete">Incomplete</option>
              <option value="new">New</option>
              <option value="in_progress">Benefits Check</option>
              <option value="confirmed">Pre-Surgery</option>
              <option value="completed">Post-Surgery</option>
              <option value="unresponsive">Unresponsive</option>
              <option value="hold">Hold</option>
              <option value="cancelled">Canceled</option>
            </select>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Facility</label>
            <select className="input text-sm" value={filters.facility}
                    onChange={e => setF({ facility: e.target.value })}>
              <option value="">All</option>
              <option value="medstar">MedStar</option>
              <option value="crmc">CRMC</option>
              <option value="office">Office</option>
            </select>
          </div>
          <label className="flex items-center gap-1.5 text-xs text-gray-700 mb-1">
            <input type="checkbox" checked={filters.urgent_only}
                   onChange={e => setF({ urgent_only: e.target.checked })} />
            🚨 Urgent
          </label>
          <label className="flex items-center gap-1.5 text-xs text-gray-700 mb-1">
            <input type="checkbox" checked={filters.behind_only}
                   onChange={e => setF({ behind_only: e.target.checked })} />
            Behind
          </label>
          <button type="button"
                  onClick={() => setShowAdvanced(v => !v)}
                  className="text-[11px] text-plum-700 hover:underline flex items-center gap-0.5 mb-1">
            {showAdvanced ? <ChevronUp size={11}/> : <ChevronDown size={11}/>}
            More filters
            {activeFilterCount(filters) > 0 && (
              <span className="ml-1 bg-plum-100 text-plum-800 px-1 rounded text-[10px]">
                {activeFilterCount(filters)}
              </span>
            )}
          </button>
          {(activeFilterCount(filters) > 0 || filters.search || filters.bucket) && (
            <button type="button"
                    onClick={() => setFilters(EMPTY_FILTERS)}
                    className="text-[11px] text-muted hover:underline mb-1">
              Clear all
            </button>
          )}
          <button type="button"
                  onClick={() => setSavingPreset(true)}
                  className="text-[11px] text-plum-700 hover:underline flex items-center gap-0.5 mb-1">
            <Save size={11}/> Save as Preset
          </button>
        </div>

        {showAdvanced && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-3 pt-3 border-t border-gray-100">
            <div>
              <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Procedure type</label>
              <select className="input text-sm w-full" value={filters.procedure_classification}
                      onChange={e => setF({ procedure_classification: e.target.value })}>
                <option value="">Any</option>
                <option value="robotic_180">Robotic 180min</option>
                <option value="robotic_240">Robotic 240min</option>
                <option value="major">Major (CRMC)</option>
                <option value="minor">Minor</option>
                <option value="office">Office</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Surgeon contains</label>
              <input className="input text-sm w-full" value={filters.surgeon}
                     onChange={e => setF({ surgeon: e.target.value })}
                     placeholder="e.g. Cooke" />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Insurance contains</label>
              <input className="input text-sm w-full" value={filters.primary_insurance}
                     onChange={e => setF({ primary_insurance: e.target.value })}
                     placeholder="e.g. Priority Partners" />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Auth status</label>
              <select className="input text-sm w-full" value={filters.auth_status}
                      onChange={e => setF({ auth_status: e.target.value })}>
                <option value="">Any</option>
                <option value="not_required">not required</option>
                <option value="required">required</option>
                <option value="sent_request">sent request</option>
                <option value="sent_records">sent records</option>
                <option value="peer_review">peer review</option>
                <option value="approved">approved</option>
                <option value="denied">denied</option>
                <option value="tbd">TBD</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Date picked?</label>
              <select className="input text-sm w-full" value={filters.has_date}
                      onChange={e => setF({ has_date: e.target.value })}>
                <option value="">Any</option>
                <option value="true">Yes — has date</option>
                <option value="false">No — unscheduled</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Surgery date ≥</label>
              <input type="date" className="input text-sm w-full" value={filters.date_from}
                     onChange={e => setF({ date_from: e.target.value })} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Surgery date ≤</label>
              <input type="date" className="input text-sm w-full" value={filters.date_to}
                     onChange={e => setF({ date_to: e.target.value })} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Robotic?</label>
              <select className="input text-sm w-full" value={filters.is_robotic}
                      onChange={e => setF({ is_robotic: e.target.value })}>
                <option value="">Any</option>
                <option value="true">Yes</option>
                <option value="false">No</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Clearance required?</label>
              <select className="input text-sm w-full" value={filters.clearance_required}
                      onChange={e => setF({ clearance_required: e.target.value })}>
                <option value="">Any</option>
                <option value="true">Yes</option>
                <option value="false">No</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Pre-op needs repeat?</label>
              <select className="input text-sm w-full" value={filters.preop_needs_repeat}
                      onChange={e => setF({ preop_needs_repeat: e.target.value })}>
                <option value="">Any</option>
                <option value="true">Yes (&gt;180d)</option>
                <option value="false">No</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Rescheduled ≥</label>
              <input type="number" min="0" className="input text-sm w-full" value={filters.reschedule_count_min}
                     onChange={e => setF({ reschedule_count_min: e.target.value })}
                     placeholder="e.g. 2" />
            </div>
            <div className="grid grid-cols-2 gap-1">
              <div>
                <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Age ≥</label>
                <input type="number" min="0" className="input text-sm w-full" value={filters.age_min}
                       onChange={e => setF({ age_min: e.target.value })} />
              </div>
              <div>
                <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Age ≤</label>
                <input type="number" min="0" className="input text-sm w-full" value={filters.age_max}
                       onChange={e => setF({ age_max: e.target.value })} />
              </div>
            </div>
          </div>
        )}

        {savingPreset && (
          <div className="mt-3 pt-3 border-t border-gray-100 flex items-end gap-2">
            <div className="flex-1 max-w-xs">
              <label className="text-[10px] uppercase text-gray-500 tracking-wide block mb-1">Preset name</label>
              <input className="input text-sm w-full"
                     autoFocus
                     placeholder="e.g. Robotic at MedStar next 30d"
                     value={presetNameDraft}
                     onChange={e => setPresetNameDraft(e.target.value)}
                     onKeyDown={e => {
                       if (e.key === 'Enter' && presetNameDraft.trim())
                         savePreset.mutate({ name: presetNameDraft.trim(),
                                             filters_json: filters,
                                             is_default: false })
                     }} />
            </div>
            <button type="button"
                    className="btn-primary text-xs flex items-center gap-1"
                    disabled={!presetNameDraft.trim() || savePreset.isPending}
                    onClick={() => savePreset.mutate({ name: presetNameDraft.trim(),
                                                        filters_json: filters,
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
      </div>

      {/* Grouped list — by bucket, ordered per BUCKET_DEFS */}
      <div className="space-y-3">
        {grouped.map(group => (
          <BucketGroup key={group.kind} group={group} onOpen={(id) => navigate(`/surgery/${id}`)} />
        ))}
        {grouped.length === 0 && (
          <div className="card text-sm text-gray-500 italic">
            No surgeries match your filters.
          </div>
        )}
      </div>
    </div>
  )
}


function NextAvailableBar({ next, horizon }) {
  const items = [
    { key: 'medstar', label: 'MedStar (robotic)', tone: 'bg-blue-50 border-blue-200 text-blue-800' },
    { key: 'crmc',    label: 'CRMC (minor/major)', tone: 'bg-violet-50 border-violet-200 text-violet-800' },
    { key: 'office',  label: 'Office (Thursdays)', tone: 'bg-green-50 border-green-200 text-green-800' },
  ]
  function daysOut(dateStr) {
    if (!dateStr) return null
    const d = new Date(dateStr + 'T00:00:00')
    const t = new Date(); t.setHours(0, 0, 0, 0)
    return Math.round((d - t) / 86400000)
  }
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mb-4">
      {items.map(it => {
        const slot = next[it.key]
        const fill = horizon?.[it.key]
        if (!slot) {
          return (
            <div key={it.key} className={`card border ${it.tone} !p-2.5`}>
              <div className="text-[10px] uppercase tracking-wide opacity-80">{it.label}</div>
              <div className="text-sm mt-1 italic opacity-70">No openings in next 180 days</div>
              {fill && (
                <div className="text-[10px] opacity-70 mt-0.5">
                  Booked through {fmt.date(fill.block_date)}
                </div>
              )}
            </div>
          )
        }
        const dOut = daysOut(slot.block_date)
        const fillOut = fill ? daysOut(fill.block_date) : null
        return (
          <div key={it.key} className={`card border ${it.tone} !p-2.5`}>
            <div className="text-[10px] uppercase tracking-wide opacity-80">{it.label}</div>
            <div className="text-lg font-bold mt-0.5 leading-tight">
              {fmt.date(slot.block_date)}
              <span className="text-[11px] font-normal opacity-70 ml-2">
                {slot.weekday} · in {dOut} day{dOut === 1 ? '' : 's'}
              </span>
            </div>
            <div className="text-[10px] opacity-80 mt-0.5">
              {slot.block_window} · {slot.cases_already_booked} case{slot.cases_already_booked === 1 ? '' : 's'} booked
            </div>
            <div className="text-[10px] opacity-70 mt-0.5">
              {fill
                ? <>Booked through {fmt.date(fill.block_date)} <span className="opacity-70">· {fillOut} day{fillOut === 1 ? '' : 's'} out</span></>
                : <em>No future bookings yet</em>}
            </div>
          </div>
        )
      })}
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


// Status filters (state, not workflow steps).
const STATUS_BUCKETS = [
  { k: 'unresponsive',       l: 'Unresponsive',       tone: 'red', descr: 'Pre-op visit was 30+ days ago and patient still has not picked a surgery date' },
  { k: 'needs_repeat_preop', l: 'Needs Repeat Pre-op', tone: 'red', descr: 'Pre-op was >180 days before surgery — must be repeated' },
]


// Step-aligned action chips. The `n` is the Step number on the Surgery
// detail page (hospital 15-step flow); `k` is the existing dashboard bucket
// key the backend computes. Ordered by step number.
const STEP_BUCKETS = [
  { n: 2,  k: 'needs_benefits',           l: 'Benefits',         descr: 'Insurance benefits not yet determined' },
  { n: 4,  k: 'needs_consent',            l: 'Consents',         descr: 'Date is picked but consent is not signed' },
  { n: 5,  k: 'needs_followup_appt',      l: 'Post-Op Dates',    descr: 'Date picked but post-op appointment not scheduled' },
  { n: 7,  k: 'needs_prior_auth',         l: 'Prior Auth',       descr: 'Authorization not yet granted' },
  { n: 8,  k: 'needs_clearance',          l: 'Clearance / EKG',  descr: 'Medical clearance pending' },
  { n: 9,  k: 'needs_assistant_surgeon',  l: 'Asst Surgeon',     descr: 'Assistant surgeon required — office not notified or patient appt not confirmed' },
  { n: 12, k: 'needs_labs',               l: 'Labs',             descr: 'Hospital surgery within 7 days, no labs sent' },
  { n: 13, k: 'needs_post_op_call',       l: 'Post-Op F/U',      descr: 'Surgery date passed and we have not spoken to patient' },
  { n: 14, k: 'needs_post_op_docs',       l: 'Notes & Reports',  descr: '5+ days post-op, op notes not received' },
  { n: 15, k: 'needs_billed',             l: 'Bill Surgery',     descr: 'Op notes received, not yet billed' },
]


function StepBucketChip({ n, label, val, descr, active, onClick }) {
  const showVal = val !== undefined && val !== null && val !== '—'
  return (
    <button type="button"
            onClick={onClick}
            title={descr}
            className={`inline-flex items-center gap-2 pl-1 pr-2.5 py-1 rounded-full
                          border text-[12px] transition ${
                            active
                              ? 'bg-plum-700 text-white border-plum-700 shadow-sm'
                              : 'bg-white text-plum-700 border-plum-200 hover:bg-plum-50'
                          }`}>
        <span className={`w-6 h-6 rounded-full grid place-items-center text-[11px] font-semibold ${
          active ? 'bg-white text-plum-700' : 'bg-plum-100 text-plum-700'
        }`}>
          {n}
        </span>
        <span className={`font-medium ${active ? 'text-white' : 'text-plum-ink'}`}>
          {label}
        </span>
        {showVal && (
          <span className={`text-[11px] font-semibold ${
            active ? 'text-white/90' : 'text-plum-700/80'
          }`}>
            {val}
          </span>
        )}
    </button>
  )
}


function BucketChip({ label, val, tone, descr, active, onClick }) {
  const tones = {
    amber:   active ? 'bg-amber-500 text-white border-amber-600'   : 'bg-amber-50 text-amber-800 border-amber-200 hover:bg-amber-100',
    red:     active ? 'bg-red-600 text-white border-red-700'       : 'bg-red-50 text-red-800 border-red-200 hover:bg-red-100',
    blue:    active ? 'bg-blue-600 text-white border-blue-700'     : 'bg-blue-50 text-blue-800 border-blue-200 hover:bg-blue-100',
    green:   active ? 'bg-green-600 text-white border-green-700'   : 'bg-green-50 text-green-800 border-green-200 hover:bg-green-100',
    violet:  active ? 'bg-violet-600 text-white border-violet-700' : 'bg-violet-50 text-violet-800 border-violet-200 hover:bg-violet-100',
    gray:    active ? 'bg-gray-700 text-white border-gray-800'     : 'bg-gray-50 text-gray-700 border-gray-200 hover:bg-gray-100',
  }
  const showVal = val !== undefined && val !== null && val !== '—'
  return (
    <button type="button"
            onClick={onClick}
            title={descr}
            className={`text-[11px] px-2 py-1 rounded-full border inline-flex items-center gap-1.5 transition ${tones[tone] || tones.gray}`}>
      <span>{label}</span>
      {showVal && (
        <span className={`font-semibold text-[11px] ${active ? 'opacity-90' : 'opacity-80'}`}>
          {val}
        </span>
      )}
    </button>
  )
}


function BucketTile({ label, val, tone, descr, active, onClick }) {
  const tones = {
    amber:   'bg-amber-50 border-amber-200 text-amber-800 hover:bg-amber-100',
    red:     'bg-red-50 border-red-200 text-red-800 hover:bg-red-100',
    blue:    'bg-blue-50 border-blue-200 text-blue-800 hover:bg-blue-100',
    green:   'bg-green-50 border-green-200 text-green-800 hover:bg-green-100',
    violet:  'bg-violet-50 border-violet-200 text-violet-800 hover:bg-violet-100',
    gray:    'bg-gray-50 border-gray-200 text-gray-800 hover:bg-gray-100',
  }
  const activeRing = active ? 'ring-2 ring-plum-500 ring-offset-1' : ''
  return (
    <button
      type="button"
      onClick={onClick}
      title={descr}
      className={`text-left rounded border p-2 transition ${tones[tone] || tones.gray} ${activeRing}`}
    >
      <div className="text-[10px] uppercase tracking-wide opacity-80 truncate">{label}</div>
      <div className="text-2xl font-bold mt-0.5 leading-tight">{val}</div>
    </button>
  )
}


function Tile({ label, val, icon, tone }) {
  const tones = {
    amber:   'bg-amber-50 border-amber-200 text-amber-800',
    red:     'bg-red-50 border-red-200 text-red-800',
    blue:    'bg-blue-50 border-blue-200 text-blue-800',
    green:   'bg-green-50 border-green-200 text-green-800',
    violet:  'bg-violet-50 border-violet-200 text-violet-800',
    neutral: 'bg-gray-50 border-gray-200 text-gray-700',
  }
  return (
    <div className={`card border ${tones[tone] || tones.neutral} flex items-center justify-between !p-2.5`}>
      <div>
        <div className="text-[10px] uppercase tracking-wide opacity-80">{label}</div>
        <div className="text-2xl font-bold mt-0.5">{val ?? '—'}</div>
      </div>
      <div className="opacity-60">{icon}</div>
    </div>
  )
}


function CriticalAlertsPanel({ alerts, onOpen }) {
  return (
    <div className="card">
      <div className="flex items-center gap-1.5 mb-2">
        <AlertTriangle size={14} className="text-red-700" />
        <h2 className="text-sm font-semibold text-gray-800">Critical alerts</h2>
        <span className="text-[11px] text-muted">(&gt;48h overdue)</span>
      </div>
      {alerts.length === 0 ? (
        <div className="text-xs text-gray-400 italic">Nothing critical right now.</div>
      ) : (
        <ul className="text-xs space-y-1">
          {alerts.map(a => (
            <li key={a.surgery_id}
                className="flex items-baseline justify-between gap-2 cursor-pointer hover:bg-red-50 px-1 py-0.5 rounded"
                onClick={() => onOpen(a.surgery_id)}>
              <span><strong>{a.patient_name}</strong> <span className="text-gray-500">— {a.milestone}</span></span>
              <span className="text-red-700 font-semibold shrink-0">{Math.floor(a.hours_overdue / 24)}d late</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}


function ToDoPanel({ todos, hospitalUnbooked = [], officeUnderbooked = [],
                     blockedConflicts = [], onResolveConflict, onOpen }) {
  const totalItems = todos.length + hospitalUnbooked.length
                   + officeUnderbooked.length + blockedConflicts.length
  return (
    <div className="card">
      <div className="flex items-center gap-1.5 mb-2">
        <Clock size={14} className="text-amber-700" />
        <h2 className="text-sm font-semibold text-gray-800">To-do</h2>
        <span className="text-[11px] text-muted">(behind schedule, release alerts)</span>
      </div>
      {totalItems === 0 ? (
        <div className="text-xs text-gray-400 italic">All caught up.</div>
      ) : (
        <div className="text-xs space-y-1">
          {/* Blocked-day conflicts */}
          {blockedConflicts.length > 0 && (
            <section className="mb-3">
              <h3 className="text-[11px] uppercase text-red-700 font-semibold mb-1.5 flex items-center gap-1">
                <AlertTriangle size={11} /> Surgery on blocked day ({blockedConflicts.length})
              </h3>
              <ul className="space-y-1">
                {blockedConflicts.map(c => (
                  <li key={c.surgery_id}
                      className="flex items-center justify-between border border-red-200 bg-red-50 rounded px-3 py-2">
                    <button onClick={() => onOpen(c.surgery_id)}
                            className="text-left text-[12px] flex-1 hover:underline">
                      <strong className="text-red-800">{c.patient_name}</strong>
                      <span className="text-gray-600"> · {fmt.date(c.scheduled_date)} · {c.facility || '—'}</span>
                      <div className="text-[11px] text-gray-500">
                        Blocked: {c.blackout_label || c.blackout_reason} ({c.blackout_scope})
                      </div>
                    </button>
                    <button onClick={() => onResolveConflict(c.surgery_id)}
                            className="text-[11px] px-2 py-1 rounded border border-red-300 text-red-700 hover:bg-red-100">
                      Mark hospital notified
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {/* Hospital release-back alerts */}
          <ul className="space-y-1">
          {hospitalUnbooked.map(b => (
            <li key={`hosp-${b.block_day_id}`}
                className="flex items-baseline justify-between gap-2 px-1 py-0.5 rounded bg-red-50/40">
              <span>
                <span className="text-[10px] uppercase tracking-wide bg-red-100 text-red-700 px-1 py-0.5 rounded mr-1">
                  Release
                </span>
                <strong>{fmt.date(b.block_date)}</strong>
                <span className="text-gray-500"> — {b.facility === 'medstar' ? 'MedStar' : 'CRMC'} unbooked, release back to hospital</span>
              </span>
              {b.alerted && <span className="text-[10px] text-gray-500 shrink-0">alerted</span>}
            </li>
          ))}
          {/* Office underbooked alerts */}
          {officeUnderbooked.map(b => (
            <li key={`off-${b.block_day_id}`}
                className="flex items-baseline justify-between gap-2 px-1 py-0.5 rounded bg-violet-50/40">
              <span>
                <span className="text-[10px] uppercase tracking-wide bg-violet-100 text-violet-700 px-1 py-0.5 rounded mr-1">
                  Open clinic
                </span>
                <strong>{fmt.date(b.block_date)}</strong>
                <span className="text-gray-500"> — only {b.booked}/{b.needed} office cases booked, open the rest for clinic</span>
              </span>
              {b.alerted && <span className="text-[10px] text-gray-500 shrink-0">alerted</span>}
            </li>
          ))}
          {/* Behind-schedule surgery to-dos */}
          {todos.map(t => (
            <li key={t.surgery_id}
                className="flex items-baseline justify-between gap-2 cursor-pointer hover:bg-amber-50 px-1 py-0.5 rounded"
                onClick={() => onOpen(t.surgery_id)}>
              <span><strong>{t.patient_name}</strong> <span className="text-gray-500">— {t.milestone}</span></span>
              <span className="text-amber-700 shrink-0">{t.hours_overdue}h</span>
            </li>
          ))}
          </ul>
        </div>
      )}
    </div>
  )
}


function MilestoneGroup({ group, onOpen }) {
  const [open, setOpen] = useState(true)
  return (
    <div className="card !p-0 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full px-4 py-2 bg-plum-50/50 hover:bg-plum-50 flex items-baseline justify-between text-left"
      >
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-semibold text-gray-800">{group.title}</span>
          <span className="text-xs text-gray-500">({group.items.length})</span>
        </div>
        <span className="text-xs text-muted">{open ? '▼' : '▶'}</span>
      </button>
      {open && (
        <table className="w-full text-xs">
          <thead className="bg-gray-50 text-gray-600 text-[10px] uppercase tracking-wide">
            <tr>
              <th className="text-left px-3 py-1">Patient</th>
              <th className="text-left px-2 py-1">Chart#</th>
              <th className="text-left px-2 py-1">Procedure</th>
              <th className="text-left px-2 py-1">Facility</th>
              <th className="text-left px-2 py-1">Surgery date</th>
              <th className="text-left px-2 py-1">Status</th>
              <th className="text-right px-3 py-1">Behind</th>
            </tr>
          </thead>
          <tbody>
            {group.items.map(s => <SurgeryRow key={s.id} s={s} onOpen={() => onOpen(s.id)} />)}
          </tbody>
        </table>
      )}
    </div>
  )
}


function SurgeryRow({ s, onOpen }) {
  const { labelOf } = useFacilities()
  const procDescr = (s.procedures || [])[0]?.description || ''
  const cpts = (s.procedures || []).map(p => p.cpt).filter(Boolean).join(', ')
  const eligibles = (s.eligible_facilities || [])
                      .map(f => labelOf(f))
                      .join(' or ')

  return (
    <tr className="border-t border-gray-100 hover:bg-plum-50/30 cursor-pointer" onClick={onOpen}>
      <td className="px-3 py-1.5">
        <div className="flex items-center gap-1">
          {s.urgency === "urgent" && <span title="Urgent">🚨</span>}
          <span className="font-medium">{s.patient_name}</span>
        </div>
      </td>
      <td className="px-2 py-1.5 font-mono text-[10px]">{s.chart_number}</td>
      <td className="px-2 py-1.5 max-w-[280px] truncate" title={procDescr}>
        {s.is_robotic && <span className="text-blue-700 font-semibold mr-1">🤖</span>}
        {procDescr || <span className="text-gray-400 italic">—</span>}
        {cpts && <span className="text-gray-400 ml-1">[{cpts}]</span>}
      </td>
      <td className="px-2 py-1.5">
        {s.selected_facility
          ? <span className="text-gray-700">{labelOf(s.selected_facility)}</span>
          : <span className="text-amber-700 italic" title={eligibles}>{eligibles || '—'}</span>}
      </td>
      <td className="px-2 py-1.5">
        {s.scheduled_date
          ? <span className="font-mono text-gray-700">{fmt.date(s.scheduled_date)}</span>
          : <span className="text-gray-400">—</span>}
      </td>
      <td className="px-2 py-1.5">
        <span className={`text-[10px] px-1.5 py-0.5 rounded ${STATUS_TONE[s.status] || 'bg-gray-100'}`}>
          {STATUS_LABEL[s.status] || s.status}
        </span>
        {s.sub_flag && (
          <span className="text-[9px] text-gray-500 ml-1">· {s.sub_flag.replace(/_/g, ' ')}</span>
        )}
      </td>
      <td className="px-3 py-1.5 text-right">
        {s.behind_schedule
          ? <span className={`text-[11px] font-semibold ${s.hours_overdue > 48 ? 'text-red-700' : 'text-amber-700'}`}>
              {s.hours_overdue > 48 ? `${Math.floor(s.hours_overdue / 24)}d late` : `${s.hours_overdue}h`}
            </span>
          : <span className="text-green-700 text-[11px]">●</span>}
      </td>
    </tr>
  )
}


function UploadDemographicsButton() {
  const { has } = useCurrentUser()
  if (!has?.('surgery:work')) return null
  return (
    <Link to="/surgery/bulk-import"
          className="btn-secondary text-sm flex items-center gap-1">
      <Upload size={13} /> Upload Surgery Patient Demographics
    </Link>
  )
}


function UploadOrderButton() {
  const { has } = useCurrentUser()
  const [open, setOpen] = useState(false)
  if (!has?.('surgery:work')) return null
  return (
    <>
      <button onClick={() => setOpen(true)}
              className="btn-primary text-sm flex items-center gap-1">
        <Upload size={13} /> Upload Surgery Order
      </button>
      {open && <UploadDrawer onClose={() => setOpen(false)} />}
    </>
  )
}


function UploadDrawer({ onClose }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [file, setFile] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const upload = useMutation({
    mutationFn: () => {
      const fd = new FormData()
      fd.append('file', file)
      return api.post('/surgery/orders/upload', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      }).then(r => r.data)
    },
    onSuccess: (data) => {
      setResult(data); setError(null)
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
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
          <h2 className="font-serif font-semibold text-ink text-[18px]">Upload Surgery Order</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        <div className="p-6 space-y-4">
          <div className="card !p-3 text-xs text-gray-700 bg-gray-50">
            Upload a ModMed surgery order PDF. The system will use Claude to
            extract patient, procedure, insurance, and facility info. If the
            chart number matches a row already imported via{' '}
            <strong>Upload Surgery Patient Demographics</strong>, the order
            will be mapped onto that existing row; otherwise a new surgery is
            created in <strong>incomplete</strong> status. Review and then
            mark as <strong>new</strong>.
          </div>

          <div className="card !p-3 space-y-2">
            <label className="flex items-center gap-2 text-sm">
              <FileText size={14} className="text-plum-700" />
              <span>Pick the order PDF</span>
            </label>
            <input
              type="file" accept=".pdf"
              className="text-xs"
              onChange={e => {
                setFile(e.target.files?.[0] || null)
                setResult(null); setError(null)
              }}
            />
            <button
              className="btn-primary text-sm flex items-center gap-1 mt-1 disabled:opacity-60"
              disabled={!file || upload.isPending}
              onClick={() => upload.mutate()}>
              <Upload size={13} /> {upload.isPending ? 'Parsing with Claude…' : 'Parse + create'}
            </button>
          </div>

          {error && (
            <div className="card !p-3 bg-red-50 border-red-200 text-xs text-red-800">
              ✗ {error}
            </div>
          )}

          {result?.duplicate && (
            <div className="card !p-3 bg-amber-50 border-amber-200 text-xs text-amber-900">
              <div className="font-semibold">⚠ Possible duplicate</div>
              <p className="mt-1">{result.message}</p>
              <div className="flex gap-2 mt-2">
                <button className="btn-secondary text-xs"
                        onClick={() => { onClose(); navigate(`/surgery/${result.existing_id}`) }}>
                  Open existing surgery
                </button>
              </div>
            </div>
          )}

          {result && !result.duplicate && (
            <div className="card !p-3 bg-green-50 border-green-200 text-xs text-green-900 space-y-1">
              <div className="font-semibold">
                {result.merged ? '✓ Order mapped to existing patient' : '✓ Surgery created'}
              </div>
              <p>{result.message}</p>
              <div className="text-[11px] text-gray-700 mt-2 space-y-0.5">
                <div><strong>Patient:</strong> {result.extracted.patient_name} (chart {result.extracted.chart_number})</div>
                {(result.extracted.procedures || []).map((p, i) => (
                  <div key={i}><strong>Procedure:</strong> {p.description}{p.cpt && ` [${p.cpt}]`}</div>
                ))}
                <div><strong>Facility:</strong> {(result.extracted.eligible_facilities || []).join(' or ') || '—'}</div>
                <div><strong>Insurance:</strong> {result.extracted.primary_insurance || '—'}</div>
                {result.extracted.is_robotic && <div className="text-blue-700">🤖 Robotic — auto-routed to MedStar</div>}
              </div>
              <div className="flex gap-2 mt-3">
                <button className="btn-primary text-xs"
                        onClick={() => { onClose(); navigate(`/surgery/${result.id}`) }}>
                  Open surgery →
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}


function ReleasePanel({ title, subtitle, tone, rows }) {
  const tones = {
    amber:  'bg-amber-50/40 border-amber-200',
    violet: 'bg-violet-50/40 border-violet-200',
  }
  return (
    <div className={`card !p-3 border ${tones[tone] || tones.amber}`}>
      <div className="text-sm font-semibold text-gray-800">{title}</div>
      <div className="text-[11px] text-gray-600 mb-2">{subtitle}</div>
      <ul className="text-xs space-y-1">
        {rows.map(r => (
          <li key={r.key} className="flex items-baseline justify-between gap-2">
            <span><strong>{r.primary}</strong> <span className="text-gray-500">{r.secondary}</span></span>
            {r.alerted && <span className="text-[9px] text-gray-400 italic">notified</span>}
          </li>
        ))}
      </ul>
    </div>
  )
}


function MessagesLink() {
  const { data } = useQuery({
    queryKey: ['staff-inbox'],
    queryFn: () => api.get('/staff/messages/inbox').then(r => r.data),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })
  const count = data?.count || 0
  return (
    <Link to="/surgery/messages"
           className="btn-secondary text-sm flex items-center gap-1">
      <MessageSquare size={13} /> Messages
      {count > 0 && (
        <span className="bg-red-500 text-white text-[10px] rounded-full
                            px-1.5 py-0.5 font-semibold">
          {count}
        </span>
      )}
    </Link>
  )
}


function ManualCreateButton() {
  const { has } = useCurrentUser()
  const [open, setOpen] = useState(false)
  if (!has?.('surgery:work')) return null
  return (
    <>
      <button onClick={() => setOpen(true)}
              className="btn-secondary text-sm flex items-center gap-1">
        + New surgery
      </button>
      {open && <ManualCreateDrawer onClose={() => setOpen(false)} />}
    </>
  )
}


function ManualCreateDrawer({ onClose }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { labelOf } = useFacilities()
  // Picklists drive insurance/surgeon dropdowns
  const { data: picks } = useQuery({
    queryKey: ['surgery-picklists'],
    queryFn: () => api.get('/surgery/picklists').then(r => r.data),
    staleTime: 300_000,
  })
  const insuranceOpts = picks?.insurance_companies || []
  const surgeonOpts   = picks?.surgeons || []
  const procedureOpts = picks?.procedures || []
  const [form, setForm] = useState({
    chart_number: '',
    patient_name: '',
    dob: '',
    phone: '',
    email: '',
    address_street: '',
    address_city: '',
    address_state: '',
    address_zip: '',
    primary_insurance: '',
    primary_member_id: '',
    secondary_insurance: '',
    secondary_member_id: '',
    surgeon_primary: '',
    surgery_name: '',
    procedures: [{ cpt: '', description: '' }],
    diagnoses:  [{ icd: '', description: '' }],
    eligible_facilities: ['medstar'],
    estimated_minutes: 180,
    preop_date: '',
    is_robotic: false,
    is_urgent: false,
    notes: '',
  })
  const [error, setError] = useState(null)

  const requiredMissing =
    !form.chart_number.trim() || !form.patient_name.trim()
    || !form.dob || !form.phone.trim() || !form.email.trim()
    || !form.address_street.trim() || !form.address_city.trim()
    || !form.address_state.trim() || !form.address_zip.trim()
    || !form.primary_insurance || !form.primary_member_id.trim()
    || !form.surgeon_primary || !form.surgery_name
    || !form.preop_date
    || !form.estimated_minutes
    || !form.eligible_facilities.length
    || !form.procedures.some(p => (p.cpt || '').trim() || (p.description || '').trim())
    || !form.diagnoses.some(d => (d.icd || '').trim() || (d.description || '').trim())

  function pickSurgery(label) {
    // The dropdown is keyed by description; auto-fill the first procedure row
    // with the matching CPT + description so coordinators don't double-enter.
    const match = procedureOpts.find(p => p.description === label)
    setForm(f => ({
      ...f,
      surgery_name: label,
      procedures: match
        ? [{ cpt: match.cpt, description: match.description },
           ...f.procedures.slice(1)]
        : f.procedures,
    }))
  }

  const create = useMutation({
    mutationFn: () => api.post('/surgery/manual', {
      chart_number: form.chart_number,
      patient_name: form.patient_name,
      dob: form.dob || null,
      phone: form.phone || null,
      email: form.email || null,
      address_street: form.address_street.trim(),
      address_city:   form.address_city.trim(),
      address_state:  form.address_state.trim(),
      address_zip:    form.address_zip.trim(),
      primary_insurance: form.primary_insurance || null,
      primary_member_id: form.primary_member_id || null,
      secondary_insurance: form.secondary_insurance || null,
      secondary_member_id: form.secondary_member_id || null,
      surgeon_primary: form.surgeon_primary || null,
      surgery_name: form.surgery_name,
      preop_date: form.preop_date,
      procedures: (form.procedures || [])
        .map(p => ({ cpt: (p.cpt || '').trim() || null,
                       description: (p.description || '').trim() || null }))
        .filter(p => p.cpt || p.description),
      diagnoses: (form.diagnoses || [])
        .map(d => ({ icd: (d.icd || '').trim() || null,
                       description: (d.description || '').trim() || null }))
        .filter(d => d.icd || d.description),
      eligible_facilities: form.eligible_facilities,
      estimated_minutes: form.estimated_minutes ? Number(form.estimated_minutes) : null,
      is_robotic: form.is_robotic,
      is_urgent: form.is_urgent,
      notes: form.notes || null,
    }).then(r => r.data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
      onClose()
      navigate(`/surgery/${data.id}`)
    },
    onError: (e) => {
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (e?.message || 'Create failed'))
    },
  })

  function toggleFacility(f) {
    const set = new Set(form.eligible_facilities)
    if (set.has(f)) set.delete(f)
    else set.add(f)
    setForm({ ...form, eligible_facilities: Array.from(set) })
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-2xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-6 py-4 flex items-center justify-between z-10">
          <h2 className="font-serif font-semibold text-ink text-[18px]">+ New surgery (manual)</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        <div className="p-6 space-y-3">
          <p className="text-xs text-gray-600">
            Use this when you don't have a PDF order to upload — e.g. patient was scheduled
            directly in ModMed and never had an order generated. Surgery is created in
            <code> incomplete</code> status; review and click <strong>Mark as new</strong> on
            the detail page to spawn milestones.
          </p>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Chart # *">
              <input className="input text-sm font-mono" value={form.chart_number}
                     onChange={e => setForm({ ...form, chart_number: e.target.value })} />
            </Field>
            <Field label="Patient name (Last, First) *">
              <input className="input text-sm" value={form.patient_name}
                     placeholder="Owens, Traci"
                     onChange={e => setForm({ ...form, patient_name: e.target.value })} />
            </Field>
            <Field label="DOB *">
              <input className="input text-sm font-mono" type="date" value={form.dob}
                     onChange={e => setForm({ ...form, dob: e.target.value })} />
            </Field>
            <Field label="Phone *">
              <input className="input text-sm" value={form.phone}
                     onChange={e => setForm({ ...form, phone: e.target.value })} />
            </Field>
            <Field label="Email *">
              <input className="input text-sm" value={form.email}
                     onChange={e => setForm({ ...form, email: e.target.value })} />
            </Field>
            <div className="col-span-2">
              <Field label="Street address *">
                <input className="input text-sm" value={form.address_street}
                       placeholder="123 Main St"
                       onChange={e => setForm({ ...form, address_street: e.target.value })} />
              </Field>
            </div>
            <Field label="City *">
              <input className="input text-sm" value={form.address_city}
                     onChange={e => setForm({ ...form, address_city: e.target.value })} />
            </Field>
            <div className="grid grid-cols-[1fr_1fr] gap-2">
              <Field label="State *">
                <input className="input text-sm" value={form.address_state}
                       maxLength={2} placeholder="MD"
                       onChange={e => setForm({ ...form, address_state: e.target.value.toUpperCase() })} />
              </Field>
              <Field label="ZIP *">
                <input className="input text-sm font-mono" value={form.address_zip}
                       placeholder="20601"
                       onChange={e => setForm({ ...form, address_zip: e.target.value })} />
              </Field>
            </div>
            <Field label="Surgeon *">
              <select className="input text-sm" value={form.surgeon_primary}
                       onChange={e => setForm({ ...form, surgeon_primary: e.target.value })}>
                <option value="">— select —</option>
                {surgeonOpts.map(n => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </Field>
            <Field label="Pre-op date *">
              <input className="input text-sm font-mono" type="date" value={form.preop_date}
                     onChange={e => setForm({ ...form, preop_date: e.target.value })} />
            </Field>
            <div className="col-span-2">
              <Field label="Surgery name *">
                <select className="input text-sm" value={form.surgery_name}
                         onChange={e => pickSurgery(e.target.value)}>
                  <option value="">— select a surgery —</option>
                  {procedureOpts.map(p => (
                    <option key={p.cpt} value={p.description}>
                      {p.description} ({p.cpt})
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <Field label="Primary insurance *">
              <select className="input text-sm" value={form.primary_insurance}
                       onChange={e => setForm({ ...form, primary_insurance: e.target.value })}>
                <option value="">— select —</option>
                {insuranceOpts.map(n => (
                  <option key={`p-${n}`} value={n}>{n}</option>
                ))}
              </select>
            </Field>
            <Field label="Primary member ID *">
              <input className="input text-sm font-mono" value={form.primary_member_id}
                     onChange={e => setForm({ ...form, primary_member_id: e.target.value })} />
            </Field>
            <Field label="Secondary insurance">
              <select className="input text-sm" value={form.secondary_insurance}
                       onChange={e => setForm({ ...form, secondary_insurance: e.target.value })}>
                <option value="">— none —</option>
                {insuranceOpts
                  .filter(n => n !== form.primary_insurance)
                  .map(n => (
                    <option key={`s-${n}`} value={n}>{n}</option>
                  ))}
              </select>
            </Field>
            <Field label="Secondary member ID">
              <input className="input text-sm font-mono" value={form.secondary_member_id}
                     onChange={e => setForm({ ...form, secondary_member_id: e.target.value })} />
            </Field>
            {/* Procedures (multi) */}
            <div className="col-span-2">
              <div className="flex items-baseline justify-between mb-1">
                <label className="text-[10px] uppercase text-gray-500">Procedure CPT codes</label>
                <button type="button"
                        className="text-[11px] text-plum-700 hover:underline"
                        onClick={() => setForm(f => ({
                          ...f, procedures: [...f.procedures, { cpt: '', description: '' }],
                        }))}>
                  + Add CPT
                </button>
              </div>
              <div className="space-y-1.5">
                {form.procedures.map((p, i) => (
                  <div key={i} className="grid grid-cols-[120px_1fr_24px] gap-2 items-center">
                    <input className="input text-sm font-mono"
                            value={p.cpt}
                            placeholder={i === 0 ? '58558' : 'CPT'}
                            onChange={e => setForm(f => ({
                              ...f,
                              procedures: f.procedures.map((row, j) =>
                                j === i ? { ...row, cpt: e.target.value } : row),
                            }))} />
                    <input className="input text-sm"
                            value={p.description}
                            placeholder={i === 0 ? 'Hysteroscopy D&C' : 'description'}
                            onChange={e => setForm(f => ({
                              ...f,
                              procedures: f.procedures.map((row, j) =>
                                j === i ? { ...row, description: e.target.value } : row),
                            }))} />
                    <button type="button"
                            className="text-gray-400 hover:text-danger"
                            title="Remove this CPT"
                            disabled={form.procedures.length === 1}
                            onClick={() => setForm(f => ({
                              ...f, procedures: f.procedures.filter((_, j) => j !== i),
                            }))}>
                      <X size={13}/>
                    </button>
                  </div>
                ))}
              </div>
            </div>

            {/* Diagnoses (multi) */}
            <div className="col-span-2">
              <div className="flex items-baseline justify-between mb-1">
                <label className="text-[10px] uppercase text-gray-500">Diagnosis ICD-10 codes</label>
                <button type="button"
                        className="text-[11px] text-plum-700 hover:underline"
                        onClick={() => setForm(f => ({
                          ...f, diagnoses: [...f.diagnoses, { icd: '', description: '' }],
                        }))}>
                  + Add ICD-10
                </button>
              </div>
              <div className="space-y-1.5">
                {form.diagnoses.map((d, i) => (
                  <div key={i} className="grid grid-cols-[120px_1fr_24px] gap-2 items-center">
                    <input className="input text-sm font-mono"
                            value={d.icd}
                            placeholder={i === 0 ? 'N92.0' : 'ICD-10'}
                            onChange={e => setForm(f => ({
                              ...f,
                              diagnoses: f.diagnoses.map((row, j) =>
                                j === i ? { ...row, icd: e.target.value } : row),
                            }))} />
                    <input className="input text-sm"
                            value={d.description}
                            placeholder={i === 0 ? 'Heavy menstrual bleeding' : 'description'}
                            onChange={e => setForm(f => ({
                              ...f,
                              diagnoses: f.diagnoses.map((row, j) =>
                                j === i ? { ...row, description: e.target.value } : row),
                            }))} />
                    <button type="button"
                            className="text-gray-400 hover:text-danger"
                            title="Remove this ICD-10"
                            disabled={form.diagnoses.length === 1}
                            onClick={() => setForm(f => ({
                              ...f, diagnoses: f.diagnoses.filter((_, j) => j !== i),
                            }))}>
                      <X size={13}/>
                    </button>
                  </div>
                ))}
              </div>
            </div>
            <Field label="Estimated minutes">
              <input className="input text-sm font-mono" type="number" value={form.estimated_minutes}
                     onChange={e => setForm({ ...form, estimated_minutes: e.target.value })} />
            </Field>
            <Field label="Eligible facilities">
              <div className="flex gap-1.5">
                {['medstar', 'crmc', 'office'].map(f => (
                  <button key={f} type="button"
                          onClick={() => toggleFacility(f)}
                          className={`text-xs px-2 py-1 rounded border ${
                            form.eligible_facilities.includes(f)
                              ? 'bg-plum-100 border-plum-300 text-plum-700 font-semibold'
                              : 'bg-white border-gray-200 text-muted'
                          }`}>
                    {labelOf(f)}
                  </button>
                ))}
              </div>
            </Field>
          </div>

          <div className="flex gap-4 text-sm">
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={form.is_robotic}
                     onChange={e => setForm({ ...form, is_robotic: e.target.checked })} />
              Robotic case (auto-routes to MedStar)
            </label>
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={form.is_urgent}
                     onChange={e => setForm({ ...form, is_urgent: e.target.checked })} />
              🚨 Urgent
            </label>
          </div>

          <Field label="Notes">
            <textarea className="input text-sm" rows={2} value={form.notes}
                      onChange={e => setForm({ ...form, notes: e.target.value })} />
          </Field>

          {create.isError && (
            <div className="text-xs text-red-600">
              {create.error?.response?.data?.detail || create.error.message}
            </div>
          )}

          {requiredMissing && (
            <div className="text-xs text-amber-700">
              All starred fields are required (Secondary insurance and Notes are optional).
            </div>
          )}
          {error && !create.isError && (
            <div className="text-xs text-red-600">{error}</div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button className="btn-secondary text-sm" onClick={onClose}>Cancel</button>
            <button className="btn-primary text-sm"
                    onClick={() => create.mutate()}
                    disabled={create.isPending || requiredMissing}>
              {create.isPending ? 'Creating…' : 'Create surgery'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}


function Field({ label, children }) {
  return (
    <div>
      <div className="text-[10px] uppercase text-gray-500 tracking-wide mb-1">{label}</div>
      {children}
    </div>
  )
}


// ─── Hospital release as checkable to-do ─────────────────────────

function HospitalReleaseTodo({ rows }) {
  const qc = useQueryClient()
  return (
    <div className="card !p-3 border bg-amber-50/40 border-amber-200">
      <div className="text-sm font-semibold text-gray-800">Release back to hospital</div>
      <div className="text-[11px] text-gray-600 mb-2">
        Hospital block days within 14 days with 0 cases booked. Check off each
        once you've called the hospital to release the day.
      </div>
      <ul className="text-xs space-y-1">
        {rows.map(r => <ReleaseRow key={r.block_day_id} row={r} qc={qc} />)}
      </ul>
    </div>
  )
}


function ReleaseRow({ row, qc }) {
  const [busy, setBusy] = useState(false)

  async function release() {
    setBusy(true)
    try {
      await api.post(`/surgery/admin/block-days/${row.block_day_id}/mark-released`)
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
    } finally { setBusy(false) }
  }

  return (
    <li className="flex items-center gap-2">
      <button
        type="button"
        onClick={release}
        disabled={busy}
        title="Mark released back to hospital"
        className="w-4 h-4 rounded border border-gray-300 bg-white hover:bg-amber-100 hover:border-amber-400 flex items-center justify-center shrink-0"
      >
        {busy && <Clock size={9} className="animate-spin text-amber-600" />}
      </button>
      <span className="flex-1">
        <strong>{row.block_date}</strong>{' '}
        <span className="text-gray-500">
          {row.facility === 'medstar' ? 'MedStar' : 'CRMC'} · {row.hours}
        </span>
      </span>
      {row.alerted && <span className="text-[9px] text-gray-400 italic">notified</span>}
    </li>
  )
}


// ─── Bucket-grouped list ─────────────────────────────────────────

function BucketGroup({ group, onOpen }) {
  const [open, setOpen] = useState(true)
  const tones = {
    amber:   'bg-amber-50/60 hover:bg-amber-50',
    red:     'bg-red-50/60 hover:bg-red-50',
    blue:    'bg-blue-50/60 hover:bg-blue-50',
    green:   'bg-green-50/60 hover:bg-green-50',
    violet:  'bg-violet-50/60 hover:bg-violet-50',
    gray:    'bg-gray-50/60 hover:bg-gray-50',
  }
  return (
    <div className="card !p-0 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className={`w-full px-4 py-2 ${tones[group.tone] || tones.gray} flex items-baseline justify-between text-left`}
      >
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-semibold text-gray-800">{group.title}</span>
          <span className="text-xs text-gray-500">({group.items.length})</span>
        </div>
        <span className="text-xs text-muted">{open ? '▼' : '▶'}</span>
      </button>
      {open && (
        <table className="w-full text-xs">
          <thead className="bg-gray-50 text-gray-600 text-[10px] uppercase tracking-wide">
            <tr>
              <th className="text-left px-3 py-1">Patient</th>
              <th className="text-left px-2 py-1">Chart#</th>
              <th className="text-left px-2 py-1">Procedure</th>
              <th className="text-left px-2 py-1">Facility</th>
              <th className="text-left px-2 py-1">Surgery date</th>
              <th className="text-left px-2 py-1">Status</th>
              <th className="text-right px-3 py-1">Behind</th>
            </tr>
          </thead>
          <tbody>
            {group.items.map(s => <SurgeryRow key={s.id} s={s} onOpen={() => onOpen(s.id)} />)}
          </tbody>
        </table>
      )}
    </div>
  )
}
