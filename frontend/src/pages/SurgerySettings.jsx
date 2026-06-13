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

function StepsTab()     { return <Placeholder name="Workflow Steps" /> }
function PostOpTab()    { return <Placeholder name="Post-Op Schedules" /> }
function CapacityTab()  { return <Placeholder name="Facilities & Capacity" /> }
function TemplatesTab() { return <Placeholder name="Templates" /> }
