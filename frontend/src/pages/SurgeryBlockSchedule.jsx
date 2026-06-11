import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  ArrowLeft, Calendar, Plus, RefreshCw, Trash2, AlertTriangle, X,
  CalendarRange,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import { useFacilities } from '../hooks/useFacilities'
import EmptyState from '../components/EmptyState'

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


export default function SurgeryBlockSchedule() {
  const qc = useQueryClient()
  const [tab, setTab] = useState('upcoming')   // upcoming | schedules | blackouts
  const { labelOf } = useFacilities()

  const { data: schedules } = useQuery({
    queryKey: ['surgery-block-schedules'],
    queryFn: () => api.get('/surgery/admin/block-schedules').then(r => r.data),
  })

  const { data: blockDays } = useQuery({
    queryKey: ['surgery-block-days'],
    queryFn: () => api.get('/surgery/admin/block-days?days=60').then(r => r.data),
  })

  const { data: blackouts } = useQuery({
    queryKey: ['surgery-blackouts'],
    queryFn: () => api.get('/surgery/admin/blackouts?days=365').then(r => r.data),
  })

  const rematerialize = useMutation({
    mutationFn: () => api.post('/surgery/admin/block-schedules/materialize').then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery-block-days'] })
    },
  })

  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <Link to="/surgery" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-1">
            <ArrowLeft size={12} /> Surgery dashboard
          </Link>
          <h1 className="font-serif font-semibold text-ink text-[22px] m-0">Block Schedule</h1>
          <p className="text-muted text-[12px] mt-0.5">
            Recurring surgery days + ad-hoc add-ons + holiday/PTO blackouts.
          </p>
        </div>
        <button className="btn-secondary text-sm flex items-center gap-1"
                onClick={() => rematerialize.mutate()}
                disabled={rematerialize.isPending}>
          <RefreshCw size={13} className={rematerialize.isPending ? 'animate-spin' : ''} /> Re-materialize
        </button>
      </div>

      {rematerialize.data && (
        <div className="card text-xs bg-blue-50 border-blue-200 text-blue-800 mb-3">
          ✓ Materialized {rematerialize.data.blockdays_created} new + refreshed {rematerialize.data.blockdays_updated} block days,
          skipped {rematerialize.data.blackout_skips} dates due to blackouts.
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-border-subtle mb-4 flex gap-4 text-sm">
        {[
          { v: 'upcoming',   l: `Upcoming days (${blockDays?.days?.length || 0})` },
          { v: 'schedules',  l: `Recurring schedules (${schedules?.schedules?.length || 0})` },
          { v: 'blackouts',  l: `Blackouts (${blackouts?.blackouts?.length || 0})` },
        ].map(t => (
          <button key={t.v}
                  onClick={() => setTab(t.v)}
                  className={`px-1 py-2 -mb-px border-b-2 ${
                    tab === t.v ? 'border-plum-700 text-plum-700 font-medium'
                                : 'border-transparent text-muted hover:text-plum-700'
                  }`}>
            {t.l}
          </button>
        ))}
      </div>

      {tab === 'upcoming' && <UpcomingTab days={blockDays?.days || []} />}
      {tab === 'schedules' && <SchedulesTab schedules={schedules?.schedules || []} qc={qc} />}
      {tab === 'blackouts' && <BlackoutsTab blackouts={blackouts?.blackouts || []} qc={qc} />}
    </div>
  )
}


function UpcomingTab({ days }) {
  if (days.length === 0) {
    return (
      <div className="card">
        <EmptyState
          icon={CalendarRange}
          title="No upcoming block days"
          body="Add a recurring schedule first — block days will populate from that."
        />
      </div>
    )
  }
  return (
    <div className="space-y-2">
      {days.map(d => <BlockDayRow key={d.id} d={d} />)}
    </div>
  )
}


