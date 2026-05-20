import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, Users, Plus, Search, X, Save, Calendar, Clock,
  AlertTriangle, DollarSign, CheckCircle2, Pill, Package, Star, Trash2,
  ChevronLeft, ChevronRight, Upload, MoreVertical,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import { useCurrentUser } from '../hooks/useCurrentUser'


const VIEWS = [
  { k: 'upcoming',     l: 'Upcoming',         icon: Clock },
  { k: 'roster',       l: 'All patients',     icon: Users },
  { k: 'last_visits',  l: 'Last visits',      icon: Calendar },
  { k: 'recall_due',   l: 'Recall due',       icon: AlertTriangle },
  { k: 'needs_mammo',  l: 'Needs mammo',      icon: AlertTriangle },
  { k: 'needs_dosing', l: 'Needs dosing',     icon: Pill },
  { k: 'ready',        l: 'Ready to insert',  icon: CheckCircle2 },
  { k: 'paid',         l: 'Paid',             icon: DollarSign },
  { k: 'unpaid',       l: 'Unpaid',           icon: DollarSign },
]


export default function PelletPatients() {
  const navigate = useNavigate()
  // Upcoming is the default on login. A saved "default preset" (if any)
  // still overrides this via the applyPreset effect below.
  const [view, setView] = useState('upcoming')
  const [filters, setFilters] = useState({
    search: '', patient_type: '', status: '',
  })
  const [adding, setAdding] = useState(false)
  const [uploading, setUploading] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['pellet-patients', view, filters],
    queryFn: () => api.get('/pellets/patients', {
      params: { view, ...Object.fromEntries(
        Object.entries(filters).filter(([_, v]) => v)
      )},
    }).then(r => r.data),
  })

  // Lean count endpoint — one call, in-memory bucketing on the server
  const counts = useQuery({
    queryKey: ['pellet-patient-counts'],
    queryFn: () => api.get('/pellets/patient-view-counts').then(r => r.data),
    staleTime: 60_000,
  })

  // Saved filter presets (per-user)
  const [savingPreset, setSavingPreset] = useState(false)
  const [presetNameDraft, setPresetNameDraft] = useState('')
  const currentFilters = { view, ...filters }

  function applyPreset(f) {
    setView(f.view ?? 'upcoming')
    setFilters({
      search:       f.search ?? '',
      patient_type: f.patient_type ?? '',
      status:       f.status ?? '',
    })
  }

  const presets = useQuery({
    queryKey: ['pellet-filter-presets'],
    queryFn: () => api.get('/pellets/filter-presets').then(r => r.data),
  })

  // Auto-load default once on first mount
  const [defaultApplied, setDefaultApplied] = useState(false)
  useEffect(() => {
    if (defaultApplied || !presets.data) return
    const def = presets.data.find(p => p.is_default)
    if (def) applyPreset(def.filters_json || {})
    setDefaultApplied(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presets.data, defaultApplied])

  const qc = useQueryClient()
  const savePreset = useMutation({
    mutationFn: (body) => api.post('/pellets/filter-presets', body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-filter-presets'] })
      setSavingPreset(false); setPresetNameDraft('')
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  const deletePreset = useMutation({
    mutationFn: (id) => api.delete(`/pellets/filter-presets/${id}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pellet-filter-presets'] }),
  })
  const setDefaultPreset = useMutation({
    mutationFn: (preset) => api.put(`/pellets/filter-presets/${preset.id}`,
                                      { name: preset.name,
                                        filters_json: preset.filters_json,
                                        is_default: true }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pellet-filter-presets'] }),
  })

  const patients = data?.patients || []

  return (
    <div>
      <div className="flex items-baseline justify-between mb-3 flex-wrap gap-2">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <Users size={22} className="text-plum-700" />
          Pellet patients
        </h1>
        <div className="flex gap-2">
          <Link to="/pellets/inventory" className="btn-secondary text-sm flex items-center gap-1">
            <Package size={13}/> Inventory
          </Link>
          <button className="btn-secondary text-sm flex items-center gap-1"
                  onClick={() => setUploading(true)}>
            <Upload size={13}/> Upload ModMed appts
          </button>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => setAdding(true)}>
            <Plus size={13}/> Enroll patient
          </button>
        </div>
      </div>

      {/* Saved filter presets — chip bar */}
      {(presets.data?.length ?? 0) > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap mb-2">
          <span className="text-[10px] uppercase tracking-wide text-gray-500 mr-1">Saved:</span>
          {presets.data.map(p => (
            <PresetChip key={p.id} preset={p}
                         onLoad={() => applyPreset(p.filters_json || {})}
                         onSetDefault={() => setDefaultPreset.mutate(p)}
                         onDelete={() => {
                           if (window.confirm(`Delete preset "${p.name}"?`)) deletePreset.mutate(p.id)
                         }} />
          ))}
        </div>
      )}

      {/* View tab strip */}
      <div className="border-b border-border-subtle mb-3 overflow-x-auto">
        <nav className="flex gap-0.5 min-w-max">
          {VIEWS.map(t => {
            const Icon = t.icon
            const active = view === t.k
            const cnt = counts.data?.[t.k]
            return (
              <button key={t.k} type="button"
                       onClick={() => setView(t.k)}
                       className={`flex items-center gap-1.5 px-3 py-2 text-[12px] border-b-2 transition whitespace-nowrap ${
                         active
                           ? 'border-plum-600 text-plum-700 font-medium'
                           : 'border-transparent text-gray-500 hover:text-plum-700 hover:border-plum-200'
                       }`}>
                <Icon size={13} />
                {t.l}
                {cnt != null && (
                  <span className={`ml-1 text-[10px] px-1.5 py-0 rounded ${
                    active ? 'bg-plum-100 text-plum-800' : 'bg-gray-100 text-gray-600'
                  }`}>
                    {cnt}
                  </span>
                )}
              </button>
            )
          })}
        </nav>
      </div>

      {/* Search + type filters */}
      <div className="card mb-3">
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-sm">
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Search</label>
            <div className="relative">
              <Search size={11} className="absolute left-2 top-2 text-muted" />
              <input className="input text-sm pl-7 w-full"
                     placeholder="name or chart #"
                     value={filters.search}
                     onChange={e => setFilters({ ...filters, search: e.target.value })} />
            </div>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Patient type</label>
            <select className="input text-sm w-full" value={filters.patient_type}
                    onChange={e => setFilters({ ...filters, patient_type: e.target.value })}>
              <option value="">All</option>
              <option value="new">New ($500)</option>
              <option value="established">Established ($400)</option>
            </select>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1 flex items-center justify-between">
              <span>Status</span>
              <ActiveMonthsControl />
            </label>
            <select className="input text-sm w-full" value={filters.status}
                    onChange={e => setFilters({ ...filters, status: e.target.value })}>
              <option value="">All</option>
              <option value="active">Active (seen recently)</option>
              <option value="inactive">Inactive (not seen)</option>
              <option value="declined">Declined</option>
            </select>
          </div>
        </div>
        <div className="flex items-center gap-3 mt-2 text-[11px] text-gray-500">
          <span>Showing <strong>{data?.total ?? 0}</strong> patient{data?.total === 1 ? '' : 's'}</span>
          <button type="button"
                   onClick={() => setSavingPreset(true)}
                   className="text-plum-700 hover:underline flex items-center gap-0.5">
            <Save size={11}/> Save current view as preset
          </button>
        </div>
        {savingPreset && (
          <div className="mt-2 pt-2 border-t border-gray-100 flex items-end gap-2">
            <div className="flex-1 max-w-xs">
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Preset name</label>
              <input className="input text-sm w-full"
                     autoFocus
                     placeholder="e.g. Recall-due established"
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
            <button className="btn-primary text-xs flex items-center gap-1"
                     disabled={!presetNameDraft.trim() || savePreset.isPending}
                     onClick={() => savePreset.mutate({ name: presetNameDraft.trim(),
                                                         filters_json: currentFilters,
                                                         is_default: false })}>
              <Save size={11}/> Save
            </button>
            <button className="text-xs text-muted hover:underline"
                     onClick={() => { setSavingPreset(false); setPresetNameDraft('') }}>
              Cancel
            </button>
          </div>
        )}
      </div>

      {/* View body — calendar for upcoming, table for everything else */}
      {view === 'upcoming' ? (
        <UpcomingCalendar onOpen={(id) => navigate(`/pellets/patients/${id}`)} />
      ) : (
        <ViewTable view={view} patients={patients} isLoading={isLoading}
                    onOpen={(id) => navigate(`/pellets/patients/${id}`)} />
      )}

      {adding && (
        <EnrollDrawer onClose={() => setAdding(false)} />
      )}
      {uploading && (
        <ApptUploadDrawer onClose={() => setUploading(false)} />
      )}
    </div>
  )
}


// ─── 7-day calendar (Upcoming view) ─────────────────────────────────

function toISO(d) {
  return d.toISOString().slice(0, 10)
}
function addDays(d, n) {
  const out = new Date(d)
  out.setDate(out.getDate() + n)
  return out
}
function dayLabel(d) {
  return d.toLocaleDateString('en-US', { weekday: 'short' })
}
function monthDay(d) {
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}


function ActiveMonthsControl() {
  const { has } = useCurrentUser()
  const isAdmin = has?.('pellet:manage') || has?.('user:manage')
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')

  const { data } = useQuery({
    queryKey: ['pellet-active-months'],
    queryFn: () => api.get('/pellets/settings/active-months').then(r => r.data),
    staleTime: 60_000,
  })
  const months = data?.months ?? 6

  const save = useMutation({
    mutationFn: () => api.patch('/pellets/settings/active-months',
                                  { months: Number(draft) }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-active-months'] })
      qc.invalidateQueries({ queryKey: ['pellet-patients'] })
      qc.invalidateQueries({ queryKey: ['pellet-patient-counts'] })
      setEditing(false)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  if (!isAdmin) {
    return <span className="text-[10px] text-gray-400 normal-case">
      seen ≤ {months}mo = active
    </span>
  }
  return editing ? (
    <span className="flex items-center gap-1 normal-case text-[10px]">
      <input type="number" min="1" max="120"
              className="input text-[10px] py-0 w-12"
              value={draft} onChange={e => setDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') save.mutate()
                if (e.key === 'Escape') setEditing(false)
              }} autoFocus />
      <span>mo</span>
      <button className="text-plum-700 hover:underline"
              onClick={() => save.mutate()}
              disabled={!draft || save.isPending}>save</button>
      <button className="text-gray-400 hover:underline"
              onClick={() => setEditing(false)}>cancel</button>
    </span>
  ) : (
    <button type="button"
             className="text-[10px] text-plum-700 hover:underline normal-case"
             title="Edit the active-patient threshold"
             onClick={() => { setDraft(String(months)); setEditing(true) }}>
      seen ≤ {months}mo = active ✎
    </button>
  )
}


function UpcomingCalendar({ onOpen }) {
  const [start, setStart] = useState(() => {
    const t = new Date()
    t.setHours(0, 0, 0, 0)
    return t
  })

  const days = Array.from({ length: 7 }, (_, i) => addDays(start, i))
  const fromDate = toISO(days[0])
  const toDate = toISO(days[6])
  const today = toISO(new Date())

  const { data, isLoading } = useQuery({
    queryKey: ['pellet-upcoming-calendar', fromDate, toDate],
    queryFn: () => api.get('/pellets/patients', {
      params: { view: 'upcoming', from_date: fromDate, to_date: toDate, per_page: 500 },
    }).then(r => r.data),
  })

  const byDay = {}
  for (const p of (data?.patients || [])) {
    const d = p.next_scheduled_date
    if (!d) continue
    if (!byDay[d]) byDay[d] = []
    byDay[d].push(p)
  }

  return (
    <div>
      {/* Toolbar */}
      <div className="card mb-3 flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-1.5">
          <button className="btn-secondary text-xs flex items-center gap-1 py-1.5 px-2"
                   onClick={() => setStart(s => addDays(s, -7))}
                   title="Previous 7 days">
            <ChevronLeft size={13}/> Prev
          </button>
          <button className="btn-secondary text-xs py-1.5 px-2"
                   onClick={() => {
                     const t = new Date(); t.setHours(0, 0, 0, 0)
                     setStart(t)
                   }}
                   title="Jump to today">
            Today
          </button>
          <button className="btn-secondary text-xs flex items-center gap-1 py-1.5 px-2"
                   onClick={() => setStart(s => addDays(s, 7))}
                   title="Next 7 days">
            Next <ChevronRight size={13}/>
          </button>
        </div>
        <div className="text-[13px] text-gray-700 font-medium">
          {monthDay(days[0])} — {monthDay(days[6])}, {days[0].getFullYear()}
        </div>
        <div className="flex items-center gap-1">
          <label className="text-[11px] text-gray-500">Jump to:</label>
          <input type="date" className="input text-xs py-1"
                  value={toISO(start)}
                  onChange={e => {
                    const v = e.target.value
                    if (v) {
                      const d = new Date(v + 'T00:00:00')
                      setStart(d)
                    }
                  }} />
        </div>
      </div>

      {isLoading && (
        <div className="card text-gray-400 italic">Loading week…</div>
      )}

      {/* 7 day columns */}
      <div className="grid grid-cols-1 sm:grid-cols-7 gap-2">
        {days.map(d => {
          const iso = toISO(d)
          const list = byDay[iso] || []
          const isToday = iso === today
          const isWeekend = d.getDay() === 0 || d.getDay() === 6
          return (
            <div key={iso}
                 className={`card !p-2 flex flex-col min-h-[180px] ${
                   isToday ? 'ring-2 ring-plum-400' :
                   isWeekend ? 'bg-gray-50/40' : ''
                 }`}>
              <div className="text-[10px] uppercase tracking-wide text-gray-500">
                {dayLabel(d)}
              </div>
              <div className={`text-[15px] font-bold mb-1 ${isToday ? 'text-plum-700' : ''}`}>
                {d.getDate()}
                <span className="ml-1 text-[10px] font-normal text-gray-500">
                  {d.toLocaleDateString('en-US', { month: 'short' })}
                </span>
              </div>
              <div className="flex-1 space-y-1 overflow-y-auto">
                {list.length === 0 ? (
                  <div className="text-[10px] text-gray-300 italic">—</div>
                ) : (
                  list.map(p => (
                    <CalendarVisitCard key={p.id} patient={p} onOpen={onOpen} />
                  ))
                )}
              </div>
              {list.length > 0 && (
                <div className="text-[10px] text-gray-500 text-right mt-1">
                  {list.length} pt{list.length === 1 ? '' : 's'}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}


function CalendarVisitCard({ patient, onOpen }) {
  const qc = useQueryClient()
  const [menuOpen, setMenuOpen]               = useState(false)
  const [rescheduleOpen, setRescheduleOpen]   = useState(false)
  const [cancelOpen, setCancelOpen]           = useState(false)

  // Status pip — payment + bag state at a glance
  const paid = patient.active_visit_payment_status === 'collected'
  const sent = patient.active_visit_payment_status === 'sent'
  const hasDoses = patient.active_visit_has_doses
  // Bagged = the visit's 'bagged' milestone is done (Fill Bag, dose-card,
  // or manual advance). See active_visit_bagged in the API.
  const bagged = !!patient.active_visit_bagged

  // Color: green = bagged + paid, blue = paid, amber = not paid yet
  const tone =
    bagged && paid        ? 'bg-green-50 border-green-200 hover:bg-green-100' :
    paid                  ? 'bg-blue-50 border-blue-200 hover:bg-blue-100' :
    sent                  ? 'bg-amber-50 border-amber-200 hover:bg-amber-100' :
                            'bg-gray-50 border-gray-200 hover:bg-gray-100'

  // The kebab needs the visit ID, not the patient ID.
  const visitId = patient.active_visit_id

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return
    const onClick = () => setMenuOpen(false)
    window.addEventListener('click', onClick)
    return () => window.removeEventListener('click', onClick)
  }, [menuOpen])

  const isNew = patient.patient_type === 'new'
  const mammoOk = !!patient.mammo_verified
  const labsOk  = !!patient.labs_verified

  return (
    <div className={`relative border rounded transition ${tone}`}>
      <button type="button"
               onClick={() => onOpen(patient.id)}
               className="block w-full text-left p-1.5 pr-6">
        <div className="flex items-center gap-1">
          <span className={`text-[8px] font-bold uppercase tracking-wide px-1 rounded ${
            isNew ? 'bg-violet-200 text-violet-800' : 'bg-slate-200 text-slate-700'
          }`} title={isNew ? 'New patient' : 'Established patient'}>
            {isNew ? 'NEW' : 'EST'}
          </span>
          <span className="text-[11px] font-medium truncate flex-1">{patient.patient_name}</span>
        </div>
        <div className="flex flex-wrap items-center gap-1 text-[9px] text-gray-600 mt-0.5">
          {/* Mammogram */}
          <span className={`px-1 rounded ${
            mammoOk ? 'bg-green-200 text-green-800' : 'bg-red-100 text-red-700'
          }`} title={mammoOk
                    ? `Mammo verified ${patient.mammo_date || ''}`.trim()
                    : 'Mammo NOT verified'}>
            {mammoOk ? 'mammo ✓' : 'mammo ✗'}
          </span>
          {/* Labs */}
          <span className={`px-1 rounded ${
            labsOk ? 'bg-green-200 text-green-800' : 'bg-red-100 text-red-700'
          }`} title={labsOk
                    ? `Labs verified ${patient.labs_date || ''}`.trim()
                    : 'Labs NOT verified'}>
            {labsOk ? 'labs ✓' : 'labs ✗'}
          </span>
          {/* Payment */}
          {paid && (
            <span className="bg-blue-200 text-blue-800 px-1 rounded" title="Payment collected">paid</span>
          )}
          {sent && !paid && (
            <span className="bg-amber-200 text-amber-800 px-1 rounded" title="Klara sent, awaiting payment">awaiting $</span>
          )}
          {!sent && !paid && (
            <span className="bg-gray-200 text-gray-700 px-1 rounded" title="No payment activity yet">no $</span>
          )}
          {/* Bag state */}
          {bagged && paid && (
            <span className="bg-green-200 text-green-800 px-1 rounded" title="Bag filled + paid">ready</span>
          )}
          {bagged && !paid && (
            <span className="bg-indigo-200 text-indigo-800 px-1 rounded"
                  title={`Bag filled ${patient.active_visit_bagged_at?.slice(0, 10) || ''} — awaiting payment`.trim()}>
              bagged
            </span>
          )}
          {!hasDoses && (
            <span className="bg-red-100 text-red-700 px-1 rounded" title="No doses on visit yet">no doses</span>
          )}
        </div>
      </button>

      {visitId && (
        <>
          <button type="button"
                   className="absolute top-1 right-1 text-gray-400 hover:text-gray-700 p-0.5 rounded"
                   onClick={e => { e.stopPropagation(); setMenuOpen(o => !o) }}
                   title="More…">
            <MoreVertical size={12}/>
          </button>
          {menuOpen && (
            <div className="absolute top-5 right-1 z-10 bg-white border border-gray-200 rounded shadow text-[11px] py-1 min-w-[140px]"
                  onClick={e => e.stopPropagation()}>
              <button className="block w-full text-left px-3 py-1 hover:bg-gray-100"
                       onClick={() => { setMenuOpen(false); setRescheduleOpen(true) }}>
                Reschedule…
              </button>
              <button className="block w-full text-left px-3 py-1 hover:bg-red-50 text-red-700"
                       onClick={() => { setMenuOpen(false); setCancelOpen(true) }}>
                Cancel visit…
              </button>
            </div>
          )}
        </>
      )}

      {rescheduleOpen && (
        <CalendarRescheduleDialog
          visitId={visitId}
          currentDate={patient.active_visit_scheduled_date || patient.next_scheduled_date}
          patientName={patient.patient_name}
          qc={qc}
          onClose={() => setRescheduleOpen(false)} />
      )}
      {cancelOpen && (
        <CalendarCancelDialog
          visitId={visitId}
          patientName={patient.patient_name}
          qc={qc}
          onClose={() => setCancelOpen(false)} />
      )}
    </div>
  )
}


function CalendarRescheduleDialog({ visitId, currentDate, patientName, qc, onClose }) {
  const [date, setDate]     = useState(currentDate || '')
  const [reason, setReason] = useState('')
  const [busy, setBusy]     = useState(false)
  const [err, setErr]       = useState(null)

  async function submit() {
    if (!date) { setErr('Pick a new date'); return }
    setBusy(true); setErr(null)
    try {
      await api.post(`/pellets/visits/${visitId}/reschedule`, {
        new_date: date,
        reason: reason.trim() || null,
      })
      qc.invalidateQueries({ queryKey: ['pellet-upcoming-calendar'] })
      qc.invalidateQueries({ queryKey: ['pellet-patients'] })
      qc.invalidateQueries({ queryKey: ['pellet-patient'] })
      onClose()
    } catch (e) {
      setErr(e?.response?.data?.detail || e.message)
    } finally { setBusy(false) }
  }

  return (
    <MiniDialog title={`Reschedule ${patientName}`} onClose={onClose}>
      <div>
        <div className="text-[10px] uppercase text-gray-500 mb-1">New date</div>
        <input type="date" className="input text-sm w-full"
                value={date} onChange={e => setDate(e.target.value)} />
        <div className="text-[10px] text-gray-500 mt-1">
          Current: {currentDate ? fmt.date(currentDate) : '(unscheduled)'}
        </div>
      </div>
      <div>
        <div className="text-[10px] uppercase text-gray-500 mb-1">Reason (optional)</div>
        <textarea className="input text-sm w-full" rows={2}
                  value={reason} onChange={e => setReason(e.target.value)} />
      </div>
      {err && <div className="text-xs text-red-600">{err}</div>}
      <div className="flex gap-2 justify-end">
        <button className="btn-secondary text-xs" onClick={onClose}>Cancel</button>
        <button className="btn-primary text-xs" onClick={submit} disabled={busy || !date}>
          {busy ? 'Saving…' : 'Reschedule'}
        </button>
      </div>
    </MiniDialog>
  )
}


function CalendarCancelDialog({ visitId, patientName, qc, onClose }) {
  const [reason, setReason] = useState('')
  const [busy, setBusy]     = useState(false)
  const [err, setErr]       = useState(null)

  async function submit() {
    if (!reason.trim()) { setErr('Reason required'); return }
    setBusy(true); setErr(null)
    try {
      await api.post(`/pellets/visits/${visitId}/cancel`, { reason: reason.trim() })
      qc.invalidateQueries({ queryKey: ['pellet-upcoming-calendar'] })
      qc.invalidateQueries({ queryKey: ['pellet-patients'] })
      qc.invalidateQueries({ queryKey: ['pellet-patient'] })
      onClose()
    } catch (e) {
      setErr(e?.response?.data?.detail || e.message)
    } finally { setBusy(false) }
  }

  return (
    <MiniDialog title={`Cancel ${patientName}'s visit`} onClose={onClose}>
      <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded p-2">
        Any pulled doses will be returned to stock automatically.
      </div>
      <div>
        <div className="text-[10px] uppercase text-gray-500 mb-1">Reason *</div>
        <textarea className="input text-sm w-full" rows={3}
                  value={reason} onChange={e => setReason(e.target.value)} autoFocus />
      </div>
      {err && <div className="text-xs text-red-600">{err}</div>}
      <div className="flex gap-2 justify-end">
        <button className="btn-secondary text-xs" onClick={onClose}>Keep visit</button>
        <button className="text-xs px-3 py-1.5 rounded bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
                onClick={submit} disabled={busy || !reason.trim()}>
          {busy ? 'Cancelling…' : 'Cancel visit'}
        </button>
      </div>
    </MiniDialog>
  )
}


function MiniDialog({ title, onClose, children }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
          onClick={onClose}>
      <div className="absolute inset-0 bg-black/40" />
      <div className="relative bg-white rounded-lg shadow-xl w-full max-w-sm"
            onClick={e => e.stopPropagation()}>
        <div className="border-b border-border-subtle px-4 py-2.5 flex items-center justify-between">
          <h3 className="font-serif font-semibold text-ink text-[14px]">{title}</h3>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={14}/></button>
        </div>
        <div className="p-4 space-y-3">{children}</div>
      </div>
    </div>
  )
}


// ─── Per-view tables ────────────────────────────────────────────────

function ViewTable({ view, patients, isLoading, onOpen }) {
  if (isLoading) return <div className="card text-gray-400 italic">Loading…</div>
  if (patients.length === 0) {
    return <div className="card text-center text-gray-400 italic py-8">No patients match.</div>
  }

  return (
    <div className="card !p-0 overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-plum-50">
          <tr>
            {viewColumns(view).map(c => (
              <th key={c.key} className={`table-th ${c.right ? 'text-right' : ''}`}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {patients.map(p => (
            <tr key={p.id} className="hover:bg-plum-50/40 cursor-pointer"
                onClick={() => onOpen(p.id)}>
              {viewColumns(view).map(c => (
                <td key={c.key} className={`table-td ${c.right ? 'text-right' : ''}`}>
                  {c.render(p)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}


function viewColumns(view) {
  const Patient = {
    key: 'patient', label: 'Patient',
    render: p => (
      <div>
        <div className="font-medium">{p.patient_name}</div>
        <div className="text-[10px] text-gray-500 font-mono">#{p.chart_number}</div>
      </div>
    ),
  }
  const Type = {
    key: 'type', label: 'Type',
    render: p => (
      <span className={`text-[10px] uppercase px-1.5 py-0.5 rounded ${
        p.patient_type === 'new' ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-700'
      }`}>
        {p.patient_type}
      </span>
    ),
  }

  if (view === 'roster') return [
    Patient, Type,
    {
      key: 'mammo', label: 'Mammo',
      render: p => p.mammo_verified
        ? <span className="text-green-700 text-[11px]">✓ {p.mammo_result || ''} {p.mammo_date && fmt.date(p.mammo_date)}</span>
        : <span className="text-amber-700 italic text-[11px]">unverified</span>
    },
    {
      key: 'labs', label: 'Labs',
      render: p => p.labs_verified
        ? <span className="text-green-700 text-[11px]">✓ {p.labs_date && fmt.date(p.labs_date)}</span>
        : <span className="text-amber-700 italic text-[11px]">unverified</span>
    },
    {
      key: 'last_visit', label: 'Last visit',
      render: p => p.last_visit_date
        ? <span className="text-[11px]">
            {fmt.date(p.last_visit_date)}
            {p.days_since_last_visit != null && (
              <span className="text-gray-500"> · {p.days_since_last_visit}d ago</span>
            )}
          </span>
        : <span className="text-gray-400 italic">—</span>
    },
    {
      key: 'visits', label: 'Total', right: true,
      render: p => <span className="text-[11px] font-mono">{p.visits_total ?? 0}</span>
    },
  ]

  if (view === 'last_visits') return [
    Patient, Type,
    {
      key: 'last_visit', label: 'Last visit',
      render: p => <span className="text-[12px]">{fmt.date(p.last_visit_date)}</span>
    },
    {
      key: 'days', label: 'Days since', right: true,
      render: p => <span className="text-[11px] font-mono">{p.days_since_last_visit ?? '—'}</span>
    },
    {
      key: 'recall', label: 'Recall (mo)', right: true,
      render: p => <span className="text-[11px] font-mono">{p.recall_interval_months}</span>
    },
    {
      key: 'next_due', label: 'Next due',
      render: p => p.recall_due_date
        ? <span className={`text-[11px] ${p.recall_is_due ? 'text-red-700 font-semibold' : 'text-gray-600'}`}>
            {fmt.date(p.recall_due_date)}
          </span>
        : <span className="text-gray-400">—</span>
    },
  ]

  if (view === 'upcoming') return [
    Patient,
    {
      key: 'sched', label: 'Scheduled',
      render: p => <span className="text-[12px] font-medium">{fmt.date(p.next_scheduled_date)}</span>
    },
    {
      key: 'days_until', label: 'Days', right: true,
      render: p => {
        if (!p.next_scheduled_date) return '—'
        const days = Math.round(
          (new Date(p.next_scheduled_date + 'T00:00:00') - new Date()) / 86400000
        )
        return <span className={`text-[11px] font-mono ${
          days < 0 ? 'text-red-700' : days < 7 ? 'text-amber-700' : 'text-gray-600'
        }`}>{days}</span>
      },
    },
    {
      key: 'status', label: 'Visit status',
      render: p => <span className="text-[11px] capitalize">{p.active_visit_status?.replace('_', ' ') || '—'}</span>
    },
    {
      key: 'doses', label: 'Doses', right: true,
      render: p => p.active_visit_has_doses
        ? <span className="text-[11px] text-green-700">{p.active_visit_doses_pulled} pulled · {p.active_visit_doses_planned} planned</span>
        : <span className="text-[11px] text-amber-700 italic">no doses yet</span>
    },
  ]

  if (view === 'recall_due') return [
    Patient, Type,
    {
      key: 'last', label: 'Last visit',
      render: p => <span className="text-[12px]">{fmt.date(p.last_visit_date)}</span>
    },
    {
      key: 'interval', label: 'Interval (mo)', right: true,
      render: p => <span className="text-[11px] font-mono">{p.recall_interval_months}</span>
    },
    {
      key: 'overdue', label: 'Days since', right: true,
      render: p => <span className="text-[12px] font-mono text-red-700 font-semibold">{p.days_since_last_visit}</span>
    },
  ]

  if (view === 'needs_mammo') return [
    Patient, Type,
    {
      key: 'mammo', label: 'Mammo status',
      render: p => <span className="text-[11px] text-amber-700 italic">unverified</span>
    },
    {
      key: 'last', label: 'Last pellet visit',
      render: p => p.last_visit_date
        ? <span className="text-[11px]">{fmt.date(p.last_visit_date)} <span className="text-gray-500">· {p.days_since_last_visit}d ago</span></span>
        : <span className="text-gray-400 italic">—</span>
    },
  ]

  if (view === 'needs_dosing') return [
    Patient, Type,
    {
      key: 'sched', label: 'Scheduled',
      render: p => p.active_visit_scheduled_date
        ? <span className="text-[12px]">{fmt.date(p.active_visit_scheduled_date)}</span>
        : <span className="text-gray-400 italic">no date</span>
    },
    {
      key: 'visit_status', label: 'Visit status',
      render: p => <span className="text-[11px] capitalize">{p.active_visit_status?.replace('_', ' ') || '—'}</span>
    },
  ]

  if (view === 'ready') return [
    Patient,
    {
      key: 'sched', label: 'Scheduled',
      render: p => <span className="text-[12px]">{fmt.date(p.active_visit_scheduled_date)}</span>
    },
    {
      key: 'bag', label: 'Bag status',
      render: p => <span className="text-[11px] text-green-700">✓ {p.active_visit_doses_pulled} doses pulled</span>
    },
    {
      key: 'payment', label: 'Payment',
      render: p => <span className="text-[11px] text-green-700">✓ collected</span>
    },
  ]

  if (view === 'paid') return [
    Patient, Type,
    {
      key: 'payment', label: 'Payment',
      render: p => <span className="text-[11px] text-green-700">✓ collected</span>
    },
    {
      key: 'sched', label: 'Scheduled',
      render: p => p.active_visit_scheduled_date
        ? <span className="text-[12px]">{fmt.date(p.active_visit_scheduled_date)}</span>
        : <span className="text-gray-400 italic">—</span>
    },
  ]

  if (view === 'unpaid') return [
    Patient, Type,
    {
      key: 'pay_status', label: 'Payment',
      render: p => {
        const s = p.active_visit_payment_status
        return s === 'sent'
          ? <span className="text-[11px] text-amber-700">Klara sent — awaiting payment</span>
          : <span className="text-[11px] text-red-700">Klara not sent</span>
      },
    },
    {
      key: 'sched', label: 'Scheduled',
      render: p => p.active_visit_scheduled_date
        ? <span className="text-[12px]">{fmt.date(p.active_visit_scheduled_date)}</span>
        : <span className="text-gray-400 italic">—</span>
    },
  ]

  return [Patient, Type]
}


// ─── ModMed appt upload drawer ───────────────────────────────────────

function ApptUploadDrawer({ onClose }) {
  const qc = useQueryClient()
  const [file, setFile] = useState(null)
  const [cancelMissing, setCancelMissing] = useState(false)
  const [result, setResult] = useState(null)

  const upload = useMutation({
    mutationFn: async () => {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('cancel_missing', cancelMissing ? 'true' : 'false')
      return api.post('/pellets/appointments/upload', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      }).then(r => r.data)
    },
    onSuccess: (data) => {
      setResult(data)
      qc.invalidateQueries({ queryKey: ['pellet-patients'] })
      qc.invalidateQueries({ queryKey: ['pellet-patient-counts'] })
      qc.invalidateQueries({ queryKey: ['pellet-upcoming-calendar'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Upload failed'),
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-lg bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">Upload ModMed appt list</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18}/></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <div className="text-[12px] text-gray-600 bg-plum-50/50 border border-plum-100 rounded p-2">
            Drop a ModMed <strong>Pellet Insert appointment</strong> export (.xlsx).
            Each row upserts on (MRN + appointment date):
            <ul className="list-disc pl-5 mt-1 space-y-0.5">
              <li>New patients are enrolled; existing patients get phone/email/payer refreshed</li>
              <li>Status <em>Checked Out</em> → marks the visit as <strong>inserted</strong></li>
              <li>Status <em>Pending / Confirmed</em> → leaves it as <strong>in-progress</strong></li>
              <li>User-entered dose cards, payments, claim #s are never overwritten</li>
            </ul>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Excel file (.xlsx)</label>
            <input type="file" accept=".xlsx,.xls"
                   className="text-[12px] w-full"
                   onChange={e => { setFile(e.target.files?.[0] || null); setResult(null) }} />
            {file && (
              <div className="text-[11px] text-gray-500 mt-1">
                {file.name} — {(file.size / 1024).toFixed(1)} KB
              </div>
            )}
          </div>
          <label className="flex items-start gap-2 text-[12px] cursor-pointer p-2 bg-amber-50/50 border border-amber-200 rounded">
            <input type="checkbox" className="mt-0.5" checked={cancelMissing}
                   onChange={e => setCancelMissing(e.target.checked)} />
            <span>
              <strong>Auto-cancel missing visits</strong> in the upload's date range
              <div className="text-[11px] text-gray-600 mt-0.5">
                Any in-progress visit whose date falls inside the file's range
                but isn't in the file gets marked <em>cancelled</em> with
                outcome <em>auto_cancelled_not_in_upload</em>. Billed and
                inserted visits are never touched.
              </div>
            </span>
          </label>
          {result && (
            <div className="text-[12px] bg-green-50 border border-green-200 rounded p-3 space-y-0.5">
              <div className="font-semibold text-green-800 mb-1">Import complete</div>
              <div>Rows: <strong>{result.total_rows}</strong></div>
              <div>Patients added: <strong>{result.patients_added}</strong></div>
              <div>Patients updated: <strong>{result.patients_updated}</strong></div>
              <div>Visits added: <strong>{result.visits_added}</strong></div>
              <div>Visits updated: <strong>{result.visits_updated}</strong></div>
              <div>Visits marked inserted: <strong>{result.visits_marked_inserted}</strong></div>
              {result.visits_cancelled_missing > 0 && (
                <>
                  <div className="text-amber-800 mt-1">
                    Auto-cancelled (not in upload): <strong>{result.visits_cancelled_missing}</strong>
                  </div>
                  <ul className="text-[10px] text-gray-700 list-disc pl-5">
                    {result.cancelled_visits?.slice(0, 6).map((c, i) => (
                      <li key={i}>{c.patient_name} · {c.scheduled_date}</li>
                    ))}
                    {result.cancelled_visits?.length > 6 && (
                      <li>+{result.cancelled_visits.length - 6} more</li>
                    )}
                  </ul>
                </>
              )}
            </div>
          )}
        </div>
        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
          <button className="text-sm text-muted hover:underline" onClick={onClose}>
            {result ? 'Close' : 'Cancel'}
          </button>
          {!result && (
            <button className="btn-primary text-sm flex items-center gap-1"
                    onClick={() => upload.mutate()}
                    disabled={!file || upload.isPending}>
              <Upload size={12}/> {upload.isPending ? 'Uploading…' : 'Upload'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}


// ─── Preset chip ─────────────────────────────────────────────────────

function PresetChip({ preset, onLoad, onSetDefault, onDelete }) {
  return (
    <div className={`group inline-flex items-center text-[11px] rounded-full border px-2 py-0.5 ${
      preset.is_default
        ? 'border-plum-300 bg-plum-50 text-plum-800'
        : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50'
    }`}>
      <button type="button" onClick={onLoad}
              title="Load this preset"
              className="flex items-center gap-1 pr-1">
        {preset.is_default && <Star size={10} className="text-plum-600 fill-plum-600" />}
        {preset.name}
      </button>
      {!preset.is_default && (
        <button type="button" onClick={onSetDefault}
                title="Set as default (auto-loads on next visit)"
                className="text-gray-400 hover:text-plum-700 px-1 opacity-0 group-hover:opacity-100">
          <Star size={10} />
        </button>
      )}
      <button type="button" onClick={onDelete}
              title="Delete preset"
              className="text-gray-400 hover:text-red-600 pl-1 opacity-0 group-hover:opacity-100">
        <Trash2 size={10} />
      </button>
    </div>
  )
}


// ─── Enroll drawer (unchanged) ───────────────────────────────────────

function EnrollDrawer({ onClose }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [form, setForm] = useState({
    chart_number: '', patient_name: '', patient_dob: '',
    patient_email: '', patient_phone: '', primary_insurance: '',
    patient_type: 'new', notes: '',
  })
  const upd = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const create = useMutation({
    mutationFn: () => api.post('/pellets/patients', form).then(r => r.data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['pellet-patients'] })
      qc.invalidateQueries({ queryKey: ['pellet-patient-counts'] })
      navigate(`/pellets/patients/${data.id}`)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Failed to enroll'),
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">Enroll pellet patient</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Chart # *</label>
              <input className="input text-sm w-full font-mono" required
                     value={form.chart_number}
                     onChange={e => upd('chart_number', e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Type *</label>
              <select className="input text-sm w-full" value={form.patient_type}
                      onChange={e => upd('patient_type', e.target.value)}>
                <option value="new">New ($500)</option>
                <option value="established">Established ($400)</option>
              </select>
            </div>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Patient name *</label>
            <input className="input text-sm w-full" required
                   value={form.patient_name}
                   onChange={e => upd('patient_name', e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">DOB</label>
              <input type="date" className="input text-sm w-full"
                     value={form.patient_dob}
                     onChange={e => upd('patient_dob', e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Phone</label>
              <input className="input text-sm w-full" value={form.patient_phone}
                     onChange={e => upd('patient_phone', e.target.value)} />
            </div>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Email (for Klara)</label>
            <input className="input text-sm w-full" value={form.patient_email}
                   onChange={e => upd('patient_email', e.target.value)} />
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Primary insurance</label>
            <input className="input text-sm w-full" value={form.primary_insurance}
                   onChange={e => upd('primary_insurance', e.target.value)} />
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Notes</label>
            <textarea className="input text-[12px] w-full" rows={2}
                      value={form.notes}
                      onChange={e => upd('notes', e.target.value)} />
          </div>
        </div>
        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
          <button className="text-sm text-muted hover:underline" onClick={onClose}>Cancel</button>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => create.mutate()}
                  disabled={!form.chart_number.trim() || !form.patient_name.trim() || create.isPending}>
            <Save size={12}/> {create.isPending ? 'Enrolling…' : 'Enroll'}
          </button>
        </div>
      </div>
    </div>
  )
}
