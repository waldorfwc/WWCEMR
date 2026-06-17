import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { CalendarDays, Trash2, RefreshCw, Plus } from 'lucide-react'
import api from '../utils/api'
import LoadingState from '../components/LoadingState'

const LOCATIONS = [
  { value: 'white_plains', label: 'White Plains' },
  { value: 'brandywine',   label: 'Brandywine' },
  { value: 'arlington',    label: 'Arlington' },
]
const LOCATION_LABELS = Object.fromEntries(LOCATIONS.map(l => [l.value, l.label]))

const RECURRENCE_KINDS = [
  { value: 'daily',          label: 'Daily' },
  { value: 'weekly',         label: 'Weekly' },
  { value: 'weekly_nth',     label: 'Weekly (Nth In Month)' },
  { value: 'monthly_day',    label: 'Monthly (Day Of Month)' },
  { value: 'specific_dates', label: 'Specific Dates' },
]

const WEEKDAYS = [
  { value: 0, label: 'Mon' },
  { value: 1, label: 'Tue' },
  { value: 2, label: 'Wed' },
  { value: 3, label: 'Thu' },
  { value: 4, label: 'Fri' },
  { value: 5, label: 'Sat' },
  { value: 6, label: 'Sun' },
]
const WEEKDAY_LABELS = Object.fromEntries(WEEKDAYS.map(w => [w.value, w.label]))

function fmtTime(val) {
  if (!val) return ''
  const m = String(val).match(/^(\d{1,2}):(\d{2})/)
  return m ? `${m[1].padStart(2, '0')}:${m[2]}` : String(val)
}

// Human one-line summary of a template's recurrence rule.
function recurrenceSummary(t) {
  switch (t.recurrence_kind) {
    case 'daily':
      return 'Daily'
    case 'weekly':
      return `Weekly · ${WEEKDAY_LABELS[t.weekday] ?? `Day ${t.weekday}`}`
    case 'weekly_nth': {
      const nths = Array.isArray(t.nth_in_month) ? t.nth_in_month : (t.nth_in_month != null ? [t.nth_in_month] : [])
      const wd = WEEKDAY_LABELS[t.weekday] ?? `Day ${t.weekday}`
      return `Monthly · ${nths.length ? nths.join(', ') : '?'} ${wd}`
    }
    case 'monthly_day':
      return `Monthly · day ${t.day_of_month}`
    case 'specific_dates': {
      const n = Array.isArray(t.specific_dates) ? t.specific_dates.length : 0
      return `Specific dates${n ? ` (${n})` : ''}`
    }
    default:
      return t.recurrence_kind || '—'
  }
}

function saveErrorMessage(error, fallback = 'Save failed — check values.') {
  const detail = error?.response?.data?.detail
  if (Array.isArray(detail)) return detail[0]?.msg || fallback
  if (typeof detail === 'string') return detail
  return fallback
}

