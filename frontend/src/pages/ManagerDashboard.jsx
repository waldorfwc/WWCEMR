import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  AlertTriangle, ThumbsDown, MessageSquareWarning, Clock, CheckCircle2, RefreshCw,
  UserX, ExternalLink,
} from 'lucide-react'
import api, { fmt } from '../utils/api'


export default function ManagerDashboard() {
  const qc = useQueryClient()
  const [days, setDays] = useState(7)

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['manager-dashboard', days],
    queryFn: () => api.get(`/checklist/manager/dashboard?days=${days}`).then(r => r.data),
  })

  const runEscalations = useMutation({
    mutationFn: () => api.post('/checklist/manager/run-escalations').then(r => r.data),
    onSuccess: () => { refetch() },
  })

  const noAnswers = data?.no_answers || []
  const overdue = data?.overdue || []
  const painPoints = data?.pain_points_open || []
  const reports = data?.direct_reports || []
  const unassigned = data?.unassigned_templates || []

  if (isLoading) return <div className="p-6 text-gray-400">Loading…</div>

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Manager Dashboard</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Accountability across {reports.length} direct report{reports.length === 1 ? '' : 's'}
            {reports.length > 0 && (
              <span className="text-gray-400"> · {reports.map(r => r.split('@')[0]).join(', ')}</span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select className="input text-xs" value={days} onChange={e => setDays(parseInt(e.target.value))}>
            <option value={1}>last 24h</option>
            <option value={7}>last 7 days</option>
            <option value={14}>last 14 days</option>
            <option value={30}>last 30 days</option>
          </select>
          <button className="btn-secondary text-xs flex items-center gap-1"
                  onClick={() => runEscalations.mutate()}
                  disabled={runEscalations.isPending}>
            <RefreshCw size={12} /> {runEscalations.isPending ? 'Sending…' : 'Run escalations'}
          </button>
        </div>
      </div>

      {runEscalations.data && (
        <div className="card text-xs bg-blue-50 border-blue-200 text-blue-800">
          ✓ Escalation sweep complete · {runEscalations.data.managers_notified} manager(s) notified · {runEscalations.data.instances_escalated} task(s) flagged.
        </div>
      )}

      {/* Top tiles */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <Tile
          label="No-answers"
          value={noAnswers.length}
          icon={<ThumbsDown size={18} />}
          tone="amber"
        />
        <Tile
          label="Overdue / unanswered"
          value={overdue.length}
          icon={<AlertTriangle size={18} />}
          tone="red"
        />
        <Tile
          label="Open pain points"
          value={painPoints.length}
          icon={<MessageSquareWarning size={18} />}
          tone="violet"
        />
        <Tile
          label="Unassigned templates"
          value={unassigned.length}
          icon={<UserX size={18} />}
          tone={unassigned.length > 0 ? "red" : "neutral"}
        />
      </div>

      {reports.length === 0 && (
        <div className="card bg-gray-50 text-sm text-gray-500 italic">
          No direct reports yet. To make this dashboard light up, add this user as the
          <code className="mx-1 text-xs bg-white px-1 py-0.5 rounded border border-gray-200">escalate_to_email</code>
          on a checklist template.
        </div>
      )}

      {/* No-answers */}
      <Section
        title="No-answers"
        subtitle="Tasks where the person answered No — review the follow-up and decide what to do"
        empty="Nothing flagged in this window."
        rows={noAnswers}
        renderRow={(it) => (
          <div className="flex items-start justify-between gap-3 py-2 border-b border-gray-100 last:border-0">
            <div className="min-w-0">
              <div className="text-sm font-medium text-gray-900">{it.task}</div>
              <div className="text-xs text-gray-500">
                <strong>{it.owner.split('@')[0]}</strong> · {fmt.date(it.due_date)}
                {it.answered_at && <> · answered {it.answered_at.slice(11, 16)}</>}
              </div>
              {it.followup_count != null && (
                <div className="text-xs mt-1 text-amber-800 bg-amber-50 rounded px-2 py-1 inline-block">
                  Count: <strong>{it.followup_count}</strong>
                </div>
              )}
              {it.followup_text && (
                <div className="text-xs mt-1 text-amber-800 bg-amber-50 rounded px-2 py-1">
                  <em>"{it.followup_text}"</em>
                </div>
              )}
            </div>
          </div>
        )}
      />

      {/* Overdue */}
      <Section
        title="Overdue / unanswered"
        subtitle="Past the escalation window with no answer recorded"
        empty="Nothing overdue right now — everyone's caught up."
        rows={overdue}
        renderRow={(it) => (
          <div className="flex items-start justify-between gap-3 py-2 border-b border-gray-100 last:border-0">
            <div className="min-w-0">
              <div className="text-sm font-medium text-gray-900">{it.task}</div>
              <div className="text-xs text-gray-500">
                <strong>{it.owner.split('@')[0]}</strong> · due {fmt.date(it.due_date)}
                {it.due_at && <> at {it.due_at.slice(11, 16)}</>}
                <span className="text-red-600 ml-2">· {it.hours_late}h late</span>
              </div>
            </div>
            <div className="text-[11px] text-gray-400 shrink-0 flex items-center gap-1">
              <Clock size={11} />
              {it.escalation_sent_at ? `notified ${fmt.dateTime(it.escalation_sent_at)}` : 'not yet notified'}
            </div>
          </div>
        )}
      />

      {/* Unassigned templates */}
      <UnassignedSection rows={unassigned} />

      {/* Pain points */}
      <PainPointsSection rows={painPoints} qc={qc} days={days} />
    </div>
  )
}


function UnassignedSection({ rows }) {
  return (
    <div className="card">
      <div className="mb-2">
        <h2 className="text-sm font-semibold text-gray-800">Unassigned Templates</h2>
        <p className="text-xs text-gray-500">
          Templates with zero matching users today — these will not generate any tasks.
          Listed because you're the escalation owner, or (for super admins) the template has no owner.
        </p>
      </div>
      {rows.length === 0 ? (
        <div className="text-xs text-gray-400 italic">All your templates have at least one assignee — nothing to fix here.</div>
      ) : (
        <ul className="divide-y divide-gray-100">
          {rows.map(t => (
            <li key={t.id} className="py-2 flex items-baseline justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm font-medium text-gray-900">
                  {t.question_text || t.title}
                </div>
                <div className="text-[11px] text-gray-500 capitalize">
                  {t.category}
                  {t.owner_role === 'orphan' && (
                    <span className="ml-2 text-amber-700">[no manager]</span>
                  )}
                </div>
                <div className="text-[11px] text-red-700 mt-0.5">
                  {t.reasons?.join(' · ')}
                </div>
              </div>
              <Link to="/admin/templates"
                    className="text-xs text-plum-700 hover:underline shrink-0 flex items-center gap-1">
                <ExternalLink size={11} /> Fix
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}


function Tile({ label, value, icon, tone }) {
  const tones = {
    amber:   'bg-amber-50 border-amber-200 text-amber-800',
    red:     'bg-red-50 border-red-200 text-red-800',
    violet:  'bg-violet-50 border-violet-200 text-violet-800',
    neutral: 'bg-gray-50 border-gray-200 text-gray-700',
  }
  return (
    <div className={`card border ${tones[tone] || tones.neutral} flex items-center justify-between`}>
      <div>
        <div className="text-[11px] uppercase tracking-wide opacity-80">{label}</div>
        <div className="text-3xl font-bold mt-1">{value}</div>
      </div>
      <div className="opacity-60">{icon}</div>
    </div>
  )
}


function Section({ title, subtitle, empty, rows, renderRow }) {
  return (
    <div className="card">
      <div className="mb-2">
        <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
        {subtitle && <p className="text-xs text-gray-500">{subtitle}</p>}
      </div>
      {rows.length === 0 ? (
        <div className="text-xs text-gray-400 italic">{empty}</div>
      ) : (
        <div>{rows.map((it, i) => <div key={i}>{renderRow(it)}</div>)}</div>
      )}
    </div>
  )
}


function PainPointsSection({ rows, qc, days }) {
  return (
    <div className="card">
      <div className="mb-2">
        <h2 className="text-sm font-semibold text-gray-800">Open Pain Points</h2>
        <p className="text-xs text-gray-500">Issues your direct reports flagged at the end of their checklist</p>
      </div>
      {rows.length === 0 ? (
        <div className="text-xs text-gray-400 italic">No open pain points — nice.</div>
      ) : (
        <div className="space-y-2">
          {rows.map(p => <PainPointRow key={p.id} pp={p} qc={qc} days={days} />)}
        </div>
      )}
    </div>
  )
}


function PainPointRow({ pp, qc, days }) {
  const [response, setResponse] = useState('')
  const [busy, setBusy] = useState(false)

  async function review(status) {
    setBusy(true)
    try {
      await api.patch(`/checklist/pain-points/${pp.id}`, {
        status, response: response.trim() || null,
      })
      qc.invalidateQueries({ queryKey: ['manager-dashboard', days] })
    } finally { setBusy(false) }
  }

  return (
    <div className="border border-gray-200 rounded p-2.5 bg-gray-50/50">
      <div className="text-xs text-gray-500">
        <strong>{pp.user_email.split('@')[0]}</strong> · {fmt.date(pp.occurred_on)}
      </div>
      <div className="text-sm text-gray-800 mt-1 whitespace-pre-wrap">{pp.body}</div>
      <div className="mt-2 flex items-center gap-2">
        <input
          className="input text-xs flex-1"
          placeholder="Optional response / next step"
          value={response}
          onChange={e => setResponse(e.target.value)}
        />
        <button className="btn-secondary text-xs" onClick={() => review('acknowledged')} disabled={busy}>
          Acknowledge
        </button>
        <button className="btn-primary text-xs flex items-center gap-1"
                onClick={() => review('resolved')} disabled={busy}>
          <CheckCircle2 size={12} /> Resolve
        </button>
      </div>
    </div>
  )
}
