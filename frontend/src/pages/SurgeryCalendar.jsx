import { useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft, ChevronLeft, ChevronRight, Calendar as CalIcon,
  X, Plus, User as UserIcon,
} from 'lucide-react'
import api, { fmt } from '../utils/api'


const FACILITY_BADGE = {
  medstar: { label: 'MedStar', tone: 'bg-blue-100 text-blue-700 border-blue-200' },
  crmc:    { label: 'CRMC',    tone: 'bg-violet-100 text-violet-700 border-violet-200' },
  office:  { label: 'Office',  tone: 'bg-green-100 text-green-700 border-green-200' },
}

const INDICATOR_TONE = {
  green:  'bg-green-500',
  yellow: 'bg-amber-400',
  red:    'bg-red-600',
}

const INDICATOR_LABEL = {
  green:  'Ready',
  yellow: 'Open tasks',
  red:    'Critically behind',
}

const WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


/* Anchor a given date back to the Monday of its week. Returns YYYY-MM-DD. */
function mondayOf(iso) {
  const [y, m, d] = iso.split('-').map(n => parseInt(n, 10))
  const dt = new Date(y, m - 1, d)
  const wd = (dt.getDay() + 6) % 7    // 0=Mon, 6=Sun
  dt.setDate(dt.getDate() - wd)
  return isoDate(dt)
}


function addDays(iso, n) {
  const [y, m, d] = iso.split('-').map(x => parseInt(x, 10))
  const dt = new Date(y, m - 1, d)
  dt.setDate(dt.getDate() + n)
  return isoDate(dt)
}


