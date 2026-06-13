import { useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, Settings, Plus, Trash2, Save, Edit3, Search } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../utils/api'
import LoadingState from '../components/LoadingState'

const TABS = [
  { id: 'alerts',    label: 'Alerts & Windows' },
  { id: 'steps',     label: 'Workflow Steps' },
  { id: 'postop',    label: 'Post-Op Schedules' },
  { id: 'capacity',  label: 'Facilities & Capacity' },
  { id: 'templates', label: 'Templates' },
]

export default function SurgerySettings() {
  const [tab, setTab] = useState('alerts')
  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <Link to="/surgery" className="text-muted hover:text-plum-700">
          <ArrowLeft size={18} />
        </Link>
        <h1 className="font-serif text-[24px] font-semibold text-ink m-0 flex items-center gap-2">
          <Settings size={22} className="text-plum-700" />
          Surgery Settings
        </h1>
      </div>
      <HowThisWorks />
      <div className="flex gap-1 border-b border-border-subtle mb-6">
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
                  className={`px-3 py-2 text-[13px] border-b-2 -mb-px transition ${
                    tab === t.id
                      ? 'border-plum-700 text-plum-700 font-medium'
                      : 'border-transparent text-muted hover:text-plum-700'}`}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'alerts'    && <AlertsTab />}
      {tab === 'steps'     && <StepsTab />}
      {tab === 'postop'    && <PostOpTab />}
      {tab === 'capacity'  && <CapacityTab />}
      {tab === 'templates' && <TemplatesTab />}
    </div>
  )
}

function Placeholder({ name }) {
  return <div className="text-muted text-sm">{name} — coming in this release.</div>
}

function HowThisWorks() {
  const [open, setOpen] = useState(false)
  return (
    <section className="card p-4 mb-6">
      <button onClick={() => setOpen(o => !o)}
              className="font-medium text-[13px] w-full text-left flex items-center justify-between">
        <span>How Surgery Scheduling Works</span>
        <span className="text-muted">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="mt-3 text-[13px] leading-relaxed text-gray-800 space-y-4">
          <div>
            <div className="font-medium mb-1">Workflow Overview</div>
            <p className="text-muted">
              Each surgery moves through a fixed sequence of <strong>steps</strong>. The
              dashboard surfaces each case into workload buckets based on which steps are
              still open. Expected days per step are configurable on the Workflow Steps
              tab — when a case sits past its current step's expected window, it shows as
              behind schedule.
            </p>
          </div>

          <div>
            <div className="font-medium mb-1">Facilities & Block Schedule</div>
            <p className="text-muted">
              Three facilities: MedStar SMHC (robotic + major minimally-invasive),
              UM Charles Regional (minor outpatient or major open), and the WWC Office
              Procedure Suite (in-office procedures, Thursdays). Block days repeat on a
              5-week pattern and are materialized ahead by the Schedule Horizon. Federal
              holidays roll the affected block off; the cycle continues on the next
              eligible weekday.
            </p>
          </div>

          <div>
            <div className="font-medium mb-1">Capacity Rules</div>
            <ul className="list-disc pl-5 space-y-1 text-muted">
              <li><strong>MedStar:</strong> 3 × 180-min robotic OR 2 × 240-min robotic per day
                (mutually exclusive; 180 and 240 can't be mixed). Minor add-ons may follow
                once the robotic threshold is met and time remains.</li>
              <li><strong>CRMC:</strong> 6 minor OR 2 major per day (mutually exclusive).</li>
              <li><strong>Office:</strong> a fixed set of Thursday slot start times.</li>
            </ul>
            <p className="text-muted mt-1">
              The booking system always re-checks the day's real time window, so a case mix
              exceeding the available minutes is rejected even if the per-kind counts allow it.
            </p>
          </div>

          <div>
            <div className="font-medium mb-1">Pre-Op Validity</div>
            <p className="text-muted">
              The pre-op H&P must be dated within the Pre-Op Validity window of the surgery
              date. Older pre-ops are flagged for a repeat visit and surface in the dashboard.
            </p>
          </div>

          <div>
            <div className="font-medium mb-1">Consents & E-Signatures</div>
            <p className="text-muted">
              Each procedure is matched to one primary consent template (keyword match),
              plus any supplemental templates (e.g. Medicaid sterilization) whose
              procedure + insurance + facility conditions are met. Consents are sent for
              signature through <strong>BoldSign</strong>. Medicaid sterilization consent
              must be signed at least 30 and no more than 180 days before the procedure.
            </p>
          </div>

          <div>
            <div className="font-medium mb-1">Patient Messaging</div>
            <p className="text-muted">
              Klara messages are <strong>drafted by the system for staff to copy and paste
              into Klara manually</strong> — there is no automated Klara send. Patient SMS
              reminders go out automatically through the SMS templates as the surgery date
              approaches.
            </p>
          </div>

          <div>
            <div className="font-medium mb-1">Patient Date Picker</div>
            <p className="text-muted">
              Patients self-schedule via a soft-auth picker (date of birth + last 4 digits
              of phone). They see only days where their procedure fits the capacity rules.
              Picking a date records the scheduled date and start time.
            </p>
          </div>
        </div>
      )}
    </section>
  )
}

// ─── Alerts & Windows tab ───────────────────────────────────────────

const ALERT_FIELDS = [
  { key: 'critical_overdue_hours',  label: 'Critical Overdue Threshold (Hours)',
    hint: 'A stuck step turns red on the dashboard after this many hours late.' },
  { key: 'labs_alert_window_days',  label: 'Labs Alert Window (Days)',
    hint: 'Flag hospital surgeries this many days out that lack a lab shipment.' },
  { key: 'post_op_docs_alert_days', label: 'Post-Op Docs Alert (Days)',
    hint: 'Flag surgeries this many days post-op with no operative notes.' },
  { key: 'unresponsive_after_days', label: 'Unresponsive After (Days)',
    hint: 'Mark unresponsive when no date picked this long after pre-op.' },
  { key: 'preop_valid_days',        label: 'Pre-Op Validity (Days)',
    hint: 'Pre-op exams older than this require a repeat.' },
  { key: 'schedule_horizon_days',   label: 'Schedule Horizon (Days)',
    hint: 'How far ahead block days are materialized and offered.' },
  { key: 'completed_window_days',   label: 'Completed Window (Days)',
    hint: 'Dashboard "completed surgeries" metric lookback.' },
  { key: 'office_full_threshold',   label: 'Office Full Threshold (Cases)' },
  { key: 'office_lookahead_days',   label: 'Office Alert Lookahead (Days)' },
  { key: 'hospital_lookahead_days', label: 'Hospital Alert Lookahead (Days)' },
]

