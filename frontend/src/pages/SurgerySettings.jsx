import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, Settings } from 'lucide-react'
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
                onClick={() => save.mutate(draft)}>
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

function CapacityTab()  { return <Placeholder name="Facilities & Capacity" /> }
function TemplatesTab() { return <Placeholder name="Templates" /> }