function isoDate(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
function pad(n) { return n < 10 ? `0${n}` : `${n}` }


function todayIso() {
  return isoDate(new Date())
}


/* WeeklyCalendar — embeddable 7-day calendar widget.
   - Used standalone via /surgery/calendar (full page with header)
   - Embedded on the surgery dashboard (no header, no back link)
   The widget owns its own week state and the slot data query. */
export function WeeklyCalendar({ compact = false }) {
  const navigate = useNavigate()
  const [start, setStart] = useState(() => mondayOf(todayIso()))

  const { data, isLoading } = useQuery({
    queryKey: ['surgery-calendar', start],
    queryFn: () => api.get('/surgery/calendar', {
      params: { start_date: start, days: 7 },
    }).then(r => r.data),
    keepPreviousData: true,
  })

  const cells = useMemo(() => {
    const days = []
    for (let i = 0; i < 7; i++) {
      const iso = addDays(start, i)
      const dt = parseIso(iso)
      days.push({
        iso,
        date: dt,
        weekday: WEEKDAY_LABELS[(dt.getDay() + 6) % 7],
        isToday: iso === todayIso(),
        isWeekend: dt.getDay() === 0 || dt.getDay() === 6,
      })
    }
    return days
  }, [start])

  const byDate = useMemo(() => {
    const out = {}
    for (const s of (data?.surgeries || [])) {
      if (!out[s.scheduled_date]) out[s.scheduled_date] = []
      out[s.scheduled_date].push(s)
    }
    return out
  }, [data])

  function jumpToToday() { setStart(mondayOf(todayIso())) }
  function prevWeek()    { setStart(addDays(start, -7)) }
  function nextWeek()    { setStart(addDays(start,  7)) }

  return (
    <div>
      {/* Week navigation */}
      <div className="card !p-2 mb-2 flex items-center gap-2 flex-wrap">
        <button onClick={prevWeek}
                className="btn-secondary text-xs flex items-center gap-1">
          <ChevronLeft size={12} /> Prev
        </button>
        <button onClick={nextWeek}
                className="btn-secondary text-xs flex items-center gap-1">
          Next <ChevronRight size={12} />
        </button>
        <button onClick={jumpToToday}
                className="btn-secondary text-xs flex items-center gap-1">
          <CalIcon size={12} /> This week
        </button>
        <span className="text-[11px] text-gray-600 mx-1">
          Week of <strong>{fmt.date(start)}</strong>
          {data && <> · {data.surgeries.length} scheduled</>}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <label className="text-[11px] text-gray-600">Jump to:</label>
          <input type="date"
                 aria-label="Jump to week starting"
                 value={start}
                 onChange={e => e.target.value && setStart(mondayOf(e.target.value))}
                 className="input text-xs" />
          {!compact && <span className="text-[10px] text-gray-500">(snaps to Monday)</span>}
          <Legend />
        </div>
      </div>

      {isLoading && !data && <div className="text-gray-400 text-sm">Loading…</div>}

      <div className="card !p-0 overflow-hidden">
        <div className="grid grid-cols-1 md:grid-cols-7">
          {cells.map((cell) => {
            const surgeries = byDate[cell.iso] || []
            return (
              <div key={cell.iso}
                   className={`border-b md:border-b-0 md:border-r border-border-subtle last:border-r-0 last:border-b-0 ${compact ? 'min-h-[120px]' : 'min-h-[140px]'} p-2 ${
                     cell.isToday ? 'bg-plum-50/40 ring-1 ring-plum-300 ring-inset' :
                     cell.isWeekend ? 'bg-gray-50/40' : 'bg-white'
                   }`}>
                <div className="flex items-baseline justify-between mb-2">
                  <div className={`text-[11px] uppercase tracking-wide ${
                    cell.isToday ? 'font-bold text-plum-700' :
                    cell.isWeekend ? 'text-gray-400' : 'text-gray-600'
                  }`}>
                    {cell.weekday}
                  </div>
                  <div className={`text-[13px] font-semibold ${
                    cell.isToday ? 'text-plum-700' :
                    cell.isWeekend ? 'text-gray-400' : 'text-gray-800'
                  }`}>
                    {cell.date.getDate()}
                  </div>
                </div>
                {surgeries.length === 0 ? (
                  <div className="text-[11px] text-gray-400 italic">—</div>
                ) : (
                  <div className="space-y-1.5">
                    {surgeries.map(s => (
                      <SurgeryCard key={s.id}
                                    surgery={s}
                                    onClick={() => navigate(`/surgery/${s.id}`)} />
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}


export function MonthlyCalendar() {
  const navigate = useNavigate()
  const [anchor, setAnchor] = useState(() => isoDate(new Date()))
  const [openDay, setOpenDay] = useState(null)    // YYYY-MM-DD selected
  const gridStart = useMemo(() => startOfMonthGrid(anchor), [anchor])
  const gridEnd = useMemo(() => addDays(gridStart, 41), [gridStart])  // 6 rows × 7 cols - 1

  const { data, isLoading } = useQuery({
    queryKey: ['surgery-calendar', gridStart, gridEnd],
    queryFn: () => api.get('/surgery/calendar', {
      params: { start: gridStart, end: gridEnd },
    }).then(r => r.data),
    keepPreviousData: true,
  })

  // Days that are allocated as surgery days (have at least one BlockDay).
  // Non-surgery days get visually dimmed.
  const { data: blockDatesData } = useQuery({
    queryKey: ['surgery-block-dates', gridStart, gridEnd],
    queryFn: () => api.get('/surgery/admin/block-dates', {
      params: { start: gridStart, end: gridEnd },
    }).then(r => r.data),
    keepPreviousData: true,
  })
  const blockDateSet = useMemo(
    () => new Set(blockDatesData?.dates || []),
    [blockDatesData]
  )

  // Build day → surgeries map.
  const byDay = useMemo(() => {
    const m = {}
    for (const s of (data?.surgeries || [])) {
      const k = s.scheduled_date
      if (!k) continue
      if (!m[k]) m[k] = []
      m[k].push(s)
    }
    return m
  }, [data])

  const days = Array.from({ length: 42 }, (_, i) => addDays(gridStart, i))

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <button className="btn-secondary text-sm flex items-center gap-1"
                  onClick={() => setAnchor(a => addMonths(a, -1))}>
            <ChevronLeft size={14} /> Prev
          </button>
          <button className="btn-secondary text-sm"
                  onClick={() => setAnchor(isoDate(new Date()))}>Today</button>
          <button className="btn-secondary text-sm flex items-center gap-1"
                  onClick={() => setAnchor(a => addMonths(a, 1))}>
            Next <ChevronRight size={14} />
          </button>
        </div>
        <h2 className="text-lg font-semibold text-gray-900">{monthLabel(anchor)}</h2>
        <div></div>
      </div>

      <div className="grid grid-cols-7 text-[11px] uppercase text-gray-500 mb-1">
        {WEEKDAY_LABELS.map(d => (
          <div key={d} className="text-center py-1">{d}</div>
        ))}
      </div>
      <div className="grid grid-cols-7 border-t border-l border-border-subtle">
        {days.map(iso => {
          const surgs = byDay[iso] || []
          const isToday = iso === isoDate(new Date())
          const dimMonth = !inSameMonth(iso, anchor)
          const isSurgeryDay = blockDateSet.has(iso)
          // Days without a BlockDay get visually dimmed so coordinators
          // can see at a glance which dates are even bookable.
          const dim = dimMonth || !isSurgeryDay
          return (
            <button key={iso}
                    type="button"
                    onClick={() => setOpenDay(iso)}
                    title={isSurgeryDay
                      ? `Open ${iso} schedule`
                      : `${iso} — not a surgery day. Click to add one.`}
                    className={`min-h-[110px] border-r border-b border-border-subtle p-1 text-left ${
                      dim ? 'bg-gray-100 text-gray-400' : 'bg-white'
                    } ${isToday ? 'ring-2 ring-plum-400 ring-inset' : ''}
                       ${isSurgeryDay
                         ? 'hover:bg-plum-50/40 cursor-pointer'
                         : 'hover:bg-plum-50/30 cursor-pointer'} transition-colors`}>
              <div className="text-[11px] font-semibold mb-1">
                {iso.slice(-2)}
                {!isSurgeryDay && !dimMonth && (
                  <span className="ml-1 text-[9px] text-gray-400 normal-case font-normal">
                    no block
                  </span>
                )}
              </div>
              {surgs.slice(0, 6).map(s => {
                const fac = FACILITY_BADGE[s.facility] || { label: s.facility, tone: 'bg-gray-100 text-gray-700 border-gray-200' }
                return (
                  <span key={s.id}
                          onClick={(e) => { e.stopPropagation(); navigate(`/surgery/${s.id}`) }}
                          title={s.patient_name}
                          className={`block w-full text-[10px] truncate border rounded mb-0.5 px-1 py-0.5 ${fac.tone} hover:opacity-80 cursor-pointer`}>
                    <span className={`inline-block w-1.5 h-1.5 rounded-full mr-1 ${INDICATOR_TONE[s.indicator] || 'bg-gray-400'}`} />
                    {s.patient_name}
                  </span>
                )
              })}
              {surgs.length > 6 && (
                <span onClick={(e) => { e.stopPropagation(); navigate(`/surgery/calendar?view=week&anchor=${iso}`) }}
                        className="text-[10px] text-plum-700 hover:underline cursor-pointer">
                  +{surgs.length - 6} more
                </span>
              )}
            </button>
          )
        })}
      </div>

      {openDay && (
        <DayDetailDrawer date={openDay} onClose={() => setOpenDay(null)} />
      )}
    </div>
  )
}


// Side-drawer showing one day's full schedule. Per-facility columns
// each with a 30-min grid. Booked slots link out to the surgery; open
// slots offer a picker of unscheduled surgeries to book.
function DayDetailDrawer({ date, onClose }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['surgery-calendar-day', date],
    queryFn: () => api.get(`/surgery/admin/calendar-day/${date}`).then(r => r.data),
  })
  const [pickerSlot, setPickerSlot] = useState(null)
    // { blockDayId, facility, time, durationMinutes? }

  const bds = data?.block_days || []
  const blackouts = data?.blackouts || []
  const unscheduled = data?.unscheduled_surgeries || []
  const dayObj = new Date(date + 'T00:00:00')
  const weekday = dayObj.toLocaleDateString('en-US', { weekday: 'long' })
  const pretty = dayObj.toLocaleDateString('en-US',
    { month: 'short', day: 'numeric', year: 'numeric' })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-5xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between z-10">
          <div>
            <h2 className="text-[15px] font-semibold text-gray-900">{weekday}, {pretty}</h2>
            <div className="text-[11px] text-muted">{date}</div>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-800">
            <X size={18} />
          </button>
        </div>
        <div className="p-5 space-y-4">
          {blackouts.length > 0 && (
            <div className="text-[12px] bg-amber-50 border border-amber-200 rounded p-2 space-y-0.5">
              <div className="font-semibold text-amber-900">Blackouts on this date</div>
              {blackouts.map(b => (
                <div key={b.id} className="text-amber-800">
                  • {b.label || b.reason}
                  {b.facility && ` — ${b.facility}`}
                  {b.owner_email && ` — ${b.owner_email}`}
                  {b.notes && <span className="text-amber-700"> · {b.notes}</span>}
                </div>
              ))}
            </div>
          )}
          {isLoading ? (
            <div className="text-sm text-muted italic">Loading…</div>
          ) : bds.length === 0 ? (
            <div className="text-sm bg-gray-50 border border-gray-200 rounded p-4 text-center">
              <div className="text-gray-600 mb-2">
                Not allocated as a surgery day.
              </div>
              <div className="text-[11px] text-gray-500">
                Use Block Schedule → Blackouts → <em>Add surgery day</em> to make this date bookable.
              </div>
            </div>
          ) : (
            <div className={`grid gap-3 ${
              bds.length === 1 ? 'grid-cols-1' :
              bds.length === 2 ? 'grid-cols-2' : 'grid-cols-3'
            }`}>
              {bds.map(bd => (
                <FacilityColumn key={bd.id} bd={bd}
                  onPickOpen={(time) => setPickerSlot({
                    blockDayId: bd.id, facility: bd.facility, time,
                  })}
                  onPickBooked={(surgeryId) => navigate(`/surgery/${surgeryId}`)}
                />
              ))}
            </div>
          )}
        </div>

        {pickerSlot && (
          <BookSurgeryPicker slot={pickerSlot}
            unscheduled={unscheduled.filter(s =>
              !pickerSlot.facility ||
              (s.eligible_facilities || []).includes(pickerSlot.facility)
            )}
            date={date}
            onBooked={() => {
              setPickerSlot(null)
              qc.invalidateQueries({ queryKey: ['surgery-calendar-day', date] })
              qc.invalidateQueries({ queryKey: ['surgery-calendar'] })
              qc.invalidateQueries({ queryKey: ['surgery-block-dates'] })
            }}
            onClose={() => setPickerSlot(null)}
          />
        )}
      </div>
    </div>
  )
}


function FacilityColumn({ bd, onPickOpen, onPickBooked }) {
  const fac = FACILITY_BADGE[bd.facility] ||
    { label: bd.facility, tone: 'bg-gray-100 text-gray-700 border-gray-200' }
  return (
    <div className="border border-gray-200 rounded">
      <div className={`px-2 py-1.5 border-b border-gray-200 flex items-baseline justify-between ${fac.tone}`}>
        <strong className="text-[12px]">{fac.label}</strong>
        <span className="text-[10px] opacity-80">
          {bd.start_time?.slice(0, 5)}–{bd.end_time?.slice(0, 5)}
          {bd.is_addon && ' · add-on'}
        </span>
      </div>
      <ul className="divide-y divide-gray-100 max-h-[60vh] overflow-y-auto">
        {(bd.grid || []).map(row => (
          <li key={row.time}
              className={`flex items-baseline gap-2 px-2 py-1 text-[12px] ${
                row.booking ? 'bg-plum-50/30' : 'hover:bg-green-50/60 cursor-pointer'
              }`}
              onClick={() => row.booking
                ? row.booking.surgery_id && onPickBooked(row.booking.surgery_id)
                : onPickOpen(row.time)}>
            <span className="font-mono text-[11px] text-gray-500 w-12 shrink-0">
              {row.time}
            </span>
            {row.booking ? (
              <span className="flex-1 truncate">
                <UserIcon size={10} className="inline mr-1 text-plum-600" />
                <span className="font-medium">{row.booking.patient_name || '?'}</span>
                <span className="ml-1 text-[10px] text-gray-500">
                  {row.booking.chart_number ? `· ${row.booking.chart_number}` : ''}
                  {row.booking.duration_minutes ? ` · ${row.booking.duration_minutes}m` : ''}
                </span>
              </span>
            ) : (
              <span className="flex-1 text-[11px] text-green-700 italic flex items-center gap-1">
                <Plus size={9} /> available
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}


function BookSurgeryPicker({ slot, unscheduled, date, onBooked, onClose }) {
  const [error, setError] = useState(null)
  const book = useMutation({
    mutationFn: ({ surgeryId, duration, kind }) =>
      api.post(`/surgery/${surgeryId}/book-slot`, {
        block_day_id:     slot.blockDayId,
        start_time:       slot.time,
        duration_minutes: duration,
        procedure_kind:   kind,
      }).then(r => r.data),
    onSuccess: () => onBooked(),
    onError: (e) => setError(e?.response?.data?.detail || e.message),
  })

  function bookSurgery(s) {
    const duration = s.estimated_minutes || 60   // sane default
    // procedure_kind keys match the backend can_fit() rules
    const cls = s.procedure_classification || 'minor'
    const kind = slot.facility === 'office' ? 'office' : cls
    book.mutate({ surgeryId: s.id, duration, kind })
  }

  return (
    <div className="fixed inset-0 z-[60] flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40" />
      <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-gray-200 px-4 py-3 flex items-center justify-between">
          <div>
            <div className="text-[13px] font-semibold">Book a surgery at {slot.time}</div>
            <div className="text-[10px] text-muted">
              {date} · {slot.facility} — {unscheduled.length} eligible surgery(ies)
            </div>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-800">
            <X size={16} />
          </button>
        </div>
        <div className="p-4 space-y-2">
          {unscheduled.length === 0 ? (
            <div className="text-[12px] text-muted italic">
              No unscheduled surgeries match {slot.facility}.
            </div>
          ) : (
            <ul className="divide-y divide-gray-100">
              {unscheduled.map(s => (
                <li key={s.id} className="py-2 flex items-baseline justify-between gap-2">
                  <div className="text-[12px]">
                    <div className="font-medium">{s.patient_name}</div>
                    <div className="text-[10px] text-gray-500">
                      chart {s.chart_number || '—'}
                      {s.estimated_minutes ? ` · ~${s.estimated_minutes}m` : ''}
                      {s.procedure_classification ? ` · ${s.procedure_classification}` : ''}
                      {s.preop_date ? ` · preop ${s.preop_date}` : ''}
                    </div>
                  </div>
                  <button className="btn-primary text-[11px] disabled:opacity-50"
                          disabled={book.isPending}
                          onClick={() => bookSurgery(s)}>
                    Book
                  </button>
                </li>
              ))}
            </ul>
          )}
          {error && (
            <div className="text-[12px] text-red-700 bg-red-50 border border-red-200 rounded p-2">
              {error}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}


export default function SurgeryCalendarPage() {
  const [params, setParams] = useSearchParams()
  const view = params.get('view') === 'week' ? 'week' : 'month'

  function setView(v) {
    const next = new URLSearchParams(params)
    next.set('view', v)
    setParams(next, { replace: true })
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <CalIcon size={20} /> Surgery Calendar
        </h1>
        <div className="inline-flex rounded border border-border-subtle overflow-hidden text-sm">
          <button className={`px-3 py-1 ${view === 'month' ? 'bg-plum-600 text-white' : 'bg-white text-gray-700 hover:bg-plum-50'}`}
                  onClick={() => setView('month')}>Month</button>
          <button className={`px-3 py-1 ${view === 'week' ? 'bg-plum-600 text-white' : 'bg-white text-gray-700 hover:bg-plum-50'}`}
                  onClick={() => setView('week')}>Week</button>
        </div>
      </div>
      {view === 'month' ? <MonthlyCalendar /> : <WeeklyCalendar />}
    </div>
  )
}


function SurgeryCard({ surgery, onClick }) {
  const fac = FACILITY_BADGE[surgery.facility] || { label: surgery.facility, tone: 'bg-gray-100 text-gray-700 border-gray-200' }
  const indicatorDot = INDICATOR_TONE[surgery.indicator] || INDICATOR_TONE.yellow
  const indicatorLabel = INDICATOR_LABEL[surgery.indicator] || 'open'
  const tooltip = [
    surgery.patient_name,
    surgery.procedure,
    surgery.scheduled_start_time && `Start ${surgery.scheduled_start_time}`,
    `Status: ${indicatorLabel}`,
    surgery.open_milestones?.length > 0
      ? `Pending: ${surgery.open_milestones.join(', ')}${surgery.critical_count ? ` (${surgery.critical_count} critical)` : ''}`
      : 'All pre-op milestones complete',
  ].filter(Boolean).join('\n')

  const short = shortName(surgery.patient_name)
  const procShort = (surgery.procedure || '').length > 28
    ? surgery.procedure.slice(0, 28) + '…'
    : (surgery.procedure || '')

  const cardClass = surgery.is_incomplete
    ? 'w-full text-left rounded border border-amber-300 bg-amber-50/50 hover:border-amber-500 hover:bg-amber-50 px-1.5 py-1 transition'
    : 'w-full text-left rounded border border-gray-200 bg-white hover:border-plum-300 hover:bg-plum-50 px-1.5 py-1 transition'

  return (
    <button type="button"
            onClick={onClick}
            title={surgery.is_incomplete
              ? `${tooltip}\n⚠ Incomplete — fill in chart #, DOB, procedure from EHR`
              : tooltip}
            className={cardClass}>
      <div className="flex items-center gap-1.5">
        <span className={`w-2 h-2 rounded-full shrink-0 ${indicatorDot}`} />
        {surgery.urgency === "urgent" && <span className="text-[10px] shrink-0" title="urgent">🚨</span>}
        {surgery.is_robotic && <span className="text-[10px] shrink-0" title="robotic">🤖</span>}
        {surgery.is_incomplete && <span className="text-[10px] shrink-0" title="incomplete — needs chart #, DOB, procedure">⚠</span>}
        <span className="text-[11px] font-semibold text-gray-900 truncate flex-1">{short}</span>
      </div>
      <div className="flex items-baseline gap-1 mt-0.5">
        <span className={`text-[9px] uppercase tracking-wide px-1 py-px rounded border ${fac.tone} shrink-0`}>
          {fac.label}
        </span>
        {surgery.scheduled_start_time && (
          <span className="text-[10px] font-mono text-gray-700 shrink-0">
            {surgery.scheduled_start_time}
          </span>
        )}
      </div>
      {procShort && (
        <div className="text-[10px] text-gray-600 truncate mt-0.5">{procShort}</div>
      )}
    </button>
  )
}


function Legend() {
  return (
    <div className="flex flex-wrap gap-3 text-[11px] text-gray-700">
      <span className="flex items-center gap-1">
        <span className="w-2.5 h-2.5 rounded-full bg-green-500" /> ready
      </span>
      <span className="flex items-center gap-1">
        <span className="w-2.5 h-2.5 rounded-full bg-amber-400" /> open tasks
      </span>
      <span className="flex items-center gap-1">
        <span className="w-2.5 h-2.5 rounded-full bg-red-600" /> critically behind
      </span>
      <span className="flex items-center gap-1 text-gray-500">🚨 urgent · 🤖 robotic · ⚠ incomplete</span>
    </div>
  )
}


function shortName(fullName) {
  if (!fullName) return ''
  if (fullName.includes(',')) {
    const [last, first] = fullName.split(',').map(s => s.trim())
    return `${last}, ${(first || '').charAt(0)}.`
  }
  const parts = fullName.trim().split(/\s+/)
  if (parts.length === 1) return parts[0]
  const last = parts[parts.length - 1]
  const first = parts[0]
  return `${last}, ${first.charAt(0)}.`
}


function parseIso(iso) {
  const [y, m, d] = iso.split('-').map(n => parseInt(n, 10))
  return new Date(y, m - 1, d)
}

function startOfMonthGrid(iso) {
  const [y, m] = iso.split('-').map(n => parseInt(n, 10))
  const first = new Date(y, m - 1, 1)
  const wd = (first.getDay() + 6) % 7  // 0=Mon, 6=Sun
  first.setDate(first.getDate() - wd)
  return isoDate(first)
}
function monthLabel(iso) {
  const [y, m] = iso.split('-').map(n => parseInt(n, 10))
  return new Date(y, m - 1, 1).toLocaleString('en-US', { month: 'long', year: 'numeric' })
}
function addMonths(iso, n) {
  const [y, m] = iso.split('-').map(x => parseInt(x, 10))
  const dt = new Date(y, m - 1 + n, 1)
  return isoDate(dt)
}
function inSameMonth(iso, anchorIso) {
  return iso.slice(0, 7) === anchorIso.slice(0, 7)
}