function saveErrorMessage(error) {
  const detail = error?.response?.data?.detail
  if (Array.isArray(detail)) return detail[0]?.msg || 'Save failed — check values.'
  if (typeof detail === 'string') return detail
  return 'Save failed — check values.'
}

function AlertsTab() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['surgery-config'],
    queryFn: () => api.get('/surgery/config').then(r => r.data),
  })
  const [draft, setDraft] = useState({})
  const save = useMutation({
    mutationFn: (body) => api.put('/surgery/config', body).then(r => r.data),
    onSuccess: () => { setDraft({}); qc.invalidateQueries({ queryKey: ['surgery-config'] }) },
  })
  if (!data) return <LoadingState />
  const val = (k) => draft[k] ?? data[k] ?? ''
  return (
    <div className="space-y-6">
      <section className="card p-4">
        <h2 className="font-medium mb-3">Alert Thresholds & Windows</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {ALERT_FIELDS.map(f => (
            <label key={f.key} className="block text-[13px]">
              <span className="font-medium">{f.label}</span>
              <input type="number" className="input mt-1 w-28"
                     value={val(f.key)}
                     onChange={e => setDraft(d => ({ ...d, [f.key]: Number(e.target.value) }))} />
              {f.hint && <p className="text-[11px] text-muted mt-0.5">{f.hint}</p>}
            </label>
          ))}
        </div>
        <button className="btn-primary text-xs mt-4"
                disabled={!Object.keys(draft).length || save.isPending}
                onClick={() => save.mutate(draft)}>
          {save.isPending ? 'Saving…' : 'Save Changes'}
        </button>
        {save.isError && (
          <p className="text-xs text-red-700 mt-2">{saveErrorMessage(save.error)}</p>
        )}
      </section>
      <AlertRecipientsSection />
      <ReminderLeadDaysSection />
    </div>
  )
}

function AlertRecipientsSection() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['surgery-recipients'],
    queryFn: () => api.get('/surgery/admin/alert-recipients').then(r => r.data),
  })
  const add = useMutation({
    mutationFn: ({ alert_kind, email }) =>
      api.post('/surgery/admin/alert-recipients', { alert_kind, email }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery-recipients'] }),
    onError: (e) => alert(saveErrorMessage(e)),
  })
  const remove = useMutation({
    mutationFn: ({ alert_kind, email }) =>
      api.delete('/surgery/admin/alert-recipients', { params: { alert_kind, email } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery-recipients'] }),
  })

  function ListEditor({ title, kind, hint }) {
    const [entry, setEntry] = useState('')
    const list = data?.[kind] || []
    return (
      <div className="mb-3">
        <h3 className="text-sm font-medium mb-1">{title}</h3>
        <p className="text-[11px] text-muted mb-2">{hint}</p>
        <div className="flex items-center gap-2 mb-2">
          <input className="input text-sm flex-1"
                 placeholder="someone@waldorfwomenscare.com"
                 value={entry} onChange={e => setEntry(e.target.value)} />
          <button className="btn-primary text-xs" disabled={!entry.trim()}
                  onClick={() => { add.mutate({ alert_kind: kind, email: entry.trim() }); setEntry('') }}>
            Add
          </button>
        </div>
        {list.length === 0 ? (
          <div className="text-[11px] text-muted italic">
            No configured recipients — falling back to role-based query.
          </div>
        ) : (
          <ul className="space-y-1">
            {list.map(e => (
              <li key={e} className="flex items-center justify-between text-[12px]">
                <span>{e}</span>
                <button onClick={() => remove.mutate({ alert_kind: kind, email: e })}
                        className="text-xs text-red-700 hover:underline">Remove</button>
              </li>
            ))}
          </ul>
        )}
      </div>
    )
  }

  return (
    <section className="card p-4">
      <h2 className="font-medium mb-3">Alert Recipients</h2>
      <ListEditor title="Office Release Alert" kind="office_release"
                  hint="Notified when an office procedure day is short on bookings." />
      <ListEditor title="Hospital Release Alert" kind="hospital_release"
                  hint="Notified when a hospital block day is fully empty." />
    </section>
  )
}

function ReminderLeadDaysSection() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['surgery-config'],
    queryFn: () => api.get('/surgery/config').then(r => r.data),
  })
  const [text, setText] = useState(null)
  const save = useMutation({
    mutationFn: (body) => api.put('/surgery/config', body).then(r => r.data),
    onSuccess: () => { setText(null); qc.invalidateQueries({ queryKey: ['surgery-config'] }) },
  })
  if (!data) return null
  const current = Array.isArray(data.reminder_lead_days) ? data.reminder_lead_days : []
  const value = text ?? current.join(', ')
  const parse = (s) =>
    s.split(',').map(x => parseInt(x.trim(), 10)).filter(n => Number.isFinite(n))
  return (
    <section className="card p-4">
      <h2 className="font-medium mb-1">Reminder Lead Days</h2>
      <p className="text-[11px] text-muted mb-2">
        Comma-separated days before surgery to send reminders (e.g. "3, 1").
      </p>
      <div className="flex items-center gap-2">
        <input className="input text-sm w-40" value={value}
               onChange={e => setText(e.target.value)} />
        <button className="btn-primary text-xs"
                disabled={text === null || save.isPending}
                onClick={() => save.mutate({ reminder_lead_days: parse(value) })}>
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
        {text !== null && (
          <button className="text-xs text-plum-700 hover:underline"
                  onClick={() => setText(null)}>Cancel</button>
        )}
      </div>
      {save.isError && (
        <p className="text-xs text-red-700 mt-2">{saveErrorMessage(save.error)}</p>
      )}
    </section>
  )
}

// ─── Workflow Steps tab ─────────────────────────────────────────────

