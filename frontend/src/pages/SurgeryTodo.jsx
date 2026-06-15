import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import {
  ArrowLeft, AlertTriangle, CheckCircle2, Activity, CalendarCheck,
  CalendarX, CalendarClock, FileSignature, FileX, FileUp, FlaskConical,
  DollarSign, RefreshCw, UserX, AlertCircle, Circle,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import LoadingState from '../components/LoadingState'

// Icon + dot tone for each activity kind. Falls back to a neutral dot.
const KIND_META = {
  date_picked:           { icon: CalendarCheck,  tone: 'text-emerald-600' },
  rescheduled:           { icon: CalendarClock,  tone: 'text-amber-600' },
  cancelled:             { icon: CalendarX,      tone: 'text-red-600' },
  consent_signed:        { icon: FileSignature,  tone: 'text-emerald-600' },
  consent_declined:      { icon: FileX,          tone: 'text-red-600' },
  document_uploaded:     { icon: FileUp,         tone: 'text-plum-600' },
  labs_reported:         { icon: FlaskConical,   tone: 'text-plum-600' },
  payment_made:          { icon: DollarSign,     tone: 'text-emerald-600' },
  date_change_requested: { icon: RefreshCw,      tone: 'text-amber-600' },
  auto_unresponsive:     { icon: UserX,          tone: 'text-gray-500' },
  step_overdue:          { icon: AlertCircle,    tone: 'text-red-600' },
}


export default function SurgeryTodo() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [behindOnly, setBehindOnly] = useState(false)

  const { data: todos, isLoading: todosLoading } = useQuery({
    queryKey: ['surgery-todos', behindOnly],
    queryFn: () => api.get('/surgery/todos', {
      params: behindOnly ? { behind_only: true } : {},
    }).then(r => r.data),
    refetchInterval: 60_000,
  })

  const { data: activity, isLoading: activityLoading } = useQuery({
    queryKey: ['surgery-activity'],
    queryFn: () => api.get('/surgery/activity', { params: { limit: 100 } }).then(r => r.data),
    refetchInterval: 60_000,
  })

  const markRead = useMutation({
    mutationFn: (id) => api.post(`/surgery/activity/${id}/read`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery-activity'] })
      qc.invalidateQueries({ queryKey: ['surgery-activity-unread'] })
    },
  })

  const markAllRead = useMutation({
    mutationFn: () => api.post('/surgery/activity/read-all').then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery-activity'] })
      qc.invalidateQueries({ queryKey: ['surgery-activity-unread'] })
    },
  })

  function openActivity(row) {
    if (!row.read_at) markRead.mutate(row.id)
    navigate(`/surgery/${row.surgery_id}`)
  }

  const items = todos?.items || []
  const feed = activity || []

  return (
    <div>
      <div className="mb-4">
        <Link to="/surgery" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-1">
          <ArrowLeft size={12} /> Surgery dashboard
        </Link>
        <h1 className="font-serif font-semibold text-ink text-[22px] m-0">Scheduler To-Do</h1>
        <p className="text-muted text-[12px] mt-0.5">
          The next open step for each active surgery, plus a live feed of patient and system activity.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
        {/* ── Action Needed ────────────────────────────────── */}
        <section className="card !p-0 overflow-hidden">
          <header className="flex items-center justify-between px-4 py-3 border-b border-border-subtle">
            <div className="flex items-center gap-2">
              <h2 className="font-serif font-semibold text-ink text-[16px] m-0">Action Needed</h2>
              {todos && (
                <span className="text-[12px] text-muted">
                  {todos.open_count} open · <span className={todos.behind_count ? 'text-red-600 font-medium' : ''}>
                    {todos.behind_count} behind
                  </span>
                </span>
              )}
            </div>
            <label className="flex items-center gap-1.5 text-[12px] text-muted cursor-pointer select-none">
              <input
                type="checkbox"
                checked={behindOnly}
                onChange={e => setBehindOnly(e.target.checked)}
              />
              Behind only
            </label>
          </header>

          {todosLoading ? (
            <LoadingState />
          ) : items.length === 0 ? (
            <div className="flex items-center justify-center gap-2 text-sm text-muted py-10">
              <CheckCircle2 size={16} className="text-emerald-500" /> Nothing needs action.
            </div>
          ) : (
            <ul className="divide-y divide-border-subtle">
              {items.map(it => {
                const behind = it.state === 'behind'
                return (
                  <li
                    key={it.surgery_id}
                    className={`px-4 py-3 ${behind ? 'border-l-2 border-l-red-500 bg-red-50/40' : ''}`}
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <Link
                        to={`/surgery/${it.surgery_id}`}
                        className="text-plum-700 hover:underline text-sm font-medium truncate"
                      >
                        {it.patient_name}
                      </Link>
                      {behind && (
                        <span className="shrink-0 inline-flex items-center gap-1 text-[10px] bg-red-100 text-red-700 px-1.5 py-0.5 rounded font-semibold">
                          <AlertTriangle size={10} /> {it.days_behind}d behind
                        </span>
                      )}
                    </div>
                    <div className="text-[11px] text-gray-500 mt-0.5">
                      <span className="font-mono">{it.chart_number}</span>
                      {it.surgery_number != null && <> · #{it.surgery_number}</>}
                      {it.facility && <> · {it.facility}</>}
                    </div>
                    <div className="flex items-baseline justify-between gap-2 mt-1">
                      <span className="text-[13px] text-gray-800">{it.step_title}</span>
                      {it.expected_date && (
                        <span className={`text-[11px] shrink-0 ${behind ? 'text-red-600' : 'text-muted'}`}>
                          due {fmt.date(it.expected_date)}
                        </span>
                      )}
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        {/* ── Recent Activity ──────────────────────────────── */}
        <section className="card !p-0 overflow-hidden">
          <header className="flex items-center justify-between px-4 py-3 border-b border-border-subtle">
            <div className="flex items-center gap-2">
              <Activity size={16} className="text-plum-600" />
              <h2 className="font-serif font-semibold text-ink text-[16px] m-0">Recent Activity</h2>
            </div>
            <button
              className="text-[11px] px-2 py-1 rounded border border-border-subtle hover:bg-plum-50 disabled:opacity-50"
              onClick={() => markAllRead.mutate()}
              disabled={markAllRead.isPending || feed.every(r => r.read_at)}
            >
              Mark all read
            </button>
          </header>

          {activityLoading ? (
            <LoadingState />
          ) : feed.length === 0 ? (
            <div className="flex items-center justify-center text-sm text-muted py-10">
              No recent activity.
            </div>
          ) : (
            <ul className="divide-y divide-border-subtle max-h-[70vh] overflow-y-auto">
              {feed.map(row => {
                const meta = KIND_META[row.kind]
                const Icon = meta?.icon || Circle
                const unread = !row.read_at
                return (
                  <li
                    key={row.id}
                    onClick={() => openActivity(row)}
                    className={`px-4 py-3 flex items-start gap-3 cursor-pointer hover:bg-gray-50 ${
                      unread ? 'border-l-2 border-l-plum-500 bg-plum-50/40' : ''
                    }`}
                  >
                    <Icon size={16} className={`shrink-0 mt-0.5 ${meta?.tone || 'text-gray-400'}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="text-[13px] text-gray-800">{row.summary}</span>
                        <span className="shrink-0 inline-flex items-center gap-1.5">
                          {unread && <span className="w-1.5 h-1.5 rounded-full bg-plum-500" />}
                          <span className="text-[10px] text-muted whitespace-nowrap">
                            {fmt.dateTime(row.created_at)}
                          </span>
                        </span>
                      </div>
                      <div className="text-[11px] text-gray-500 mt-0.5">
                        {row.patient_name}
                        {row.chart_number && <> · <span className="font-mono">{row.chart_number}</span></>}
                      </div>
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </section>
      </div>
    </div>
  )
}
