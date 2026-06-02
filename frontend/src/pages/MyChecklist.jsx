import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle2, Circle, SkipForward, RotateCcw, Settings, Clock, AlertCircle,
  ThumbsUp, ThumbsDown, MessageSquareWarning, PackageCheck, CalendarClock,
  ClipboardList, X, Printer, Plus, Pencil, Trash2, ChevronDown, ChevronRight,
  Share2, BookOpen,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import api, { fmt } from '../utils/api'
import { useCurrentUser } from '../hooks/useCurrentUser'


const ROLE_LABELS = {
  ma:                'Medical Assistant',
  front_desk:        'Front Desk Receptionist',
  billing_coding:    'Billing — Coding',
  billing_payments:  'Billing — Payments',
  billing_denials:   'Billing — Denials',
  caribcall:         'CaribCall (Virtual Receptionist)',
  office_manager:    'Office Manager',
  provider:          'Provider',
}

const PRIORITY_BADGE = {
  critical: 'bg-red-100 text-red-700 border-red-200',
  high:     'bg-orange-100 text-orange-700 border-orange-200',
  medium:   'bg-blue-50 text-blue-700 border-blue-100',
  low:      'bg-gray-100 text-gray-600 border-gray-200',
}

const CATEGORY_BADGE = {
  clinical:      'bg-emerald-50 text-emerald-700',
  admin:         'bg-blue-50 text-blue-700',
  billing:       'bg-purple-50 text-purple-700',
  safety:        'bg-amber-50 text-amber-700',
  compliance:    'bg-rose-50 text-rose-700',
  communication: 'bg-indigo-50 text-indigo-700',
}


export default function MyChecklist() {
  const qc = useQueryClient()
  const [showSettings, setShowSettings] = useState(false)
  const [showResponsibilities, setShowResponsibilities] = useState(false)
  const { has } = useCurrentUser()
  const canCheckoutLarc = !!has?.('larc:checkout')
  const canScheduleSurgery = !!has?.('surgery:work')

  const { data, isLoading } = useQuery({
    queryKey: ['checklist-today'],
    queryFn: () => api.get('/checklist/my-today').then(r => r.data),
  })

  const tasks = data?.tasks || []
  const counts = data?.counts || {}
  const role = data?.user?.practice_role
  const total = tasks.length
  const done = counts.done || 0
  const skipped = counts.skipped || 0
  const remaining = (counts.pending || 0) + (counts.in_progress || 0)
  const pct = total > 0 ? Math.round(((done + skipped) / total) * 100) : 0
  const allAnswered = total > 0 && remaining === 0

  if (isLoading) return <div className="p-6 text-gray-400">Loading…</div>

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">My Checklist</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            {data?.user?.display_name || data?.user?.email?.split('@')[0]}
            {role && <> · <span className="font-medium">{ROLE_LABELS[role] || role}</span></>}
            {!role && <> · <span className="text-amber-600">No role assigned</span></>}
            <span className="text-gray-400 ml-2">· {data?.date}</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn-secondary text-sm flex items-center gap-1"
                  onClick={() => setShowResponsibilities(true)}>
            <ClipboardList size={14} /> My Job Responsibilities
          </button>
          <a className="btn-secondary text-sm flex items-center gap-1"
             href="https://training.waldorfwomenscare.com"
             target="_blank" rel="noopener noreferrer">
            <BookOpen size={14} /> Documentation &amp; Training
          </a>
          <button className="btn-secondary text-sm flex items-center gap-1"
                  onClick={() => setShowSettings(s => !s)}>
            <Settings size={14} /> {showSettings ? 'Close Settings' : 'Settings'}
          </button>
        </div>
      </div>

      {showSettings && <SettingsCard onClose={() => setShowSettings(false)} />}
      {showResponsibilities && (
        <ResponsibilitiesDrawer onClose={() => setShowResponsibilities(false)} />
      )}

      {/* Progress bar */}
      {total > 0 && (
        <div className="card">
          <div className="flex items-baseline justify-between mb-2">
            <div className="text-sm">
              <strong>{done}</strong> done · <strong>{skipped}</strong> skipped · <strong>{remaining}</strong> remaining of {total}
            </div>
            <div className="text-xs text-gray-500">{pct}% complete</div>
          </div>
          <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
            <div className="h-full bg-green-500 transition-all" style={{ width: `${pct}%` }}></div>
          </div>
        </div>
      )}

      {canScheduleSurgery && <SchedulerAlertsCard />}

      <PainPointOwnerQueueCard />
      <PainPointResponsesCard />

      <MyTasksCard />


      {canCheckoutLarc && <LarcCheckoutCard />}

      {!role && total === 0 && (
        <div className="card bg-amber-50 border-amber-200 text-sm text-amber-800">
          You don't have a practice role set yet — your administrator needs to assign one
          before your daily checklist will appear here.
        </div>
      )}

      {role && total === 0 && (
        <div className="card text-sm text-gray-500 italic">
          No tasks for today. Either you've finished everything (nice work) or nothing has been
          generated yet — your administrator may need to seed templates.
        </div>
      )}

      {/* Task list */}
      <ul className="space-y-2">
        {tasks.map(t => <TaskRow key={t.id} task={t} qc={qc} />)}
      </ul>

      {/* Pain points — always offered, prominent once everything else is answered */}
      <PainPointPanel highlight={allAnswered} />
    </div>
  )
}