function StepsTab() {
  const qc = useQueryClient()
  const { data: catalog } = useQuery({
    queryKey: ['step-catalog'],
    queryFn: () => api.get('/surgery/config/step-catalog').then(r => r.data),
  })
  const { data: config } = useQuery({
    queryKey: ['surgery-config'],
    queryFn: () => api.get('/surgery/config').then(r => r.data),
  })
  const [draft, setDraft] = useState({})
  const save = useMutation({
    mutationFn: (body) => api.put('/surgery/config', body).then(r => r.data),
    onSuccess: () => { setDraft({}); qc.invalidateQueries({ queryKey: ['surgery-config'] }) },
  })
  if (!catalog || !config) return <LoadingState />

  const pathway = (id, label, cfgDaysKey, cfgTitlesKey) => {
    const days = { ...(config[cfgDaysKey] || {}), ...(draft[cfgDaysKey] || {}) }
    const titles = { ...(config[cfgTitlesKey] || {}), ...(draft[cfgTitlesKey] || {}) }
    return (
      <section className="card p-4">
        <h2 className="font-medium mb-1">{label}</h2>
        <p className="text-[11px] text-muted mb-3">
          Expected Days drives the behind-schedule and Critical Alerts logic —
          a surgery is flagged when its current step is older than this.
        </p>
        <table className="w-full text-[13px]">
          <thead><tr className="text-left text-muted">
            <th className="py-1 w-8">#</th><th>Step</th>
            <th className="w-32">Expected Days</th></tr></thead>
          <tbody>
            {catalog[id].map(st => (
              <tr key={st.key} className="border-t border-border-subtle">
                <td className="py-1.5 align-top">{st.n}</td>
                <td className="py-1.5">
                  <div className="flex items-center gap-2">
                    <input className="input w-full" value={titles[st.key] ?? st.title}
                           onChange={e => setDraft(d => ({ ...d,
                             [cfgTitlesKey]: { ...(d[cfgTitlesKey] || {}), [st.key]: e.target.value } }))} />
                    {st.optional && <span className="chip-neutral">optional</span>}
                  </div>
                </td>
                <td className="py-1.5 align-top">
                  <input type="number" min={1} max={90} className="input w-20"
                         value={days[st.key] ?? st.default_days}
                         onChange={e => setDraft(d => ({ ...d,
                           [cfgDaysKey]: { ...(d[cfgDaysKey] || {}), [st.key]: Number(e.target.value) } }))} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    )
  }

  return (
    <div className="space-y-6">
      {pathway('hospital', 'Hospital Pathway (15 Steps)',
               'step_expected_days_hospital', 'step_titles_hospital')}
      {pathway('office', 'Office Pathway (12 Steps)',
               'step_expected_days_office', 'step_titles_office')}
      <div>
        <button className="btn-primary text-xs"
                disabled={!Object.keys(draft).length || save.isPending}
                onClick={() => {
                  // Defense in depth (audit #7): send the full merged dict
                  // for each touched step key, not just the draft delta, so
                  // the save can't drop previously-saved sibling sub-keys
                  // even if the server-side merge regresses.
                  const body = {}
                  for (const key of Object.keys(draft)) {
                    body[key] = { ...(config[key] || {}), ...(draft[key] || {}) }
                  }
                  save.mutate(body)
                }}>
          {save.isPending ? 'Saving…' : 'Save Changes'}
        </button>
        {save.isError && (
          <p className="text-xs text-red-700 mt-2">{saveErrorMessage(save.error)}</p>
        )}
      </div>
    </div>
  )
}

// ─── Post-Op Schedules tab ──────────────────────────────────────────

function PostOpTab() {
  const qc = useQueryClient()
  const { data: config } = useQuery({
    queryKey: ['surgery-config'],
    queryFn: () => api.get('/surgery/config').then(r => r.data),
  })
  const { data: defaults } = useQuery({
    queryKey: ['post-op-defaults'],
    queryFn: () => api.get('/surgery/config/post-op-defaults').then(r => r.data),
  })
  const [rules, setRules] = useState(null)
  const save = useMutation({
    mutationFn: (body) => api.put('/surgery/config', { post_op_schedules: body }).then(r => r.data),
    onSuccess: () => { setRules(null); qc.invalidateQueries({ queryKey: ['surgery-config'] }) },
  })
  if (!config || !defaults) return <LoadingState />
  const effective = rules ?? config.post_op_schedules ?? defaults.rules

  const upd = (i, fn) => setRules(effective.map((r, j) => j === i ? fn(r) : r))

  return (
    <div className="space-y-4">
      <p className="text-[12px] text-muted">
        First matching rule (top-down) sets a procedure's follow-up visits.
        Keywords match anywhere in the procedure description.
      </p>
      {effective.map((rule, i) => (
        <section key={i} className="card p-4">
          <div className="flex items-center justify-between mb-2">
            <input className="input w-80 font-medium"
                   value={rule.match.join(', ')}
                   onChange={e => upd(i, r => ({ ...r,
                     match: e.target.value.split(',').map(s => s.trim().toLowerCase()).filter(Boolean) }))} />
            <button className="text-xs text-red-700 hover:underline"
                    onClick={() => setRules(effective.filter((_, j) => j !== i))}>
              Remove Rule
            </button>
          </div>
          {rule.visits.map((v, k) => (
            <div key={k} className="flex items-center gap-2 text-[13px] py-1">
              <input className="input w-44" value={v.label}
                     onChange={e => upd(i, r => ({ ...r, visits: r.visits.map((x, m) =>
                       m === k ? { ...x, label: e.target.value } : x) }))} />
              <input type="number" min={1} max={365} className="input w-20" value={v.offset_days}
                     onChange={e => upd(i, r => ({ ...r, visits: r.visits.map((x, m) =>
                       m === k ? { ...x, offset_days: Number(e.target.value) } : x) }))} />
              <span className="text-muted">days after surgery</span>
              <select className="input w-28" value={v.mode}
                      onChange={e => upd(i, r => ({ ...r, visits: r.visits.map((x, m) =>
                        m === k ? { ...x, mode: e.target.value } : x) }))}>
                <option value="office">Office</option>
                <option value="telehealth">Telehealth</option>
              </select>
              <label className="text-[11px] flex items-center gap-1">
                <input type="checkbox" checked={!!v.location_locked}
                       onChange={e => upd(i, r => ({ ...r, visits: r.visits.map((x, m) =>
                         m === k ? { ...x, location_locked: e.target.checked } : x) }))} />
                In-Person Required
              </label>
              <button className="text-xs text-plum-700 hover:underline"
                      onClick={() => upd(i, r => ({ ...r, visits: r.visits.filter((_, m) => m !== k) }))}>
                ✕
              </button>
            </div>
          ))}
          <button className="text-xs text-plum-700 hover:underline mt-1"
                  onClick={() => upd(i, r => ({ ...r, visits: [...r.visits,
                    { label: 'New visit', offset_days: 14, mode: 'office', location_locked: false }] }))}>
            + Add Visit
          </button>
        </section>
      ))}
      <div className="flex items-center gap-3">
        <button className="text-xs text-plum-700 hover:underline"
                onClick={() => setRules([...effective,
                  { match: ['keyword'], visits: [{ label: '2 weeks post-op',
                    offset_days: 14, mode: 'office', location_locked: false }] }])}>
          + Add Rule
        </button>
        <button className="btn-primary text-xs" disabled={!rules || save.isPending}
                onClick={() => save.mutate(rules)}>
          {save.isPending ? 'Saving…' : 'Save Changes'}
        </button>
        {save.isError && (
          <span className="text-xs text-red-700">{saveErrorMessage(save.error)}</span>
        )}
      </div>
    </div>
  )
}

// ─── Facilities & Capacity tab ──────────────────────────────────────

const NEW_FACILITY = {
  id:         '__new',
  code:       '',
  label:      '',
  address:    '',
  is_active:  true,
  sort_order: 100,
}

function FacilitiesSection() {
  const qc = useQueryClient()
  const [editingId, setEditingId] = useState(null)
  const [draft, setDraft]         = useState(null)
  const [filter, setFilter]       = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['surgery-facilities'],
    queryFn:  () => api.get('/surgery/admin/facilities').then(r => r.data.facilities),
  })

  const facilities = useMemo(() => {
    const rows = data || []
    const q = filter.trim().toLowerCase()
    if (!q) return rows
    return rows.filter(f =>
      (f.code    || '').toLowerCase().includes(q) ||
      (f.label   || '').toLowerCase().includes(q) ||
      (f.address || '').toLowerCase().includes(q)
    )
  }, [data, filter])

  const createMut = useMutation({
    mutationFn: (body) => api.post('/surgery/admin/facilities', body).then(r => r.data),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['surgery-facilities'] }); setEditingId(null); setDraft(null) },
    onError:    (e) => alert(saveErrorMessage(e)),
  })

  const patchMut = useMutation({
    mutationFn: ({ id, body }) => api.patch(`/surgery/admin/facilities/${id}`, body).then(r => r.data),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['surgery-facilities'] }); setEditingId(null); setDraft(null) },
    onError:    (e) => alert(saveErrorMessage(e)),
  })

  const deleteMut = useMutation({
    mutationFn: (id) => api.delete(`/surgery/admin/facilities/${id}`),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['surgery-facilities'] }),
    onError:    (e) => alert(saveErrorMessage(e)),
  })

  function startEdit(row) {
    setEditingId(row.id)
    setDraft({
      code:       row.code       || '',
      label:      row.label      || '',
      address:    row.address    || '',
      is_active:  row.is_active  ?? true,
      sort_order: row.sort_order ?? 100,
    })
  }

  function cancelEdit() { setEditingId(null); setDraft(null) }

  function startNewRow() {
    setEditingId('__new')
    setDraft({ code: '', label: '', address: '', is_active: true, sort_order: 100 })
  }

  function save() {
    if (!draft?.code?.trim())  { alert('Code is required.');  return }
    if (!draft?.label?.trim()) { alert('Label is required.'); return }
    const body = {
      code:       draft.code.trim(),
      label:      draft.label.trim(),
      address:    draft.address.trim() || null,
      is_active:  draft.is_active,
      sort_order: Number(draft.sort_order) || 100,
    }
    if (editingId === '__new') createMut.mutate(body)
    else                       patchMut.mutate({ id: editingId, body })
  }

  function confirmDelete(row) {
    if (!window.confirm(`Delete "${row.label}"?`)) return
    deleteMut.mutate(row.id)
  }

  const showNewRow = editingId === '__new'
  const rows = showNewRow ? [NEW_FACILITY, ...facilities] : facilities
  const isSaving = createMut.isPending || patchMut.isPending

  return (
    <div>
      <div className="bg-white rounded-lg border border-border-subtle">
        <div className="px-5 py-4 border-b border-border-subtle flex items-center gap-3">
          <div className="flex-1 min-w-0">
            <h2 className="text-lg font-semibold text-gray-900">Facilities</h2>
            <p className="text-[12px] text-gray-500 mt-0.5">
              Surgical facilities available for scheduling. Inactive facilities are hidden from the scheduler.
            </p>
          </div>
          <div className="relative">
            <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
            <input className="input text-sm pl-7 pr-2 py-1 w-48"
                   placeholder="Filter…"
                   value={filter}
                   onChange={e => setFilter(e.target.value)} />
          </div>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={startNewRow}
                  disabled={!!editingId}>
            <Plus size={12} /> Add Row
          </button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-[11px] uppercase text-gray-500">
              <tr>
                <th className="text-left px-5 py-2 w-[10%]">Code</th>
                <th className="text-left px-3 py-2 w-[28%]">Label</th>
                <th className="text-left px-3 py-2 w-[28%]">Address</th>
                <th className="text-center px-3 py-2 w-[8%]">Active</th>
                <th className="text-center px-3 py-2 w-[8%]">Sort</th>
                <th className="text-right px-5 py-2 w-[120px]">Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr><td colSpan={6} className="px-5 py-6 text-gray-400 text-[12px]">Loading…</td></tr>
              )}
              {!isLoading && rows.length === 0 && (
                <tr><td colSpan={6} className="px-5 py-6 text-gray-400 text-[12px] italic">
                  No facilities yet — click <strong>Add Row</strong> to start.
                </td></tr>
              )}
              {rows.map(row => {
                const isEditing = editingId === row.id
                const dimmed    = !isEditing && !row.is_active
                return (
                  <tr key={row.id}
                      className={`border-t border-border-subtle ${
                        isEditing ? 'bg-plum-50/40'
                        : dimmed  ? 'opacity-60 hover:bg-gray-50'
                        :           'hover:bg-gray-50'
                      }`}>
                    {isEditing ? (
                      <FacilityEditRow
                        draft={draft}
                        setDraft={setDraft}
                        save={save}
                        cancel={cancelEdit}
                        isSaving={isSaving}
                      />
                    ) : (
                      <FacilityDisplayRow
                        row={row}
                        startEdit={() => startEdit(row)}
                        onDelete={() => confirmDelete(row)}
                        disabled={!!editingId}
                      />
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function FacilityDisplayRow({ row, startEdit, onDelete, disabled }) {
  return (
    <>
      <td className="px-5 py-3 align-middle">
        <code className="text-[12px] bg-gray-100 px-1 py-0.5 rounded">{row.code}</code>
      </td>
      <td className="px-3 py-3 align-middle font-medium text-gray-900">{row.label}</td>
      <td className="px-3 py-3 align-middle text-[12px] text-gray-600">{row.address || <span className="italic text-gray-400">—</span>}</td>
      <td className="px-3 py-3 align-middle text-center">
        <span className={`inline-block w-2 h-2 rounded-full ${row.is_active ? 'bg-green-500' : 'bg-gray-300'}`} title={row.is_active ? 'Active' : 'Inactive'} />
      </td>
      <td className="px-3 py-3 align-middle text-center text-[12px] text-gray-500">{row.sort_order}</td>
      <td className="px-5 py-3 align-middle text-right">
        <div className="inline-flex items-center gap-1">
          <button className="text-[11px] px-2 py-1 rounded border border-border-subtle hover:bg-plum-50 flex items-center gap-1 disabled:opacity-30"
                  onClick={startEdit}
                  disabled={disabled}
                  title="Edit row">
            <Edit3 size={11} /> Edit
          </button>
          <button className="text-[11px] px-2 py-1 rounded border border-red-200 text-red-600 hover:bg-red-50 flex items-center gap-1 disabled:opacity-30"
                  onClick={onDelete}
                  disabled={disabled}
                  title="Delete">
            <Trash2 size={11} />
          </button>
        </div>
      </td>
    </>
  )
}

function FacilityEditRow({ draft, setDraft, save, cancel, isSaving }) {
  return (
    <>
      <td className="px-5 py-3 align-top">
        <input className="input text-sm w-24"
               placeholder="office"
               value={draft.code}
               onChange={e => setDraft({ ...draft, code: e.target.value })}
               autoFocus />
      </td>
      <td className="px-3 py-3 align-top">
        <input className="input text-sm w-full"
               placeholder="Facility label"
               value={draft.label}
               onChange={e => setDraft({ ...draft, label: e.target.value })} />
      </td>
      <td className="px-3 py-3 align-top">
        <input className="input text-sm w-full"
               placeholder="City, ST  or full address"
               value={draft.address}
               onChange={e => setDraft({ ...draft, address: e.target.value })} />
      </td>
      <td className="px-3 py-3 align-top text-center">
        <input type="checkbox"
               className="h-4 w-4 rounded border-gray-300 text-plum-600 focus:ring-plum-500"
               checked={draft.is_active}
               onChange={e => setDraft({ ...draft, is_active: e.target.checked })} />
      </td>
      <td className="px-3 py-3 align-top">
        <input type="number" min="1"
               className="input text-sm w-16 text-center"
               value={draft.sort_order}
               onChange={e => setDraft({ ...draft, sort_order: e.target.value })} />
      </td>
      <td className="px-5 py-3 align-top text-right">
        <div className="inline-flex items-center gap-1">
          <button className="text-[11px] px-2 py-1 rounded bg-plum-600 text-white hover:bg-plum-700 flex items-center gap-1 disabled:opacity-50"
                  onClick={save}
                  disabled={isSaving}>
            <Save size={11} /> {isSaving ? 'Saving…' : 'Save'}
          </button>
          <button className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:bg-gray-50"
                  onClick={cancel}
                  disabled={isSaving}>
            Cancel
          </button>
        </div>
      </td>
    </>
  )
}

function CapacityRulesSection() {
  const qc = useQueryClient()
  const { data: config } = useQuery({
    queryKey: ['surgery-config'],
    queryFn: () => api.get('/surgery/config').then(r => r.data),
  })
  const { data: defaults } = useQuery({
    queryKey: ['capacity-defaults'],
    queryFn: () => api.get('/surgery/config/capacity-defaults').then(r => r.data),
  })
  const [draft, setDraft] = useState(null)
  const save = useMutation({
    mutationFn: (body) => api.put('/surgery/config', { capacity_rules: body }).then(r => r.data),
    onSuccess: () => { setDraft(null); qc.invalidateQueries({ queryKey: ['surgery-config'] }) },
  })
  if (!config || !defaults) return <LoadingState />
  const rules = draft ?? config.capacity_rules ?? defaults.defaults
  const upd = (fac, fn) => setDraft({ ...rules, [fac]: fn(rules[fac]) })

  return (
    <section className="card p-4">
      <h2 className="font-medium mb-1">Daily Capacity Rules</h2>
      <p className="text-[11px] text-amber-800 bg-amber-50 border border-amber-200 rounded p-2 mb-3">
        Changing these affects what the booking system accepts. Values are
        validated against each block day's real time window — a case mix
        that exceeds the day's minutes is still rejected at booking time.
      </p>
      {Object.entries(rules).map(([fac, r]) => (
        <div key={fac} className="border-t border-border-subtle py-3">
          <div className="font-medium text-[13px] uppercase mb-2">{fac}</div>
          {(r.options || []).map((o, i) => (
            <div key={o.case_kind} className="flex items-center gap-2 text-[13px] py-0.5">
              <span className="w-28">{o.case_kind}</span>
              <span className="text-muted">max</span>
              <input type="number" min={1} max={20} className="input w-16" value={o.max}
                     onChange={e => upd(fac, x => ({ ...x, options: x.options.map((y, j) =>
                       j === i ? { ...y, max: Number(e.target.value) } : y) }))} />
              <span className="text-muted">cases/day
                ({defaults.durations[o.case_kind] || 60} min each)</span>
            </div>
          ))}
          {r.kind === 'fixed_slots' && (
            <div className="text-[13px]">
              <span className="text-muted">Slot start times (HH:MM, comma-separated):</span>
              <input className="input w-full mt-1"
                     value={(r.slot_times || []).join(', ')}
                     onChange={e => upd(fac, x => ({ ...x,
                       slot_times: e.target.value.split(',').map(s => s.trim()).filter(Boolean) }))} />
            </div>
          )}
          {r.minor_addon && (
            <div className="flex items-center gap-2 text-[13px] py-0.5">
              <span className="text-muted">Minor add-on allowed after</span>
              <input type="number" min={0} max={20} className="input w-16"
                     value={r.minor_addon.after_count}
                     onChange={e => upd(fac, x => ({ ...x, minor_addon:
                       { ...x.minor_addon, after_count: Number(e.target.value) } }))} />
              <span className="text-muted">robotics; blocked at</span>
              <input type="number" min={1} max={20} className="input w-16"
                     value={r.minor_addon.blocked_at}
                     onChange={e => upd(fac, x => ({ ...x, minor_addon:
                       { ...x.minor_addon, blocked_at: Number(e.target.value) } }))} />
            </div>
          )}
        </div>
      ))}
      <button className="btn-primary text-xs mt-3" disabled={!draft || save.isPending}
              onClick={() => save.mutate(rules)}>
        {save.isPending ? 'Saving…' : 'Save Capacity Rules'}
      </button>
      {save.isError && (
        <p className="text-xs text-red-700 mt-2">{saveErrorMessage(save.error)}</p>
      )}
    </section>
  )
}

function CapacityTab() {
  return (
    <div className="space-y-6">
      <FacilitiesSection />
      <CapacityRulesSection />
    </div>
  )
}

// ─── Templates tab ──────────────────────────────────────────────────

const PROCEDURE_KINDS = ['minor', 'major', 'office', 'robotic_180', 'robotic_240']

const NEW_TEMPLATE = {
  id:                       '__new',
  code:                     '',
  name:                     '',
  procedure_kind:           'minor',
  default_duration_minutes: 60,
  default_cpt_code:         '',
  is_active:                true,
}

function ProcedureTemplatesSection() {
  const qc = useQueryClient()
  const [editingId, setEditingId] = useState(null)
  const [draft, setDraft]         = useState(null)
  const [filter, setFilter]       = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['surgery-templates-admin'],
    queryFn:  () => api.get('/surgery/admin/procedure-templates').then(r => r.data.templates),
  })

  const templates = useMemo(() => {
    const rows = data || []
    const q = filter.trim().toLowerCase()
    if (!q) return rows
    return rows.filter(t =>
      (t.code           || '').toLowerCase().includes(q) ||
      (t.name           || '').toLowerCase().includes(q) ||
      (t.procedure_kind || '').toLowerCase().includes(q)
    )
  }, [data, filter])

  const createMut = useMutation({
    mutationFn: (body) => api.post('/surgery/admin/procedure-templates', body).then(r => r.data),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['surgery-templates-admin'] }); setEditingId(null); setDraft(null) },
    onError:    (e) => alert(saveErrorMessage(e)),
  })

  const patchMut = useMutation({
    mutationFn: ({ id, body }) => api.patch(`/surgery/admin/procedure-templates/${id}`, body).then(r => r.data),
    onSuccess:  () => { qc.invalidateQueries({ queryKey: ['surgery-templates-admin'] }); setEditingId(null); setDraft(null) },
    onError:    (e) => alert(saveErrorMessage(e)),
  })

  const deleteMut = useMutation({
    mutationFn: (id) => api.delete(`/surgery/admin/procedure-templates/${id}`),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ['surgery-templates-admin'] }),
    onError:    (e) => alert(saveErrorMessage(e)),
  })

  function startEdit(row) {
    setEditingId(row.id)
    setDraft({
      code:                     row.code                     || '',
      name:                     row.name                     || '',
      procedure_kind:           row.procedure_kind           || 'minor',
      default_duration_minutes: row.default_duration_minutes ?? 60,
      default_cpt_code:         row.default_cpt_code         || '',
      is_active:                row.is_active                ?? true,
    })
  }

  function cancelEdit() { setEditingId(null); setDraft(null) }

  function startNewRow() {
    setEditingId('__new')
    setDraft({ code: '', name: '', procedure_kind: 'minor', default_duration_minutes: 60, default_cpt_code: '', is_active: true })
  }

  function save() {
    if (!draft?.code?.trim()) { alert('Code is required.');  return }
    if (!draft?.name?.trim()) { alert('Name is required.'); return }
    const body = {
      code:                     draft.code.trim(),
      name:                     draft.name.trim(),
      procedure_kind:           draft.procedure_kind,
      default_duration_minutes: Number(draft.default_duration_minutes) || 60,
      default_cpt_code:         draft.default_cpt_code.trim() || null,
      is_active:                draft.is_active,
    }
    if (editingId === '__new') createMut.mutate(body)
    else                       patchMut.mutate({ id: editingId, body })
  }

  function confirmDelete(row) {
    if (!window.confirm(`Delete "${row.name}"?`)) return
    deleteMut.mutate(row.id)
  }

  const showNewRow = editingId === '__new'
  const rows = showNewRow ? [NEW_TEMPLATE, ...templates] : templates
  const isSaving = createMut.isPending || patchMut.isPending

  return (
    <div>
      <div className="bg-white rounded-lg border border-border-subtle">
        <div className="px-5 py-4 border-b border-border-subtle flex items-center gap-3">
          <div className="flex-1 min-w-0">
            <h2 className="text-lg font-semibold text-gray-900">Procedure Templates</h2>
            <p className="text-[12px] text-gray-500 mt-0.5">
              Default procedure templates used when scheduling a surgery. Inactive templates are hidden from the scheduler.
            </p>
          </div>
          <div className="relative">
            <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
            <input className="input text-sm pl-7 pr-2 py-1 w-48"
                   placeholder="Filter…"
                   value={filter}
                   onChange={e => setFilter(e.target.value)} />
          </div>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={startNewRow}
                  disabled={!!editingId}>
            <Plus size={12} /> Add Row
          </button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-[11px] uppercase text-gray-500">
              <tr>
                <th className="text-left px-5 py-2 w-[10%]">Code</th>
                <th className="text-left px-3 py-2 w-[24%]">Name</th>
                <th className="text-left px-3 py-2 w-[16%]">Procedure Kind</th>
                <th className="text-center px-3 py-2 w-[10%]">Default Min</th>
                <th className="text-left px-3 py-2 w-[10%]">Default CPT</th>
                <th className="text-center px-3 py-2 w-[8%]">Active</th>
                <th className="text-right px-5 py-2 w-[120px]">Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr><td colSpan={7} className="px-5 py-6 text-gray-400 text-[12px]">Loading…</td></tr>
              )}
              {!isLoading && rows.length === 0 && (
                <tr><td colSpan={7} className="px-5 py-6 text-gray-400 text-[12px] italic">
                  No templates yet — click <strong>Add Row</strong> to start.
                </td></tr>
              )}
              {rows.map(row => {
                const isEditing = editingId === row.id
                const dimmed    = !isEditing && !row.is_active
                return (
                  <tr key={row.id}
                      className={`border-t border-border-subtle ${
                        isEditing ? 'bg-plum-50/40'
                        : dimmed  ? 'opacity-60 hover:bg-gray-50'
                        :           'hover:bg-gray-50'
                      }`}>
                    {isEditing ? (
                      <TemplateEditRow
                        draft={draft}
                        setDraft={setDraft}
                        save={save}
                        cancel={cancelEdit}
                        isSaving={isSaving}
                      />
                    ) : (
                      <TemplateDisplayRow
                        row={row}
                        startEdit={() => startEdit(row)}
                        onDelete={() => confirmDelete(row)}
                        disabled={!!editingId}
                      />
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function TemplateDisplayRow({ row, startEdit, onDelete, disabled }) {
  return (
    <>
      <td className="px-5 py-3 align-middle">
        <code className="text-[12px] bg-gray-100 px-1 py-0.5 rounded">{row.code}</code>
      </td>
      <td className="px-3 py-3 align-middle font-medium text-gray-900">{row.name}</td>
      <td className="px-3 py-3 align-middle text-[12px] text-gray-600">{row.procedure_kind || <span className="italic text-gray-400">—</span>}</td>
      <td className="px-3 py-3 align-middle text-center text-[12px] text-gray-600">{row.default_duration_minutes ?? <span className="italic text-gray-400">—</span>}</td>
      <td className="px-3 py-3 align-middle text-[12px] text-gray-600">
        {row.default_cpt_code
          ? <code className="bg-gray-100 px-1 py-0.5 rounded">{row.default_cpt_code}</code>
          : <span className="italic text-gray-400">—</span>}
      </td>
      <td className="px-3 py-3 align-middle text-center">
        <span className={`inline-block w-2 h-2 rounded-full ${row.is_active ? 'bg-green-500' : 'bg-gray-300'}`} title={row.is_active ? 'Active' : 'Inactive'} />
      </td>
      <td className="px-5 py-3 align-middle text-right">
        <div className="inline-flex items-center gap-1">
          <button className="text-[11px] px-2 py-1 rounded border border-border-subtle hover:bg-plum-50 flex items-center gap-1 disabled:opacity-30"
                  onClick={startEdit}
                  disabled={disabled}
                  title="Edit row">
            <Edit3 size={11} /> Edit
          </button>
          <button className="text-[11px] px-2 py-1 rounded border border-red-200 text-red-600 hover:bg-red-50 flex items-center gap-1 disabled:opacity-30"
                  onClick={onDelete}
                  disabled={disabled}
                  title="Delete">
            <Trash2 size={11} />
          </button>
        </div>
      </td>
    </>
  )
}

function TemplateEditRow({ draft, setDraft, save, cancel, isSaving }) {
  return (
    <>
      <td className="px-5 py-3 align-top">
        <input className="input text-sm w-24"
               placeholder="office_30"
               value={draft.code}
               onChange={e => setDraft({ ...draft, code: e.target.value })}
               autoFocus />
      </td>
      <td className="px-3 py-3 align-top">
        <input className="input text-sm w-full"
               placeholder="Template name"
               value={draft.name}
               onChange={e => setDraft({ ...draft, name: e.target.value })} />
      </td>
      <td className="px-3 py-3 align-top">
        <select className="input text-sm w-full"
                value={draft.procedure_kind}
                onChange={e => setDraft({ ...draft, procedure_kind: e.target.value })}>
          {PROCEDURE_KINDS.map(k => <option key={k} value={k}>{k}</option>)}
        </select>
      </td>
      <td className="px-3 py-3 align-top">
        <input type="number" min="1"
               className="input text-sm w-20 text-center"
               value={draft.default_duration_minutes}
               onChange={e => setDraft({ ...draft, default_duration_minutes: e.target.value })} />
      </td>
      <td className="px-3 py-3 align-top">
        <input className="input text-sm w-24"
               placeholder="58571"
               value={draft.default_cpt_code}
               onChange={e => setDraft({ ...draft, default_cpt_code: e.target.value })} />
      </td>
      <td className="px-3 py-3 align-top text-center">
        <input type="checkbox"
               className="h-4 w-4 rounded border-gray-300 text-plum-600 focus:ring-plum-500"
               checked={draft.is_active}
               onChange={e => setDraft({ ...draft, is_active: e.target.checked })} />
      </td>
      <td className="px-5 py-3 align-top text-right">
        <div className="inline-flex items-center gap-1">
          <button className="text-[11px] px-2 py-1 rounded bg-plum-600 text-white hover:bg-plum-700 flex items-center gap-1 disabled:opacity-50"
                  onClick={save}
                  disabled={isSaving}>
            <Save size={11} /> {isSaving ? 'Saving…' : 'Save'}
          </button>
          <button className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:bg-gray-50"
                  onClick={cancel}
                  disabled={isSaving}>
            Cancel
          </button>
        </div>
      </td>
    </>
  )
}

function EmailTemplatesSection() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['email-templates'],
    queryFn: () => api.get('/surgery/admin/email-templates').then(r => r.data),
  })

  const [editingId, setEditingId] = useState(null)
  const [draft, setDraft] = useState(null)
  const [previewVars, setPreviewVars] = useState('{\n  "patient_name": "Pat",\n  "surgery_date": "06/15/2026"\n}')
  const [preview, setPreview] = useState(null)

  const patch = useMutation({
    mutationFn: ({ id, body }) =>
      api.patch(`/surgery/admin/email-templates/${id}`, body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['email-templates'] })
      setEditingId(null); setDraft(null); setPreview(null)
    },
    onError: (e) => alert(saveErrorMessage(e)),
  })

  const previewMut = useMutation({
    mutationFn: (body) =>
      api.post('/surgery/admin/email-templates/preview', body).then(r => r.data),
    onSuccess: (data) => setPreview(data),
    onError: (e) => alert(saveErrorMessage(e)),
  })

  function startEdit(t) {
    setEditingId(t.id)
    setDraft({
      label:     t.label,
      subject:   t.subject,
      html_body: t.html_body,
      is_active: t.is_active,
    })
    setPreview(null)
  }

  function runPreview() {
    let ctx
    try { ctx = JSON.parse(previewVars) }
    catch { return alert('Preview vars JSON is invalid') }
    previewMut.mutate({
      subject:   draft?.subject || '',
      html_body: draft?.html_body || '',
      context:   ctx,
    })
  }

  const list = data?.templates || []

  return (
    <section className="card p-4">
      <h2 className="font-medium mb-3">Email Templates</h2>
      <div className="space-y-3">
        {list.map(t => (
          <div key={t.id}
               className={`bg-white border rounded-lg p-4 ${
                 editingId === t.id ? 'border-plum-400' : 'border-border-subtle'
               }`}>
            <div className="flex items-center justify-between mb-1">
              <div>
                <div className="text-sm font-semibold">{t.label}</div>
                <div className="text-[11px] text-gray-500 font-mono">{t.kind}</div>
              </div>
              <div className="flex items-center gap-2">
                <span className={`text-[11px] px-2 py-0.5 rounded ${
                  t.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                }`}>{t.is_active ? 'active' : 'inactive'}</span>
                {editingId !== t.id && (
                  <button className="btn-secondary text-[11px]" onClick={() => startEdit(t)}>
                    Edit
                  </button>
                )}
              </div>
            </div>

            {editingId === t.id ? (
              <div className="mt-2 space-y-2">
                <div>
                  <label className="text-[11px] uppercase text-gray-500 block mb-0.5">Subject</label>
                  <input className="input text-sm w-full"
                         value={draft.subject}
                         onChange={e => setDraft({ ...draft, subject: e.target.value })} />
                </div>
                <div>
                  <label className="text-[11px] uppercase text-gray-500 block mb-0.5">HTML Body</label>
                  <textarea className="input text-sm w-full font-mono" rows={8}
                            value={draft.html_body}
                            onChange={e => setDraft({ ...draft, html_body: e.target.value })} />
                </div>
                <div>
                  <label className="text-[11px] uppercase text-gray-500 block mb-0.5">
                    Preview Vars (JSON)
                  </label>
                  <textarea className="input text-[11px] w-full font-mono" rows={4}
                            value={previewVars}
                            onChange={e => setPreviewVars(e.target.value)} />
                </div>
                {preview && (
                  <div className="bg-gray-50 border border-border-subtle rounded p-2">
                    <div className="text-[11px] uppercase text-gray-500 mb-1">Preview</div>
                    <div className="text-[12px] font-semibold">{preview.subject}</div>
                    <div className="text-[12px] mt-1" dangerouslySetInnerHTML={{ __html: preview.html_body }} />
                  </div>
                )}
                <div className="flex items-center gap-2 pt-1">
                  <button className="btn-primary text-sm"
                          onClick={() => patch.mutate({ id: t.id, body: draft })}
                          disabled={patch.isPending}>
                    {patch.isPending ? 'Saving…' : 'Save'}
                  </button>
                  <button className="btn-secondary text-sm" onClick={runPreview}
                          disabled={previewMut.isPending}>
                    {previewMut.isPending ? 'Rendering…' : 'Preview'}
                  </button>
                  <label className="text-[11px] flex items-center gap-1 ml-2">
                    <input type="checkbox"
                           checked={draft.is_active}
                           onChange={e => setDraft({ ...draft, is_active: e.target.checked })} />
                    Active
                  </label>
                  <button className="btn-secondary text-sm ml-auto"
                          onClick={() => { setEditingId(null); setDraft(null); setPreview(null) }}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div className="text-[12px] text-gray-700 mt-2 font-mono whitespace-pre-wrap line-clamp-3">
                {t.subject}
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

function SmsTemplatesSection() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['sms-templates'],
    queryFn: () => api.get('/surgery/admin/sms-templates').then(r => r.data),
  })

  const [editingId, setEditingId] = useState(null)
  const [draft, setDraft] = useState(null)
  const [previewVars, setPreviewVars] = useState('{\n  "patient_name": "Pat",\n  "surgery_date": "06/15/2026",\n  "start_time": "07:30",\n  "facility": "MedStar",\n  "days_until": "3"\n}')
  const [preview, setPreview] = useState(null)

  const patch = useMutation({
    mutationFn: ({ id, body }) =>
      api.patch(`/surgery/admin/sms-templates/${id}`, body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sms-templates'] })
      setEditingId(null); setDraft(null); setPreview(null)
    },
    onError: (e) => alert(saveErrorMessage(e)),
  })

  const previewMut = useMutation({
    mutationFn: (body) =>
      api.post('/surgery/admin/sms-templates/preview', body).then(r => r.data),
    onSuccess: (data) => setPreview(data),
    onError: (e) => alert(saveErrorMessage(e)),
  })

  function startEdit(t) {
    setEditingId(t.id)
    setDraft({ label: t.label, body: t.body, is_active: t.is_active })
    setPreview(null)
  }

  function runPreview() {
    let ctx
    try { ctx = JSON.parse(previewVars) }
    catch { return alert('Preview vars JSON is invalid') }
    previewMut.mutate({ body: draft?.body || '', context: ctx })
  }

  const list = data?.templates || []

  return (
    <section className="card p-4">
      <h2 className="font-medium mb-3">SMS Templates</h2>
      <div className="space-y-3">
        {list.map(t => (
          <div key={t.id}
               className={`bg-white border rounded-lg p-4 ${
                 editingId === t.id ? 'border-plum-400' : 'border-border-subtle'
               }`}>
            <div className="flex items-center justify-between mb-1">
              <div>
                <div className="text-sm font-semibold">{t.label}</div>
                <div className="text-[11px] text-gray-500 font-mono">{t.kind}</div>
              </div>
              <div className="flex items-center gap-2">
                <span className={`text-[11px] px-2 py-0.5 rounded ${
                  t.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                }`}>{t.is_active ? 'active' : 'inactive'}</span>
                {editingId !== t.id && (
                  <button className="btn-secondary text-[11px]" onClick={() => startEdit(t)}>
                    Edit
                  </button>
                )}
              </div>
            </div>

            {editingId === t.id ? (
              <div className="mt-2 space-y-2">
                <div>
                  <label className="text-[11px] uppercase text-gray-500 block mb-0.5">
                    {'Body (plain text — {{var}} for substitution)'}
                  </label>
                  <textarea className="input text-sm w-full font-mono" rows={4}
                            value={draft.body}
                            onChange={e => setDraft({ ...draft, body: e.target.value })} />
                  <div className="text-[10px] text-gray-400 mt-0.5">
                    {draft.body.length} chars
                    {draft.body.length > 160 && (
                      <span className="text-amber-700 ml-2">
                        (will send as multiple segments)
                      </span>
                    )}
                  </div>
                </div>
                <div>
                  <label className="text-[11px] uppercase text-gray-500 block mb-0.5">
                    Preview Vars (JSON)
                  </label>
                  <textarea className="input text-[11px] w-full font-mono" rows={4}
                            value={previewVars}
                            onChange={e => setPreviewVars(e.target.value)} />
                </div>
                {preview && (
                  <div className="bg-gray-50 border border-border-subtle rounded p-2">
                    <div className="text-[11px] uppercase text-gray-500 mb-1">
                      Preview ({preview.length} chars · {preview.segments} segment{preview.segments === 1 ? '' : 's'})
                    </div>
                    <div className="text-[12px] font-mono whitespace-pre-wrap">{preview.body}</div>
                  </div>
                )}
                <div className="flex items-center gap-2 pt-1">
                  <button className="btn-primary text-sm"
                          onClick={() => patch.mutate({ id: t.id, body: draft })}
                          disabled={patch.isPending}>
                    {patch.isPending ? 'Saving…' : 'Save'}
                  </button>
                  <button className="btn-secondary text-sm" onClick={runPreview}
                          disabled={previewMut.isPending}>
                    {previewMut.isPending ? 'Rendering…' : 'Preview'}
                  </button>
                  <label className="text-[11px] flex items-center gap-1 ml-2">
                    <input type="checkbox"
                           checked={draft.is_active}
                           onChange={e => setDraft({ ...draft, is_active: e.target.checked })} />
                    Active
                  </label>
                  <button className="btn-secondary text-sm ml-auto"
                          onClick={() => { setEditingId(null); setDraft(null); setPreview(null) }}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div className="text-[12px] text-gray-700 mt-2 font-mono whitespace-pre-wrap line-clamp-3">
                {t.body}
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

function TemplatesTab() {
  return (
    <div className="space-y-6">
      <ProcedureTemplatesSection />
      <EmailTemplatesSection />
      <SmsTemplatesSection />
    </div>
  )
}