function BlockDayRow({ d }) {
  const { labelOf } = useFacilities()
  const slots = d.slots || []
  const cap = capacitySummary(d, slots)
  return (
    <div className="card !p-3">
      <div className="flex items-baseline justify-between gap-2">
        <div>
          <div className="text-sm font-medium text-gray-900">
            <Calendar size={12} className="inline mr-1 text-plum-600" />
            {fmt.date(d.block_date)} ({new Date(d.block_date).toLocaleDateString('en-US', { weekday: 'short' })})
            <span className="text-gray-500 font-normal ml-2">{labelOf(d.facility)}</span>
          </div>
          <div className="text-[10px] text-gray-500">
            {d.start_time?.slice(0, 5)}–{d.end_time?.slice(0, 5)} · {d.block_kind.replace(/_/g, ' ')}
          </div>
        </div>
        <div className="text-[11px] text-gray-700">
          <strong>{slots.length}</strong> {slots.length === 1 ? 'case' : 'cases'} · {cap}
        </div>
      </div>
      {slots.length > 0 && (
        <ul className="mt-2 text-[11px] text-gray-700 space-y-0.5">
          {slots.map(sl => (
            <li key={sl.id} className="flex items-baseline gap-2">
              <span className="font-mono text-gray-500">{sl.start_time?.slice(0, 5)}</span>
              <span className="text-gray-400">{sl.duration_minutes}min</span>
              <span className="text-gray-500 capitalize">{sl.procedure_kind.replace(/_/g, ' ')}</span>
              <span className="font-medium ml-2">{sl.patient_name || '—'}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}


function capacitySummary(d, slots) {
  const counts = { robotic_180: 0, robotic_240: 0, minor: 0, major: 0, office: 0 }
  for (const sl of slots) counts[sl.procedure_kind] = (counts[sl.procedure_kind] || 0) + 1
  if (d.facility === 'medstar') {
    if (counts.robotic_240 > 0) return `${counts.robotic_240}/2 × 240min robotic`
    if (counts.robotic_180 + counts.minor > 0) return `${counts.robotic_180}/3 × 180min + ${counts.minor} minor`
    return 'open · max 3×180 or 2×240 robotic'
  }
  if (d.facility === 'crmc') {
    if (counts.major > 0) return `${counts.major}/2 majors`
    if (counts.minor > 0) return `${counts.minor}/6 minors`
    return 'open · 6 minors OR 2 majors'
  }
  return 'office (per-provider)'
}


function SchedulesTab({ schedules, qc }) {
  const [adding, setAdding] = useState(false)

  return (
    <div>
      <div className="space-y-2 mb-3">
        {schedules.map(s => <ScheduleRow key={s.id} s={s} qc={qc} />)}
        {schedules.length === 0 && (
          <div className="card">
            <EmptyState
              icon={Calendar}
              title="No recurring schedules yet"
              body="Click the button below to set up which weekdays a facility has surgical blocks."
              compact
            />
          </div>
        )}
      </div>
      {!adding && (
        <button className="btn-primary text-sm flex items-center gap-1"
                onClick={() => setAdding(true)}>
          <Plus size={13} /> Add Recurring Schedule
        </button>
      )}
      {adding && <ScheduleForm onClose={() => setAdding(false)} qc={qc} />}
    </div>
  )
}


function ScheduleRow({ s, qc }) {
  const { labelOf } = useFacilities()
  const remove = useMutation({
    mutationFn: () => api.delete(`/surgery/admin/block-schedules/${s.id}`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery-block-schedules'] })
      qc.invalidateQueries({ queryKey: ['surgery-block-days'] })
    },
  })
  return (
    <div className="card !p-3 flex items-baseline justify-between gap-2">
      <div>
        <div className="text-sm font-medium">{labelOf(s.facility)}</div>
        <div className="text-[11px] text-gray-700">
          {recurrenceLabel(s)} · {s.start_time?.slice(0, 5)}–{s.end_time?.slice(0, 5)} · {s.block_kind.replace(/_/g, ' ')}
        </div>
        {s.notes && <div className="text-[10px] text-gray-500 italic mt-0.5">{s.notes}</div>}
      </div>
      <button className="text-xs text-red-700 hover:underline flex items-center gap-1"
              onClick={() => {
                if (confirm(`Delete this ${labelOf(s.facility)} schedule? Future block days created from it won't be auto-removed but new ones won't be made.`)) {
                  remove.mutate()
                }
              }}>
        <Trash2 size={11} /> Delete
      </button>
    </div>
  )
}


function recurrenceLabel(s) {
  const wd = WEEKDAYS[s.weekday] || ''
  if (s.recurrence_kind === 'weekly') return `Every ${wd}`
  if (s.recurrence_kind === 'weekly_nth') {
    const ords = (s.nth_in_month || []).map(n => ['1st', '2nd', '3rd', '4th', '5th'][n - 1] || `${n}th`).join(' & ')
    return `${ords} ${wd} of every month`
  }
  if (s.recurrence_kind === 'specific_dates') return `Specific: ${(s.specific_dates || []).join(', ')}`
  return s.recurrence_kind
}


function ScheduleForm({ onClose, qc }) {
  const [form, setForm] = useState({
    facility: 'medstar',
    recurrence_kind: 'weekly_nth',
    weekday: 0,
    nth_in_month: [1, 3],
    specific_dates: [],          // now a list of YYYY-MM-DD strings
    start_time: '07:30',
    end_time: '16:30',
    block_kind: 'mixed',
    notes: '',
  })
  const [dateDraft, setDateDraft] = useState('')
  const [rangeFrom, setRangeFrom] = useState('')
  const [rangeTo, setRangeTo] = useState('')
  const [rangeWeekday, setRangeWeekday] = useState(0)

  const create = useMutation({
    mutationFn: () => api.post('/surgery/admin/block-schedules', {
      ...form,
      specific_dates: form.recurrence_kind === 'specific_dates' ? form.specific_dates : null,
      nth_in_month: form.recurrence_kind === 'weekly_nth' ? form.nth_in_month : null,
      weekday: form.recurrence_kind !== 'specific_dates' ? form.weekday : null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery-block-schedules'] })
      qc.invalidateQueries({ queryKey: ['surgery-block-days'] })
      onClose()
    },
  })

  function update(patch) { setForm(f => ({ ...f, ...patch })) }
  function toggleNth(n) {
    const cur = new Set(form.nth_in_month)
    if (cur.has(n)) cur.delete(n)
    else cur.add(n)
    update({ nth_in_month: Array.from(cur).sort() })
  }
  function addDate(iso) {
    if (!iso) return
    const cur = new Set(form.specific_dates)
    cur.add(iso)
    update({ specific_dates: Array.from(cur).sort() })
  }
  function removeDate(iso) {
    update({ specific_dates: form.specific_dates.filter(d => d !== iso) })
  }
  function generateFromRange() {
    if (!rangeFrom || !rangeTo) return
    const fromDt = new Date(rangeFrom + 'T00:00:00')
    const toDt = new Date(rangeTo + 'T00:00:00')
    if (fromDt > toDt) return
    const dates = []
    // weekday in our enum is 0=Mon..6=Sun, JS getDay returns 0=Sun..6=Sat
    const targetJsWd = (rangeWeekday + 1) % 7
    const cur = new Date(fromDt)
    while (cur <= toDt) {
      if (cur.getDay() === targetJsWd) {
        const yyyy = cur.getFullYear()
        const mm = String(cur.getMonth() + 1).padStart(2, '0')
        const dd = String(cur.getDate()).padStart(2, '0')
        dates.push(`${yyyy}-${mm}-${dd}`)
      }
      cur.setDate(cur.getDate() + 1)
    }
    const merged = Array.from(new Set([...form.specific_dates, ...dates])).sort()
    update({ specific_dates: merged })
  }

  return (
    <div className="card">
      <h3 className="text-sm font-semibold mb-2">New Recurring Schedule</h3>
      <div className="grid grid-cols-2 gap-3 text-sm">
        <Field label="Facility">
          <select className="input text-sm" value={form.facility}
                  onChange={e => update({ facility: e.target.value })}>
            <option value="medstar">MedStar</option>
            <option value="crmc">CRMC</option>
            <option value="office">Office (White Plains)</option>
          </select>
        </Field>
        <Field label="Recurrence">
          <select className="input text-sm" value={form.recurrence_kind}
                  onChange={e => update({ recurrence_kind: e.target.value })}>
            <option value="weekly">Every week</option>
            <option value="weekly_nth">Specific weeks of the month</option>
            <option value="specific_dates">Specific dates only</option>
          </select>
        </Field>

        {form.recurrence_kind !== 'specific_dates' && (
          <Field label="Weekday">
            <select className="input text-sm" value={form.weekday}
                    onChange={e => update({ weekday: parseInt(e.target.value) })}>
              {WEEKDAYS.map((d, i) => <option key={i} value={i}>{d}</option>)}
            </select>
          </Field>
        )}

        {form.recurrence_kind === 'weekly_nth' && (
          <Field label="Which weeks of the month">
            <div className="flex gap-1">
              {[1, 2, 3, 4, 5].map(n => {
                const on = form.nth_in_month.includes(n)
                return (
                  <button key={n} type="button"
                          onClick={() => toggleNth(n)}
                          className={`text-xs w-10 h-9 rounded border ${on
                            ? 'bg-plum-100 border-plum-300 text-plum-700 font-semibold'
                            : 'bg-white border-gray-200 text-muted'}`}>
                    {['1st', '2nd', '3rd', '4th', '5th'][n - 1]}
                  </button>
                )
              })}
            </div>
          </Field>
        )}

        {form.recurrence_kind === 'specific_dates' && (
          <Field label="Dates">
            <div className="space-y-2">
              {/* Selected dates as chips */}
              <div className="flex flex-wrap gap-1 min-h-[26px]">
                {form.specific_dates.length === 0 && (
                  <span className="text-[11px] text-gray-400 italic">No dates yet — add below.</span>
                )}
                {form.specific_dates.map(d => (
                  <span key={d}
                        className="inline-flex items-center gap-1 text-[11px] bg-plum-50 border border-plum-200 text-plum-700 rounded-full px-2 py-0.5">
                    {d}
                    <button type="button"
                            onClick={() => removeDate(d)}
                            className="text-plum-600 hover:text-red-600">×</button>
                  </span>
                ))}
              </div>

              {/* Add a single date */}
              <div className="flex items-center gap-1">
                <input type="date" className="input text-xs"
                       value={dateDraft}
                       onChange={e => setDateDraft(e.target.value)} />
                <button type="button"
                        className="btn-secondary text-xs"
                        disabled={!dateDraft}
                        onClick={() => { addDate(dateDraft); setDateDraft('') }}>
                  + Add date
                </button>
              </div>

              {/* Or generate from a range */}
              <div className="border-t border-gray-100 pt-2">
                <div className="text-[11px] uppercase tracking-wide text-gray-500 mb-1">
                  Or fill from a date range
                </div>
                <div className="flex items-center gap-1 flex-wrap">
                  <input type="date" className="input text-xs"
                         value={rangeFrom}
                         onChange={e => setRangeFrom(e.target.value)} />
                  <span className="text-[11px] text-gray-500">to</span>
                  <input type="date" className="input text-xs"
                         value={rangeTo}
                         onChange={e => setRangeTo(e.target.value)} />
                  <span className="text-[11px] text-gray-500">on</span>
                  <select className="input text-xs"
                          value={rangeWeekday}
                          onChange={e => setRangeWeekday(parseInt(e.target.value))}>
                    {WEEKDAYS.map((d, i) => <option key={i} value={i}>{d}</option>)}
                  </select>
                  <button type="button"
                          className="btn-secondary text-xs"
                          disabled={!rangeFrom || !rangeTo}
                          onClick={generateFromRange}>
                    Generate
                  </button>
                </div>
                <div className="text-[10px] text-gray-500 mt-0.5">
                  Adds every matching weekday in the range to the list above.
                </div>
              </div>
            </div>
          </Field>
        )}

        <Field label="Start time">
          <input className="input text-sm font-mono" type="time"
                 value={form.start_time}
                 onChange={e => update({ start_time: e.target.value })} />
        </Field>
        <Field label="End time">
          <input className="input text-sm font-mono" type="time"
                 value={form.end_time}
                 onChange={e => update({ end_time: e.target.value })} />
        </Field>
        <Field label="Block kind">
          <select className="input text-sm" value={form.block_kind}
                  onChange={e => update({ block_kind: e.target.value })}>
            <option value="mixed">Mixed</option>
            <option value="robotic_only">Robotic only</option>
            <option value="minor_only">Minors only</option>
            <option value="major_only">Majors only</option>
            <option value="office">Office</option>
          </select>
        </Field>
        <Field label="Notes (optional)">
          <input className="input text-sm" value={form.notes}
                 onChange={e => update({ notes: e.target.value })} />
        </Field>
      </div>
      {create.isError && (
        <div className="text-xs text-red-600 mt-2">
          {create.error?.response?.data?.detail || create.error.message}
        </div>
      )}
      <div className="flex justify-end gap-2 mt-3">
        <button className="btn-secondary text-sm" onClick={onClose}>Cancel</button>
        <button className="btn-primary text-sm"
                onClick={() => create.mutate()}
                disabled={create.isPending}>
          {create.isPending ? 'Saving…' : 'Create schedule'}
        </button>
      </div>
    </div>
  )
}


function BlackoutsTab({ blackouts, qc }) {
  const [adding, setAdding] = useState(false)
  const [addingDay, setAddingDay] = useState(false)
  const today = new Date().toISOString().slice(0, 10)
  const upcoming = blackouts.filter(b => b.blackout_date >= today)

  return (
    <div>
      <p className="text-xs text-muted mb-2">
        Days when surgeries can't be scheduled. Holidays auto-seed through 2031;
        add PTO/equipment-down dates manually.
      </p>
      <div className="space-y-1 mb-3">
        {upcoming.map(b => <BlackoutRow key={b.id} b={b} qc={qc} />)}
        {upcoming.length === 0 && (
          <div className="text-xs text-gray-400 italic">No upcoming blackouts.</div>
        )}
      </div>
      {!adding && !addingDay && (
        <div className="flex items-center gap-2">
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => setAdding(true)}>
            <Plus size={13} /> Add PTO / blackout
          </button>
          <button className="btn-secondary text-sm flex items-center gap-1"
                  onClick={() => setAddingDay(true)}>
            <Plus size={13} /> Add surgery day
          </button>
        </div>
      )}
      {adding && <BlackoutForm onClose={() => setAdding(false)} qc={qc} />}
      {addingDay && <AdHocBlockDayForm onClose={() => setAddingDay(false)} qc={qc} />}
    </div>
  )
}


function AdHocBlockDayForm({ onClose, qc }) {
  const [date, setDate] = useState('')
  const [facility, setFacility] = useState('office')
  const [startTime, setStartTime] = useState('08:00')
  const [endTime, setEndTime] = useState('16:00')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState(null)

  const create = useMutation({
    mutationFn: () => api.post('/surgery/admin/block-days', {
      block_date: date,
      facility,
      block_kind: 'addon',
      start_time: startTime,
      end_time:   endTime,
      notes:      notes || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery-block-days'] })
      qc.invalidateQueries({ queryKey: ['surgery-block-dates'] })
      qc.invalidateQueries({ queryKey: ['surgery-calendar'] })
      onClose()
    },
    onError: (e) => setError(e?.response?.data?.detail || e.message),
  })

  const canSave = date && facility && startTime && endTime && startTime < endTime

  return (
    <div className="card">
      <h3 className="text-sm font-semibold mb-2">Add Surgery Day</h3>
      <p className="text-[11px] text-muted mb-2">
        Mark a date as a one-off (ad-hoc) surgery day so coordinators can book
        cases there. Use for vacation make-ups, extra hospital days, etc.
      </p>
      <div className="grid grid-cols-2 gap-3 text-sm">
        <Field label="Date *">
          <input type="date" className="input text-sm" value={date}
                 onChange={e => setDate(e.target.value)} />
        </Field>
        <Field label="Facility *">
          <select className="input text-sm" value={facility}
                  onChange={e => setFacility(e.target.value)}>
            <option value="office">Office</option>
            <option value="medstar">MedStar</option>
            <option value="crmc">CRMC</option>
          </select>
        </Field>
        <Field label="Start time *">
          <input type="time" className="input text-sm" value={startTime}
                 onChange={e => setStartTime(e.target.value)} />
        </Field>
        <Field label="End time *">
          <input type="time" className="input text-sm" value={endTime}
                 onChange={e => setEndTime(e.target.value)} />
        </Field>
        <div className="col-span-2">
          <Field label="Notes">
            <input className="input text-sm" value={notes}
                   onChange={e => setNotes(e.target.value)}
                   placeholder="e.g. make-up day after July 4 closure" />
          </Field>
        </div>
      </div>
      {error && (
        <div className="text-[11px] text-red-700 mt-2">{error}</div>
      )}
      <div className="flex justify-end gap-2 mt-3">
        <button className="btn-secondary text-sm" onClick={onClose}
                disabled={create.isPending}>
          Cancel
        </button>
        <button className="btn-primary text-sm" onClick={() => create.mutate()}
                disabled={!canSave || create.isPending}>
          {create.isPending ? 'Saving…' : 'Add surgery day'}
        </button>
      </div>
    </div>
  )
}


function BlackoutRow({ b, qc }) {
  const { labelOf } = useFacilities()
  const remove = useMutation({
    mutationFn: () => api.delete(`/surgery/admin/blackouts/${b.id}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery-blackouts'] }),
  })
  const tone = b.reason === 'holiday' ? 'bg-blue-50' :
               b.reason === 'pto' ? 'bg-violet-50' : 'bg-amber-50'
  return (
    <div className={`flex items-baseline justify-between gap-2 ${tone} px-3 py-1.5 rounded text-xs`}>
      <div>
        <span className="font-mono">{b.blackout_date}</span>
        <span className="text-gray-500 ml-2">({new Date(b.blackout_date).toLocaleDateString('en-US', { weekday: 'short' })})</span>
        {b.start_time && b.end_time && (
          <span className="font-mono text-amber-700 ml-2">{b.start_time}–{b.end_time}</span>
        )}
        <strong className="ml-2">{b.label || b.reason}</strong>
        <span className="text-gray-500 ml-2">· {b.scope}</span>
        {b.facility && <span className="text-gray-500 ml-1">· {labelOf(b.facility)}</span>}
        {b.owner_email && <span className="text-gray-500 ml-1">· {b.owner_email.split('@')[0]}</span>}
      </div>
      <button className="text-gray-400 hover:text-red-700"
              onClick={() => { if (confirm(`Remove blackout on ${b.blackout_date}?`)) remove.mutate() }}>
        <Trash2 size={11} />
      </button>
    </div>
  )
}


function BlackoutForm({ onClose, qc }) {
  const [form, setForm] = useState({
    scope: 'office',
    reason: 'pto',
    label: '',
    owner_email: '',
    facility: '',
    notes: '',
  })
  const [dates, setDates] = useState([])           // YYYY-MM-DD strings
  const [dateDraft, setDateDraft] = useState('')
  const [rangeFrom, setRangeFrom] = useState('')
  const [rangeTo, setRangeTo] = useState('')
  // Partial-day window. wholeDay=true (the default) sends start/end as
  // null → whole-day blackout. wholeDay=false → server gets the times.
  const [wholeDay, setWholeDay] = useState(true)
  const [startTime, setStartTime] = useState('08:00')
  const [endTime, setEndTime] = useState('17:00')
  const [progress, setProgress] = useState({ done: 0, total: 0, errors: [] })

  function addDate(iso) {
    if (!iso) return
    setDates(prev => Array.from(new Set([...prev, iso])).sort())
  }
  function removeDate(iso) {
    setDates(prev => prev.filter(d => d !== iso))
  }
  function generateFromRange() {
    if (!rangeFrom || !rangeTo) return
    const fromDt = new Date(rangeFrom + 'T00:00:00')
    const toDt = new Date(rangeTo + 'T00:00:00')
    if (fromDt > toDt) return
    const out = []
    const cur = new Date(fromDt)
    while (cur <= toDt) {
      const yyyy = cur.getFullYear()
      const mm = String(cur.getMonth() + 1).padStart(2, '0')
      const dd = String(cur.getDate()).padStart(2, '0')
      out.push(`${yyyy}-${mm}-${dd}`)
      cur.setDate(cur.getDate() + 1)
    }
    setDates(prev => Array.from(new Set([...prev, ...out])).sort())
  }

  async function submit() {
    // Auto-include a date the user typed into the picker but never
    // clicked "+ Add" for — common mistake that used to silently
    // do nothing on submit.
    let effective = dates
    if (effective.length === 0 && dateDraft) {
      effective = [dateDraft]
    }
    if (effective.length === 0) {
      alert("Pick at least one date first — use the date picker above and click '+ Add', or pick a range.")
      return
    }
    setProgress({ done: 0, total: effective.length, errors: [] })
    const errors = []
    for (let i = 0; i < effective.length; i++) {
      try {
        await api.post('/surgery/admin/blackouts', {
          blackout_date: effective[i],
          scope: form.scope,
          reason: form.reason,
          label: form.label,
          notes: form.notes,
          owner_email: form.scope === 'provider' ? form.owner_email : null,
          facility: form.scope === 'facility' ? form.facility : null,
          start_time: wholeDay ? null : startTime,
          end_time:   wholeDay ? null : endTime,
        })
        setProgress({ done: i + 1, total: effective.length, errors })
      } catch (err) {
        errors.push({ date: effective[i], msg: err?.response?.data?.detail || err.message })
        setProgress({ done: i + 1, total: effective.length, errors })
      }
    }
    qc.invalidateQueries({ queryKey: ['surgery-blackouts'] })
    if (errors.length === 0) {
      onClose()
    } else {
      // Surface a clear alert so the user notices the inline error list
      // even if it scrolled below the visible viewport.
      alert(
        `${errors.length} of ${effective.length} date(s) failed:\n` +
        errors.slice(0, 5).map(e => `  • ${e.date}: ${e.msg}`).join('\n') +
        (errors.length > 5 ? `\n  …and ${errors.length - 5} more` : '')
      )
    }
  }

  const submitting = progress.total > 0 && progress.done < progress.total

  return (
    <div className="card">
      <h3 className="text-sm font-semibold mb-2">Add Blackout</h3>
      <div className="grid grid-cols-2 gap-3 text-sm">
        <Field label="Date(s)">
          <div className="space-y-2">
            {/* Chip list */}
            <div className="flex flex-wrap gap-1 min-h-[26px]">
              {dates.length === 0 && (
                <span className="text-[11px] text-gray-400 italic">No dates yet — add below.</span>
              )}
              {dates.map(d => (
                <span key={d}
                      className="inline-flex items-center gap-1 text-[11px] bg-plum-50 border border-plum-200 text-plum-700 rounded-full px-2 py-0.5">
                  {d}
                  <button type="button"
                          onClick={() => removeDate(d)}
                          className="text-plum-600 hover:text-red-600">×</button>
                </span>
              ))}
              {dates.length > 1 && (
                <button type="button"
                        onClick={() => setDates([])}
                        className="text-[10px] text-muted hover:text-red-600 ml-1">
                  clear all
                </button>
              )}
            </div>
            {/* Add one date */}
            <div className="flex items-center gap-1">
              <input type="date" className="input text-xs"
                     value={dateDraft}
                     onChange={e => setDateDraft(e.target.value)} />
              <button type="button"
                      className="btn-secondary text-xs"
                      disabled={!dateDraft}
                      onClick={() => { addDate(dateDraft); setDateDraft('') }}>
                + Add
              </button>
            </div>
            {/* Or a date range (every day inclusive) */}
            <div className="border-t border-gray-100 pt-2">
              <div className="text-[11px] uppercase tracking-wide text-gray-500 mb-1">
                Or add a range (every day inclusive — useful for vacations)
              </div>
              <div className="flex items-center gap-1">
                <input type="date" className="input text-xs"
                       value={rangeFrom}
                       onChange={e => setRangeFrom(e.target.value)} />
                <span className="text-[11px] text-gray-500">to</span>
                <input type="date" className="input text-xs"
                       value={rangeTo}
                       onChange={e => setRangeTo(e.target.value)} />
                <button type="button"
                        className="btn-secondary text-xs"
                        disabled={!rangeFrom || !rangeTo}
                        onClick={generateFromRange}>
                  Add range
                </button>
              </div>
            </div>
          </div>
        </Field>
        <Field label="Scope">
          <select className="input text-sm" value={form.scope}
                  onChange={e => setForm({ ...form, scope: e.target.value })}>
            <option value="office">Office-wide</option>
            <option value="provider">Single provider (PTO)</option>
            <option value="facility">Facility closure</option>
          </select>
        </Field>
        <Field label="Reason">
          <select className="input text-sm" value={form.reason}
                  onChange={e => setForm({ ...form, reason: e.target.value })}>
            <option value="pto">PTO</option>
            <option value="holiday">Holiday</option>
            <option value="facility_closed">Facility closed</option>
            <option value="equipment_down">Equipment down</option>
            <option value="other">Other</option>
          </select>
        </Field>
        <Field label="Label (e.g. Dr. Cooke vacation)">
          <input className="input text-sm" value={form.label}
                 onChange={e => setForm({ ...form, label: e.target.value })} />
        </Field>
        {/* Whole-day vs partial-day window. Times snap to 30-min on the
            server; the inputs only let users pick HH:00 or HH:30. */}
        <div className="col-span-2">
          <div className="text-[11px] uppercase text-gray-500 tracking-wide mb-1">Time window</div>
          <div className="flex items-center gap-3">
            <label className="text-xs flex items-center gap-1.5">
              <input type="radio" checked={wholeDay} onChange={() => setWholeDay(true)} />
              Whole day
            </label>
            <label className="text-xs flex items-center gap-1.5">
              <input type="radio" checked={!wholeDay} onChange={() => setWholeDay(false)} />
              Partial day
            </label>
            {!wholeDay && (
              <>
                <span className="text-[11px] text-gray-500">from</span>
                <select className="input text-xs" value={startTime}
                        onChange={e => setStartTime(e.target.value)}>
                  {_thirtyMinOptions().map(t => <option key={`s${t}`} value={t}>{t}</option>)}
                </select>
                <span className="text-[11px] text-gray-500">to</span>
                <select className="input text-xs" value={endTime}
                        onChange={e => setEndTime(e.target.value)}>
                  {_thirtyMinOptions().map(t => <option key={`e${t}`} value={t}>{t}</option>)}
                </select>
              </>
            )}
          </div>
          {!wholeDay && startTime >= endTime && (
            <div className="text-[11px] text-red-700 mt-1">
              Start time must be before end time.
            </div>
          )}
        </div>
        {form.scope === 'provider' && (
          <Field label="Owner email">
            <input className="input text-sm font-mono" type="email"
                   placeholder="acooke@waldorfwomenscare.com"
                   value={form.owner_email}
                   onChange={e => setForm({ ...form, owner_email: e.target.value })} />
          </Field>
        )}
        {form.scope === 'facility' && (
          <Field label="Facility">
            <select className="input text-sm" value={form.facility}
                    onChange={e => setForm({ ...form, facility: e.target.value })}>
              <option value="">— pick —</option>
              <option value="medstar">MedStar</option>
              <option value="crmc">CRMC</option>
              <option value="office">Office</option>
            </select>
          </Field>
        )}
      </div>
      {progress.errors.length > 0 && (
        <div className="text-[11px] text-red-700 mt-2">
          {progress.errors.length} date(s) failed:
          <ul className="list-disc pl-5">
            {progress.errors.slice(0, 5).map((e, i) => <li key={i}>{e.date}: {e.msg}</li>)}
          </ul>
        </div>
      )}
      <div className="flex justify-end gap-2 mt-3 items-center">
        {submitting && (
          <span className="text-[11px] text-gray-500">
            Saving {progress.done} / {progress.total}…
          </span>
        )}
        <button className="btn-secondary text-sm" onClick={onClose} disabled={submitting}>
          Cancel
        </button>
        <button className="btn-primary text-sm"
                onClick={submit}
                disabled={submitting || (dates.length === 0 && !dateDraft)
                          || (form.scope === 'provider' && !form.owner_email)
                          || (form.scope === 'facility' && !form.facility)
                          || (!wholeDay && startTime >= endTime)}>
          {submitting ? 'Saving…' :
            (dates.length === 0 && dateDraft) ? 'Add 1 blackout' :
            dates.length === 1 ? 'Add 1 blackout' :
            dates.length === 0 ? 'Add blackout' :
            `Add ${dates.length} blackouts`}
        </button>
      </div>
    </div>
  )
}


function Field({ label, children }) {
  return (
    <div>
      <div className="text-[11px] uppercase text-gray-500 tracking-wide mb-1">{label}</div>
      {children}
    </div>
  )
}


/** HH:MM strings on a 30-minute grid, 06:00 → 22:00. Server enforces
 *  the same grid; this is just a friendlier picker than a raw input. */
function _thirtyMinOptions() {
  const out = []
  for (let h = 6; h <= 22; h++) {
    const hh = String(h).padStart(2, '0')
    out.push(`${hh}:00`)
    out.push(`${hh}:30`)
  }
  return out
}