export default function PelletAvailability() {
  const qc = useQueryClient()
  const [toast, setToast] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['pellet-availability-templates'],
    queryFn: () => api.get('/pellets/availability/templates').then(r => r.data),
  })

  const del = useMutation({
    mutationFn: (id) => api.delete(`/pellets/availability/templates/${id}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pellet-availability-templates'] }),
  })

  const materialize = useMutation({
    mutationFn: () => api.post('/pellets/availability/materialize').then(r => r.data),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ['pellet-availability-templates'] })
      setToast(`Re-materialized — ${d?.created ?? 0} slot${d?.created === 1 ? '' : 's'} created (${d?.horizon_days ?? '?'}-day horizon).`)
    },
    onError: (e) => setToast(saveErrorMessage(e, 'Re-materialize failed.')),
  })

  const templates = data?.items || []

  return (
    <div>
      <div className="mb-4">
        <h1 className="font-serif font-semibold text-ink text-[22px] m-0 flex items-center gap-2">
          <CalendarDays size={20} className="text-plum-700" />
          Scheduling
        </h1>
        <p className="text-muted text-[12px] mt-0.5">
          Define recurring availability windows. Slots are generated from these templates
          for patients to book.
        </p>
      </div>

      {toast && (
        <div className="mb-4 text-[12px] text-emerald-800 bg-emerald-50 border border-emerald-200 rounded px-3 py-2">
          {toast}
        </div>
      )}

      {/* Templates table */}
      <section className="card !p-0 overflow-hidden mb-6">
        <header className="flex items-center justify-between px-4 py-3 border-b border-border-subtle">
          <h2 className="font-serif font-semibold text-ink text-[16px] m-0">Availability Templates</h2>
          <button
            className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded border border-border-subtle hover:bg-plum-50 disabled:opacity-50"
            onClick={() => { setToast(''); materialize.mutate() }}
            disabled={materialize.isPending}
          >
            <RefreshCw size={12} className={materialize.isPending ? 'animate-spin' : ''} />
            Re-materialize Slots
          </button>
        </header>

        {isLoading ? (
          <LoadingState />
        ) : templates.length === 0 ? (
          <div className="flex items-center justify-center text-sm text-muted py-10">
            No availability templates yet.
          </div>
        ) : (
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left text-muted border-b border-border-subtle">
                <th className="px-4 py-2 font-medium">Location</th>
                <th className="px-4 py-2 font-medium">Recurrence</th>
                <th className="px-4 py-2 font-medium">Time Window</th>
                <th className="px-4 py-2 font-medium">Slot Length</th>
                <th className="px-4 py-2 font-medium">Provider</th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {templates.map(t => (
                <tr key={t.id} className={t.active ? '' : 'opacity-50'}>
                  <td className="px-4 py-2">{LOCATION_LABELS[t.location] || t.location}</td>
                  <td className="px-4 py-2">{recurrenceSummary(t)}</td>
                  <td className="px-4 py-2">{fmtTime(t.start_time)}–{fmtTime(t.end_time)}</td>
                  <td className="px-4 py-2">{t.slot_minutes ? `${t.slot_minutes} min` : '—'}</td>
                  <td className="px-4 py-2">{t.provider || '—'}</td>
                  <td className="px-4 py-2 text-right">
                    {t.active ? (
                      <button
                        className="inline-flex items-center gap-1 text-[11px] text-red-700 hover:underline disabled:opacity-50"
                        onClick={() => del.mutate(t.id)}
                        disabled={del.isPending}
                      >
                        <Trash2 size={12} /> Delete
                      </button>
                    ) : (
                      <span className="text-[11px] text-muted">Disabled</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <NewTemplateForm />
        <OneOffSlotForm />
      </div>
    </div>
  )
}

// ─── New Availability template ──────────────────────────────────────

const BLANK_TEMPLATE = {
  location: 'white_plains',
  recurrence_kind: 'weekly',
  weekday: 2,
  nth_in_month: [],
  day_of_month: 1,
  specific_dates: '',
  start_time: '09:00',
  end_time: '12:00',
  slot_minutes: 30,
  provider: '',
}

function NewTemplateForm() {
  const qc = useQueryClient()
  const [form, setForm] = useState(BLANK_TEMPLATE)
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const create = useMutation({
    mutationFn: (body) => api.post('/pellets/availability/templates', body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-availability-templates'] })
      setForm(BLANK_TEMPLATE)
    },
  })

  const kind = form.recurrence_kind

  const submit = () => {
    const body = {
      location: form.location,
      recurrence_kind: kind,
      start_time: form.start_time,
      end_time: form.end_time,
      slot_minutes: Number(form.slot_minutes) || undefined,
      provider: form.provider || undefined,
    }
    if (kind === 'weekly' || kind === 'weekly_nth') body.weekday = Number(form.weekday)
    if (kind === 'weekly_nth') body.nth_in_month = form.nth_in_month
    if (kind === 'monthly_day') body.day_of_month = Number(form.day_of_month)
    if (kind === 'specific_dates') {
      body.specific_dates = form.specific_dates
        .split(/[\s,]+/)
        .map(s => s.trim())
        .filter(Boolean)
    }
    create.mutate(body)
  }

  const toggleNth = (n) =>
    set('nth_in_month',
      form.nth_in_month.includes(n)
        ? form.nth_in_month.filter(x => x !== n)
        : [...form.nth_in_month, n].sort((a, b) => a - b))

  return (
    <section className="card p-4">
      <h2 className="font-medium mb-3 flex items-center gap-1.5">
        <Plus size={15} className="text-plum-700" /> New Availability
      </h2>
      <div className="space-y-3">
        <label className="block text-[13px]">
          <span className="font-medium">Location</span>
          <select className="input mt-1 w-full"
                  value={form.location}
                  onChange={e => set('location', e.target.value)}>
            {LOCATIONS.map(l => <option key={l.value} value={l.value}>{l.label}</option>)}
          </select>
        </label>

        <label className="block text-[13px]">
          <span className="font-medium">Recurrence</span>
          <select className="input mt-1 w-full"
                  value={kind}
                  onChange={e => set('recurrence_kind', e.target.value)}>
            {RECURRENCE_KINDS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </label>

        {(kind === 'weekly' || kind === 'weekly_nth') && (
          <label className="block text-[13px]">
            <span className="font-medium">Weekday</span>
            <select className="input mt-1 w-full"
                    value={form.weekday}
                    onChange={e => set('weekday', Number(e.target.value))}>
              {WEEKDAYS.map(w => <option key={w.value} value={w.value}>{w.label}</option>)}
            </select>
          </label>
        )}

        {kind === 'weekly_nth' && (
          <div className="text-[13px]">
            <span className="font-medium">Which Weeks Of The Month</span>
            <div className="flex gap-3 mt-1">
              {[1, 2, 3, 4].map(n => (
                <label key={n} className="flex items-center gap-1">
                  <input type="checkbox"
                         checked={form.nth_in_month.includes(n)}
                         onChange={() => toggleNth(n)} />
                  <span>{n}</span>
                </label>
              ))}
            </div>
          </div>
        )}

        {kind === 'monthly_day' && (
          <label className="block text-[13px]">
            <span className="font-medium">Day Of Month</span>
            <input type="number" min={1} max={31} className="input mt-1 w-28"
                   value={form.day_of_month}
                   onChange={e => set('day_of_month', e.target.value)} />
          </label>
        )}

        {kind === 'specific_dates' && (
          <label className="block text-[13px]">
            <span className="font-medium">Dates</span>
            <textarea className="input mt-1 w-full h-20"
                      placeholder="YYYY-MM-DD, one per line or comma-separated"
                      value={form.specific_dates}
                      onChange={e => set('specific_dates', e.target.value)} />
          </label>
        )}

        <div className="grid grid-cols-2 gap-3">
          <label className="block text-[13px]">
            <span className="font-medium">Start Time</span>
            <input type="time" className="input mt-1 w-full"
                   value={form.start_time}
                   onChange={e => set('start_time', e.target.value)} />
          </label>
          <label className="block text-[13px]">
            <span className="font-medium">End Time</span>
            <input type="time" className="input mt-1 w-full"
                   value={form.end_time}
                   onChange={e => set('end_time', e.target.value)} />
          </label>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <label className="block text-[13px]">
            <span className="font-medium">Slot Length (Min)</span>
            <input type="number" min={5} className="input mt-1 w-full"
                   value={form.slot_minutes}
                   onChange={e => set('slot_minutes', e.target.value)} />
          </label>
          <label className="block text-[13px]">
            <span className="font-medium">Provider (Optional)</span>
            <input type="text" className="input mt-1 w-full"
                   value={form.provider}
                   onChange={e => set('provider', e.target.value)} />
          </label>
        </div>
      </div>

      <button className="btn-primary text-xs mt-4"
              disabled={create.isPending}
              onClick={submit}>
        {create.isPending ? 'Saving…' : 'Add Availability'}
      </button>
      {create.isError && (
        <p className="text-xs text-red-700 mt-2">{saveErrorMessage(create.error)}</p>
      )}
      {create.isSuccess && (
        <p className="text-xs text-emerald-700 mt-2">Availability added.</p>
      )}
    </section>
  )
}

// ─── One-off ad-hoc slot ────────────────────────────────────────────

const BLANK_SLOT = {
  location: 'white_plains',
  slot_date: '',
  start_time: '09:00',
  end_time: '09:30',
  provider: '',
}

function OneOffSlotForm() {
  const qc = useQueryClient()
  const [form, setForm] = useState(BLANK_SLOT)
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const create = useMutation({
    mutationFn: (body) => api.post('/pellets/availability/slots', body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pellet-availability-templates'] })
      setForm(BLANK_SLOT)
    },
  })

  const submit = () => create.mutate({
    location: form.location,
    slot_date: form.slot_date,
    start_time: form.start_time,
    end_time: form.end_time,
    provider: form.provider || undefined,
  })

  return (
    <section className="card p-4">
      <h2 className="font-medium mb-3 flex items-center gap-1.5">
        <Plus size={15} className="text-plum-700" /> Add One-Off Slot
      </h2>
      <p className="text-[11px] text-muted mb-3">
        Add a single ad-hoc slot that isn't part of a recurring template.
      </p>
      <div className="space-y-3">
        <label className="block text-[13px]">
          <span className="font-medium">Location</span>
          <select className="input mt-1 w-full"
                  value={form.location}
                  onChange={e => set('location', e.target.value)}>
            {LOCATIONS.map(l => <option key={l.value} value={l.value}>{l.label}</option>)}
          </select>
        </label>
        <label className="block text-[13px]">
          <span className="font-medium">Date</span>
          <input type="date" className="input mt-1 w-full"
                 value={form.slot_date}
                 onChange={e => set('slot_date', e.target.value)} />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-[13px]">
            <span className="font-medium">Start Time</span>
            <input type="time" className="input mt-1 w-full"
                   value={form.start_time}
                   onChange={e => set('start_time', e.target.value)} />
          </label>
          <label className="block text-[13px]">
            <span className="font-medium">End Time</span>
            <input type="time" className="input mt-1 w-full"
                   value={form.end_time}
                   onChange={e => set('end_time', e.target.value)} />
          </label>
        </div>
        <label className="block text-[13px]">
          <span className="font-medium">Provider (Optional)</span>
          <input type="text" className="input mt-1 w-full"
                 value={form.provider}
                 onChange={e => set('provider', e.target.value)} />
        </label>
      </div>

      <button className="btn-primary text-xs mt-4"
              disabled={create.isPending || !form.slot_date}
              onClick={submit}>
        {create.isPending ? 'Adding…' : 'Add Slot'}
      </button>
      {create.isError && (
        <p className="text-xs text-red-700 mt-2">{saveErrorMessage(create.error)}</p>
      )}
      {create.isSuccess && (
        <p className="text-xs text-emerald-700 mt-2">Slot added.</p>
      )}
    </section>
  )
}