function TaskRow({ task, qc }) {
  const [showSkip, setShowSkip] = useState(false)
  const [skipReason, setSkipReason] = useState('')
  const [showNoForm, setShowNoForm] = useState(false)
  const [followupCount, setFollowupCount] = useState('')
  const [followupText, setFollowupText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const isDone = task.status === 'done'
  const isSkipped = task.status === 'skipped'
  const isFinal = isDone || isSkipped
  const answeredYes = isDone && task.answer === 'yes'
  const answeredNo = isDone && task.answer === 'no'

  const followupKind = task.followup_kind || 'none'
  const promptLabel = task.question_text || task.title

  async function answer(value, extras = {}) {
    setBusy(true); setError(null)
    try {
      await api.post(`/checklist/instances/${task.id}/answer`, {
        answer: value,
        ...extras,
      })
      setShowNoForm(false)
      setFollowupCount(''); setFollowupText('')
      qc.invalidateQueries({ queryKey: ['checklist-today'] })
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally { setBusy(false) }
  }

  function handleNo() {
    if (followupKind === 'none') {
      answer('no')
    } else {
      setShowNoForm(true)
    }
  }

  async function submitNo() {
    if (followupKind === 'count') {
      const n = parseInt(followupCount, 10)
      if (Number.isNaN(n) || n < 0) {
        setError('Enter a number ≥ 0')
        return
      }
      await answer('no', { followup_count: n })
    } else if (followupKind === 'reason') {
      if (!followupText.trim()) {
        setError('Please write a brief reason')
        return
      }
      await answer('no', { followup_text: followupText.trim() })
    }
  }

  async function reopen() {
    setBusy(true); setError(null)
    try {
      await api.post(`/checklist/instances/${task.id}/reopen`)
      setShowNoForm(false); setShowSkip(false)
      qc.invalidateQueries({ queryKey: ['checklist-today'] })
    } finally { setBusy(false) }
  }

  async function submitSkip() {
    if (!skipReason.trim()) return
    setBusy(true); setError(null)
    try {
      await api.post(`/checklist/instances/${task.id}/skip`, { reason: skipReason })
      setShowSkip(false); setSkipReason('')
      qc.invalidateQueries({ queryKey: ['checklist-today'] })
    } finally { setBusy(false) }
  }

  const dueTime = task.due_at ? task.due_at.slice(11, 16) : null

  const cardTone = answeredYes
    ? 'bg-green-50/40'
    : answeredNo
      ? 'bg-amber-50/60 border-amber-200'
      : isSkipped
        ? 'bg-gray-50'
        : ''

  return (
    <li className={`card transition-colors ${cardTone}`}>
      <div className="flex items-start gap-3">
        <div className="mt-0.5 shrink-0">
          {answeredYes ? (
            <CheckCircle2 size={22} className="text-green-600" />
          ) : answeredNo ? (
            <AlertCircle size={22} className="text-amber-600" />
          ) : isSkipped ? (
            <SkipForward size={22} className="text-gray-400" />
          ) : (
            <Circle size={22} className="text-gray-300" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className={`font-medium ${isFinal ? 'text-gray-700' : 'text-gray-900'}`}>
              {promptLabel}
            </span>
            <span className={`text-[10px] px-1.5 py-0.5 rounded border ${PRIORITY_BADGE[task.priority]}`}>
              {task.priority}
            </span>
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${CATEGORY_BADGE[task.category]}`}>
              {task.category}
            </span>
            {dueTime && (
              <span className="text-xs text-gray-500 flex items-center gap-1">
                <Clock size={11} /> due {dueTime}
              </span>
            )}
          </div>
          {task.description && (
            <p className="text-xs text-gray-600 mt-1">{task.description}</p>
          )}

          {/* Recorded answer summary */}
          {answeredYes && (
            <div className="text-[11px] text-green-700 mt-1">
              ✓ Yes — {task.completed_by?.split('@')[0]} · {fmt.date(task.completed_at?.slice(0, 10))} {task.completed_at?.slice(11, 16)}
            </div>
          )}
          {answeredNo && (
            <div className="mt-1.5 text-xs text-amber-800 bg-amber-100/60 rounded px-2 py-1">
              <span className="font-semibold">No</span> · answered by {task.completed_by?.split('@')[0]}{' '}
              {task.completed_at && (<>· {fmt.date(task.completed_at?.slice(0, 10))} {task.completed_at?.slice(11, 16)}</>)}
              {task.followup_count != null && (
                <div className="mt-0.5">
                  <span className="text-amber-900/70">{task.followup_prompt || 'How many?'}</span>{' '}
                  <strong>{task.followup_count}</strong>
                </div>
              )}
              {task.followup_text && (
                <div className="mt-0.5">
                  <span className="text-amber-900/70">{task.followup_prompt || 'Reason:'}</span>{' '}
                  <em>{task.followup_text}</em>
                </div>
              )}
            </div>
          )}
          {isSkipped && (
            <div className="text-[11px] text-gray-500 mt-1">
              ⊘ Skipped by {task.completed_by?.split('@')[0]} — {task.skipped_reason}
            </div>
          )}
        </div>

        {/* Right-side actions */}
        {!isFinal && !showSkip && !showNoForm && (
          <div className="flex items-center gap-2 shrink-0">
            <button
              className="btn-primary text-xs flex items-center gap-1 px-3 py-1.5"
              onClick={() => answer('yes')}
              disabled={busy}
              title="Yes — task completed"
            >
              <ThumbsUp size={13} /> Yes
            </button>
            <button
              className="text-xs flex items-center gap-1 px-3 py-1.5 rounded border border-amber-300 bg-white hover:bg-amber-50 text-amber-700"
              onClick={handleNo}
              disabled={busy}
              title="No — record why / how many"
            >
              <ThumbsDown size={13} /> No
            </button>
            <button
              className="text-xs text-gray-400 hover:text-gray-700"
              onClick={() => setShowSkip(true)}
            >Skip</button>
          </div>
        )}
        {isFinal && (
          <button
            className="text-xs text-gray-400 hover:text-gray-700 shrink-0 flex items-center gap-1"
            onClick={reopen} disabled={busy}
          ><RotateCcw size={11} /> Reopen</button>
        )}
      </div>

      {/* No-answer follow-up */}
      {showNoForm && !isFinal && (
        <div className="mt-3 bg-amber-50/70 border border-amber-200 rounded p-3 space-y-2">
          <div className="text-xs font-medium text-amber-900">
            {task.followup_prompt || (followupKind === 'count' ? 'How many are left?' : 'Why hasn\'t it been completed?')}
          </div>
          {followupKind === 'count' ? (
            <input
              className="input text-sm w-32"
              type="number" min={0}
              placeholder="0"
              value={followupCount}
              onChange={e => setFollowupCount(e.target.value)}
              autoFocus
            />
          ) : (
            <textarea
              className="input text-sm w-full"
              rows={2}
              placeholder="Brief explanation"
              value={followupText}
              onChange={e => setFollowupText(e.target.value)}
              autoFocus
            />
          )}
          {error && <div className="text-xs text-red-600">{error}</div>}
          <div className="flex gap-2 justify-end">
            <button className="btn-secondary text-xs"
              onClick={() => { setShowNoForm(false); setFollowupCount(''); setFollowupText(''); setError(null) }}>
              Cancel
            </button>
            <button className="btn-primary text-xs"
              onClick={submitNo} disabled={busy}>
              {busy ? 'Saving…' : 'Submit'}
            </button>
          </div>
        </div>
      )}

      {showSkip && !isFinal && (
        <div className="mt-2 flex gap-2 items-center">
          <input
            className="input text-xs flex-1"
            placeholder="Reason for skipping (e.g. patient cancelled, equipment unavailable)"
            value={skipReason}
            onChange={e => setSkipReason(e.target.value)}
            autoFocus
          />
          <button className="btn-secondary text-xs" onClick={() => { setShowSkip(false); setSkipReason('') }}>Cancel</button>
          <button className="btn-primary text-xs" onClick={submitSkip} disabled={!skipReason.trim() || busy}>Skip Task</button>
        </div>
      )}
    </li>
  )
}


function PainPointPanel({ highlight }) {
  const qc = useQueryClient()
  const [hasOne, setHasOne] = useState(null)  // null/'yes'/'no'
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [submitted, setSubmitted] = useState(false)

  const { data } = useQuery({
    queryKey: ['my-pain-points'],
    queryFn: () => api.get('/checklist/pain-points/mine').then(r => r.data),
  })
  const todays = (data?.pain_points || []).filter(p => p.occurred_on === new Date().toISOString().slice(0, 10))

  async function submit() {
    if (!body.trim()) {
      setError('Tell us briefly what came up')
      return
    }
    setBusy(true); setError(null)
    try {
      await api.post('/checklist/pain-points', { body: body.trim() })
      setSubmitted(true)
      setBody('')
      qc.invalidateQueries({ queryKey: ['my-pain-points'] })
      setTimeout(() => { setSubmitted(false); setHasOne(null) }, 2500)
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally { setBusy(false) }
  }

  return (
    <div className={`card mt-4 ${highlight ? 'border-primary-200 bg-primary-50/40' : ''}`}>
      <div className="flex items-center gap-2 mb-2">
        <MessageSquareWarning size={16} className="text-primary-700" />
        <h3 className="text-sm font-semibold text-gray-800">Pain points</h3>
      </div>

      {todays.length > 0 && (
        <div className="text-xs text-gray-600 mb-2">
          Logged today: {todays.length} · {todays.map(p => p.status).join(', ')}
        </div>
      )}

      {hasOne === null && (
        <div>
          <div className="text-sm text-gray-700 mb-2">
            Are there any pain points to flag for your manager today?
          </div>
          <div className="flex gap-2">
            <button className="btn-primary text-xs flex items-center gap-1" onClick={() => setHasOne('yes')}>
              <ThumbsDown size={13} /> Yes — there's something
            </button>
            <button className="btn-secondary text-xs flex items-center gap-1" onClick={() => setHasOne('no')}>
              <ThumbsUp size={13} /> No — all good
            </button>
          </div>
        </div>
      )}

      {hasOne === 'no' && !submitted && (
        <div className="text-xs text-gray-500 italic">Thanks — nothing logged.</div>
      )}

      {hasOne === 'yes' && !submitted && (
        <div className="space-y-2">
          <textarea
            className="input text-sm w-full"
            rows={3}
            placeholder="What came up? (e.g. 'Front-desk printer down all morning — couldn't print encounter forms')"
            value={body}
            onChange={e => setBody(e.target.value)}
            autoFocus
          />
          {error && <div className="text-xs text-red-600">{error}</div>}
          <div className="flex gap-2 justify-end">
            <button className="btn-secondary text-xs" onClick={() => { setHasOne(null); setBody(''); setError(null) }}>
              Cancel
            </button>
            <button className="btn-primary text-xs" onClick={submit} disabled={busy || !body.trim()}>
              {busy ? 'Submitting…' : 'Submit pain point'}
            </button>
          </div>
        </div>
      )}

      {submitted && (
        <div className="text-xs text-green-700">✓ Pain point logged — your manager will see it.</div>
      )}
    </div>
  )
}


function SettingsCard({ onClose }) {
  const qc = useQueryClient()
  const { data: me } = useQuery({
    queryKey: ['checklist-me'],
    queryFn: () => api.get('/checklist/me').then(r => r.data),
  })

  const [phone, setPhone] = useState(me?.phone_number || '')
  const [slack, setSlack] = useState(me?.slack_user_id || '')
  const [notifyEmail, setNotifyEmail] = useState(me?.notify_email !== false)
  const [notifySlack, setNotifySlack] = useState(me?.notify_slack !== false)
  const [notifySms, setNotifySms] = useState(me?.notify_sms === true)
  const [busy, setBusy] = useState(false)
  const [savedMsg, setSavedMsg] = useState(null)

  // Sync state when `me` arrives
  useEffect(() => {
    if (me) {
      setPhone(me.phone_number || '')
      setSlack(me.slack_user_id || '')
      setNotifyEmail(me.notify_email !== false)
      setNotifySlack(me.notify_slack !== false)
      setNotifySms(me.notify_sms === true)
    }
  }, [me])

  async function save() {
    setBusy(true); setSavedMsg(null)
    try {
      await api.patch('/checklist/me', {
        phone_number: phone || null,
        slack_user_id: slack || null,
        notify_email: notifyEmail,
        notify_slack: notifySlack,
        notify_sms: notifySms,
      })
      setSavedMsg('Saved.')
      qc.invalidateQueries()
      setTimeout(() => setSavedMsg(null), 2000)
    } catch (e) {
      setSavedMsg(e?.response?.data?.detail || e.message)
    } finally { setBusy(false) }
  }

  const roleLabel = me?.practice_role
    ? (ROLE_LABELS[me.practice_role] || me.practice_role)
    : null

  return (
    <div className="card">
      <h2 className="text-sm font-semibold text-gray-700 mb-3">Settings</h2>
      <div className="mb-3 p-2.5 bg-gray-50 border border-gray-100 rounded text-sm">
        <div className="text-[10px] uppercase text-gray-500 tracking-wide mb-1">Practice Role</div>
        <div className="flex items-baseline gap-2">
          <span className={`font-medium ${roleLabel ? 'text-gray-900' : 'text-amber-700'}`}>
            {roleLabel || 'Not assigned'}
          </span>
          <span className="text-[11px] text-gray-500">— ask your administrator to change this</span>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
        <Labeled label="Slack User ID (e.g. U01234567)">
          <input className="input font-mono text-xs" value={slack} onChange={e => setSlack(e.target.value)} />
        </Labeled>
        <Labeled label="Phone (for SMS reminders)">
          <input className="input font-mono text-xs" value={phone} onChange={e => setPhone(e.target.value)} placeholder="+13015551234" />
        </Labeled>
        <div className="flex flex-col gap-1.5 justify-center text-xs">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={notifyEmail} onChange={e => setNotifyEmail(e.target.checked)} />
            Email reminders
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={notifySlack} onChange={e => setNotifySlack(e.target.checked)} />
            Slack reminders
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={notifySms}
                   onChange={e => setNotifySms(e.target.checked)}
                   disabled={!phone} />
            <span className={!phone ? 'text-gray-400' : ''}>
              SMS reminders {!phone && <span className="text-[10px]">(add phone first)</span>}
            </span>
          </label>
        </div>
      </div>
      {savedMsg && <div className="text-xs text-green-700 mt-2">{savedMsg}</div>}
      <div className="flex gap-2 justify-end mt-3">
        <button className="btn-secondary text-sm" onClick={onClose}>Close</button>
        <button className="btn-primary text-sm" onClick={save} disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
      </div>
    </div>
  )
}


function SchedulerAlertsCard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['surgery-scheduler-alerts'],
    queryFn: () => api.get('/surgery/scheduler-alerts').then(r => r.data),
    staleTime: 60_000,
  })

  if (isLoading || error) return null
  const rows = data?.office_underbooked || []
  if (rows.length === 0) return null

  const threshold = data?.threshold || 6

  return (
    <div className="card border-amber-200 bg-amber-50/40">
      <div className="flex items-center gap-2 mb-2">
        <CalendarClock size={18} className="text-amber-700" />
        <div>
          <div className="text-sm font-semibold text-gray-800">
            Office procedure day under-booked
          </div>
          <div className="text-xs text-gray-600">
            {rows.length === 1
              ? '1 upcoming office day has fewer than '
              : `${rows.length} upcoming office days have fewer than `}
            <strong>{threshold}</strong> procedures booked — open Dr. Cooke's schedule
            to take clinic patients.
          </div>
        </div>
      </div>
      <ul className="space-y-2">
        {rows.map(r => (
          <li key={r.block_day_id}
              className="bg-white border border-amber-200 rounded px-3 py-2 flex items-center justify-between text-xs">
            <div>
              <span className="font-medium text-gray-900">
                {r.weekday}, {fmt.date(r.block_date)}
              </span>
              <span className="text-gray-600 ml-2">
                in {r.days_out} day{r.days_out === 1 ? '' : 's'} ·{' '}
                <strong>{r.booked}/{r.threshold}</strong> booked
                {' · '}{r.open_slots} slot{r.open_slots === 1 ? '' : 's'} to open
              </span>
            </div>
            <Link to="/surgery/block-schedule"
                  className="text-amber-800 hover:underline font-medium">
              Open block schedule →
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}


function LarcCheckoutCard() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const { data, isLoading, error } = useQuery({
    queryKey: ['larc-ready-to-checkout'],
    queryFn: () => api.get('/larc/checkouts/ready').then(r => r.data),
  })

  const rows = data || []
  const count = rows.length

  return (
    <div className="card border-primary-100 bg-primary-50/30">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <PackageCheck size={18} className="text-primary-700" />
          <div>
            <div className="text-sm font-semibold text-gray-800">LARC checkout</div>
            <div className="text-xs text-gray-600">
              {isLoading
                ? 'Loading…'
                : error
                  ? <span className="text-red-600">Couldn't load — {error?.response?.data?.detail || error.message}</span>
                  : count === 0
                    ? 'No devices waiting to be checked out.'
                    : `${count} ${count === 1 ? 'device' : 'devices'} ready to check out.`}
            </div>
          </div>
        </div>
        <button
          className="btn-primary text-xs"
          onClick={() => setOpen(o => !o)}
          disabled={count === 0}
        >
          {open ? 'Close' : 'Check out a device'}
        </button>
      </div>

      {open && (
        <div className="mt-3 space-y-2">
          {rows.map(r => (
            <LarcCheckoutRow key={r.assignment_id} row={r} qc={qc} />
          ))}
        </div>
      )}
    </div>
  )
}


function LarcCheckoutRow({ row, qc }) {
  const [deviceId, setDeviceId] = useState('')
  const [givenTo, setGivenTo] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [done, setDone] = useState(false)

  async function submit() {
    if (!deviceId.trim()) {
      setErr('Enter the device ID from the label')
      return
    }
    setBusy(true); setErr(null)
    try {
      await api.post(`/larc/assignments/${row.assignment_id}/checkout-direct`, {
        device_our_id: deviceId.trim(),
        given_to: givenTo.trim() || null,
      })
      setDone(true)
      qc.invalidateQueries({ queryKey: ['larc-ready-to-checkout'] })
    } catch (e) {
      setErr(e?.response?.data?.detail || e.message)
    } finally { setBusy(false) }
  }

  if (done) {
    return (
      <div className="bg-green-50 border border-green-200 rounded p-2 text-xs text-green-800">
        ✓ Checked out {row.device_type_name} for {row.patient_name}.
      </div>
    )
  }

  return (
    <div className="bg-white border border-gray-200 rounded p-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-gray-900 truncate">{row.patient_name}</div>
          <div className="text-xs text-gray-600">
            {row.device_type_name || 'Device'}
            {row.appt_date && <> · appt {fmt.date(row.appt_date)}</>}
            {row.chart_number && <> · chart {row.chart_number}</>}
          </div>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input
          className="input text-xs font-mono w-40"
          placeholder="Device ID from label"
          value={deviceId}
          onChange={e => setDeviceId(e.target.value)}
          autoComplete="off"
        />
        <input
          className="input text-xs w-48"
          placeholder="Given to (optional)"
          value={givenTo}
          onChange={e => setGivenTo(e.target.value)}
        />
        <button
          className="btn-primary text-xs"
          onClick={submit}
          disabled={busy || !deviceId.trim()}
        >
          {busy ? 'Checking out…' : 'Check out'}
        </button>
      </div>
      {err && <div className="text-xs text-red-600 mt-1">{err}</div>}
    </div>
  )
}


// Owner queue: only renders for the practice-wide pain-point owner.
// Shows every non-completed pain point with a textarea to respond.
function PainPointOwnerQueueCard() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['pain-point-owner-queue'],
    queryFn: () => api.get('/checklist/pain-points/owner-queue').then(r => r.data),
    staleTime: 15_000,
  })

  if (!data?.is_owner) return null
  const rows = data?.pain_points || []
  if (rows.length === 0) return null

  return (
    <div className="card border-amber-300 bg-amber-50/40">
      <div className="flex items-center gap-2 mb-2">
        <MessageSquareWarning size={18} className="text-amber-700" />
        <div>
          <div className="text-sm font-semibold text-gray-800">
            Pain points — practice queue
          </div>
          <div className="text-xs text-gray-600">
            {rows.length} pain point{rows.length === 1 ? '' : 's'} awaiting response or acknowledgement.
          </div>
        </div>
      </div>
      <ul className="space-y-2">
        {rows.map(pp => <PainPointOwnerRow key={pp.id} pp={pp} qc={qc} />)}
      </ul>
    </div>
  )
}


function PainPointOwnerRow({ pp, qc }) {
  const [draft, setDraft] = useState(pp.response || '')
  const [editing, setEditing] = useState(!pp.response)
  const respond = useMutation({
    mutationFn: () => api.post(`/checklist/pain-points/${pp.id}/respond`,
                                 { response: draft.trim() }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pain-point-owner-queue'] })
      setEditing(false)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Respond failed'),
  })
  const STATUS_TONE = {
    new:         'bg-red-100 text-red-800',
    in_progress: 'bg-amber-100 text-amber-800',
    completed:   'bg-green-100 text-green-800',
  }
  return (
    <li className="bg-white border border-amber-200 rounded px-3 py-2 text-xs">
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <span>
          <strong className="text-gray-900">{pp.user_email?.split('@')[0]}</strong>
          <span className="text-gray-500"> · {fmt.date(pp.occurred_on)}</span>
        </span>
        <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${STATUS_TONE[pp.status] || ''}`}>
          {pp.status.replace('_', ' ')}
        </span>
      </div>
      <div className="text-gray-800 whitespace-pre-wrap mb-2">{pp.body}</div>

      {pp.response && !editing && (
        <div className="bg-plum-50/50 border-l-2 border-plum-300 pl-2 py-1 mb-2">
          <div className="text-[10px] text-plum-700 font-semibold mb-0.5">
            Your response{pp.reviewed_at && ` · ${fmt.date(pp.reviewed_at.slice(0, 10))}`}
          </div>
          <div className="text-gray-800 whitespace-pre-wrap">{pp.response}</div>
          <button type="button"
                   className="text-[10px] text-plum-700 hover:underline mt-1"
                   onClick={() => setEditing(true)}>
            Edit response
          </button>
        </div>
      )}

      {editing && (
        <div className="space-y-1">
          <textarea className="input text-[12px] w-full" rows={2}
                    placeholder="Your comment / next steps…"
                    value={draft}
                    onChange={e => setDraft(e.target.value)} />
          <div className="flex items-center gap-2 justify-end">
            {pp.response && (
              <button type="button"
                       className="text-[10px] text-gray-500 hover:text-ink"
                       onClick={() => { setDraft(pp.response || ''); setEditing(false) }}>
                Cancel
              </button>
            )}
            <button type="button"
                     className="btn-primary text-[11px]"
                     disabled={!draft.trim() || respond.isPending}
                     onClick={() => respond.mutate()}>
              {respond.isPending ? 'Saving…'
                : pp.response ? 'Update response · notify submitter'
                              : 'Send response · mark in progress'}
            </button>
          </div>
        </div>
      )}

      {pp.status === 'in_progress' && !editing && (
        <div className="text-[10px] text-amber-700 mt-1">
          Waiting for {pp.user_email?.split('@')[0]} to acknowledge.
        </div>
      )}
    </li>
  )
}


// Submitter side: any of my pain points with status='in_progress' that I
// haven't acknowledged yet.
function PainPointResponsesCard() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['my-pain-points'],
    queryFn: () => api.get('/checklist/pain-points/mine').then(r => r.data),
    staleTime: 15_000,
  })
  const ack = useMutation({
    mutationFn: (id) => api.post(`/checklist/pain-points/${id}/acknowledge-response`)
                          .then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['my-pain-points'] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Acknowledge failed'),
  })

  const rows = (data?.pain_points || [])
                 .filter(p => p.status === 'in_progress' && p.response)
  if (rows.length === 0) return null

  return (
    <div className="card border-plum-200 bg-plum-50/40">
      <div className="flex items-center gap-2 mb-2">
        <MessageSquareWarning size={18} className="text-plum-700" />
        <div>
          <div className="text-sm font-semibold text-gray-800">
            Responses to your pain points
          </div>
          <div className="text-xs text-gray-600">
            {rows.length} response{rows.length === 1 ? '' : 's'} awaiting your acknowledgement.
          </div>
        </div>
      </div>
      <ul className="space-y-2">
        {rows.map(pp => (
          <li key={pp.id} className="bg-white border border-plum-200 rounded px-3 py-2 text-xs space-y-1.5">
            <div className="text-gray-500">
              Your pain point · {fmt.date(pp.occurred_on)}
            </div>
            <div className="text-gray-800 whitespace-pre-wrap">{pp.body}</div>
            <div className="bg-plum-50/70 border-l-2 border-plum-400 pl-2 py-1">
              <div className="text-[10px] text-plum-700 font-semibold">
                Response from {pp.reviewed_by?.split('@')[0] || 'owner'}
                {pp.reviewed_at && ` · ${fmt.date(pp.reviewed_at.slice(0, 10))}`}
              </div>
              <div className="text-gray-800 whitespace-pre-wrap">{pp.response}</div>
            </div>
            <div className="flex justify-end">
              <button type="button"
                       className="btn-primary text-[11px]"
                       disabled={ack.isPending}
                       onClick={() => ack.mutate(pp.id)}>
                ✓ Acknowledge response
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}


// ─── My Tasks (personal task list) ────────────────────────────────

const PRIORITY_TONES = {
  high:   'bg-red-100 text-red-800 border-red-200',
  medium: 'bg-amber-100 text-amber-800 border-amber-200',
  low:    'bg-gray-100 text-gray-700 border-gray-200',
}
const STATUS_TONES = {
  new:         'bg-gray-100 text-gray-700',
  in_progress: 'bg-blue-100 text-blue-800',
  closed:      'bg-green-100 text-green-800',
}
const STATUS_LABEL = {
  new:         'New',
  in_progress: 'In Progress',
  closed:      'Closed',
}


function MyTasksCard() {
  const qc = useQueryClient()
  const [includeClosed, setIncludeClosed] = useState(false)
  const [editing, setEditing] = useState(null)   // task obj or 'new'
  const [parentForSub, setParentForSub] = useState(null)  // parent task obj

  const { data, isLoading } = useQuery({
    queryKey: ['my-personal-tasks', includeClosed],
    queryFn: () => api.get('/personal-tasks',
                            { params: { include_closed: includeClosed } })
                      .then(r => r.data),
  })
  const tasks = data?.tasks || []

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <ClipboardList size={16} className="text-plum-700" />
          <h3 className="text-sm font-semibold text-gray-800">My Tasks</h3>
          <span className="text-[11px] text-gray-500">({tasks.length})</span>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-[11px] text-gray-600">
            <input type="checkbox" checked={includeClosed}
                   onChange={e => setIncludeClosed(e.target.checked)} />
            Show closed
          </label>
          <button className="btn-primary text-xs flex items-center gap-1"
                  onClick={() => setEditing('new')}>
            <Plus size={11}/> New task
          </button>
        </div>
      </div>

      {isLoading && <div className="text-xs text-gray-400">Loading…</div>}
      {!isLoading && tasks.length === 0 && (
        <div className="text-xs text-gray-500 italic">
          No tasks yet — click <strong>+ New task</strong> to add one.
        </div>
      )}

      <ul className="space-y-2">
        {tasks.map(t => (
          <TaskRow2 key={t.id} task={t} qc={qc}
                    onEdit={() => setEditing(t)}
                    onAddSubtask={() => setParentForSub(t)} />
        ))}
      </ul>

      {editing && (
        <TaskDrawer task={editing === 'new' ? null : editing}
                     onClose={() => setEditing(null)} qc={qc} />
      )}
      {parentForSub && (
        <TaskDrawer task={null} parent={parentForSub}
                     onClose={() => setParentForSub(null)} qc={qc} />
      )}
    </div>
  )
}


function TaskRow2({ task, qc, onEdit, onAddSubtask }) {
  const me = (useCurrentUser().email || '').toLowerCase()
  const isOwner = task.owner_email === me
  const isAssignee = (task.assignees || []).includes(me)
  const isShared = (task.shared_with || []).includes(me)
  const canMutate = isOwner || isAssignee || isShared
  const [expanded, setExpanded] = useState(true)

  const patch = useMutation({
    mutationFn: (body) => api.patch(`/personal-tasks/${task.id}`, body)
                            .then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['my-personal-tasks'] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Update failed'),
  })
  const del = useMutation({
    mutationFn: () => api.delete(`/personal-tasks/${task.id}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['my-personal-tasks'] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Delete failed'),
  })

  function nextStatus(s) {
    if (s === 'new') return 'in_progress'
    if (s === 'in_progress') return 'closed'
    return 'new'
  }

  const subtotal = task.subtask_total || 0
  const sclosed  = task.subtask_closed || 0

  return (
    <li className={`border rounded p-2 ${task.status === 'closed' ? 'bg-gray-50 opacity-75' : 'bg-white'}`}>
      <div className="flex items-start gap-2">
        <button type="button"
                onClick={() => canMutate && patch.mutate({ status: nextStatus(task.status) })}
                disabled={!canMutate || patch.isPending}
                className="mt-0.5 shrink-0"
                title={canMutate ? `Click to advance status (current: ${task.status.replace('_',' ')})` : task.status}>
          {task.status === 'closed'
            ? <CheckCircle2 size={18} className="text-green-600" />
            : task.status === 'in_progress'
              ? <Clock size={18} className="text-blue-600" />
              : <Circle size={18} className="text-gray-300" />}
        </button>

        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className={`text-[13px] font-medium ${task.status === 'closed' ? 'line-through text-gray-500' : 'text-gray-900'}`}>
              {task.title}
            </span>
            <span className={`text-[9px] uppercase tracking-wide px-1 rounded border ${PRIORITY_TONES[task.priority] || ''}`}>
              {task.priority}
            </span>
            <span className={`text-[9px] uppercase tracking-wide px-1 rounded ${STATUS_TONES[task.status] || ''}`}>
              {STATUS_LABEL[task.status]}
            </span>
            {task.due_date && (
              <span className="text-[10px] text-gray-500 flex items-center gap-0.5">
                <CalendarClock size={10}/> due {fmt.date(task.due_date)}
              </span>
            )}
            {(task.assignees || []).filter(a => a !== me).length > 0 && (
              <span className="text-[10px] text-blue-700"
                    title={`Assigned to ${(task.assignees || []).join(', ')}`}>
                → {(task.assignees || [])
                    .map(a => a.split('@')[0])
                    .join(', ')}
              </span>
            )}
            {task.shared_with?.length > 0 && (
              <span className="text-[10px] text-gray-500 flex items-center gap-0.5"
                    title={`Shared with: ${task.shared_with.join(', ')}`}>
                <Share2 size={10}/> {task.shared_with.length}
              </span>
            )}
          </div>
          {task.description && (
            <div className="text-[11px] text-gray-600 mt-0.5 whitespace-pre-wrap">
              {task.description}
            </div>
          )}

          {/* Subtask progress */}
          {subtotal > 0 && (
            <div className="mt-1.5">
              <div className="flex items-center gap-2 text-[10px] text-gray-600">
                <button type="button" onClick={() => setExpanded(e => !e)}
                        className="flex items-center gap-0.5 hover:text-plum-700">
                  {expanded ? <ChevronDown size={11}/> : <ChevronRight size={11}/>}
                  Subtasks {sclosed}/{subtotal}
                </button>
                <span className="font-medium">
                  {Math.round((sclosed / subtotal) * 100)}%
                </span>
                <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                  <div className="h-full bg-green-500 transition-all"
                       style={{ width: `${(sclosed / subtotal) * 100}%` }} />
                </div>
              </div>
              {expanded && (
                <ul className="mt-1.5 space-y-1 pl-3 border-l-2 border-gray-100">
                  {task.subtasks.map(st => (
                    <SubtaskRow key={st.id} subtask={st} qc={qc} />
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-1 shrink-0">
          {task.parent_id === null && canMutate && (
            <button type="button" className="text-[10px] text-gray-500 hover:text-plum-700"
                    onClick={onAddSubtask} title="Add subtask">
              <Plus size={12}/>
            </button>
          )}
          {/* Edit visible to owner, assignee, and shared users (they can
              edit content; the backend prevents sharing/ownership changes
              from non-owners). */}
          <button type="button" className="text-[10px] text-gray-500 hover:text-plum-700"
                  onClick={onEdit} title="Edit">
            <Pencil size={12}/>
          </button>
          {isOwner && (
            <button type="button" className="text-[10px] text-gray-500 hover:text-red-700"
                    onClick={() => {
                      if (confirm(`Delete "${task.title}"${task.subtask_total ? ' and its ' + task.subtask_total + ' subtask(s)' : ''}?`))
                        del.mutate()
                    }}
                    title="Delete">
              <Trash2 size={12}/>
            </button>
          )}
        </div>
      </div>
    </li>
  )
}


function SubtaskRow({ subtask, qc }) {
  const me = (useCurrentUser().email || '').toLowerCase()
  const isOwner    = subtask.owner_email === me
  const isAssignee = (subtask.assignees || []).includes(me)
  const isShared   = (subtask.shared_with || []).includes(me)
  const canMutate  = isOwner || isAssignee || isShared
  const canEdit    = canMutate   // owner / assignee / shared can all edit content
  const [editing, setEditing] = useState(false)

  const patch = useMutation({
    mutationFn: (body) => api.patch(`/personal-tasks/${subtask.id}`, body)
                            .then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['my-personal-tasks'] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Update failed'),
  })
  const del = useMutation({
    mutationFn: () => api.delete(`/personal-tasks/${subtask.id}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['my-personal-tasks'] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Delete failed'),
  })

  function toggle() {
    if (!canMutate) return
    patch.mutate({ status: subtask.status === 'closed' ? 'new' : 'closed' })
  }

  return (
    <>
      <li className="flex items-baseline gap-2 text-[11px] group">
        <button type="button" onClick={toggle} disabled={!canMutate || patch.isPending}>
          {subtask.status === 'closed'
            ? <CheckCircle2 size={13} className="text-green-600"/>
            : <Circle size={13} className="text-gray-300"/>}
        </button>
        <span className={`flex-1 ${subtask.status === 'closed' ? 'line-through text-gray-500' : 'text-gray-800'}`}>
          {subtask.title}
        </span>
        {(subtask.assignees || []).filter(a => a !== me).length > 0 && (
          <span className="text-[9px] text-blue-700"
                title={`Assigned to ${(subtask.assignees || []).join(', ')}`}>
            → {(subtask.assignees || [])
                .map(a => a.split('@')[0])
                .join(', ')}
          </span>
        )}
        {(canEdit || isOwner) && (
          <span className="opacity-40 group-hover:opacity-100 transition-opacity flex items-center gap-1">
            {canEdit && (
              <button type="button"
                       className="text-gray-500 hover:text-plum-700"
                       onClick={() => setEditing(true)}
                       title="Edit subtask">
                <Pencil size={10}/>
              </button>
            )}
            {isOwner && (
              <button type="button"
                       className="text-gray-500 hover:text-red-700"
                       onClick={() => {
                         if (confirm(`Delete subtask "${subtask.title}"?`))
                           del.mutate()
                       }}
                       title="Delete subtask">
                <Trash2 size={10}/>
              </button>
            )}
          </span>
        )}
      </li>
      {editing && (
        <TaskDrawer task={subtask}
                     onClose={() => setEditing(false)} qc={qc} />
      )}
    </>
  )
}


function TaskDrawer({ task, parent, onClose, qc }) {
  const isNew = !task
  const isSubtask = isNew && !!parent
  const me = (useCurrentUser().email || '').toLowerCase()
  // Owner has full control. Assignee + shared can edit content but not
  // change ownership fields. Both can collaborate on title/desc/etc.
  const isOwner = isNew || task.owner_email === me

  const [title, setTitle]               = useState(task?.title || '')
  const [description, setDescription]   = useState(task?.description || '')
  const [priority, setPriority]         = useState(task?.priority || 'medium')
  const [status, setStatus]             = useState(task?.status   || 'new')
  const [dueDate, setDueDate]           = useState(task?.due_date || '')
  const [assignees, setAssignees]       = useState(task?.assignees || [])
  const [sharedWith, setSharedWith]     = useState(task?.shared_with || [])

  const { data: usersData } = useQuery({
    queryKey: ['users-for-task-picker'],
    queryFn: () => api.get('/billing/documents/workforce/assignable').then(r => r.data),
    staleTime: 300_000,
  })
  const userOptions = (usersData || []).filter(u => u.email !== me)

  const save = useMutation({
    mutationFn: () => {
      const body = {
        title, description, priority, due_date: dueDate || null,
      }
      if (!isNew) body.status = status
      // Only owners may send ownership / sharing fields. Backend will
      // reject these from non-owners, so we omit them entirely otherwise.
      if (isOwner) {
        body.assignees = assignees
        body.shared_with = sharedWith
      }
      if (isNew) {
        if (parent) body.parent_id = parent.id
        return api.post('/personal-tasks', body).then(r => r.data)
      }
      return api.patch(`/personal-tasks/${task.id}`, body).then(r => r.data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['my-personal-tasks'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-4 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[15px]">
            {isSubtask ? `New subtask under "${parent.title}"` :
             isNew ? 'New task' : 'Edit task'}
          </h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={16}/></button>
        </div>
        <div className="p-4 space-y-3 text-sm">
          <div>
            <div className="text-[10px] uppercase text-gray-500 mb-1">Title *</div>
            <input className="input text-sm w-full" value={title}
                   onChange={e => setTitle(e.target.value)} autoFocus />
          </div>
          <div>
            <div className="text-[10px] uppercase text-gray-500 mb-1">Description</div>
            <textarea className="input text-[12px] w-full" rows={3}
                      value={description}
                      onChange={e => setDescription(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <div className="text-[10px] uppercase text-gray-500 mb-1">Priority</div>
              <select className="input text-sm w-full" value={priority}
                      onChange={e => setPriority(e.target.value)}>
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
              </select>
            </div>
            <div>
              <div className="text-[10px] uppercase text-gray-500 mb-1">Due date</div>
              <input type="date" className="input text-sm w-full"
                     value={dueDate}
                     onChange={e => setDueDate(e.target.value)} />
            </div>
          </div>
          {!isNew && (
            <div>
              <div className="text-[10px] uppercase text-gray-500 mb-1">Status</div>
              <select className="input text-sm w-full" value={status}
                      onChange={e => setStatus(e.target.value)}>
                <option value="new">New</option>
                <option value="in_progress">In Progress</option>
                <option value="closed">Closed</option>
              </select>
            </div>
          )}
          <div>
            <div className="text-[10px] uppercase text-gray-500 mb-1">
              Assign to (one or more users)
            </div>
            <SharedWithPicker
              users={userOptions}
              selected={assignees}
              onChange={setAssignees}
              disabled={!isOwner} />
            {!isOwner && (
              <div className="text-[9px] text-gray-400 mt-1">
                Only the task owner can change the assignees.
              </div>
            )}
          </div>
          <div>
            <div className="text-[10px] uppercase text-gray-500 mb-1">
              Share with (collaborators — can edit, can't reshare or delete)
            </div>
            <SharedWithPicker
              users={userOptions}
              selected={sharedWith}
              onChange={setSharedWith}
              disabled={!isOwner} />
            {!isOwner && (
              <div className="text-[9px] text-gray-400 mt-1">
                Only the task owner can change the sharing list.
              </div>
            )}
          </div>
          <div className="flex gap-2 justify-end pt-2">
            <button className="btn-secondary text-xs" onClick={onClose}>Cancel</button>
            <button className="btn-primary text-xs" onClick={() => save.mutate()}
                    disabled={!title.trim() || save.isPending}>
              {save.isPending ? 'Saving…' : (isNew ? 'Create' : 'Save')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}


function SharedWithPicker({ users, selected, onChange, disabled }) {
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState('')
  function toggle(email) {
    if (selected.includes(email)) onChange(selected.filter(e => e !== email))
    else onChange([...selected, email])
  }
  function removeOne(email) {
    onChange(selected.filter(e => e !== email))
  }
  const filtered = users.filter(u => {
    if (!filter) return true
    const hay = `${u.name} ${u.email}`.toLowerCase()
    return hay.includes(filter.toLowerCase())
  })

  return (
    <div className={`border rounded ${disabled ? 'bg-gray-50' : 'bg-white'}`}>
      <div className="flex flex-wrap gap-1 p-1.5 min-h-[28px]">
        {selected.map(em => {
          const u = users.find(x => x.email === em)
          return (
            <span key={em}
                   className="inline-flex items-center gap-1 bg-plum-100 text-plum-800 text-[11px] px-1.5 py-0.5 rounded">
              {u?.name || em.split('@')[0]}
              {!disabled && (
                <button type="button" onClick={() => removeOne(em)}
                         className="hover:text-red-700">
                  <X size={9}/>
                </button>
              )}
            </span>
          )
        })}
        {!disabled && (
          <button type="button"
                   className="text-[11px] text-plum-700 hover:underline px-1"
                   onClick={() => setOpen(o => !o)}>
            {open ? 'Done' : '+ Add'}
          </button>
        )}
      </div>
      {open && !disabled && (
        <div className="border-t border-gray-200 max-h-48 overflow-y-auto">
          <input className="input text-[11px] w-full !border-0 !rounded-none"
                  placeholder="Filter by name or email…"
                  value={filter}
                  onChange={e => setFilter(e.target.value)}
                  autoFocus />
          <ul>
            {filtered.length === 0 && (
              <li className="text-[11px] text-gray-400 italic px-2 py-1.5">No matches.</li>
            )}
            {filtered.map(u => {
              const picked = selected.includes(u.email)
              return (
                <li key={u.email}>
                  <button type="button"
                           onClick={() => toggle(u.email)}
                           className={`block w-full text-left text-[11px] px-2 py-1 hover:bg-plum-50 ${
                             picked ? 'bg-plum-50 font-medium' : ''
                           }`}>
                    <input type="checkbox" checked={picked} readOnly className="mr-1.5"/>
                    {u.name} <span className="text-gray-500">({u.email})</span>
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}


function ResponsibilitiesDrawer({ onClose }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['my-job-responsibilities'],
    queryFn: () => api.get('/training/mine/responsibilities').then(r => r.data),
  })
  const [printing, setPrinting] = useState(false)

  async function printPdf() {
    setPrinting(true)
    try {
      const res = await api.get('/training/mine/responsibilities.pdf',
                                { responseType: 'blob' })
      const url = URL.createObjectURL(res.data)
      const win = window.open(url, '_blank', 'noopener')
      if (!win) {
        // Pop-up blocked — fall back to direct download
        const a = document.createElement('a')
        a.href = url
        a.download = `my-job-responsibilities-${new Date().toISOString().slice(0,10)}.pdf`
        document.body.appendChild(a); a.click(); a.remove()
      }
      setTimeout(() => URL.revokeObjectURL(url), 30_000)
    } catch (e) {
      alert(e?.response?.data?.detail || 'PDF export failed')
    } finally { setPrinting(false) }
  }

  const items = data?.items || []
  const summary = data?.summary || {}

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-4xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between z-10">
          <div>
            <h2 className="font-serif font-semibold text-ink text-[16px]">My Job Responsibilities</h2>
            {data && (
              <div className="text-[11px] text-muted">
                {summary.total} total · <strong className="text-green-700">{summary.trained}</strong> trained ·{' '}
                <strong className="text-red-700">{summary.untrained}</strong> not-trained
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button className="btn-secondary text-xs flex items-center gap-1"
                    disabled={printing || !data}
                    onClick={printPdf}>
              <Printer size={11}/> {printing ? '…' : 'Print PDF'}
            </button>
            <button onClick={onClose} className="text-muted hover:text-ink"><X size={18}/></button>
          </div>
        </div>
        <div className="p-5">
          {isLoading && <div className="text-sm text-gray-400">Loading…</div>}
          {error && (
            <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2">
              {error?.response?.data?.detail || error.message}
            </div>
          )}
          {!isLoading && !error && items.length === 0 && (
            <div className="text-sm text-gray-500 italic">
              No tasks assigned to you yet.
            </div>
          )}
          {!isLoading && items.length > 0 && (
            <div className="card !p-0 overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-plum-50">
                  <tr>
                    <th className="table-th">Task</th>
                    <th className="table-th text-center">Trained</th>
                    <th className="table-th text-center">Not-trained</th>
                    <th className="table-th">Date Trained</th>
                    <th className="table-th">Assigned by</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {items.map(it => {
                    const date = (it.trainee_signed_at || it.trainer_signed_at || '').slice(0, 10)
                    return (
                      <tr key={it.template_id}
                          className={it.trained ? '' : 'bg-red-50/40'}>
                        <td className="table-td">
                          <div className="font-medium text-[13px]">
                            {it.question_text || it.title}
                          </div>
                          <div className="text-[10px] text-muted capitalize">
                            {it.category}{it.priority && ` · ${it.priority}`}
                            {it.training_material_url && (
                              <> · <a href={it.training_material_url}
                                       target="_blank" rel="noreferrer"
                                       className="text-plum-700 hover:underline">training material</a></>
                            )}
                          </div>
                        </td>
                        <td className="table-td text-center">
                          {it.trained && <CheckCircle2 size={16} className="inline text-green-600" />}
                        </td>
                        <td className="table-td text-center">
                          {!it.trained && (
                            <span className="inline-flex items-center gap-1 text-red-700 text-[11px]">
                              <AlertCircle size={13}/>
                              {it.status === 'pending_trainee' ? 'awaiting your acknowledgement'
                                : it.status === 'disputed'       ? 'disputed'
                                : it.status === 'revoked'        ? 'revoked'
                                : it.requires_training            ? 'not trained'
                                : 'training not required'}
                            </span>
                          )}
                        </td>
                        <td className="table-td text-[12px] font-mono">
                          {it.trained && date ? fmt.date(date) : <span className="text-muted">—</span>}
                        </td>
                        <td className="table-td text-[12px]">
                          {it.trained && it.trainer_email
                            ? <span title={it.trainer_email}>{it.trainer_email.split('@')[0]}</span>
                            : <span className="text-muted">—</span>}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}


function Labeled({ label, children }) {
  return (
    <div>
      <div className="text-[10px] uppercase text-gray-500 tracking-wide mb-1">{label}</div>
      {children}
    </div>
  )
}
