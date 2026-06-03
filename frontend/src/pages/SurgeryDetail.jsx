import { useState, useMemo, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import {
  ArrowLeft, AlertTriangle, CheckCircle2, Circle, Clock, Hospital, FileText,
  Check, SkipForward, RotateCcw, X, Flag, Pause, Save, Edit3,
  MessageSquare, Download, Upload, Copy, ListPlus, Send, RefreshCw,
  ChevronDown, ChevronUp, Package, Eye,
  DollarSign, HeartPulse, UserPlus, FlaskConical, Mail, Phone, Calculator,
  ShieldCheck,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import MessagesSection from '../components/MessagesSection'
import PdfPreviewDrawer from '../components/PdfPreviewDrawer'
import ErrorBoundary from '../components/ErrorBoundary'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { MatchesDrawer } from './SurgeryWaitlist'
import { useFacilities } from '../hooks/useFacilities'

const STATUS_TONE = {
  incomplete:    'bg-amber-100 text-amber-800',
  new:           'bg-gray-100 text-gray-700',
  in_progress:   'bg-amber-50 text-amber-800',
  confirmed:     'bg-blue-50 text-blue-800',
  completed:     'bg-green-50 text-green-800',
  hold:          'bg-violet-50 text-violet-800',
  cancelled:     'bg-red-50 text-red-700',
  unresponsive:  'bg-gray-100 text-gray-500',
}

const STATUS_LABEL = {
  incomplete:    'Incomplete',
  new:           'New',
  in_progress:   'Benefits Check',
  confirmed:     'Pre-Surgery',
  completed:     'Post-Surgery',
  hold:          'Hold',
  cancelled:     'Canceled',
  unresponsive:  'Unresponsive',
}

const MILESTONE_ICON = {
  done:           <CheckCircle2 size={16} className="text-green-600" />,
  in_progress:    <Clock size={16} className="text-amber-600" />,
  pending:        <Circle size={16} className="text-gray-300" />,
  locked:         <Circle size={16} className="text-gray-200" />,
  skipped:        <Circle size={16} className="text-gray-400" />,
  not_applicable: <Circle size={16} className="text-gray-300" />,
}


export default function SurgeryDetail() {
  const { id } = useParams()
  const qc = useQueryClient()
  const [showCancel, setShowCancel] = useState(false)
  const [freedBlockDayId, setFreedBlockDayId] = useState(null)
  const [showSchedule, setShowSchedule] = useState(false)

  const { data: tpl } = useQuery({
    queryKey: ['surgery-templates'],
    queryFn: () => api.get('/surgery/picklists/procedure-templates').then(r => r.data.templates),
    staleTime: 60_000,
  })

  const { data, isLoading, error } = useQuery({
    queryKey: ['surgery', id],
    queryFn: () => api.get(`/surgery/${id}`).then(r => r.data),
  })

  const patch = useMutation({
    mutationFn: (body) => api.patch(`/surgery/${id}`, body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery', id] })
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
    },
  })

  async function viewAsPatient() {
    try {
      const { data } = await api.post(`/admin/surgeries/${id}/portal-preview-token`)
      const url = `/portal/s/${data.surgery_id}?staff_token=${encodeURIComponent(data.token)}`
      window.open(url, '_blank', 'noopener,noreferrer')
    } catch (e) {
      alert(e?.response?.data?.detail || 'Could not start preview.')
    }
  }

  const gateOverride = useMutation({
    mutationFn: (enabled) =>
      api.patch(`/surgery/${id}/schedule-gate-override`, { enabled }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery', id] }),
  })

  if (isLoading) return <div className="p-6 text-gray-400">Loading…</div>
  if (error) return <div className="p-6 text-red-600">{error?.response?.data?.detail || error.message}</div>
  if (!data) return null

  const s = data
  const procs = s.procedures || []
  const dxs = s.diagnoses || []
  const milestones = s.milestones || []
  const isCancelable = !['cancelled', 'completed'].includes(s.status)

  return (
    <ErrorBoundary label="Surgery detail">
    <div>
      <Link to="/surgery" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
        <ArrowLeft size={12} /> Surgery dashboard
      </Link>

      {/* Patient header */}
      <div className="card mb-4">
        <div className="flex items-baseline justify-between gap-3 mb-2">
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-xl font-bold text-gray-900">{s.patient_name}</h1>
              {s.urgency === "urgent" && <span className="text-xs bg-red-100 text-red-700 px-2 py-0.5 rounded">🚨 URGENT</span>}
              <span className={`text-[11px] px-2 py-0.5 rounded ${STATUS_TONE[s.status]}`}>{STATUS_LABEL[s.status] || s.status}</span>
              {s.sub_flag && <span className="text-[10px] text-gray-500">· {s.sub_flag.replace(/_/g, ' ')}</span>}
              {s.behind_schedule && (
                <span className={`text-[11px] font-semibold ${s.hours_overdue > 48 ? 'text-red-700' : 'text-amber-700'}`}>
                  {s.hours_overdue > 48 ? `${Math.floor(s.hours_overdue / 24)}d behind` : `${s.hours_overdue}h behind`}
                </span>
              )}
            </div>
            <div className="text-xs text-gray-500 mt-0.5">
              Chart #{s.chart_number}
              {s.dob && <> · DOB {fmt.date(s.dob)}{s.age != null && ` (age ${s.age})`}</>}
              {s.phone && <> · {s.phone}</>}
            </div>
            <PickDateLink />
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={viewAsPatient}
              className="text-xs px-2 py-1 rounded border bg-white border-gray-200 text-gray-600 hover:border-plum-300 hover:bg-plum-50 flex items-center gap-1"
              title="Open this patient's portal in a new tab (read-only)"
            >
              <Eye size={11} /> View as patient
            </button>
            <button
              type="button"
              onClick={() => patch.mutate({ urgency: s.urgency === "urgent" ? "routine" : "urgent" })}
              className={`text-xs px-2 py-1 rounded border flex items-center gap-1 ${
                s.urgency === "urgent" ? 'bg-red-50 border-red-200 text-red-700'
                                       : 'bg-white border-gray-200 text-gray-600 hover:border-red-300'
              }`}
              disabled={patch.isPending}
            >
              <Flag size={11} /> {s.urgency === "urgent" ? 'Clear urgent' : 'Mark urgent'}
            </button>
            {s.status === 'incomplete' && (
              <button type="button"
                      onClick={() => patch.mutate({ status: 'new' })}
                      className="btn-primary text-xs flex items-center gap-1"
                      disabled={patch.isPending}>
                <Check size={11} /> Mark as new (spawn milestones)
              </button>
            )}
            {!s.scheduled_date && isCancelable && (
              <WaitlistToggle surgeryId={s.id} />
            )}
            {!s.scheduled_date && isCancelable && (
              <button type="button"
                      className="btn-primary text-xs flex items-center gap-1"
                      onClick={() => setShowSchedule(true)}>
                Schedule for patient
              </button>
            )}
            {isCancelable && (
              <button type="button"
                      onClick={() => setShowCancel(true)}
                      className="text-xs px-2 py-1 rounded border border-red-300 bg-white text-red-700 hover:bg-red-50 flex items-center gap-1">
                <X size={11} /> Cancel / hold
              </button>
            )}
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs mt-3">
          <Field label="Procedure">
            <ProcedureListEditor s={s} onPatch={patch.mutate} />
          </Field>
          <Field label="Diagnosis">
            <DiagnosisListEditor s={s} onPatch={patch.mutate} />
          </Field>
          <Field label="Surgeon">
            <SurgeonCell s={s} onPatch={patch.mutate} />
          </Field>
          <Field label="Facility">
            <FacilityCell s={s} onPatch={patch.mutate} />
          </Field>

          <Field label="Surgery date">
            <SurgeryDateCell s={s} />
          </Field>
          <Field label="Pre-op date">
            <PreopDateCell s={s} onPatch={patch.mutate} />
          </Field>
          <Field label="Estimated time">
            <EstimatedTimeCell s={s} onPatch={patch.mutate} />
          </Field>
          <Field label="Insurance">
            <InsuranceCell s={s} onPatch={patch.mutate} />
          </Field>

          <Field label="Address">
            <AddressCell s={s} onPatch={patch.mutate} />
          </Field>
          <Field label="Auth">
            <AuthSummaryCell s={s} />
          </Field>
          <Field label="Clearance">
            <ClearanceCell s={s} onPatch={patch.mutate} />
          </Field>
          <Field label="Pt responsibility">
            <PtResponsibilityCell s={s} />
          </Field>

          <Field label="Order PDF">
            <OrderPdfCell surgery={s} />
          </Field>
          <Field label="Chart # / Phone / Email">
            <ContactCell s={s} onPatch={patch.mutate} />
          </Field>
          <Field label="Urgency">
            <select className="input text-sm"
                    value={s.urgency || 'routine'}
                    onChange={e => patch.mutate({ urgency: e.target.value })}>
              <option value="routine">Routine</option>
              <option value="expedited">Expedited</option>
              <option value="urgent">Urgent</option>
            </select>
          </Field>

          <Field label="Complexity">
            <select className="input text-sm"
                    value={s.complexity || 'standard'}
                    onChange={e => patch.mutate({ complexity: e.target.value })}>
              <option value="standard">Standard</option>
              <option value="complex">Complex</option>
            </select>
          </Field>

          <Field label="Duration (min)">
            <div className="flex items-center gap-2">
              <input type="number" min="0" className="input text-sm w-24"
                     defaultValue={s.duration_minutes ?? ''}
                     onBlur={e => {
                       const v = e.target.value === '' ? null : Number(e.target.value)
                       if (v !== s.duration_minutes) patch.mutate({ duration_minutes: v })
                     }} />
              {s.duration_source && (
                <span className="text-[10px] text-gray-500">
                  from {s.duration_source}
                </span>
              )}
            </div>
          </Field>

          <Field label="Surgeon email">
            <input className="input text-sm w-full"
                   defaultValue={s.surgeon_email ?? ''}
                   placeholder="acooke@waldorfwomenscare.com"
                   onBlur={e => {
                     const v = e.target.value.trim() || null
                     if (v !== s.surgeon_email) patch.mutate({ surgeon_email: v })
                   }} />
          </Field>

          <Field label="Cell phone">
            <input className="input text-sm w-full"
                   defaultValue={s.cell_phone || ''}
                   placeholder="+15555550100"
                   onBlur={e => {
                     const v = e.target.value.trim() || null
                     if (v !== s.cell_phone) patch.mutate({ cell_phone: v })
                   }} />
          </Field>

          <Field label="SMS opt-in">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox"
                     checked={!!s.sms_consent}
                     onChange={e => patch.mutate({ sms_consent: e.target.checked })} />
              <span>
                {s.sms_consent
                  ? `Opted in${s.sms_consented_at ? ' on ' + s.sms_consented_at.slice(0,10) : ''}`
                  : 'Not opted in — SMS sends will skip'}
              </span>
            </label>
          </Field>

          <Field label="Schedule gate">
            <label className="flex items-center gap-2 text-xs text-gray-700">
              <input type="checkbox"
                     checked={!!s.schedule_gate_override}
                     onChange={e => gateOverride.mutate(e.target.checked)}
                     disabled={gateOverride.isPending} />
              Allow patient to self-schedule without payment
            </label>
            {s.schedule_gate_override && s.schedule_gate_override_by && (
              <div className="text-[10px] text-gray-400 mt-0.5">
                Set by {s.schedule_gate_override_by}
                {s.schedule_gate_override_at ? ' on ' + s.schedule_gate_override_at.slice(0, 10) : ''}
              </div>
            )}
          </Field>

          <Field label="Device">
            <DeviceCell surgery={s} />
          </Field>

          <Field label="Consent">
            <ConsentStatusCell surgery={s} />
          </Field>

          <Field label="Pathology">
            <PathologyStatusCell surgery={s} />
          </Field>

          <Field label="Billed">
            <BilledStatusCell surgery={s} />
          </Field>
        </div>
      </div>

      {/* Grouped surgery sections (Phase L1) */}
      {milestones.length === 0 ? (
        <div className="card text-xs text-gray-500 italic bg-amber-50 border border-amber-200">
          No milestones yet — surgery is in <code>{s.status}</code> status. Click <strong>Mark as new</strong> above to generate milestones.
        </div>
      ) : (
        <GroupedSurgeryBody surgery={s} milestones={milestones} />
      )}

      <NotesPanel surgery={s} />

      {showCancel && (
        <CancelDrawer
          surgery={s}
          onClose={() => setShowCancel(false)}
          onFreedBlockDay={(id) => { setShowCancel(false); setFreedBlockDayId(id) }}
        />
      )}

      {freedBlockDayId && (
        <MatchesDrawer
          blockDayId={freedBlockDayId}
          onClose={() => setFreedBlockDayId(null)}
        />
      )}

      {showSchedule && (
        <ScheduleForPatientModal
          surgery={s}
          templates={tpl || []}
          onClose={() => setShowSchedule(false)}
          onSaved={() => {
            qc.invalidateQueries({ queryKey: ['surgery', s.id] })
            qc.invalidateQueries({ queryKey: ['surgery-list'] })
            qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
          }}
        />
      )}
    </div>
    </ErrorBoundary>
  )
}


// ─── Payments section ─────────────────────────────────────────────

function PaymentsSection({ surgery, flat = false }) {
  const qc = useQueryClient()
  const { data, refetch } = useQuery({
    queryKey: ['surgery-payments', surgery.id],
    queryFn: () => api.get(`/surgery/${surgery.id}/payments`).then(r => r.data),
  })

  const requestMut = useMutation({
    mutationFn: (body) =>
      api.post(`/surgery/${surgery.id}/request-payment`, body || {}).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery-payments', surgery.id] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Stripe error'),
  })

  const outstanding = Number(data?.outstanding_balance || 0)
  // Show every non-requested row (paid/refunded/failed/expired = audit trail)
  // but collapse stacked 'requested' rows to just the newest one — each Pay-Now
  // click creates a fresh Stripe Checkout session, and stale ones just clutter
  // the view.
  const allPayments = data?.payments || []
  const payments = (() => {
    const seenRequested = false ? null : { v: false }
    const out = []
    for (const p of allPayments) {
      if (p.status === 'requested') {
        if (seenRequested.v) continue
        seenRequested.v = true
      }
      out.push(p)
    }
    return out
  })()
  const fmtMoney = (v) => `$${Number(v || 0).toFixed(2)}`

  function copy(url) {
    if (navigator.clipboard) navigator.clipboard.writeText(url)
  }

  const inner = (
    <>
      <div className="flex items-center justify-between mb-3">
        <h3 className={`flex items-center gap-1.5 ${flat ? "text-sm font-semibold text-gray-800" : "text-lg font-semibold"}`}>
          <DollarSign size={14} className="text-emerald-700" /> Payment Status
        </h3>
        {outstanding > 0 && (
          <button className="btn-primary text-sm"
                  onClick={() => requestMut.mutate({})}
                  disabled={requestMut.isPending}>
            {requestMut.isPending ? 'Creating link…' : `Request payment (${fmtMoney(outstanding)})`}
          </button>
        )}
      </div>

      <div className="grid grid-cols-3 gap-3 text-[12px] mb-3">
        <div>
          <div className="text-gray-500">Patient responsibility</div>
          <div className="font-mono">{fmtMoney(data?.patient_responsibility)}</div>
        </div>
        <div>
          <div className="text-gray-500">Amount paid</div>
          <div className="font-mono">{fmtMoney(data?.amount_paid)}</div>
        </div>
        <div>
          <div className="text-gray-500">Outstanding balance</div>
          <div className={`font-mono ${outstanding > 0 ? 'text-amber-700 font-semibold' : 'text-green-700'}`}>
            {fmtMoney(outstanding)}
          </div>
        </div>
      </div>

      {payments.length === 0 ? (
        <div className="text-[12px] text-gray-400 italic">No payment activity.</div>
      ) : (
        <table className="w-full text-[12px]">
          <thead className="text-[11px] uppercase text-gray-500">
            <tr>
              <th className="text-left py-1">Status</th>
              <th className="text-left py-1">Amount</th>
              <th className="text-left py-1">Requested</th>
              <th className="text-left py-1">Description</th>
              <th className="text-left py-1">Link</th>
            </tr>
          </thead>
          <tbody>
            {payments.map(p => (
              <tr key={p.id} className="border-t border-border-subtle">
                <td className="py-1.5">
                  <span className={`px-2 py-0.5 rounded text-[11px] ${
                    p.status === 'paid'      ? 'bg-green-100 text-green-700' :
                    p.status === 'refunded'  ? 'bg-violet-100 text-violet-700' :
                    p.status === 'failed'    ? 'bg-red-100 text-red-700' :
                    p.status === 'expired'   ? 'bg-gray-100 text-gray-500' :
                                               'bg-amber-100 text-amber-700'
                  }`}>{p.status}</span>
                </td>
                <td className="py-1.5 font-mono">{fmtMoney(p.amount_requested)}</td>
                <td className="py-1.5">{(p.requested_at || '').slice(0, 10)}</td>
                <td className="py-1.5">{p.description || '—'}</td>
                <td className="py-1.5">
                  {p.checkout_url && p.status === 'requested' ? (
                    <>
                      <a href={p.checkout_url} target="_blank" rel="noopener noreferrer"
                         className="text-plum-700 hover:underline">Open</a>
                      {' · '}
                      <button onClick={() => copy(p.checkout_url)}
                              className="text-plum-700 hover:underline">Copy</button>
                    </>
                  ) : (
                    <span className="text-gray-400">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )

  if (flat) return inner
  return (
    <div className="bg-white border border-border-subtle rounded-lg p-5 mb-4 mt-4">
      {inner}
    </div>
  )
}


// ─── Portal access invite ─────────────────────────────────────────

function FeeScheduleButton({ surgeryId, onApplied }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const [open, setOpen] = useState(false)
  const [error, setError] = useState(null)

  async function preview() {
    setBusy(true); setError(null)
    try {
      const r = await api.get(`/surgery/${surgeryId}/fee-schedule/preview`)
      setResult(r.data); setOpen(true)
    } catch (e) {
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (e?.message || 'Lookup failed'))
    } finally { setBusy(false) }
  }
  async function apply() {
    setBusy(true); setError(null)
    try {
      const r = await api.post(`/surgery/${surgeryId}/fee-schedule/apply`)
      onApplied?.(r.data.allowed_amount)
      setResult(r.data.preview); setOpen(true)
      qc.invalidateQueries({ queryKey: ['surgery', surgeryId] })
      qc.invalidateQueries({ queryKey: ['surgery-payments', surgeryId] })
    } catch (e) {
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (e?.message || 'Apply failed'))
    } finally { setBusy(false) }
  }

  return (
    <>
      <button onClick={preview} disabled={busy}
              className="btn-secondary text-[11px] flex items-center gap-1">
        <DollarSign size={11} /> {busy ? 'Looking up…' : 'Pull from Fee Schedule'}
      </button>
      {error && <span className="text-[11px] text-red-700 ml-2">{error}</span>}
      {open && result && (
        <FeeSchedulePreviewModal
          result={result}
          busy={busy}
          onApply={apply}
          onClose={() => { setOpen(false); setResult(null) }} />
      )}
    </>
  )
}


function FeeSchedulePreviewModal({ result, busy, onApply, onClose }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative bg-white rounded-lg shadow-xl max-w-2xl w-full"
           onClick={e => e.stopPropagation()}>
        <div className="px-5 py-3 border-b border-gray-200 flex items-center justify-between">
          <h3 className="font-semibold text-gray-900 text-sm">
            Fee schedule preview · {result.insurance || 'no insurance set'}
          </h3>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-800">
            <X size={16} />
          </button>
        </div>
        <div className="p-5 space-y-4">
          <div className="text-[12px] text-gray-700">
            Total allowed: <span className="font-mono font-semibold text-lg text-emerald-700">
              ${result.total_allowed.toFixed(2)}
            </span>
          </div>
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-[10px] uppercase text-gray-500 border-b">
                <th className="py-1">CPT</th>
                <th className="py-1 text-right">Schedule</th>
                <th className="py-1 text-right">Applied</th>
                <th className="py-1">Why</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {result.per_cpt.map(r => (
                <tr key={r.cpt}>
                  <td className="py-1.5 font-mono">{r.cpt}</td>
                  <td className="py-1.5 text-right font-mono">
                    {r.allowed_from_schedule != null
                      ? `$${r.allowed_from_schedule.toFixed(2)}` : '—'}
                  </td>
                  <td className="py-1.5 text-right font-mono">
                    {r.applied != null ? `$${r.applied.toFixed(2)}` : '—'}
                  </td>
                  <td className="py-1.5 text-gray-600">{r.reason || ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {result.warnings?.length > 0 && (
            <div className="bg-amber-50 border border-amber-200 rounded p-2 text-[11px] text-amber-900">
              <ul className="list-disc pl-4 space-y-0.5">
                {result.warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            </div>
          )}
        </div>
        <div className="px-5 py-3 border-t border-gray-200 flex justify-end gap-2">
          <button onClick={onClose} className="btn-secondary text-sm">Close</button>
          <button onClick={onApply} disabled={busy}
                  className="btn-primary text-sm flex items-center gap-1">
            <DollarSign size={12} /> {busy ? 'Applying…' : 'Set as allowed amount'}
          </button>
        </div>
      </div>
    </div>
  )
}


function PortalAccessPanel({ surgery }) {
  const qc = useQueryClient()
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const send = useMutation({
    mutationFn: () => api.post(`/surgery/${surgery.id}/portal-access/send`).then(r => r.data),
    onSuccess: (d) => {
      setResult(d); setError(null)
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
    },
    onError: (e) => {
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (e?.message || 'Send failed'))
      setResult(null)
    },
  })

  const portalUrl = `https://gw.waldorfwomenscare.com/portal/login`
  const [copied, setCopied] = useState(false)
  function copyLink() {
    navigator.clipboard.writeText(portalUrl)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="card !p-3">
      <div className="flex items-center gap-1.5 mb-2">
        <Send size={14} className="text-plum-700" />
        <h3 className="text-sm font-semibold text-gray-800">Send Surgery Portal Access</h3>
      </div>
      <p className="text-[11px] text-gray-500 mb-2">
        Emails the patient a link to log in to their surgery portal. They verify with
        DOB + last 4 of phone. Access ends 30 days post-surgery. Use the Klara drafter below for SMS.
      </p>
      <div className="flex flex-wrap gap-1.5 items-center">
        <button className="btn-primary text-xs flex items-center gap-1"
                disabled={!surgery.email || send.isPending}
                onClick={() => send.mutate()}
                title={surgery.email ? `Email ${surgery.email}` : 'No email on file'}>
          <Send size={11} /> {send.isPending ? 'Sending…' : 'Email portal link'}
        </button>
        <button className="btn-secondary text-xs flex items-center gap-1"
                onClick={copyLink}>
          <Copy size={11} /> {copied ? 'Copied!' : 'Copy link'}
        </button>
        <span className="text-[11px] text-gray-500 font-mono truncate max-w-xs">
          {portalUrl}
        </span>
      </div>
      {!surgery.email && (
        <div className="text-[11px] text-amber-700 mt-2">
          No email on file — paste the link into Klara to send by SMS.
        </div>
      )}
      {result && (
        <div className="text-[11px] text-green-700 mt-2 flex items-center gap-1">
          <Check size={11} /> Sent to {result.sent_to}
        </div>
      )}
      {error && (
        <div className="text-[11px] text-red-700 mt-2">✗ {error}</div>
      )}
    </div>
  )
}


// ─── Klara message drafts ─────────────────────────────────────────

function KlaraPanel({ surgery }) {
  const qc = useQueryClient()
  const [kind, setKind] = useState('initial_scheduling')
  const [draft, setDraft] = useState(null)
  const [copied, setCopied] = useState(false)

  const fetchDraft = useMutation({
    mutationFn: () => api.get(`/surgery/${surgery.id}/klara-draft/${kind}`).then(r => r.data),
    onSuccess: (d) => { setDraft(d); setCopied(false) },
  })

  const logSent = useMutation({
    mutationFn: () => api.post(`/surgery/${surgery.id}/klara-sent`, {
      kind: kind === 'initial_scheduling' ? 'klara_initial' :
            kind === 'date_reminder'      ? 'klara_reminder' :
                                              'klara_post_op',
      body_preview: draft?.body?.slice(0, 200),
    }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery', surgery.id] }),
  })

  function copyToClipboard() {
    if (!draft) return
    navigator.clipboard.writeText(draft.body)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="card !p-3">
      <div className="flex items-center gap-1.5 mb-2">
        <MessageSquare size={14} className="text-plum-700" />
        <h3 className="text-sm font-semibold text-gray-800">Klara message drafter</h3>
      </div>
      <p className="text-[11px] text-gray-500 mb-2">
        Generate a templated message, copy to clipboard, paste into Klara. Logs the send so milestones advance.
      </p>
      <div className="flex flex-wrap gap-2 mb-2">
        <select className="input text-xs" value={kind}
                onChange={e => { setKind(e.target.value); setDraft(null) }}>
          <option value="initial_scheduling">Initial scheduling outreach</option>
          <option value="date_reminder">Date reminder (unbooked)</option>
          <option value="post_op_check_in">Post-op check-in</option>
        </select>
        <button className="btn-primary text-xs flex items-center gap-1"
                onClick={() => fetchDraft.mutate()}
                disabled={fetchDraft.isPending}>
          {fetchDraft.isPending ? 'Drafting…' : 'Generate'}
        </button>
      </div>

      {draft && (
        <div className="border border-gray-200 rounded p-2 bg-gray-50 space-y-2">
          <div className="text-[11px] font-semibold text-gray-700">{draft.subject}</div>
          <pre className="text-[11px] text-gray-800 whitespace-pre-wrap font-sans">{draft.body}</pre>
          <div className="flex gap-1.5">
            <button className="btn-secondary text-[11px] flex items-center gap-1"
                    onClick={copyToClipboard}>
              <Copy size={11} /> {copied ? 'Copied!' : 'Copy to clipboard'}
            </button>
            <button className="btn-primary text-[11px] flex items-center gap-1"
                    onClick={() => logSent.mutate()}
                    disabled={logSent.isPending}>
              <Check size={11} /> {logSent.isPending ? 'Logging…' : 'I sent it in Klara'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}


// ─── Boarding slip ────────────────────────────────────────────────

function BoardingSlipPanel({ surgery }) {
  const qc = useQueryClient()
  const { labelOf } = useFacilities()
  const [error, setError] = useState(null)
  // Seed `generated` from the latest saved boarding slip so the success
  // block survives page reloads. Local state still wins after a fresh
  // regeneration (onSuccess overwrites it).
  const [generated, setGenerated] = useState(surgery.latest_boarding_slip || null)
  const [previewing, setPreviewing] = useState(false)
  const [editing, setEditing] = useState(false)

  const generate = useMutation({
    mutationFn: (overrides) => api.post(`/surgery/${surgery.id}/boarding-slip`,
                                        { overrides: overrides || null }).then(r => r.data),
    onSuccess: (d) => {
      setGenerated(d); setError(null); setEditing(false)
      qc.invalidateQueries({ queryKey: ['surgery-files', surgery.id] })
      // So the editor reopens with the freshly-saved overrides applied
      // on top of the surgery defaults (and so the main surgery dict
      // picks up the new latest_boarding_slip).
      qc.invalidateQueries({ queryKey: ['boarding-slip-prefill', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
    },
    onError: (e) => {
      // Defensive: a 422 from FastAPI returns detail as an array of
      // {type, loc, msg, input} objects which would crash React if
      // dropped into JSX as a child. Flatten to a string.
      const d = e?.response?.data?.detail
      let msg
      if (Array.isArray(d)) {
        msg = d.map(x => (x?.msg || JSON.stringify(x))).join('; ')
      } else if (typeof d === 'string') {
        msg = d
      } else if (d) {
        msg = JSON.stringify(d)
      } else {
        msg = e?.message || 'Unknown error'
      }
      setError(msg)
    },
  })

  const facility = surgery.selected_facility
  const facilityLabel = labelOf(facility)
  const ready = facility === 'medstar' || facility === 'crmc'

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1.5">
        <FileText size={14} className="text-plum-700" />
        <h3 className="text-sm font-semibold text-gray-800">Hospital Posting</h3>
      </div>
      {!ready && (
        <div className="text-[11px] text-gray-500 italic">
          Hospital posting is for hospital surgeries only. Office procedures don't need one.
          {!facility && <> Pick a facility first.</>}
        </div>
      )}
      {ready && (
        <>
          <p className="text-[11px] text-gray-500">
            Generate a {facility === 'medstar' ? 'MedStar Posting Form' : 'CRMC Posting Request'}{' '}
            prefilled with this patient's details. After the hospital confirms the booking,
            upload the confirmation below.
          </p>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary text-xs flex items-center gap-1"
                    onClick={() => generate.mutate()}
                    disabled={generate.isPending}>
              <FileText size={11} /> {generate.isPending ? 'Generating…' : `Generate ${facilityLabel} slip`}
            </button>
            <button className="btn-secondary text-xs flex items-center gap-1"
                    onClick={() => setEditing(true)}
                    disabled={generate.isPending}>
              <Edit3 size={11} /> Edit Fields
            </button>
          </div>
          {error && <div className="text-xs text-red-600">{error}</div>}
          {editing && (
            <BoardingSlipFieldsEditor
              surgery={surgery}
              onClose={() => setEditing(false)}
              onRegenerate={(overrides) => generate.mutate(overrides)}
              isPending={generate.isPending}
            />
          )}
          {generated && (
            <>
              <div className="text-[11px] bg-green-50 border border-green-200 rounded p-2 flex items-baseline justify-between gap-2">
                <span className="truncate">✓ Generated <code>{generated.filename}</code></span>
                <div className="flex items-center gap-2 shrink-0">
                  <button onClick={() => setPreviewing(true)}
                          className="text-plum-700 hover:underline flex items-center gap-1">
                    <Eye size={11} /> Preview
                  </button>
                  <button onClick={() => setEditing(true)}
                          className="text-plum-700 hover:underline flex items-center gap-1">
                    <Edit3 size={11} /> Edit fields
                  </button>
                  <a href={`/api${generated.download_url.replace(/^\/api/, '')}`}
                     download
                     className="text-plum-700 hover:underline flex items-center gap-1">
                    <Download size={11} /> Download
                  </a>
                </div>
              </div>
              <SendBoardingSlipPanel surgery={surgery} fileId={generated.id}
                                       sendHistory={generated.send_history} />
            </>
          )}
          {previewing && generated && (
            <PdfPreviewDrawer
              apiPath={generated.download_url.replace(/^\/api/, '')}
              filename={generated.filename}
              title={`Preview · ${generated.filename}`}
              onClose={() => setPreviewing(false)}
            />
          )}
          <FilesPanel surgery={surgery} kindFilter="boarding_slip_confirmation"
                       label="Hospital Posting Confirmation" />
        </>
      )}
    </div>
  )
}


function SendBoardingSlipPanel({ surgery, fileId, sendHistory }) {
  const qc = useQueryClient()
  const [mode, setMode] = useState(null)  // null | 'fax' | 'email'
  const [to, setTo] = useState('')
  const [subject, setSubject] = useState('')
  const [message, setMessage] = useState('')
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [history, setHistory] = useState(sendHistory || [])

  // Keep local history in sync with surgery dict updates from parent
  useEffect(() => { setHistory(sendHistory || []) }, [sendHistory])

  const send = useMutation({
    mutationFn: (body) => api.post(`/surgery/${surgery.id}/boarding-slip/send`, body)
                              .then(r => r.data),
    onSuccess: (d) => {
      setResult(d); setError(null)
      if (Array.isArray(d.send_history)) setHistory(d.send_history)
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
      setTo(''); setMessage('')
      setTimeout(() => { setMode(null); setResult(null) }, 2500)
    },
    onError: (e) => {
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (e?.message || 'Send failed.'))
    },
  })

  function submit() {
    setError(null); setResult(null)
    const body = { kind: mode, to: to.trim(), file_id: fileId, message: message || null }
    if (mode === 'email') body.subject = subject || null
    send.mutate(body)
  }

  const SendHistoryList = () => history.length === 0 ? null : (
    <div className="border border-gray-200 rounded p-2 bg-gray-50 space-y-1">
      <div className="text-[10px] uppercase tracking-wide text-gray-500">
        Send history ({history.length})
      </div>
      <ul className="text-[11px] space-y-0.5">
        {[...history].reverse().map((h, i) => (
          <li key={i} className="flex items-center gap-2">
            {h.status === 'sent'
              ? <span className="text-green-700">✓</span>
              : <span className="text-red-700">✗</span>}
            <span className="capitalize w-10">{h.kind}</span>
            <span className="font-mono text-gray-700">{h.to}</span>
            <span className="text-gray-500 ml-auto">
              {h.at ? new Date(h.at).toLocaleString(undefined, {
                month: '2-digit', day: '2-digit', year: 'numeric',
                hour: 'numeric', minute: '2-digit',
              }) : ''}
            </span>
            {h.by && <span className="text-gray-400">· {h.by.split('@')[0]}</span>}
            {h.error && <span className="text-red-600 text-[10px]">· {h.error}</span>}
          </li>
        ))}
      </ul>
    </div>
  )

  if (!mode) {
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-[11px]">
          <span className="text-gray-500">Send to hospital:</span>
          <button className="btn-secondary text-xs flex items-center gap-1"
                  onClick={() => setMode('fax')}>
            <Send size={11} /> Fax
          </button>
          <button className="btn-secondary text-xs flex items-center gap-1"
                  onClick={() => setMode('email')}>
            <Mail size={11} /> Email
          </button>
        </div>
        <SendHistoryList />
      </div>
    )
  }

  return (
    <div className="border border-plum-200 bg-plum-50/40 rounded p-2 space-y-2 text-[11px]">
      <div className="flex items-center gap-2">
        <span className="font-medium text-gray-800">
          {mode === 'fax' ? 'Fax' : 'Email'} boarding slip
        </span>
        <button className="ml-auto text-muted hover:text-ink"
                onClick={() => { setMode(null); setResult(null); setError(null) }}>
          <X size={12} />
        </button>
      </div>

      <div>
        <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-0.5">
          {mode === 'fax' ? 'Fax number' : 'Email address'}
        </div>
        <input className={`input text-[12px] w-full ${mode === 'fax' ? 'font-mono' : ''}`}
               type={mode === 'email' ? 'email' : 'tel'}
               value={to}
               onChange={e => setTo(e.target.value)}
               placeholder={mode === 'fax' ? '240-555-0100' : 'scheduling@hospital.com'} />
      </div>

      {mode === 'email' && (
        <div>
          <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-0.5">
            Subject (optional)
          </div>
          <input className="input text-[12px] w-full"
                 value={subject}
                 onChange={e => setSubject(e.target.value)}
                 placeholder="Boarding slip — patient name" />
        </div>
      )}

      <div>
        <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-0.5">
          {mode === 'fax' ? 'Cover sheet message' : 'Message'} (optional)
        </div>
        <textarea className="input text-[12px] w-full" rows={2}
                  value={message}
                  onChange={e => setMessage(e.target.value)}
                  placeholder="Anything the recipient needs to know" />
      </div>

      {error && <div className="text-red-600">{error}</div>}
      {result && (
        <div className="text-green-700">
          ✓ {mode === 'fax' ? 'Fax queued' : 'Email sent'} to <strong>{result.to}</strong>
          {result.message_id && <> · <span className="font-mono text-[10px]">{result.message_id}</span></>}
        </div>
      )}

      <div className="flex gap-2">
        <button className="btn-primary text-xs flex items-center gap-1"
                onClick={submit}
                disabled={!to.trim() || send.isPending}>
          {mode === 'fax' ? <Send size={11} /> : <Mail size={11} />}
          {send.isPending
            ? (mode === 'fax' ? 'Sending fax…' : 'Sending email…')
            : (mode === 'fax' ? 'Send fax' : 'Send email')}
        </button>
        <button className="text-[11px] text-muted hover:underline"
                onClick={() => { setMode(null); setResult(null); setError(null) }}>
          Cancel
        </button>
      </div>

      <SendHistoryList />
    </div>
  )
}


function BoardingSlipFieldsEditor({ surgery, onClose, onRegenerate, isPending }) {
  const { data: prefill, isLoading } = useQuery({
    queryKey: ['boarding-slip-prefill', surgery.id],
    queryFn: () => api.get(`/surgery/${surgery.id}/boarding-slip/prefill`)
                       .then(r => r.data),
  })

  const [form, setForm] = useState({})
  const [seeded, setSeeded] = useState(false)

  // Seed the form with the prefill values once they arrive. Guarded so
  // background refetches don't clobber the user's in-progress edits.
  useEffect(() => {
    if (prefill?.fields && !seeded) {
      setForm(prefill.fields)
      setSeeded(true)
    }
  }, [prefill, seeded])

  const update = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const facility = prefill?.facility
  const isMedstar = facility === 'medstar'
  const isCrmc = facility === 'crmc'

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40" />
      <div className="relative w-full max-w-xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between z-10">
          <h2 className="font-serif font-semibold text-ink text-[16px]">
            Edit posting form fields
          </h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        <div className="p-5 space-y-3 text-sm">
          {isLoading && <div className="text-gray-400 italic">Loading…</div>}
          {prefill && (
            <>
              <div className="text-[11px] text-gray-500">
                Edit any prefilled value below. Click <strong>Regenerate</strong> and a fresh PDF
                will replace the old one. The surgery record itself is not modified — these are
                overrides for the posting form only.
              </div>

              <FE label="Surgery date" value={form.surgery_date}
                  type="date"
                  onChange={v => update('surgery_date', v)} />
              <FE label="Start time" value={form.start_time}
                  type="time"
                  onChange={v => update('start_time', v)} />
              <FE label="Estimated minutes" value={form.estimated_minutes}
                  type="number"
                  onChange={v => update('estimated_minutes', v)} />

              <FE label="Primary surgeon" value={form.primary_surgeon}
                  onChange={v => update('primary_surgeon', v)} />
              <FE label="Secondary surgeon" value={form.secondary_surgeon}
                  onChange={v => update('secondary_surgeon', v)} />

              <div className="grid grid-cols-2 gap-2">
                <FE label="Primary CPT" mono value={form.primary_cpt}
                    onChange={v => update('primary_cpt', v)} />
                <FE label="Primary procedure" value={form.primary_description}
                    onChange={v => update('primary_description', v)} />
                <FE label="Secondary CPT" mono value={form.secondary_cpt}
                    onChange={v => update('secondary_cpt', v)} />
                <FE label="Secondary procedure" value={form.secondary_description}
                    onChange={v => update('secondary_description', v)} />
              </div>

              <div className="grid grid-cols-2 gap-2">
                <FE label="ICD-10" mono value={form.icd}
                    onChange={v => update('icd', v)} />
                <FE label="Diagnosis description" value={form.diagnosis_description}
                    onChange={v => update('diagnosis_description', v)} />
              </div>

              {isCrmc && (
                <FE label="Anesthesia" value={form.anesthesia}
                    onChange={v => update('anesthesia', v)} />
              )}

              <FE label="Special equipment / request" textarea
                  value={form.special_request}
                  onChange={v => update('special_request', v)} />

              <FE label="Auth number" mono value={form.auth_number}
                  onChange={v => update('auth_number', v)} />

              {isMedstar && (
                <FE label="Additional notes" textarea
                    value={form.additional_notes}
                    onChange={v => update('additional_notes', v)} />
              )}
            </>
          )}
        </div>

        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
          <button className="text-sm text-muted hover:underline" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => onRegenerate(form)}
                  disabled={isPending || isLoading}>
            <Save size={12} /> {isPending ? 'Regenerating…' : 'Save & Regenerate'}
          </button>
        </div>
      </div>
    </div>
  )
}


function FE({ label, value, onChange, type = 'text', mono = false, textarea = false }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-0.5">{label}</div>
      {textarea ? (
        <textarea className={`input text-[12px] w-full ${mono ? 'font-mono' : ''}`}
                  rows={2}
                  value={value || ''}
                  onChange={e => onChange(e.target.value)} />
      ) : (
        <input className={`input text-[12px] w-full ${mono ? 'font-mono' : ''}`}
               type={type}
               value={value ?? ''}
               onChange={e => onChange(e.target.value)} />
      )}
    </div>
  )
}


// ─── Files (prior auth, op notes, path report, etc.) ────────────

function NotesPanel({ surgery }) {
  const qc = useQueryClient()
  const currentUser = useCurrentUser()
  const [draft, setDraft] = useState('')

  const { data: notes = [], isLoading } = useQuery({
    queryKey: ['surgery-notes', surgery.id],
    queryFn: () => api.get(`/surgery/${surgery.id}/notes`).then(r => r.data),
  })

  const add = useMutation({
    mutationFn: (content) => api.post(`/surgery/${surgery.id}/notes`,
                                       { content }).then(r => r.data),
    onSuccess: () => {
      setDraft('')
      qc.invalidateQueries({ queryKey: ['surgery-notes', surgery.id] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Failed to post note'),
  })

  const remove = useMutation({
    mutationFn: (id) => api.delete(`/surgery/${surgery.id}/notes/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery-notes', surgery.id] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Delete failed'),
  })

  function submit() {
    const text = draft.trim()
    if (text) add.mutate(text)
  }

  function formatStamp(iso) {
    if (!iso) return ''
    const d = new Date(iso)
    return d.toLocaleString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit',
    })
  }

  const meEmail = (currentUser?.email || '').toLowerCase()

  return (
    <div className="card mt-3">
      <h2 className="text-sm font-semibold text-gray-800 mb-2">Notes</h2>

      {/* Legacy notes (pre-log field) preserved here so nothing is hidden */}
      {surgery.notes && (
        <div className="mb-2 text-[11px] text-gray-700 italic border-l-2 border-gray-200 pl-2 whitespace-pre-wrap">
          <div className="text-[9px] uppercase tracking-wide text-gray-400 not-italic mb-0.5">
            Legacy note
          </div>
          {surgery.notes}
        </div>
      )}

      <div className="flex flex-col gap-2 mb-3">
        <textarea
          className="input text-[12px] w-full"
          rows={2}
          placeholder="Add a note (timestamped + signed automatically)…"
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') submit()
          }}
        />
        <div className="flex items-center justify-between">
          <span className="text-[10px] text-muted">⌘/Ctrl+Enter to post</span>
          <button className="btn-primary text-xs"
                  onClick={submit}
                  disabled={!draft.trim() || add.isPending}>
            {add.isPending ? 'Posting…' : 'Post note'}
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="text-[11px] text-gray-400 italic">Loading…</div>
      ) : notes.length === 0 ? (
        <div className="text-[11px] text-gray-400 italic">No notes yet.</div>
      ) : (
        <ul className="space-y-2">
          {notes.map(n => {
            const isAuthor = (n.created_by || '').toLowerCase() === meEmail
            return (
              <li key={n.id} className="border-l-2 border-plum-200 pl-2 py-0.5">
                <div className="text-[10px] text-gray-500 flex items-center gap-2">
                  <span className="font-medium text-gray-700">
                    {n.created_by?.split('@')[0] || '—'}
                  </span>
                  <span>·</span>
                  <span>{formatStamp(n.created_at)}</span>
                  {isAuthor && (
                    <button type="button"
                            onClick={() => {
                              if (confirm('Delete this note?')) remove.mutate(n.id)
                            }}
                            className="ml-auto text-[10px] text-red-600 hover:underline">
                      delete
                    </button>
                  )}
                </div>
                <div className="text-[12px] text-gray-800 whitespace-pre-wrap mt-0.5">
                  {n.content}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}


function FilesPanel({ surgery, kindFilter = null, label = 'Files' }) {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['surgery-files', surgery.id],
    queryFn: () => api.get(`/surgery/${surgery.id}/files`).then(r => r.data),
  })
  const allFiles = data?.files || []
  const files = kindFilter ? allFiles.filter(f => f.kind === kindFilter) : allFiles

  const [kind, setKind] = useState(kindFilter || 'prior_auth')
  const [notes, setNotes] = useState('')
  const [file, setFile] = useState(null)
  const [error, setError] = useState(null)
  const [previewFile, setPreviewFile] = useState(null)

  const upload = useMutation({
    mutationFn: () => {
      const fd = new FormData()
      fd.append('file', file)
      return api.post(`/surgery/${surgery.id}/files?kind=${kind}${notes ? `&notes=${encodeURIComponent(notes)}` : ''}`,
                       fd, { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
    },
    onSuccess: () => {
      setFile(null); setNotes(''); setError(null)
      qc.invalidateQueries({ queryKey: ['surgery-files', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
    },
    onError: (e) => setError(e?.response?.data?.detail || e.message),
  })

  return (
    <div className={kindFilter ? '' : 'card !p-3 mt-3'}>
      <div className="flex items-center gap-1.5 mb-2">
        <Upload size={14} className="text-plum-700" />
        <h3 className="text-sm font-semibold text-gray-800">{label}</h3>
        <span className="text-[11px] text-gray-500">({files.length})</span>
      </div>

      {/* Upload form */}
      <div className="border border-gray-200 rounded p-2 mb-2 grid grid-cols-1 md:grid-cols-4 gap-2 items-end">
        {!kindFilter && (
          <div>
            <div className="text-[10px] uppercase text-gray-500">Kind</div>
            <select className="input text-xs" value={kind} onChange={e => setKind(e.target.value)}>
              <option value="prior_auth">Prior Auth Response</option>
              <option value="op_notes">Operative Notes</option>
              <option value="path_report">Pathology Report</option>
              <option value="clearance">Clearance</option>
              <option value="consent">Consent</option>
              <option value="fmla">FMLA Paperwork</option>
              <option value="other">Other</option>
            </select>
          </div>
        )}
        <div>
          <div className="text-[10px] uppercase text-gray-500">File</div>
          <input type="file" className="text-xs"
                 onChange={e => { setFile(e.target.files?.[0] || null); setError(null) }} />
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500">Notes (optional)</div>
          <input className="input text-xs" value={notes} onChange={e => setNotes(e.target.value)} />
        </div>
        <button className="btn-primary text-xs flex items-center gap-1"
                onClick={() => upload.mutate()}
                disabled={!file || upload.isPending}>
          <Upload size={11} /> {upload.isPending ? 'Uploading…' : 'Upload'}
        </button>
      </div>
      {error && <div className="text-xs text-red-600 mb-2">{error}</div>}

      {files.length === 0 ? (
        <div className="text-[11px] text-gray-400 italic">No files uploaded yet.</div>
      ) : (
        <ul className="text-xs divide-y divide-gray-100">
          {files.map(f => {
            const isPdf = (f.mime_type || '').includes('pdf')
              || (f.filename || '').toLowerCase().endsWith('.pdf')
            return (
              <li key={f.id} className="py-1.5 flex items-baseline gap-3">
                {!kindFilter && (
                  <span className="text-[10px] uppercase text-gray-500 w-20 shrink-0">
                    {f.kind.replace(/_/g, ' ')}
                  </span>
                )}
                <a href={`/api${f.download_url.replace(/^\/api/, '')}`}
                   download
                   className="text-plum-700 hover:underline flex-1 truncate">
                  {f.filename}
                </a>
                {isPdf && (
                  <button onClick={() => setPreviewFile(f)}
                          className="text-[10px] text-plum-700 hover:underline shrink-0 flex items-center gap-0.5">
                    <Eye size={10} /> Preview
                  </button>
                )}
                <span className="text-[10px] text-gray-500 shrink-0">
                  {fmt.date(f.uploaded_at?.slice(0, 10))} · {f.uploaded_by?.split('@')[0] || '—'}
                </span>
              </li>
            )
          })}
        </ul>
      )}
      {previewFile && (
        <PdfPreviewDrawer
          apiPath={previewFile.download_url.replace(/^\/api/, '')}
          filename={previewFile.filename}
          title={`Preview · ${previewFile.filename}`}
          onClose={() => setPreviewFile(null)}
        />
      )}
    </div>
  )
}


function Field({ label, children }) {
  return (
    <div>
      <div className="text-[9px] uppercase tracking-wide text-gray-400 mb-0.5">{label}</div>
      <div className="text-gray-800">{children}</div>
    </div>
  )
}


function jumpTo(kind) {
  const el = document.getElementById(`milestone-${kind}`)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}


function DeviceCell({ surgery }) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const { data } = useQuery({
    queryKey: ['larc-assignments-by-surgery', surgery.id],
    queryFn: () => api.get('/larc/assignments', {
      params: { linked_surgery_id: surgery.id, include_completed: true },
    }).then(r => r.data),
    staleTime: 30_000,
  })
  const assignments = data?.assignments || []
  const active = assignments.find(a => !['cancelled', 'billed'].includes(a.status))
  const completed = !active && assignments.length > 0 ? assignments[0] : null

  if (active) {
    return (
      <div className="flex items-center gap-2 flex-wrap">
        <Link to={`/larc/assignments/${active.id}`}
              className="text-plum-700 hover:underline text-sm font-medium">
          {active.device_type_name} #{active.device_our_id || '—'}
        </Link>
        <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${
          active.status === 'inserted' ? 'bg-blue-100 text-blue-700'
            : 'bg-amber-100 text-amber-700'
        }`}>
          {active.status.replace(/_/g, ' ')}
        </span>
        <button className="text-[10px] text-plum-700 hover:underline"
                onClick={() => setPickerOpen(true)}>change</button>
        {pickerOpen && <LarcDevicePickerDrawer surgery={surgery}
                                                  preferred={inferOpDeviceHint(surgery)}
                                                  onClose={() => setPickerOpen(false)} />}
      </div>
    )
  }
  return (
    <div className="flex items-center gap-2 flex-wrap">
      {completed && (
        <Link to={`/larc/assignments/${completed.id}`}
              className="text-gray-500 hover:underline text-xs">
          {completed.device_type_name} (billed)
        </Link>
      )}
      <button className="text-[11px] text-plum-700 hover:underline"
              onClick={() => setPickerOpen(true)}>
        {completed ? '+ Add another' : '+ Pick Device'}
      </button>
      {pickerOpen && <LarcDevicePickerDrawer surgery={surgery}
                                                preferred={inferOpDeviceHint(surgery)}
                                                onClose={() => setPickerOpen(false)} />}
    </div>
  )
}


const CONSENT_LABELS = {
  not_required: 'Not required',
  required:     'Required',
  sent:         'Sent',
  signed:       'Signed',
  declined:     'Declined',
  voided:       'Voided',
  completed:    'Completed',
}

function ConsentStatusCell({ surgery }) {
  const status = surgery.consent_status || 'not_required'
  const envelopes = surgery.consent_envelopes || []
  const isSigned = status === 'signed' || status === 'completed'
    || (envelopes.length > 0 && envelopes.every(e => e.status === 'signed'))
  const isSent = envelopes.length > 0 && !isSigned
  const tone = isSigned ? 'bg-green-100 text-green-700'
    : isSent ? 'bg-blue-100 text-blue-700'
    : status === 'declined' || status === 'voided' ? 'bg-red-100 text-red-700'
    : status === 'required' ? 'bg-amber-100 text-amber-700'
    : 'bg-gray-100 text-gray-600'
  const label = isSigned ? 'Signed' : isSent ? 'Sent' : (CONSENT_LABELS[status] || status)
  return (
    <button onClick={() => jumpTo('consent')}
            className="flex items-center gap-1 text-left group">
      <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${tone}`}>
        {label}
      </span>
      <span className="text-[10px] text-plum-700 group-hover:underline">jump ↓</span>
    </button>
  )
}


const PATHOLOGY_LABELS = {
  none_expected: 'None expected',
  expected:      'Expected',
  received:      'Received',
  not_required:  'Not required',
  completed:     'Completed',
}

function PathologyStatusCell({ surgery }) {
  const status = surgery.pathology_status || 'none_expected'
  const tone = status === 'received' || status === 'completed' ? 'bg-green-100 text-green-700'
    : status === 'expected' ? 'bg-amber-100 text-amber-700'
    : 'bg-gray-100 text-gray-600'
  return (
    <button onClick={() => jumpTo('path_report')}
            className="flex items-center gap-1 text-left group">
      <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${tone}`}>
        {PATHOLOGY_LABELS[status] || status}
      </span>
      <span className="text-[10px] text-plum-700 group-hover:underline">jump ↓</span>
    </button>
  )
}


function BilledStatusCell({ surgery }) {
  const billed = !!surgery.billed_at
  const tone = billed ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'
  const label = billed
    ? (surgery.modmed_claim_number ? `Billed · #${surgery.modmed_claim_number}` : 'Billed')
    : 'Not billed'
  return (
    <button onClick={() => jumpTo('surgery_billed')}
            className="flex items-center gap-1 text-left group">
      <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${tone}`}>
        {label}
      </span>
      <span className="text-[10px] text-plum-700 group-hover:underline">jump ↓</span>
    </button>
  )
}


const FACILITY_SHORT = {
  medstar: 'MedStar',
  crmc:    'Charles Regional',
  office:  'Office',
}


function PickDateLink() {
  const url = `${window.location.origin}/portal/login`
  const [copied, setCopied] = useState(false)
  function copy() {
    navigator.clipboard.writeText(url).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }
  return (
    <div className="mt-1 flex items-center gap-1.5 text-[11px]">
      <span className="text-gray-500">Surgery portal link:</span>
      <a href={url}
         target="_blank"
         rel="noopener noreferrer"
         className="text-plum-700 hover:underline truncate max-w-[280px] font-mono">
        {url}
      </a>
      <button type="button"
              onClick={copy}
              title="Copy to clipboard"
              className="text-plum-700 hover:bg-plum-50 rounded p-0.5">
        <Copy size={11} />
      </button>
      {copied && <span className="text-[10px] text-green-700">copied!</span>}
    </div>
  )
}


/* ───── Picklists hook ───── */

function usePicklists() {
  const { data } = useQuery({
    queryKey: ['surgery-picklists'],
    queryFn: () => api.get('/surgery/picklists').then(r => r.data),
    staleTime: 5 * 60_000,
  })
  return data || { surgeons: [], insurance_companies: [], procedures: [], diagnoses: [] }
}


/* ───── Editable cells for the patient header ───── */

function SurgeonCell({ s, onPatch }) {
  const picks = usePicklists()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(s.surgeon_primary || '')
  if (!editing) {
    return (
      <div className="flex items-baseline gap-2">
        <span>{s.surgeon_primary || <span className="text-gray-400">—</span>}</span>
        <button className="text-[10px] text-plum-700 hover:underline" onClick={() => setEditing(true)}>edit</button>
      </div>
    )
  }
  return (
    <div className="space-y-1">
      <select className="input text-[12px] w-full" value={value} onChange={e => setValue(e.target.value)}>
        <option value="">—</option>
        {picks.surgeons.map(name => <option key={name} value={name}>{name}</option>)}
        {value && !picks.surgeons.includes(value) && <option value={value}>{value}</option>}
      </select>
      <input className="input text-[12px] w-full" placeholder="Other (free-text)"
             value={picks.surgeons.includes(value) ? '' : value}
             onChange={e => setValue(e.target.value)} />
      <div className="flex gap-1">
        <button className="btn-primary text-[10px]"
                onClick={() => { onPatch({ surgeon_primary: value || null }); setEditing(false) }}>Save</button>
        <button className="text-[10px] text-muted hover:underline" onClick={() => setEditing(false)}>Cancel</button>
      </div>
    </div>
  )
}


function InsuranceCell({ s, onPatch }) {
  const picks = usePicklists()
  const [editing, setEditing] = useState(false)
  const [company, setCompany] = useState(s.primary_insurance || '')
  const [memberId, setMemberId] = useState(s.primary_member_id || '')
  const inList = picks.insurance_companies.includes(company)
  const picklistEmpty = (picks.insurance_companies || []).length === 0

  if (!editing) {
    return (
      <div>
        <div className="flex items-baseline gap-2">
          <span>{s.primary_insurance || <span className="text-gray-400">—</span>}</span>
          <button className="text-[10px] text-plum-700 hover:underline" onClick={() => setEditing(true)}>edit</button>
        </div>
        {s.primary_member_id && <div className="text-[10px] text-gray-500">{s.primary_member_id}</div>}
      </div>
    )
  }
  return (
    <div className="space-y-1">
      {picklistEmpty ? (
        <div className="text-[10px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-1.5 py-1">
          Picklist endpoint not reachable — restart the backend, or type the payer name below.
        </div>
      ) : (
        <select className="input text-[12px] w-full"
                value={inList ? company : (company ? 'Other' : '')}
                onChange={e => setCompany(e.target.value === 'Other' ? '' : e.target.value)}>
          <option value="">— pick payer —</option>
          {picks.insurance_companies.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      )}
      {(!inList || picklistEmpty) && (
        <input className="input text-[12px] w-full"
               placeholder={picklistEmpty ? 'Payer name' : 'Type other payer name'}
               autoFocus
               value={company}
               onChange={e => setCompany(e.target.value)} />
      )}
      <input className="input text-[12px] w-full font-mono" placeholder="Member ID"
             value={memberId} onChange={e => setMemberId(e.target.value)} />
      <div className="flex gap-1">
        <button className="btn-primary text-[10px]"
                onClick={() => { onPatch({ primary_insurance: company || null, primary_member_id: memberId || null }); setEditing(false) }}>
          Save
        </button>
        <button className="text-[10px] text-muted hover:underline" onClick={() => setEditing(false)}>Cancel</button>
      </div>
    </div>
  )
}


function ProcedureListEditor({ s, onPatch }) {
  const picks = usePicklists()
  const [editing, setEditing] = useState(false)
  const [items, setItems] = useState(s.procedures || [])
  const [picked, setPicked] = useState('')

  if (!editing) {
    const hasProcs = (s.procedures || []).length > 0
    return (
      <div className="flex items-start gap-2">
        <div className="flex-1">
          {s.is_robotic && <span className="text-blue-700">🤖 </span>}
          {hasProcs
            ? (s.procedures || []).map((p, i) => (
                <div key={i}>
                  {p.description}
                  {p.cpt && <span className="text-gray-500 ml-1">[{p.cpt}]</span>}
                </div>
              ))
            : (
                <button className="text-[11px] text-plum-700 hover:underline"
                        onClick={() => setEditing(true)}>
                  + Add Procedure
                </button>
              )}
        </div>
        {hasProcs && (
          <button className="text-[11px] text-plum-700 hover:underline shrink-0 flex items-center gap-0.5"
                  onClick={() => setEditing(true)} title="Edit procedures">
            <Edit3 size={10} /> Edit
          </button>
        )}
      </div>
    )
  }

  function addFromPicklist() {
    if (!picked) return
    const proc = picks.procedures.find(p => p.cpt === picked)
    if (proc && !items.some(i => i.cpt === proc.cpt)) {
      setItems([...items, proc])
    }
    setPicked('')
  }
  function addCustom() {
    setItems([...items, { cpt: '', description: 'Custom procedure — edit me' }])
  }

  return (
    <div className="space-y-1">
      <ul className="space-y-1">
        {items.map((p, i) => (
          <li key={i} className="flex items-center gap-1">
            <input className="input text-[10px] w-16 font-mono" value={p.cpt || ''}
                   onChange={e => {
                     const next = [...items]; next[i] = { ...p, cpt: e.target.value }; setItems(next)
                   }} placeholder="CPT" />
            <input className="input text-[11px] flex-1" value={p.description || ''}
                   onChange={e => {
                     const next = [...items]; next[i] = { ...p, description: e.target.value }; setItems(next)
                   }} />
            <button className="text-red-600 hover:bg-red-50 rounded p-0.5"
                    onClick={() => setItems(items.filter((_, ix) => ix !== i))} title="Remove">
              <X size={10} />
            </button>
          </li>
        ))}
      </ul>
      <div className="flex items-center gap-1">
        <select className="input text-[10px] flex-1" value={picked} onChange={e => setPicked(e.target.value)}>
          <option value="">+ Add from list…</option>
          {picks.procedures.map(p => (
            <option key={p.cpt} value={p.cpt}>{p.cpt} — {p.description}</option>
          ))}
        </select>
        <button className="text-[10px] btn-secondary px-1.5 py-0.5" onClick={addFromPicklist} disabled={!picked}>Add</button>
      </div>
      <button className="text-[10px] text-plum-700 hover:underline" onClick={addCustom}>+ Custom</button>
      <div className="flex gap-1 pt-1">
        <button className="btn-primary text-[10px]"
                onClick={() => { onPatch({ procedures: items }); setEditing(false) }}>Save</button>
        <button className="text-[10px] text-muted hover:underline" onClick={() => setEditing(false)}>Cancel</button>
      </div>
    </div>
  )
}


function DiagnosisListEditor({ s, onPatch }) {
  const picks = usePicklists()
  const [editing, setEditing] = useState(false)
  const [items, setItems] = useState(s.diagnoses || [])
  const [picked, setPicked] = useState('')

  if (!editing) {
    const hasDx = (s.diagnoses || []).length > 0
    return (
      <div className="flex items-start gap-2">
        <div className="flex-1">
          {hasDx
            ? (s.diagnoses || []).map((d, i) => (
                <div key={i}>
                  {d.description}
                  {d.icd && <span className="text-gray-500 ml-1">{d.icd}</span>}
                </div>
              ))
            : (
                <button className="text-[11px] text-plum-700 hover:underline"
                        onClick={() => setEditing(true)}>
                  + Add Diagnosis
                </button>
              )}
        </div>
        {hasDx && (
          <button className="text-[11px] text-plum-700 hover:underline shrink-0 flex items-center gap-0.5"
                  onClick={() => setEditing(true)} title="Edit diagnoses (multiple OK)">
            <Edit3 size={10} /> Edit
          </button>
        )}
      </div>
    )
  }

  function addFromPicklist() {
    if (!picked) return
    const d = picks.diagnoses.find(x => x.icd === picked)
    if (d && !items.some(i => i.icd === d.icd)) {
      setItems([...items, d])
    }
    setPicked('')
  }
  function addCustom() {
    setItems([...items, { icd: '', description: 'Custom dx — edit me' }])
  }

  return (
    <div className="space-y-1">
      <ul className="space-y-1">
        {items.map((d, i) => (
          <li key={i} className="flex items-center gap-1">
            <input className="input text-[10px] w-20 font-mono" value={d.icd || ''}
                   onChange={e => {
                     const next = [...items]; next[i] = { ...d, icd: e.target.value }; setItems(next)
                   }} placeholder="ICD" />
            <input className="input text-[11px] flex-1" value={d.description || ''}
                   onChange={e => {
                     const next = [...items]; next[i] = { ...d, description: e.target.value }; setItems(next)
                   }} />
            <button className="text-red-600 hover:bg-red-50 rounded p-0.5"
                    onClick={() => setItems(items.filter((_, ix) => ix !== i))} title="Remove">
              <X size={10} />
            </button>
          </li>
        ))}
      </ul>
      <div className="flex items-center gap-1">
        <select className="input text-[10px] flex-1" value={picked} onChange={e => setPicked(e.target.value)}>
          <option value="">+ Add from list…</option>
          {picks.diagnoses.map(d => (
            <option key={d.icd} value={d.icd}>{d.icd} — {d.description}</option>
          ))}
        </select>
        <button className="text-[10px] btn-secondary px-1.5 py-0.5" onClick={addFromPicklist} disabled={!picked}>Add</button>
      </div>
      <button className="text-[10px] text-plum-700 hover:underline" onClick={addCustom}>+ Custom</button>
      <div className="flex gap-1 pt-1">
        <button className="btn-primary text-[10px]"
                onClick={() => { onPatch({ diagnoses: items }); setEditing(false) }}>Save</button>
        <button className="text-[10px] text-muted hover:underline" onClick={() => setEditing(false)}>Cancel</button>
      </div>
    </div>
  )
}


function PreopDateCell({ s, onPatch }) {
  const [editing, setEditing] = useState(false)
  const [date, setDate] = useState(s.preop_date || '')
  if (!editing) {
    return (
      <div className="flex items-baseline gap-2">
        <span>
          {s.preop_date
            ? <span className="inline-flex items-center gap-1">
                {fmt.date(s.preop_date)}
                {s.preop_needs_repeat && (
                  <span className="text-[9px] font-semibold uppercase bg-red-100 text-red-700 px-1 py-0.5 rounded">
                    needs repeat (&gt;180d)
                  </span>
                )}
              </span>
            : <span className="text-gray-400">—</span>}
        </span>
        <button className="text-[10px] text-plum-700 hover:underline" onClick={() => setEditing(true)}>edit</button>
      </div>
    )
  }
  return (
    <div className="space-y-1">
      <input type="date" className="input text-[12px] w-full" value={date} onChange={e => setDate(e.target.value)} />
      <div className="flex gap-1">
        <button className="btn-primary text-[10px]"
                onClick={() => { onPatch({ preop_date: date || null }); setEditing(false) }}>Save</button>
        <button className="text-[10px] text-muted hover:underline" onClick={() => setEditing(false)}>Cancel</button>
      </div>
    </div>
  )
}


const CLASS_DEFAULT_MINUTES = {
  robotic_180: 180,
  robotic_240: 240,
  major:       180,
  minor:       90,
  office:      60,
}

const PROC_CLASS_OPTIONS = [
  { v: '',            l: '—' },
  { v: 'robotic_180', l: 'Robotic 180min' },
  { v: 'robotic_240', l: 'Robotic 240min' },
  { v: 'major',       l: 'Major (CRMC) — 180min' },
  { v: 'minor',       l: 'Minor — 90min' },
  { v: 'office',      l: 'Office — 60min' },
]


function EstimatedTimeCell({ s, onPatch }) {
  const [editing, setEditing] = useState(false)
  const [mins, setMins] = useState(s.estimated_minutes || '')
  const [cls, setCls] = useState(s.procedure_classification || '')

  function changeClass(newCls) {
    setCls(newCls)
    const def = CLASS_DEFAULT_MINUTES[newCls]
    // Auto-fill default mins if blank OR matched the prior class default
    const priorDef = CLASS_DEFAULT_MINUTES[cls]
    if (def && (!mins || Number(mins) === priorDef)) {
      setMins(String(def))
    }
  }

  if (!editing) {
    return (
      <div className="flex items-baseline gap-2">
        <span>
          {s.estimated_minutes
            ? <>{s.estimated_minutes} min{s.procedure_classification && ` · ${s.procedure_classification.replace('_', ' ')}`}</>
            : <span className="text-gray-400">—</span>}
        </span>
        <button className="text-[10px] text-plum-700 hover:underline" onClick={() => setEditing(true)}>edit</button>
      </div>
    )
  }
  return (
    <div className="space-y-1">
      <div className="flex gap-1">
        <input type="number" className="input text-[12px] w-20" value={mins}
               onChange={e => setMins(e.target.value)} placeholder="min" />
        <select className="input text-[12px] flex-1" value={cls}
                onChange={e => changeClass(e.target.value)}>
          {PROC_CLASS_OPTIONS.map(o => <option key={o.v} value={o.v}>{o.l}</option>)}
        </select>
      </div>
      <div className="flex gap-1">
        <button className="btn-primary text-[10px]"
                onClick={() => {
                  onPatch({
                    estimated_minutes: mins === '' ? null : Number(mins),
                    procedure_classification: cls || null,
                  })
                  setEditing(false)
                }}>Save</button>
        <button className="text-[10px] text-muted hover:underline" onClick={() => setEditing(false)}>Cancel</button>
      </div>
    </div>
  )
}


function ClearanceCell({ s, onPatch }) {
  const [editing, setEditing] = useState(false)
  const [required, setRequired] = useState(!!s.clearance_required)
  const [status, setStatus] = useState(s.clearance_status || 'not_required')
  if (!editing) {
    return (
      <div className="flex items-baseline gap-2">
        <span>
          {s.clearance_required
            ? <span className="text-amber-700 capitalize">{(s.clearance_status || 'required').replace(/_/g, ' ')}</span>
            : <span className="text-gray-500">not required</span>}
        </span>
        <button className="text-[10px] text-plum-700 hover:underline" onClick={() => setEditing(true)}>edit</button>
      </div>
    )
  }
  return (
    <div className="space-y-1">
      <label className="flex items-center gap-1 text-[11px]">
        <input type="checkbox" checked={required} onChange={e => setRequired(e.target.checked)} />
        Clearance required
      </label>
      {required && (
        <select className="input text-[12px] w-full" value={status} onChange={e => setStatus(e.target.value)}>
          <option value="required">required</option>
          <option value="request_sent">request sent</option>
          <option value="received">received</option>
          <option value="sent_to_hospital">sent to hospital</option>
          <option value="completed">completed</option>
        </select>
      )}
      <div className="flex gap-1">
        <button className="btn-primary text-[10px]"
                onClick={() => {
                  onPatch({
                    clearance_required: required,
                    clearance_status: required ? status : 'not_required',
                  })
                  setEditing(false)
                }}>Save</button>
        <button className="text-[10px] text-muted hover:underline" onClick={() => setEditing(false)}>Cancel</button>
      </div>
    </div>
  )
}


function AuthSummaryCell({ s }) {
  // Auth is edited inside the Prior Auth milestone card. Surface a summary here.
  return (
    <div>
      <span className="capitalize">{s.auth_status?.replace(/_/g, ' ') || '—'}</span>
      {s.auth_number && <div className="text-[10px] text-gray-500 font-mono">{s.auth_number}</div>}
      <div className="text-[9px] text-gray-400 italic">edit on Prior Auth card below</div>
    </div>
  )
}


function PtResponsibilityCell({ s }) {
  return (
    <div>
      {s.patient_responsibility != null
        ? <span className="font-mono">${s.patient_responsibility}</span>
        : <span className="text-gray-400">TBD</span>}
      {s.amount_paid && Number(s.amount_paid) > 0 && (
        <div className="text-[10px] text-green-700">paid ${s.amount_paid}</div>
      )}
      <div className="text-[9px] text-gray-400 italic">edit on Benefits card below</div>
    </div>
  )
}


function ContactCell({ s, onPatch }) {
  const [editing, setEditing] = useState(false)
  const [chart, setChart] = useState(s.chart_number || '')
  const [phone, setPhone] = useState(s.phone || '')
  const [email, setEmail] = useState(s.email || '')
  if (!editing) {
    return (
      <div>
        <div className="flex items-baseline gap-2">
          <span className="font-mono">{s.chart_number || <span className="text-gray-400">—</span>}</span>
          <button className="text-[10px] text-plum-700 hover:underline" onClick={() => setEditing(true)}>edit</button>
        </div>
        {s.phone && <div className="text-[10px] font-mono text-gray-600">{s.phone}</div>}
        {s.email && <div className="text-[10px] text-gray-600 truncate">{s.email}</div>}
      </div>
    )
  }
  return (
    <div className="space-y-1">
      <input className="input text-[11px] w-full font-mono" placeholder="Chart #"
             value={chart} onChange={e => setChart(e.target.value)} />
      <input className="input text-[11px] w-full font-mono" placeholder="Phone"
             value={phone} onChange={e => setPhone(e.target.value)} />
      <input className="input text-[11px] w-full" placeholder="Email"
             value={email} onChange={e => setEmail(e.target.value)} />
      <div className="flex gap-1">
        <button className="btn-primary text-[10px]"
                onClick={() => {
                  onPatch({ chart_number: chart || null, phone: phone || null, email: email || null })
                  setEditing(false)
                }}>Save</button>
        <button className="text-[10px] text-muted hover:underline" onClick={() => setEditing(false)}>Cancel</button>
      </div>
    </div>
  )
}


function OrderPdfCell({ surgery }) {
  const qc = useQueryClient()
  const [file, setFile] = useState(null)
  const [error, setError] = useState(null)

  const { data } = useQuery({
    queryKey: ['surgery-files', surgery.id],
    queryFn: () => api.get(`/surgery/${surgery.id}/files`).then(r => r.data),
  })
  const orderFiles = (data?.files || []).filter(f => f.kind === 'order')

  const upload = useMutation({
    mutationFn: () => {
      const fd = new FormData()
      fd.append('file', file)
      return api.post(`/surgery/${surgery.id}/files?kind=order`, fd,
                       { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
    },
    onSuccess: () => {
      setFile(null); setError(null)
      qc.invalidateQueries({ queryKey: ['surgery-files', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
    },
    onError: (e) => setError(e?.response?.data?.detail || 'Upload failed'),
  })

  return (
    <div className="space-y-1">
      {orderFiles.length > 0 ? (
        <div className="space-y-0.5">
          {orderFiles.map(f => (
            <a key={f.id}
               href={`/api${f.download_url.replace(/^\/api/, '')}`}
               download
               className="text-plum-700 hover:underline text-[11px] flex items-center gap-1 truncate">
              📎 {f.filename}
            </a>
          ))}
        </div>
      ) : (
        <span className="text-gray-400 text-[11px]">no order on file</span>
      )}
      <label className="text-[10px] text-plum-700 hover:underline cursor-pointer inline-flex items-center gap-1 mt-1">
        <Upload size={10} />
        {file ? file.name.slice(0, 18) + '…' : 'add / replace'}
        <input type="file" accept="application/pdf" className="hidden"
               onChange={e => { setFile(e.target.files?.[0] || null); setError(null) }} />
      </label>
      {file && (
        <button type="button"
                className="ml-1 text-[10px] btn-primary px-2 py-0.5"
                onClick={() => upload.mutate()}
                disabled={upload.isPending}>
          {upload.isPending ? 'Uploading…' : 'Upload'}
        </button>
      )}
      {error && <div className="text-[10px] text-red-700">{error}</div>}
    </div>
  )
}


function AddressCell({ s, onPatch }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState({
    address_street: s.address_street || '',
    address_city: s.address_city || '',
    address_state: s.address_state || '',
    address_zip: s.address_zip || '',
  })
  const hasAddress = s.address_street || s.address_city || s.address_zip
  const formatted = [
    s.address_street,
    [s.address_city, s.address_state, s.address_zip].filter(Boolean).join(', ').replace(', , ', ', '),
  ].filter(Boolean).join(' · ')
  if (!editing) {
    return (
      <div className="flex items-baseline gap-2">
        <span>{hasAddress ? formatted : <span className="text-amber-700 italic">missing — needed for boarding slip</span>}</span>
        <button className="text-[10px] text-plum-700 hover:underline"
                onClick={() => setEditing(true)}>edit</button>
      </div>
    )
  }
  return (
    <div className="space-y-1 mt-1">
      <input className="input text-[12px] w-full" placeholder="Street"
             value={draft.address_street}
             onChange={e => setDraft({ ...draft, address_street: e.target.value })} />
      <div className="grid grid-cols-3 gap-1">
        <input className="input text-[12px]" placeholder="City"
               value={draft.address_city}
               onChange={e => setDraft({ ...draft, address_city: e.target.value })} />
        <input className="input text-[12px]" placeholder="ST" maxLength={2}
               value={draft.address_state}
               onChange={e => setDraft({ ...draft, address_state: e.target.value.toUpperCase() })} />
        <input className="input text-[12px]" placeholder="ZIP"
               value={draft.address_zip}
               onChange={e => setDraft({ ...draft, address_zip: e.target.value })} />
      </div>
      <div className="flex gap-1">
        <button className="btn-primary text-[10px]"
                onClick={() => { onPatch(draft); setEditing(false) }}>Save</button>
        <button className="text-[10px] text-muted hover:underline"
                onClick={() => setEditing(false)}>Cancel</button>
      </div>
    </div>
  )
}


function SurgeryDateCell({ s }) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const hasDate = !!s.scheduled_date

  return (
    <div className="flex items-center gap-2 flex-wrap">
      {hasDate ? (
        <span className="font-medium">
          {fmt.date(s.scheduled_date)}
          {s.scheduled_start_time && ` · ${s.scheduled_start_time.slice(0, 5)}`}
        </span>
      ) : (
        <span className="text-gray-400">not yet picked</span>
      )}
      <button type="button"
              onClick={() => setPickerOpen(true)}
              className="text-[10px] text-plum-700 hover:underline flex items-center gap-0.5">
        {hasDate ? <><RotateCcw size={10}/> reschedule</> : <><Edit3 size={10}/> pick</>}
      </button>
      {(s.reschedule_count || 0) > 0 && (
        <span className="text-[10px] text-amber-700"
              title={`Last by ${s.last_rescheduled_by || 'unknown'}`}>
          rescheduled {s.reschedule_count}×
        </span>
      )}
      {pickerOpen && (
        <SchedulerDatePicker surgery={s} onClose={() => setPickerOpen(false)} />
      )}
    </div>
  )
}


function SchedulerDatePicker({ surgery, onClose }) {
  const qc = useQueryClient()
  const [selected, setSelected] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  const { data, isLoading } = useQuery({
    queryKey: ['surgery-available-slots', surgery.id],
    queryFn: () => api.get(`/surgery/${surgery.id}/available-slots`).then(r => r.data),
    staleTime: 30_000,
  })

  async function pick() {
    if (!selected) return
    setSubmitting(true); setError(null)
    try {
      await api.post(`/surgery/${surgery.id}/pick-date`,
                     { block_day_id: selected.block_day_id })
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
      qc.invalidateQueries({ queryKey: ['surgery-available-slots', surgery.id] })
      onClose()
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not book that date.')
      setSubmitting(false)
    }
  }

  const days = data?.days || []
  const byFacility = {}
  for (const d of days) {
    if (!byFacility[d.facility]) byFacility[d.facility] = []
    byFacility[d.facility].push(d)
  }
  const currentBlockDayId = data?.current_block_day_id

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <div>
            <h2 className="font-serif font-semibold text-ink text-[16px]">
              {surgery.scheduled_date ? 'Reschedule' : 'Pick a date'} — {surgery.patient_name}
            </h2>
            <div className="text-muted text-[11px] mt-0.5">
              {(surgery.procedures || []).map(p => p.description || p).join(', ')}
              {surgery.scheduled_date && (
                <> · currently <strong>{fmt.date(surgery.scheduled_date)}</strong></>
              )}
            </div>
          </div>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        <div className="p-5">
          {isLoading && <div className="text-gray-500 text-sm">Loading available dates…</div>}
          {!isLoading && days.length === 0 && (
            <div className="bg-amber-50 border border-amber-200 text-amber-900 p-3 rounded text-sm">
              No openings in the next 6 months for this surgery's procedure / facility combination.
            </div>
          )}
          {!isLoading && Object.entries(byFacility).map(([fac, list]) => (
            <div key={fac} className="mb-4">
              <div className="text-[11px] uppercase tracking-wide text-gray-500 mb-1">
                {FACILITY_SHORT[fac] || fac}
              </div>
              <div className="grid grid-cols-2 gap-1.5">
                {list.map(d => {
                  const isCurrent = d.block_day_id === currentBlockDayId
                  const isSelected = selected?.block_day_id === d.block_day_id
                  return (
                    <button key={d.block_day_id}
                            type="button"
                            onClick={() => !isCurrent && setSelected(d)}
                            disabled={isCurrent}
                            className={`text-left px-2 py-1.5 rounded border text-[12px]
                              ${isSelected ? 'border-plum-600 bg-plum-50' :
                                isCurrent  ? 'border-gray-200 bg-gray-100 opacity-60 cursor-not-allowed' :
                                'border-border-subtle hover:border-plum-300'}`}>
                      <div className="font-medium">
                        {d.weekday}, {fmt.date(d.block_date)}
                      </div>
                      <div className="text-[10px] text-gray-600">
                        {d.proposed_start_time} · {d.duration_minutes}min
                        {d.cases_already_booked > 0 && ` · ${d.cases_already_booked} booked`}
                        {isCurrent && ' · current'}
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>
          ))}

          {error && (
            <div className="bg-red-50 border border-red-200 text-red-800 text-sm p-2 rounded mt-3">
              {error}
            </div>
          )}
        </div>

        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
          <button type="button"
                  onClick={onClose}
                  className="text-sm text-muted hover:underline">
            Cancel
          </button>
          <button type="button"
                  onClick={pick}
                  disabled={!selected || submitting}
                  className="btn-primary text-sm">
            {submitting ? 'Booking…' :
              selected
                ? `${surgery.scheduled_date ? 'Reschedule' : 'Book'} to ${fmt.date(selected.block_date)}`
                : 'Pick a date above'}
          </button>
        </div>
      </div>
    </div>
  )
}


const FACILITY_OPTIONS = [
  { v: 'medstar', l: 'MedStar' },
  { v: 'crmc',    l: 'CRMC' },
  { v: 'office',  l: 'Office' },
]


function FacilityCell({ s, onPatch }) {
  const { labelOf } = useFacilities()
  const [editing, setEditing] = useState(false)
  const [selected, setSelected] = useState(s.selected_facility || '')
  const [eligible, setEligible] = useState(new Set(s.eligible_facilities || []))

  if (!editing) {
    return (
      <div>
        <div className="flex items-baseline gap-2">
          {s.selected_facility ? (
            <span>{labelOf(s.selected_facility)}</span>
          ) : (s.eligible_facilities || []).length > 1 ? (
            <span className="text-amber-700">
              {s.eligible_facilities.map(f => labelOf(f)).join(' OR ')}
            </span>
          ) : (s.eligible_facilities || []).length === 1 ? (
            <span>{labelOf(s.eligible_facilities[0])}</span>
          ) : (
            <span className="text-gray-400">—</span>
          )}
          <button className="text-[10px] text-plum-700 hover:underline"
                  onClick={() => setEditing(true)}>edit</button>
        </div>
        {!s.selected_facility && (s.eligible_facilities || []).length > 1 && (
          <div className="text-[10px] text-amber-600 italic">patient to choose</div>
        )}
      </div>
    )
  }

  function toggleEligible(v) {
    const next = new Set(eligible)
    if (next.has(v)) next.delete(v); else next.add(v)
    setEligible(next)
  }

  return (
    <div className="space-y-1">
      <div className="text-[10px] uppercase tracking-wide text-gray-500">Selected facility</div>
      <select className="input text-[12px] w-full" value={selected}
              onChange={e => setSelected(e.target.value)}>
        <option value="">(not yet selected)</option>
        {FACILITY_OPTIONS.map(f => <option key={f.v} value={f.v}>{f.l}</option>)}
      </select>
      <div className="text-[10px] uppercase tracking-wide text-gray-500 mt-1">Eligible facilities</div>
      <div className="flex flex-wrap gap-1">
        {FACILITY_OPTIONS.map(f => {
          const on = eligible.has(f.v)
          return (
            <button key={f.v} type="button"
                    onClick={() => toggleEligible(f.v)}
                    className={`text-[10px] px-1.5 py-0.5 rounded border ${
                      on ? 'bg-plum-100 border-plum-300 text-plum-800'
                         : 'border-gray-200 text-gray-600 hover:bg-gray-50'
                    }`}>
              {f.l}
            </button>
          )
        })}
      </div>
      <div className="flex gap-1 pt-1">
        <button className="btn-primary text-[10px]"
                onClick={() => {
                  const elig = [...eligible]
                  // Selected must be in eligibles if set
                  if (selected && !elig.includes(selected)) elig.push(selected)
                  onPatch({
                    selected_facility: selected || null,
                    eligible_facilities: elig,
                  })
                  setEditing(false)
                }}>Save</button>
        <button className="text-[10px] text-muted hover:underline"
                onClick={() => setEditing(false)}>Cancel</button>
      </div>
    </div>
  )
}


function Tip({ text, children }) {
  return (
    <span className="relative group inline-flex">
      {children}
      <span className="pointer-events-none absolute z-30 bottom-full left-1/2 -translate-x-1/2 mb-1.5
                       opacity-0 group-hover:opacity-100 transition-opacity duration-100
                       bg-gray-900 text-white text-[10px] px-2 py-1 rounded shadow-lg
                       w-44 whitespace-normal text-center leading-snug">
        {text}
      </span>
    </span>
  )
}


const MILESTONE_TITLE_OVERRIDE = {
  consent: 'Consent',
  benefits_determined: 'Benefits Determination',
}


function MilestoneRow({ m, surgery }) {
  const qc = useQueryClient()
  const [showNotes, setShowNotes] = useState(false)
  const [notes, setNotes] = useState(m.notes || '')

  const action = useMutation({
    mutationFn: ({ act, body }) =>
      api.post(`/surgery/${surgery.id}/milestones/${m.kind}/${act}`, body || {}).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
      setShowNotes(false)
    },
  })

  const isDone = m.status === 'done'
  const isInProgress = m.status === 'in_progress'
  const isOpen = !['done', 'skipped', 'not_applicable'].includes(m.status)

  return (
    <li className={`p-2 rounded border ${
      isDone ? 'bg-green-50/40 border-green-100'
      : isInProgress ? 'bg-amber-50/30 border-amber-100'
      : m.status === 'not_applicable' ? 'bg-gray-50 border-gray-100 opacity-60'
      : 'bg-white border-gray-100'
    }`}>
      <div className="flex items-start gap-3">
        <div className="mt-0.5 shrink-0">{MILESTONE_ICON[m.status] || MILESTONE_ICON.pending}</div>
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className={`text-sm font-medium ${m.status === 'not_applicable' ? 'line-through' : ''}`}>
              {MILESTONE_TITLE_OVERRIDE[m.kind] || m.title}
            </span>
            <span className="text-[10px] text-gray-500 capitalize">{m.status.replace(/_/g, ' ')}</span>
            {m.expected_duration_days && (
              <span className="text-[10px] text-gray-400">expected within {m.expected_duration_days}d</span>
            )}
          </div>
          {m.completed_at && (
            <div className="text-[10px] text-gray-500 mt-0.5">
              {isDone ? '✓ Completed' : 'Updated'} {fmt.date(m.completed_at?.slice(0, 10))}
              {m.completed_by && ` by ${m.completed_by.replace('system:', '')}`}
            </div>
          )}
          {m.notes && !showNotes && (
            <div className="text-[11px] text-gray-700 bg-amber-50/50 px-2 py-1 rounded mt-1">{m.notes}</div>
          )}

          {showNotes && (
            <div className="mt-2 space-y-1">
              <textarea
                className="input text-xs w-full"
                rows={2}
                placeholder="Notes for this milestone (optional)"
                value={notes}
                onChange={e => setNotes(e.target.value)}
              />
              <div className="flex gap-1">
                <button className="btn-primary text-[11px]"
                        onClick={() => action.mutate({ act: 'done', body: { notes } })}
                        disabled={action.isPending}>
                  Save & mark done
                </button>
                <button className="btn-secondary text-[11px]"
                        onClick={() => setShowNotes(false)}>
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>

        {!showNotes && (
          <div className="shrink-0 flex items-center gap-1">
            {isOpen && !isInProgress && (
              <Tip text="Start working on this — mark in progress">
                <button aria-label="Mark in progress"
                        className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:border-amber-300 text-gray-600"
                        onClick={() => action.mutate({ act: 'start' })}
                        disabled={action.isPending}>
                  <Clock size={11} />
                </button>
              </Tip>
            )}
            {isOpen && (
              <>
                <Tip text="Mark this milestone complete">
                  <button aria-label="Mark done"
                          className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:border-green-300 hover:bg-green-50 text-green-700 flex items-center gap-1"
                          onClick={() => action.mutate({ act: 'done' })}
                          disabled={action.isPending}>
                    <Check size={11} /> Done
                  </button>
                </Tip>
                <Tip text="Add a note, then mark this milestone done">
                  <button aria-label="Add notes & mark done"
                          className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:bg-gray-50 text-gray-600"
                          onClick={() => setShowNotes(true)}>
                    <Edit3 size={11} />
                  </button>
                </Tip>
                <Tip text="Skip this step — won't be done (provide reason in notes)">
                  <button aria-label="Skip"
                          className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:bg-gray-50 text-gray-500"
                          onClick={() => action.mutate({ act: 'skip' })}
                          disabled={action.isPending}>
                    <SkipForward size={11} />
                  </button>
                </Tip>
                <Tip text="Not applicable for this patient (e.g. no clearance needed, no device)">
                  <button aria-label="Not applicable"
                          className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:bg-gray-50 text-gray-500"
                          onClick={() => action.mutate({ act: 'not_applicable' })}
                          disabled={action.isPending}>
                    N/A
                  </button>
                </Tip>
              </>
            )}
            {!isOpen && (
              <Tip text="Reopen this milestone — moves it back to in-progress">
                <button aria-label="Reopen"
                        className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:bg-gray-50 text-gray-500 flex items-center gap-1"
                        onClick={() => action.mutate({ act: 'reopen' })}
                        disabled={action.isPending}>
                  <RotateCcw size={11} /> Reopen
                </button>
              </Tip>
            )}
          </div>
        )}
      </div>
    </li>
  )
}


/* MilestoneCard wraps the per-milestone status row in its own card and
   renders the milestone-specific tool inline (calculator, drafter,
   uploader, etc.). Returns null content for milestones that need no tool. */
function MilestoneCard({ m, surgery, flat = false }) {
  const body = milestoneInlineContent(m, surgery)
  // Completed (done / skipped / not_applicable) milestones collapse by default;
  // open milestones stay expanded. User can override with the chevron.
  const isResolved = ['done', 'skipped', 'not_applicable'].includes(m.status)
  const [open, setOpen] = useState(!isResolved)
  const wrapClass = flat
    ? 'scroll-mt-16'
    : 'card !p-3 scroll-mt-16'
  return (
    <div id={`milestone-${m.kind}`} className={wrapClass}>
      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0">
          {/* Header (status icon, title, action buttons) — reuses MilestoneRow */}
          <ol className="space-y-2"><MilestoneRow m={m} surgery={surgery} /></ol>
        </div>
        {body && (
          <button type="button"
                  onClick={() => setOpen(v => !v)}
                  className="shrink-0 text-gray-400 hover:text-plum-700 p-1 -mt-1"
                  title={open ? 'Collapse' : 'Expand'}>
            {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        )}
      </div>
      {body && open && (
        <div className="mt-3 border-t border-gray-100 pt-3">{body}</div>
      )}
    </div>
  )
}


// Group color palette. Each group gets a faint tinted bg + matching left
// accent border so cards are visually distinct without compromising
// readability. Divider lines between sub-items use a darker shade of the
// same hue so they stay visible on the tinted background.
const SECTION_TONES = {
  emerald: { bg: 'bg-emerald-50/60',  accent: 'border-l-4 border-l-emerald-400',  divide: 'border-emerald-200' },
  sky:     { bg: 'bg-sky-50/60',      accent: 'border-l-4 border-l-sky-400',      divide: 'border-sky-200' },
  amber:   { bg: 'bg-amber-50/60',    accent: 'border-l-4 border-l-amber-400',    divide: 'border-amber-200' },
  plum:    { bg: 'bg-plum-50/70',     accent: 'border-l-4 border-l-plum-400',     divide: 'border-plum-300' },
  slate:   { bg: 'bg-slate-100/70',   accent: 'border-l-4 border-l-slate-400',    divide: 'border-slate-200' },
  teal:    { bg: 'bg-teal-50/60',     accent: 'border-l-4 border-l-teal-400',     divide: 'border-teal-200' },
}

function SurgerySection({ title, anchor, tone = 'slate', headerRight, children }) {
  const kids = (Array.isArray(children) ? children : [children]).filter(Boolean)
  if (kids.length === 0) return null
  const t = SECTION_TONES[tone] || SECTION_TONES.slate
  return (
    <section id={anchor} className={`card mb-4 scroll-mt-16 ${t.bg} ${t.accent}`}>
      <div className={`flex items-center gap-2 mb-3 pb-2 border-b ${t.divide}`}>
        <h2 className="text-base font-semibold text-gray-800 flex-1">{title}</h2>
        {headerRight}
      </div>
      {kids.map((child, i) => (
        <div key={i} className={i === 0 ? '' : `border-t ${t.divide} mt-3 pt-3`}>
          {child}
        </div>
      ))}
    </section>
  )
}


function GroupedSurgeryBody({ surgery, milestones }) {
  const byKind = Object.fromEntries(milestones.map(m => [m.kind, m]))
  const ms = (kind) => byKind[kind]
    ? <MilestoneCard m={byKind[kind]} surgery={surgery} flat />
    : null

  return (
    <>
      <SurgerySection title="Benefits & Payments" anchor="group-benefits-payments" tone="emerald"
                      headerRight={
                        <FeeScheduleButton
                          surgeryId={surgery.id}
                          onApplied={() => {/* surgery query auto-invalidates via the apply endpoint */}} />
                      }>
        {ms('benefits_determined')}
        <PriorAuthCardBody surgery={surgery} />
        <PaymentsSection surgery={surgery} flat />
      </SurgerySection>

      <SurgerySection title="Appointments" anchor="group-appointments" tone="sky">
        <PatientPicksDateBody surgery={surgery} />
        <PostOpApptsCardBody surgery={surgery} />
      </SurgerySection>

      <SurgerySection title="Pre-Surgery Coordination" anchor="group-pre-surgery" tone="amber">
        <ConsentPanel surgery={surgery} />
        <ClearanceCardBody surgery={surgery} />
        <AssistantSurgeonCardBody surgery={surgery} />
        <ErrorBoundary label="Hospital Posting">
          <SurgeryConfirmedBody surgery={surgery} />
        </ErrorBoundary>
        <LabsCardBody surgery={surgery} />
      </SurgerySection>

      <SurgerySection title="Communication & Messaging" anchor="group-communication" tone="plum">
        <PortalAccessPanel surgery={surgery} />
        <KlaraPanel surgery={surgery} />
        <MessagesSection sid={surgery.id} flat />
        <PatientEmailsSection surgery={surgery} flat />
        <PatientSmsSection surgery={surgery} flat />
      </SurgerySection>

      <SurgerySection title="Post Surgery" anchor="group-post-surgery" tone="slate">
        {byKind['post_op_call'] && (
          <PostOpCallCardBody surgery={surgery} milestone={byKind['post_op_call']} />
        )}
        <FilesPanel surgery={surgery} kindFilter="op_notes" label="Operative Report" />
        <FilesPanel surgery={surgery} kindFilter="path_report" label="Pathology Report" />
        <SurgeryBilledCardBody surgery={surgery} />
      </SurgerySection>

      <SurgerySection title="Devices" anchor="group-devices" tone="teal">
        <RequestDevicePanel surgery={surgery} />
        <LarcDevicePickerCard surgery={surgery} flat />
      </SurgerySection>
    </>
  )
}

function milestoneInlineContent(m, surgery) {
  switch (m.kind) {
    case 'benefits_determined':         return <BenefitsPanel surgery={surgery} />
    case 'prior_auth':                  return <PriorAuthCardBody surgery={surgery} />
    case 'klara_scheduling':            return <KlaraPanel surgery={surgery} />
    case 'patient_picks_date':          return <PatientPicksDateBody surgery={surgery} />
    case 'post_op_appts_scheduled':     return <PostOpApptsCardBody surgery={surgery} />
    case 'assistant_surgeon':           return <AssistantSurgeonCardBody surgery={surgery} />
    case 'consent':                     return <ConsentPanel surgery={surgery} />
    case 'surgery_confirmed_hospital':  return <SurgeryConfirmedBody surgery={surgery} />
    case 'labs_to_hospital':            return <LabsCardBody surgery={surgery} />
    case 'op_notes':                    return <FilesPanel surgery={surgery} kindFilter="op_notes"
                                                            label="Operative Report" />
    case 'path_report':                 return <FilesPanel surgery={surgery} kindFilter="path_report"
                                                            label="Pathology Report" />
    case 'post_op_call':                return <PostOpCallCardBody surgery={surgery} milestone={m} />
    case 'surgery_billed':              return <SurgeryBilledCardBody surgery={surgery} />
    default:                            return null
  }
}


// ─── Per-milestone inline card bodies ─────────────────────────────


function PatientPicksDateBody({ surgery }) {
  const qc = useQueryClient()
  const currentUser = useCurrentUser()
  const canEditSchedule = currentUser.has('surgery:work')
  const modmedDone = !!surgery.scheduled_in_modmed_at
  const medsDone = !!surgery.office_meds_pickup_confirmed_at
  const isOffice = surgery.selected_facility === 'office'
  const [showScheduleModal, setShowScheduleModal] = useState(false)

  const { data: tpl } = useQuery({
    queryKey: ['surgery-templates'],
    queryFn: () => api.get('/surgery/picklists/procedure-templates').then(r => r.data.templates),
    staleTime: 60_000,
  })

  const toggleModmed = useMutation({
    mutationFn: (confirmed) => api.post(`/surgery/${surgery.id}/modmed-scheduled`,
                                          { confirmed }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery', surgery.id] }),
  })
  const toggleMeds = useMutation({
    mutationFn: (confirmed) => api.post(`/surgery/${surgery.id}/office-meds-pickup`,
                                          { confirmed }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery', surgery.id] }),
  })

  if (!surgery.scheduled_date) {
    return (
      <div className="space-y-2 text-[12px]">
        <div className="text-amber-700">
          Patient can pick a surgery date and time via Surgery Portal.
        </div>
        {canEditSchedule && (
          <button
            type="button"
            className="btn-primary text-[11px] flex items-center gap-1"
            onClick={() => setShowScheduleModal(true)}>
            Schedule for patient
          </button>
        )}
        {showScheduleModal && (
          <ScheduleForPatientModal
            surgery={surgery}
            templates={tpl || []}
            onClose={() => setShowScheduleModal(false)}
            onSaved={() => {
              qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
              qc.invalidateQueries({ queryKey: ['surgery-list'] })
              qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
              setShowScheduleModal(false)
            }}
          />
        )}
      </div>
    )
  }

  return (
    <div className="space-y-2 text-[12px] text-gray-700">
      <div>
        <strong>Picked:</strong> {fmt.date(surgery.scheduled_date)}
        {surgery.scheduled_start_time && ` · ${surgery.scheduled_start_time.slice(0,5)}`}
        {surgery.selected_facility && ` · ${FACILITY_SHORT[surgery.selected_facility] || surgery.selected_facility}`}
        {canEditSchedule && surgery.booked_slot_id && surgery.booked_duration_minutes != null && (
          <span className="ml-2">
            <SlotDurationEdit
              slotId={surgery.booked_slot_id}
              currentMinutes={surgery.booked_duration_minutes}
              onSaved={() => qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })}
            />
          </span>
        )}
      </div>
      {surgery.reschedule_count > 0 && (
        <div className="text-amber-700 text-[11px]">
          Rescheduled {surgery.reschedule_count}× (last by {surgery.last_rescheduled_by || '—'})
        </div>
      )}

      <label className="flex items-start gap-2 text-[12px] cursor-pointer">
        <input type="checkbox"
               className="mt-0.5"
               checked={modmedDone}
               onChange={(e) => toggleModmed.mutate(e.target.checked)} />
        <div>
          <div className="font-medium text-gray-800">Added appointment to ModMed schedule</div>
          {modmedDone && (
            <div className="text-[11px] text-green-700">
              ✓ {fmt.date(surgery.scheduled_in_modmed_at?.slice(0,10))}
              {surgery.scheduled_in_modmed_by && ` by ${surgery.scheduled_in_modmed_by.split('@')[0]}`}
            </div>
          )}
        </div>
      </label>

      {isOffice && (
        <label className="flex items-start gap-2 text-[12px] cursor-pointer bg-amber-50 border border-amber-200 rounded px-2 py-1.5">
          <input type="checkbox"
                 className="mt-0.5"
                 checked={medsDone}
                 onChange={(e) => toggleMeds.mutate(e.target.checked)} />
          <div>
            <div className="font-medium text-amber-900">
              Remind patient to pick up their procedure meds
            </div>
            <div className="text-[10px] text-amber-800">
              Office-procedure patients must have their meds in-hand before the visit.
            </div>
            {medsDone && (
              <div className="text-[11px] text-green-700 mt-0.5">
                ✓ Confirmed {fmt.date(surgery.office_meds_pickup_confirmed_at?.slice(0,10))}
                {surgery.office_meds_pickup_confirmed_by && ` by ${surgery.office_meds_pickup_confirmed_by.split('@')[0]}`}
              </div>
            )}
          </div>
        </label>
      )}

      <div className="text-[11px] text-gray-500">
        Use the "reschedule" link next to Surgery date above to change the date.
      </div>
    </div>
  )
}


function SurgeryConfirmedBody({ surgery }) {
  return <BoardingSlipPanel surgery={surgery} />
}


function PostOpApptsCardBody({ surgery }) {
  const qc = useQueryClient()
  // Default to at least 2 visit slots; if the procedure rules say more, use those.
  // If only 1 rule matched, still allow staff to add a second appt.
  const ruleVisits = surgery.post_op_schedule_required || []
  const visits = ruleVisits.length >= 2
    ? ruleVisits
    : ruleVisits.length === 1
        ? [...ruleVisits, { label: 'Additional follow-up', days_post_op: null,
                            suggested_location: 'telehealth' }]
        : [{ label: 'Follow-up appointment', days_post_op: null,
             suggested_location: 'office' },
           { label: 'Additional follow-up', days_post_op: null,
             suggested_location: 'telehealth' }]

  // Suggested dates = surgery date + days_post_op. Computed up-front so we
  // can pre-populate the date inputs with the suggested value when nothing
  // has been saved yet — coordinator just clicks Save.
  const suggested = (() => {
    if (!surgery.scheduled_date) return {}
    const base = new Date(surgery.scheduled_date + 'T00:00:00')
    const out = {}
    visits.forEach((v, i) => {
      if (v.days_post_op == null) return
      const d = new Date(base)
      d.setDate(d.getDate() + v.days_post_op)
      out[i] = d.toISOString().slice(0, 10)
    })
    return out
  })()

  const [first, setFirst] = useState(
    surgery.post_op_appt_date || suggested[0] || '')
  const [second, setSecond] = useState(
    surgery.post_op_appt_2nd_date || suggested[1] || '')
  const [firstLoc, setFirstLoc] = useState(
    surgery.post_op_appt_location || visits[0]?.suggested_location || 'office')
  const [secondLoc, setSecondLoc] = useState(
    surgery.post_op_appt_2nd_location || visits[1]?.suggested_location || 'telehealth')

  const save = useMutation({
    mutationFn: () => {
      const firstLocked  = !!visits[0]?.location_locked
      const secondLocked = !!visits[1]?.location_locked
      return api.post(`/surgery/${surgery.id}/post-op-appts`,
                       { first_date: first || null,
                         second_date: second || null,
                         first_location:  first  ? (firstLocked  ? 'office' : firstLoc)  : null,
                         second_location: second ? (secondLocked ? 'office' : secondLoc) : null })
                 .then(r => r.data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  if (!surgery.scheduled_date) {
    return (
      <div className="space-y-2 text-[12px] text-gray-700">
        <div className="text-amber-700">
          Pick a surgery date first — post-op appointment dates will auto-fill
          from it once it's set.
        </div>
        {ruleVisits.length > 0 ? (
          <div>
            <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">
              Practice rule for this procedure
            </div>
            <ul className="space-y-0.5">
              {ruleVisits.map((v, i) => (
                <li key={i} className="flex items-center gap-2">
                  <span className="font-medium">{v.label}</span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                    v.suggested_location === 'telehealth'
                      ? 'bg-blue-50 text-blue-700'
                      : 'bg-amber-50 text-amber-800'
                  }`}>
                    {v.suggested_location === 'telehealth' ? 'Telehealth' : 'Office'}
                    {v.location_locked && ' (required)'}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <div className="text-[11px] text-gray-500 italic">
            No specific schedule rule matched this surgery's procedures. Up to 2
            follow-up appointments can be added manually once the surgery date is set.
          </div>
        )}
      </div>
    )
  }

  const savedRows = []
  if (surgery.post_op_appt_date) savedRows.push({
    label: visits[0]?.label || 'Follow-up appointment',
    date: surgery.post_op_appt_date,
    location: surgery.post_op_appt_location })
  if (surgery.post_op_appt_2nd_date) savedRows.push({
    label: visits[1]?.label || 'Additional follow-up',
    date: surgery.post_op_appt_2nd_date,
    location: surgery.post_op_appt_2nd_location })

  const allFilled = visits.length === 1
    ? !!first
    : !!first && !!second

  function applySuggested() {
    if (visits[0] && suggested[0]) setFirst(suggested[0])
    if (visits[1] && suggested[1]) setSecond(suggested[1])
  }

  function LocationToggle({ value, onChange, suggestedLoc, locked }) {
    return (
      <div className="flex items-center gap-1 text-[11px]">
        <button type="button"
                onClick={() => onChange('office')}
                className={`px-1.5 py-0.5 rounded border ${
                  value === 'office'
                    ? 'bg-plum-100 border-plum-300 text-plum-800'
                    : 'bg-white border-gray-200 text-gray-600 hover:border-plum-200'
                }`}
                title={suggestedLoc === 'office' ? 'Suggested' : ''}>
          Office{suggestedLoc === 'office' && <span className="text-[9px]"> ★</span>}
        </button>
        <button type="button"
                disabled={locked}
                onClick={() => !locked && onChange('telehealth')}
                className={`px-1.5 py-0.5 rounded border ${
                  locked
                    ? 'bg-gray-50 border-gray-200 text-gray-300 cursor-not-allowed line-through'
                    : value === 'telehealth'
                        ? 'bg-plum-100 border-plum-300 text-plum-800'
                        : 'bg-white border-gray-200 text-gray-600 hover:border-plum-200'
                }`}
                title={locked ? 'Required in-person' : (suggestedLoc === 'telehealth' ? 'Suggested' : '')}>
          Telehealth{!locked && suggestedLoc === 'telehealth' && <span className="text-[9px]"> ★</span>}
        </button>
        {locked && (
          <span className="text-[10px] text-amber-700 italic">in-person required</span>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-3 text-[12px] text-gray-700">
      {/* Saved summary */}
      {savedRows.length > 0 && (
        <div className="bg-gray-50 border border-gray-200 rounded p-2 space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-gray-500">Scheduled</div>
          {savedRows.map((r, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="text-[11px] text-gray-700 w-32 shrink-0">{r.label}:</span>
              <span className="text-[12px] font-medium">{fmt.date(r.date)}</span>
              {r.location && (
                <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                  r.location === 'telehealth'
                    ? 'bg-blue-50 text-blue-700'
                    : 'bg-amber-50 text-amber-800'
                }`}>
                  {r.location === 'telehealth' ? 'Telehealth' : 'Office'}
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="text-[11px] text-gray-600">
        {ruleVisits.length === 0
          ? <>No specific schedule rule matched. Add up to 2 follow-up appointments below.</>
          : <>Practice rule: {ruleVisits.map(v => v.label).join(' + ')}.
              {' '}Both dates must be entered to mark this milestone done.</>}
      </div>

      <div className="space-y-2">
        {visits.map((v, i) => {
          const value = i === 0 ? first : second
          const setValue = i === 0 ? setFirst : setSecond
          const loc = i === 0 ? firstLoc : secondLoc
          const setLoc = i === 0 ? setFirstLoc : setSecondLoc
          return (
            <div key={i} className="border border-gray-100 rounded p-1.5 space-y-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[11px] text-gray-700 w-32 shrink-0">{v.label}:</span>
                <input type="date" className="input text-[12px]"
                       value={value}
                       onChange={e => setValue(e.target.value)} />
                {suggested[i] && (
                  <button type="button"
                          className="text-[10px] text-plum-700 hover:underline"
                          onClick={() => setValue(suggested[i])}
                          title={`Suggested: surgery date + ${v.days_post_op} days`}>
                    use {fmt.date(suggested[i])}
                  </button>
                )}
              </div>
              <div className="flex items-center gap-2 pl-32">
                <span className="text-[10px] text-gray-500">Visit type:</span>
                <LocationToggle value={v.location_locked ? 'office' : loc}
                                onChange={setLoc}
                                suggestedLoc={v.suggested_location}
                                locked={!!v.location_locked} />
              </div>
            </div>
          )
        })}
      </div>

      <div className="flex gap-2 pt-1">
        <button className="btn-primary text-[11px]"
                onClick={() => save.mutate()}
                disabled={save.isPending}>
          {save.isPending
            ? 'Saving…'
            : allFilled ? 'Save & mark done' : 'Save'}
        </button>
        {Object.keys(suggested).length > 0 && (
          <button type="button"
                  className="btn-secondary text-[11px]"
                  onClick={applySuggested}>
            Use suggested dates
          </button>
        )}
      </div>
    </div>
  )
}


function ClearanceCardBody({ surgery }) {
  const qc = useQueryClient()
  const [required, setRequired] = useState(!!surgery.clearance_required)
  const [status, setStatus] = useState(surgery.clearance_status || 'not_required')
  const [cardioName, setCardioName] = useState(surgery.cardiologist_name || '')
  const [cardioPhone, setCardioPhone] = useState(surgery.cardiologist_phone || '')
  const [cardioFax, setCardioFax] = useState(surgery.cardiologist_fax || '')

  const patch = useMutation({
    mutationFn: (body) => api.patch(`/surgery/${surgery.id}`, body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
    },
  })

  const STATUS_TONE = {
    not_required: 'bg-gray-100 text-gray-600',
    required:     'bg-amber-100 text-amber-700',
    request_sent: 'bg-blue-100 text-blue-700',
    received:     'bg-green-100 text-green-700',
    sent_to_hospital: 'bg-green-100 text-green-700',
    completed:    'bg-green-100 text-green-700',
  }
  const tone = STATUS_TONE[status] || 'bg-gray-100 text-gray-600'

  return (
    <div id="milestone-clearance" className="scroll-mt-16 space-y-3 text-[12px]">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-1.5">
          <HeartPulse size={14} className="text-rose-600" />
          Cardiac / Anesthesia Clearance
        </h3>
        {required && (
          <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${tone}`}>
            {status.replace(/_/g, ' ')}
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <label className="flex items-center gap-2 text-[11px]">
          <input type="checkbox" checked={required}
                 onChange={e => setRequired(e.target.checked)} />
          Clearance required
        </label>
        {required && (
          <select className="input text-[12px]"
                  value={status} onChange={e => setStatus(e.target.value)}>
            <option value="required">required</option>
            <option value="request_sent">request sent</option>
            <option value="received">received</option>
            <option value="sent_to_hospital">sent to hospital</option>
            <option value="completed">completed</option>
          </select>
        )}
      </div>

      {required && (
        <div className="grid grid-cols-3 gap-2">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-gray-500">Cardiologist</div>
            <input className="input text-[12px] w-full" value={cardioName}
                   onChange={e => setCardioName(e.target.value)} />
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-gray-500">Phone</div>
            <input className="input text-[12px] w-full font-mono" value={cardioPhone}
                   onChange={e => setCardioPhone(e.target.value)}
                   placeholder="240-xxx-xxxx" />
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-gray-500">Fax</div>
            <input className="input text-[12px] w-full font-mono" value={cardioFax}
                   onChange={e => setCardioFax(e.target.value)}
                   placeholder="240-xxx-xxxx" />
          </div>
        </div>
      )}

      <button className="btn-primary text-[11px]"
              onClick={() => patch.mutate({
                clearance_required: required,
                clearance_status:   required ? status : 'not_required',
                cardiologist_name:  cardioName  || null,
                cardiologist_phone: cardioPhone || null,
                cardiologist_fax:   cardioFax   || null,
              })}
              disabled={patch.isPending}>
        {patch.isPending ? 'Saving…' : 'Save'}
      </button>

      {required && (
        <ClearanceFormGenerator surgery={surgery} />
      )}
    </div>
  )
}


function ClearanceFormGenerator({ surgery }) {
  const qc = useQueryClient()
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [previewing, setPreviewing] = useState(false)

  const generate = useMutation({
    mutationFn: () => api.post(`/surgery/${surgery.id}/clearance/generate-form`).then(r => r.data),
    onSuccess: (d) => {
      setResult(d); setError(null)
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery-files', surgery.id] })
    },
    onError: (e) => setError(e?.response?.data?.detail || e.message),
  })

  return (
    <div className="border border-amber-200 bg-white rounded p-2 space-y-1.5">
      <div className="flex items-center gap-1.5">
        <FileText size={12} className="text-plum-700" />
        <h4 className="text-[12px] font-semibold text-gray-800">Clearance Form</h4>
      </div>
      <p className="text-[11px] text-gray-600">
        Generate a fillable clearance form prefilled with patient + surgery
        details. The patient receives an email with a portal link to download
        it. They take the form to their cardiologist, then upload the signed
        letter on their portal.
      </p>
      <button className="btn-primary text-xs flex items-center gap-1"
              onClick={() => generate.mutate()}
              disabled={generate.isPending}>
        <FileText size={11} /> {generate.isPending ? 'Generating…' : 'Generate Clearance Form'}
      </button>
      {error && <div className="text-xs text-red-600">{error}</div>}
      {result && (
        <div className="text-[11px] bg-green-50 border border-green-200 rounded p-2 space-y-0.5">
          <div className="flex items-baseline justify-between gap-2">
            <span className="truncate">✓ Generated <code>{result.filename}</code></span>
            <div className="flex items-center gap-2 shrink-0">
              <button onClick={() => setPreviewing(true)}
                      className="text-plum-700 hover:underline flex items-center gap-1">
                <Eye size={11} /> Preview
              </button>
              <a href={`/api${result.download_url.replace(/^\/api/, '')}`}
                 download
                 className="text-plum-700 hover:underline flex items-center gap-1">
                <Download size={11} /> Download
              </a>
            </div>
          </div>
          <div className="text-[10px] text-gray-600">
            {result.email_sent
              ? <>✓ Emailed to patient — they'll see it on their portal.</>
              : <>⚠ Patient email not sent (no email on file or send failed). Share the download manually.</>}
          </div>
        </div>
      )}
      {previewing && result && (
        <PdfPreviewDrawer
          apiPath={result.download_url.replace(/^\/api/, '')}
          filename={result.filename}
          title={`Preview · ${result.filename}`}
          onClose={() => setPreviewing(false)}
        />
      )}
    </div>
  )
}


function AssistantSurgeonCardBody({ surgery }) {
  const qc = useQueryClient()
  const required = !!surgery.assistant_surgeon_required
  const [name, setName] = useState(surgery.assistant_surgeon_name || 'Dr. Gillespie')
  const [phone, setPhone] = useState(surgery.assistant_surgeon_office_phone || '')
  const [fax, setFax] = useState(surgery.assistant_surgeon_office_fax || '')
  const [apptDate, setApptDate] = useState(surgery.assistant_surgeon_appt_date || '')

  function refresh() {
    qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
    qc.invalidateQueries({ queryKey: ['surgery-list'] })
    qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
  }

  const patch = useMutation({
    mutationFn: (body) => api.patch(`/surgery/${surgery.id}`, body).then(r => r.data),
    onSuccess: refresh,
  })
  const notify = useMutation({
    mutationFn: () => api.post(`/surgery/${surgery.id}/assistant-surgeon/notify-office`)
                          .then(r => r.data),
    onSuccess: refresh,
    onError: (e) => alert(e?.response?.data?.detail || 'Notify failed'),
  })
  const confirmAppt = useMutation({
    mutationFn: () => api.post(`/surgery/${surgery.id}/assistant-surgeon/confirm-appt`,
                                { appt_date: apptDate || null }).then(r => r.data),
    onSuccess: refresh,
    onError: (e) => alert(e?.response?.data?.detail || 'Confirm failed'),
  })
  const reset = useMutation({
    mutationFn: () => api.post(`/surgery/${surgery.id}/assistant-surgeon/reset`)
                          .then(r => r.data),
    onSuccess: refresh,
  })

  if (!required) {
    return (
      <div className="space-y-2 text-[12px]">
        <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-1.5">
          <UserPlus size={14} className="text-plum-700" />
          Assistant Surgeon
        </h3>
        <p className="text-gray-700">
          Most surgeries don't need an assistant surgeon. Toggle this on
          when the primary surgeon has requested one (usually Dr. Gillespie).
        </p>
        <button className="btn-primary text-[11px]"
                onClick={() => patch.mutate({ assistant_surgeon_required: true })}
                disabled={patch.isPending}>
          {patch.isPending ? 'Enabling…' : 'Enable assistant surgeon for this case'}
        </button>
      </div>
    )
  }

  const officeNotified = !!surgery.assistant_surgeon_office_notified_at
  const apptConfirmed = !!surgery.assistant_surgeon_appt_confirmed_at
  const headerTone = apptConfirmed
    ? 'bg-green-100 text-green-700'
    : officeNotified ? 'bg-blue-100 text-blue-700'
    : 'bg-amber-100 text-amber-700'
  const headerLabel = apptConfirmed ? 'appt confirmed'
    : officeNotified ? 'office notified'
    : 'required'

  return (
    <div className="space-y-3 text-[12px]">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-1.5">
          <UserPlus size={14} className="text-plum-700" />
          Assistant Surgeon
        </h3>
        <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${headerTone}`}>
          {headerLabel}
        </span>
      </div>
      {/* Assistant surgeon contact */}
      <div className="grid grid-cols-3 gap-2">
        <div>
          <div className="text-[10px] uppercase tracking-wide text-gray-500">Assistant surgeon</div>
          <input className="input text-[12px] w-full" value={name}
                 onChange={e => setName(e.target.value)} />
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide text-gray-500">Office phone</div>
          <input className="input text-[12px] w-full font-mono" value={phone}
                 onChange={e => setPhone(e.target.value)} placeholder="240-xxx-xxxx" />
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide text-gray-500">Office fax</div>
          <input className="input text-[12px] w-full font-mono" value={fax}
                 onChange={e => setFax(e.target.value)} placeholder="240-xxx-xxxx" />
        </div>
      </div>
      <button className="btn-secondary text-[11px]"
              onClick={() => patch.mutate({
                assistant_surgeon_name: name,
                assistant_surgeon_office_phone: phone,
                assistant_surgeon_office_fax: fax,
              })}
              disabled={patch.isPending}>
        {patch.isPending ? 'Saving…' : 'Save contact info'}
      </button>

      {/* Two-step coordination checklist */}
      <div className="border border-gray-200 rounded p-2 space-y-2">
        <div className="flex items-start gap-2">
          <div className={`shrink-0 mt-0.5 w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold ${
            officeNotified ? 'bg-green-500 text-white' : 'bg-gray-200 text-gray-500'
          }`}>{officeNotified ? '✓' : '1'}</div>
          <div className="flex-1">
            <div className="font-medium text-gray-800">
              Notify {name || 'assistant surgeon'}'s office
            </div>
            {officeNotified ? (
              <div className="text-[11px] text-green-700">
                Notified {fmt.date(surgery.assistant_surgeon_office_notified_at?.slice(0,10))}
                {surgery.assistant_surgeon_office_notified_by &&
                  ` by ${surgery.assistant_surgeon_office_notified_by.split('@')[0]}`}
              </div>
            ) : (
              <div className="text-[11px] text-gray-500">
                Call / fax the office so they have the case on their schedule.
              </div>
            )}
          </div>
          {!officeNotified && (
            <button className="btn-primary text-[11px]"
                    onClick={() => notify.mutate()}
                    disabled={notify.isPending}>
              {notify.isPending ? '…' : 'Mark notified'}
            </button>
          )}
        </div>

        <div className="flex items-start gap-2">
          <div className={`shrink-0 mt-0.5 w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-bold ${
            apptConfirmed ? 'bg-green-500 text-white' : 'bg-gray-200 text-gray-500'
          }`}>{apptConfirmed ? '✓' : '2'}</div>
          <div className="flex-1">
            <div className="font-medium text-gray-800">
              Patient appointment with assistant surgeon
            </div>
            {apptConfirmed ? (
              <div className="text-[11px] text-green-700">
                {surgery.assistant_surgeon_appt_date && (
                  <>Appt: {fmt.date(surgery.assistant_surgeon_appt_date)} · </>
                )}
                Confirmed {fmt.date(surgery.assistant_surgeon_appt_confirmed_at?.slice(0,10))}
                {surgery.assistant_surgeon_appt_confirmed_by &&
                  ` by ${surgery.assistant_surgeon_appt_confirmed_by.split('@')[0]}`}
              </div>
            ) : (
              <div className="flex items-center gap-2 mt-1">
                <input type="date" className="input text-[11px]"
                       value={apptDate}
                       onChange={e => setApptDate(e.target.value)} />
                <span className="text-[10px] text-gray-500">(optional)</span>
              </div>
            )}
          </div>
          {!apptConfirmed && (
            <button className="btn-primary text-[11px]"
                    onClick={() => confirmAppt.mutate()}
                    disabled={confirmAppt.isPending}>
              {confirmAppt.isPending ? '…' : 'Confirm scheduled'}
            </button>
          )}
        </div>
      </div>

      <div className="flex items-center gap-3">
        {(officeNotified || apptConfirmed) && (
          <button className="text-[11px] text-muted hover:underline"
                  onClick={() => { if (confirm('Clear both confirmations?')) reset.mutate() }}>
            Reset confirmations
          </button>
        )}
        <button className="text-[11px] text-muted hover:underline ml-auto"
                onClick={() => patch.mutate({ assistant_surgeon_required: false })}>
          Disable for this case
        </button>
      </div>
    </div>
  )
}


function PriorAuthCardBody({ surgery }) {
  const qc = useQueryClient()
  const [authNum, setAuthNum] = useState(surgery.auth_number || '')
  const [authStatus, setAuthStatus] = useState(surgery.auth_status || 'not_required')

  const patchStatus = useMutation({
    mutationFn: () => api.patch(`/surgery/${surgery.id}`,
                                  { auth_status: authStatus }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery', surgery.id] }),
  })
  const patchNum = useMutation({
    mutationFn: () => api.patch(`/surgery/${surgery.id}`,
                                  { auth_number: authNum }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery', surgery.id] }),
  })

  const STATUS_TONE = {
    not_required: 'bg-gray-100 text-gray-600',
    required:     'bg-amber-100 text-amber-700',
    sent_request: 'bg-blue-100 text-blue-700',
    sent_records: 'bg-blue-100 text-blue-700',
    peer_review:  'bg-amber-100 text-amber-700',
    approved:     'bg-green-100 text-green-700',
    denied:       'bg-red-100 text-red-700',
    tbd:          'bg-gray-100 text-gray-600',
    completed:    'bg-green-100 text-green-700',
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-1.5">
          <ShieldCheck size={14} className="text-emerald-700" />
          Prior Auth
        </h3>
        <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${STATUS_TONE[authStatus] || 'bg-gray-100 text-gray-600'}`}>
          {authStatus.replace(/_/g, ' ')}
        </span>
      </div>

      <div className="flex items-end gap-2 text-[11px]">
        <div className="flex-1 max-w-[260px]">
          <div className="text-[10px] uppercase tracking-wide text-gray-500">Auth status</div>
          <select className="input text-[12px] w-full"
                  value={authStatus}
                  onChange={e => setAuthStatus(e.target.value)}>
            <option value="not_required">not required</option>
            <option value="required">required</option>
            <option value="sent_request">sent — request</option>
            <option value="sent_records">sent — records</option>
            <option value="peer_review">peer review</option>
            <option value="approved">approved</option>
            <option value="denied">denied</option>
            <option value="tbd">TBD</option>
            <option value="completed">completed</option>
          </select>
        </div>
        <button className="btn-secondary text-[11px]"
                onClick={() => patchStatus.mutate()}
                disabled={patchStatus.isPending}>
          {patchStatus.isPending ? 'Saving…' : 'Save status'}
        </button>
      </div>

      <FilesPanel surgery={surgery} kindFilter="prior_auth" label="Prior Auth Response" />

      <div className="border-t border-gray-200 pt-2 space-y-1">
        <div className="text-[10px] uppercase tracking-wide text-gray-500">
          Prior Auth No. / Reference No.
        </div>
        <div className="flex items-center gap-2">
          <input className="input text-[12px] font-mono flex-1 max-w-[280px]"
                 value={authNum}
                 onChange={e => setAuthNum(e.target.value)}
                 placeholder="e.g. AUTH-2026-1234" />
          <button className="btn-primary text-[11px]"
                  onClick={() => patchNum.mutate()}
                  disabled={patchNum.isPending || authNum === (surgery.auth_number || '')}>
            {patchNum.isPending ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}


function LabsCardBody({ surgery }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [draftDate, setDraftDate] = useState(surgery.lab_appointment_date || '')

  const patch = useMutation({
    mutationFn: (body) => api.patch(`/surgery/${surgery.id}`, body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
      setEditing(false)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  // Recommended window: 4–7 days before the surgery date.
  let recommended = null
  if (surgery.scheduled_date) {
    const base = new Date(surgery.scheduled_date + 'T00:00:00')
    const earliest = new Date(base); earliest.setDate(earliest.getDate() - 7)
    const latest   = new Date(base); latest.setDate(latest.getDate() - 4)
    recommended = {
      earliest: earliest.toISOString().slice(0, 10),
      latest:   latest.toISOString().slice(0, 10),
    }
  }

  const reportedBy = surgery.lab_appointment_reported_by || ''
  const reportedByPatient = reportedBy === 'patient'

  return (
    <div className="space-y-2 text-[12px]">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-1.5">
          <FlaskConical size={14} className="text-amber-700" />
          Pre-Op Labs
        </h3>
        <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${
          surgery.lab_appointment_date
            ? 'bg-green-100 text-green-700'
            : 'bg-amber-100 text-amber-700'
        }`}>
          {surgery.lab_appointment_date ? 'scheduled' : 'awaiting patient report'}
        </span>
      </div>

      {recommended && (
        <div className="text-[11px] text-gray-600">
          Patient should get pre-op labs drawn between{' '}
          <strong>{fmt.date(recommended.earliest)}</strong> and{' '}
          <strong>{fmt.date(recommended.latest)}</strong>{' '}
          (4–7 days before surgery).
        </div>
      )}

      {!editing ? (
        <div className="flex items-baseline gap-3 flex-wrap">
          <div>
            <span className="text-[10px] uppercase tracking-wide text-gray-500">Lab appointment:</span>{' '}
            {surgery.lab_appointment_date
              ? <span className="font-medium">{fmt.date(surgery.lab_appointment_date)}</span>
              : <span className="text-gray-400 italic">not yet reported</span>}
          </div>
          {surgery.lab_appointment_date && (
            <div className="text-[10px] text-gray-500">
              {reportedByPatient ? 'self-reported by patient'
                : reportedBy ? `entered ${reportedBy.replace('staff:', 'by ')}`
                : ''}
              {surgery.lab_appointment_reported_at &&
                ` · ${surgery.lab_appointment_reported_at.slice(0, 10)}`}
            </div>
          )}
          <button className="text-[11px] text-plum-700 hover:underline"
                  onClick={() => setEditing(true)}>
            <Edit3 size={10} className="inline" /> {surgery.lab_appointment_date ? 'Edit' : 'Enter on behalf of patient'}
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-2 flex-wrap">
          <input type="date" className="input text-[12px]"
                 min={recommended?.earliest}
                 max={recommended?.latest}
                 value={draftDate}
                 onChange={e => setDraftDate(e.target.value)} />
          <button className="btn-primary text-[11px]"
                  onClick={() => patch.mutate({ lab_appointment_date: draftDate || null })}
                  disabled={patch.isPending}>
            {patch.isPending ? 'Saving…' : 'Save'}
          </button>
          <button className="text-[11px] text-muted hover:underline"
                  onClick={() => { setEditing(false); setDraftDate(surgery.lab_appointment_date || '') }}>
            Cancel
          </button>
        </div>
      )}

      <div className="text-[10px] text-gray-500 italic">
        Patient normally reports this date on their portal. Use the editor above to backfill if they called in.
      </div>
    </div>
  )
}


// ─── Post-op call script ──────────────────────────────────────────

const POST_OP_QUESTIONS = [
  { key: 'pain',         label: 'Pain — controlled with prescribed meds?' },
  { key: 'bleeding',     label: 'Any heavy bleeding or large clots?' },
  { key: 'fever',        label: 'Fever ≥ 100.4 °F, chills, or shaking?' },
  { key: 'incision',     label: 'Incision sites — redness, drainage, warmth, opening?' },
  { key: 'voiding',      label: 'Urinating normally? Any burning?' },
  { key: 'bowel',        label: 'Bowel movement / passing gas?' },
  { key: 'nausea',       label: 'Nausea or vomiting?' },
  { key: 'leg',          label: 'Leg pain/swelling/calf tenderness (DVT)?' },
  { key: 'breathing',    label: 'Shortness of breath or chest pain?' },
  { key: 'meds',         label: 'All prescribed meds being taken? Any side effects?' },
  { key: 'activity',     label: 'Following activity / lifting restrictions?' },
  { key: 'followup',     label: 'Follow-up appointment scheduled?' },
  { key: 'questions',    label: 'Any questions or concerns?' },
]


function PostOpCallCardBody({ surgery, milestone }) {
  const qc = useQueryClient()
  const initial = (() => {
    try { return JSON.parse(milestone?.notes || '{}') } catch { return {} }
  })()
  const [answers, setAnswers] = useState(() => {
    const out = {}
    for (const q of POST_OP_QUESTIONS) {
      out[q.key] = initial[q.key] || { value: '', note: '' }
    }
    out.__free_text = initial.__free_text || ''
    return out
  })

  function setAnswer(key, patch) {
    setAnswers(prev => ({ ...prev, [key]: { ...prev[key], ...patch } }))
  }

  const save = useMutation({
    mutationFn: () => api.post(`/surgery/${surgery.id}/milestones/${milestone.kind}/done`,
                                { notes: JSON.stringify(answers) }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
    },
  })
  const saveDraft = useMutation({
    mutationFn: () => api.post(`/surgery/${surgery.id}/milestones/${milestone.kind}/start`,
                                { notes: JSON.stringify(answers) }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
    },
  })

  const isDone = milestone.status === 'done'

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-1.5">
          <Phone size={14} className="text-plum-700" />
          Spoke to Patient Post-Op
        </h3>
        {isDone && (
          <span className="text-[10px] uppercase tracking-wide bg-green-100 text-green-700 px-1.5 py-0.5 rounded">
            done
          </span>
        )}
      </div>
      <div className="text-[11px] text-gray-600">
        Ask each question and record the answer. Save → marks the milestone done with
        all answers captured.
      </div>
      <div className="space-y-1.5">
        {POST_OP_QUESTIONS.map(q => {
          const a = answers[q.key] || { value: '', note: '' }
          return (
            <div key={q.key}
                 className="flex items-start gap-2 text-[12px] py-1 border-b border-gray-50 last:border-0">
              <div className="flex-1">
                <div className="text-gray-800">{q.label}</div>
                {a.value === 'concern' && (
                  <input className="input text-[11px] w-full mt-1"
                         placeholder="Details (required when concern)"
                         value={a.note}
                         onChange={e => setAnswer(q.key, { note: e.target.value })} />
                )}
              </div>
              <div className="flex gap-1 shrink-0">
                {['ok', 'concern', 'na'].map(v => (
                  <button key={v}
                          type="button"
                          onClick={() => setAnswer(q.key, { value: v })}
                          className={`text-[10px] px-1.5 py-0.5 rounded border ${
                            a.value === v
                              ? (v === 'ok'      ? 'bg-green-100 border-green-300 text-green-800'
                                : v === 'concern' ? 'bg-red-100 border-red-300 text-red-800'
                                : 'bg-gray-100 border-gray-300 text-gray-700')
                              : 'border-gray-200 text-gray-500 hover:bg-gray-50'
                          }`}>
                    {v === 'ok' ? '✓ OK' : v === 'concern' ? '⚠ Concern' : 'N/A'}
                  </button>
                ))}
              </div>
            </div>
          )
        })}
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-0.5">Additional notes</div>
        <textarea className="input text-[12px] w-full"
                  rows={2}
                  value={answers.__free_text}
                  onChange={e => setAnswers(prev => ({ ...prev, __free_text: e.target.value }))} />
      </div>
      <div className="flex gap-2">
        {!isDone && (
          <>
            <button className="btn-secondary text-[11px]"
                    onClick={() => saveDraft.mutate()}
                    disabled={saveDraft.isPending}>
              {saveDraft.isPending ? 'Saving…' : 'Save draft'}
            </button>
            <button className="btn-primary text-[11px]"
                    onClick={() => save.mutate()}
                    disabled={save.isPending}>
              {save.isPending ? 'Saving…' : 'Save & mark done'}
            </button>
          </>
        )}
        {isDone && (
          <span className="text-[11px] text-green-700 italic">
            ✓ Post-op call recorded — milestone done
          </span>
        )}
      </div>
    </div>
  )
}


function SurgeryBilledCardBody({ surgery }) {
  const qc = useQueryClient()
  const [claimNum, setClaimNum] = useState(surgery.modmed_claim_number || '')
  const [suggesting, setSuggesting] = useState(false)
  const [error, setError] = useState(null)

  const patchClaim = useMutation({
    mutationFn: () => api.patch(`/surgery/${surgery.id}`,
                                { modmed_claim_number: claimNum }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery', surgery.id] }),
  })

  const suggest = useMutation({
    mutationFn: () => api.post(`/surgery/${surgery.id}/suggest-billing-codes`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery-files', surgery.id] })
    },
    onError: (e) => setError(e?.response?.data?.detail || 'Suggestion failed.'),
  })

  const icd10 = surgery.billed_icd10_codes || []
  const cpts = surgery.billed_cpt_codes || []
  const billed = !!surgery.billed_at

  return (
    <div className="space-y-2 text-[12px]">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-1.5">
          <DollarSign size={14} className="text-emerald-700" />
          Surgery Billed
        </h3>
        <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${
          billed ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'
        }`}>
          {billed ? 'billed' : 'not billed'}
        </span>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wide text-gray-500">ModMed claim #</div>
        <div className="flex gap-1">
          <input className="input text-[12px] flex-1 font-mono"
                 value={claimNum}
                 onChange={e => setClaimNum(e.target.value)}
                 placeholder="ModMed claim / encounter number" />
          <button className="btn-secondary text-[11px]"
                  onClick={() => patchClaim.mutate()}
                  disabled={patchClaim.isPending}>
            {patchClaim.isPending ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>

      <div className="border border-gray-200 rounded p-2">
        <div className="flex items-center justify-between mb-1.5">
          <div className="text-[10px] uppercase tracking-wide text-gray-500">
            AI billing codes (from op + path reports)
          </div>
          <button className="btn-primary text-[11px] flex items-center gap-1"
                  onClick={() => { setError(null); suggest.mutate() }}
                  disabled={suggest.isPending}>
            {suggest.isPending ? 'Reading reports…' : 'Suggest codes'}
          </button>
        </div>
        {error && <div className="text-[11px] text-red-700">{error}</div>}

        {icd10.length === 0 && cpts.length === 0 && !suggest.isPending && (
          <div className="text-[11px] text-gray-500 italic">
            No codes yet. Click "Suggest codes" once the op note + path report are uploaded.
          </div>
        )}

        {(icd10.length > 0 || cpts.length > 0) && (
          <div className="grid grid-cols-2 gap-3 mt-1">
            <div>
              <div className="text-[10px] uppercase text-gray-500 mb-1">ICD-10</div>
              <ul className="text-[11px] space-y-0.5">
                {icd10.map((c, i) => (
                  <li key={i} className="flex gap-2">
                    <code className="text-plum-700 font-medium">{c.code}</code>
                    <span className="text-gray-600 truncate">{c.description}</span>
                  </li>
                ))}
                {icd10.length === 0 && <li className="text-gray-400 italic">none</li>}
              </ul>
            </div>
            <div>
              <div className="text-[10px] uppercase text-gray-500 mb-1">CPT (+ mod / POS)</div>
              <ul className="text-[11px] space-y-0.5">
                {cpts.map((c, i) => (
                  <li key={i} className="flex gap-2">
                    <code className="text-plum-700 font-medium">{c.code}</code>
                    {c.modifier && (
                      <span className={`text-[10px] px-1 rounded ${
                        c.modifier === '22' ? 'bg-amber-100 text-amber-800'
                                            : 'bg-gray-100 text-gray-700'}`}>
                        -{c.modifier}
                      </span>
                    )}
                    <span className="text-gray-500">POS {c.pos || '—'}</span>
                    <span className="text-gray-600 truncate flex-1">{c.description}</span>
                  </li>
                ))}
                {cpts.length === 0 && <li className="text-gray-400 italic">none</li>}
              </ul>
            </div>
          </div>
        )}

        {cpts.some(c => c.modifier === '22') && (
          <div className="bg-amber-50 border border-amber-200 text-amber-900 text-[11px] p-2 rounded mt-2">
            <div className="mb-1.5">
              <strong>⚠ Modifier 22 detected.</strong> A justification letter has
              been auto-generated for the insurance company.
            </div>
            <FilesPanel surgery={surgery} kindFilter="modifier_22_letter"
                         label="Modifier-22 Justification Letter" />
          </div>
        )}

        {surgery.billed_at && (
          <div className="text-[10px] text-gray-500 mt-2">
            Codes saved {fmt.date(surgery.billed_at.slice(0,10))}
            {surgery.billed_by && ` by ${surgery.billed_by.split('@')[0]}`}
          </div>
        )}
      </div>
    </div>
  )
}


function CancelDrawer({ surgery, onClose, onFreedBlockDay }) {
  const qc = useQueryClient()
  const [reason, setReason] = useState('patient')
  const [notes, setNotes] = useState('')
  const [feeOverride, setFeeOverride] = useState(null)   // null = use system default

  // Compute fee preview
  const today = new Date()
  const surgDate = surgery.scheduled_date ? new Date(surgery.scheduled_date) : null
  const daysToSurgery = surgDate ? Math.ceil((surgDate - today) / 86400000) : null
  const within2Weeks = daysToSurgery != null && daysToSurgery >= 0 && daysToSurgery <= 14
  const systemSaysFee = reason === 'patient' && within2Weeks
  const feeApplies = feeOverride != null ? feeOverride : systemSaysFee

  const cancel = useMutation({
    mutationFn: () => api.post(`/surgery/${surgery.id}/cancel`, {
      reason, notes: notes || null,
      fee_required: feeOverride !== null ? feeOverride : undefined,
    }).then(r => r.data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
      qc.invalidateQueries({ queryKey: ['surgery-block-days'] })
      // If we freed up a hospital block day, hand control to the matches
      // drawer so the scheduler can immediately reach out to waitlisters.
      if (data?.freed_block_day_id && onFreedBlockDay) {
        onFreedBlockDay(data.freed_block_day_id)
      } else {
        onClose()
      }
    },
  })

  const reasons = [
    { v: 'patient',      l: 'Patient cancelled',        feeNote: 'Fee applies if within 2 weeks of surgery' },
    { v: 'anesthesia',   l: 'Cancelled by anesthesia',  feeNote: 'No fee' },
    { v: 'hospital',     l: 'Cancelled by hospital',    feeNote: 'No fee' },
    { v: 'medical',      l: 'Medical (clearance failed)', feeNote: 'No fee' },
    { v: 'hold',         l: 'Move to Hold queue',       feeNote: 'No fee · status → hold (resumable)' },
    { v: 'unresponsive', l: 'Mark unresponsive',         feeNote: 'No fee · auto-flagged after 180d' },
  ]

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-lg bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-4 flex items-center justify-between z-10">
          <div>
            <h2 className="font-serif font-semibold text-ink text-[16px]">Cancel / hold surgery</h2>
            <div className="text-[11px] text-muted">
              {surgery.patient_name}
              {procDescription(surgery) && <> · {procDescription(surgery)}</>}
              {surgery.scheduled_date && <> · {fmt.date(surgery.scheduled_date)}</>}
            </div>
          </div>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        <div className="p-5 space-y-4">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Reason</div>
            <div className="space-y-1">
              {reasons.map(r => (
                <label key={r.v} className="flex items-baseline gap-2 text-sm cursor-pointer hover:bg-gray-50 px-2 py-1 rounded">
                  <input type="radio" name="reason" value={r.v}
                         checked={reason === r.v}
                         onChange={() => setReason(r.v)} />
                  <span className="flex-1">
                    <div>{r.l}</div>
                    <div className="text-[10px] text-gray-500">{r.feeNote}</div>
                  </span>
                </label>
              ))}
            </div>
          </div>

          <div>
            <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Notes</div>
            <textarea className="input text-sm w-full" rows={3}
                      placeholder="What happened? (optional)"
                      value={notes} onChange={e => setNotes(e.target.value)} />
          </div>

          {/* Auto-actions preview */}
          <div className="card !p-3 bg-amber-50/40 border-amber-200">
            <div className="text-[11px] uppercase tracking-wide text-amber-900 font-semibold mb-1">
              Auto-actions on confirm
            </div>
            <ul className="text-xs text-gray-700 space-y-1">
              {surgery.scheduled_date && (
                <li>
                  ✓ Surgery date {fmt.date(surgery.scheduled_date)}
                  ({daysToSurgery >= 0 ? `in ${daysToSurgery}d` : `${-daysToSurgery}d ago`})
                  → status flips to <strong>{reason === 'hold' ? 'hold' : reason === 'unresponsive' ? 'unresponsive' : 'cancelled'}</strong>
                </li>
              )}
              {systemSaysFee && (
                <li className="text-red-700">
                  ⚠ Within 2 weeks of surgery — <strong>$351 cancellation fee</strong> required (collect via ModMed Pay).
                  <label className="flex items-center gap-1 text-[10px] mt-1">
                    <input type="checkbox"
                           checked={feeOverride === false}
                           onChange={e => setFeeOverride(e.target.checked ? false : null)} />
                    Override: don't charge fee
                  </label>
                </li>
              )}
              {!systemSaysFee && reason === 'patient' && (
                <li>No cancellation fee (more than 2 weeks out{!surgery.scheduled_date && ' / no date'}).</li>
              )}
              {Number(surgery.amount_paid || 0) > 0 && (
                <li className="text-blue-700">
                  ↩ Refund needed (${surgery.amount_paid} paid) — process if no open claims
                </li>
              )}
              <li className="text-gray-500 italic">
                ▾ Phase 2 will also: remove from ModMed schedule, cancel post-op appts,
                trigger waitlist Klara blast (manual for now).
              </li>
            </ul>
          </div>

          {cancel.isError && (
            <div className="text-xs text-red-700">
              {cancel.error?.response?.data?.detail || cancel.error.message}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button className="btn-secondary text-sm" onClick={onClose}>Cancel</button>
            <button className={`text-sm px-3 py-1.5 rounded text-white ${
                      reason === 'hold' ? 'bg-violet-700 hover:bg-violet-800'
                                        : 'bg-red-700 hover:bg-red-800'
                    } disabled:opacity-60`}
                    onClick={() => cancel.mutate()}
                    disabled={cancel.isPending}>
              {cancel.isPending ? 'Working…'
                : reason === 'hold' ? 'Move to hold'
                : reason === 'unresponsive' ? 'Mark unresponsive'
                : 'Confirm cancel'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}


function procDescription(s) {
  const p = (s.procedures || [])[0]
  if (!p) return null
  return p.description?.slice(0, 50) + (p.description?.length > 50 ? '…' : '')
}


function WaitlistToggle({ surgeryId }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [days, setDays] = useState(7)

  // Quick query of the global waitlist to know if this surgery is on it
  const { data } = useQuery({
    queryKey: ['surgery-waitlist'],
    queryFn: () => api.get('/surgery/admin/waitlist').then(r => r.data),
  })
  const onList = (data?.waitlist || []).find(w => w.surgery_id === surgeryId)

  const join = useMutation({
    mutationFn: () => api.post(`/surgery/${surgeryId}/waitlist`,
      { advance_notice_days: days }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery-waitlist'] })
      setEditing(false)
    },
  })

  const remove = useMutation({
    mutationFn: () => api.delete(`/surgery/${surgeryId}/waitlist`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['surgery-waitlist'] }),
  })

  if (onList && !editing) {
    return (
      <div className="text-xs flex items-center gap-1 px-2 py-1 rounded border border-violet-300 bg-violet-50 text-violet-800">
        <ListPlus size={11} /> Waitlisted ({onList.advance_notice_days}d notice)
        <button className="ml-1 text-violet-700 hover:underline"
                onClick={() => { setDays(onList.advance_notice_days); setEditing(true) }}>
          edit
        </button>
        <button className="ml-1 text-red-700 hover:underline"
                onClick={() => { if (confirm('Remove from waitlist?')) remove.mutate() }}>
          remove
        </button>
      </div>
    )
  }

  if (editing || !onList) {
    return (
      <div className="flex items-center gap-1 text-xs">
        <span className="text-gray-600">Notice (days):</span>
        <input type="number" min={0} max={90} value={days}
                className="input text-xs w-14"
                onChange={e => setDays(parseInt(e.target.value || '7', 10))} />
        <button className="text-xs px-2 py-1 rounded border border-violet-300 bg-white text-violet-700 hover:bg-violet-50 flex items-center gap-1"
                onClick={() => join.mutate()}
                disabled={join.isPending}>
          <ListPlus size={11} /> {onList ? 'Update' : 'Add to waitlist'}
        </button>
        {editing && (
          <button className="text-xs text-gray-500 hover:underline"
                  onClick={() => setEditing(false)}>cancel</button>
        )}
      </div>
    )
  }
}


function envelopeStatusTone(status) {
  if (status === 'signed')   return 'bg-green-50 border-green-200 text-green-800'
  if (status === 'declined') return 'bg-red-50 border-red-200 text-red-800'
  if (status === 'voided')   return 'bg-gray-100 border-gray-300 text-gray-600'
  if (status === 'failed')   return 'bg-red-50 border-red-200 text-red-800'
  return 'bg-amber-50 border-amber-200 text-amber-800'
}


function ConsentPanel({ surgery }) {
  const qc = useQueryClient()
  const status = surgery.consent_status || 'not_required'
  const envelopes = surgery.consent_envelopes || []
  const isSigned = status === 'signed' || envelopes.length > 0 && envelopes.every(e => e.status === 'signed')
  const isSent = envelopes.length > 0 && !isSigned

  const matches = useQuery({
    queryKey: ['consent-template-matches', surgery.id],
    queryFn: () => api.get(`/surgery/${surgery.id}/consent/template-matches`).then(r => r.data),
    staleTime: 30_000,
  })

  const docusignSend = useMutation({
    mutationFn: (ignoreWarnings) =>
      api.post(`/surgery/${surgery.id}/consent/docusign-send`,
              { ignore_warnings: !!ignoreWarnings }).then(r => r.data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
      if (data?.skipped?.length) {
        alert(`Sent ${data.sent.length}; skipped ${data.skipped.length} (already in flight).`)
      }
    },
    onError: (e) => alert(e?.response?.data?.detail || 'DocuSign send failed'),
  })
  const docusignSync = useMutation({
    mutationFn: () => api.post(`/surgery/${surgery.id}/consent/docusign-sync`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'DocuSign sync failed'),
  })
  const boldsignSend = useMutation({
    mutationFn: (ignoreWarnings) =>
      api.post(`/surgery/${surgery.id}/consent/boldsign-send`,
              { ignore_warnings: !!ignoreWarnings }).then(r => r.data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
      if (data?.skipped?.length) {
        alert(`Sent ${data.sent.length}; skipped ${data.skipped.length} (already in flight).`)
      }
    },
    onError: (e) => alert(e?.response?.data?.detail || 'BoldSign send failed'),
  })
  const boldsignSync = useMutation({
    mutationFn: () => api.post(`/surgery/${surgery.id}/consent/boldsign-sync`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'BoldSign sync failed'),
  })
  const sentManual = useMutation({
    mutationFn: () => api.post(`/surgery/${surgery.id}/consent/sent`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
    },
  })
  const signedManual = useMutation({
    mutationFn: () => api.post(`/surgery/${surgery.id}/consent/signed`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
    },
  })

  const tone = isSigned ? 'bg-green-50 border-green-200' :
               isSent   ? 'bg-amber-50 border-amber-200' :
                          'bg-gray-50 border-gray-200'

  const matchData = matches.data
  const blockingWarnings = matchData?.matches?.filter(m => m.warning) || []
  const unmatched = matchData?.unmatched_procedures || []
  const canSend = matchData && matchData.matches.length > 0 && unmatched.length === 0

  function handleSend(provider) {
    const send = provider === 'boldsign' ? boldsignSend : docusignSend
    if (blockingWarnings.length > 0) {
      const ok = confirm(
        'Warnings:\n\n' + blockingWarnings.map(m => '• ' + m.warning).join('\n')
        + '\n\nSend anyway?'
      )
      if (!ok) return
      send.mutate(true)
    } else {
      send.mutate(false)
    }
  }

  const fmtDateTime = (iso) => {
    if (!iso) return null
    const d = new Date(iso)
    return d.toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit',
    })
  }

  return (
    <div className={`card !p-3 mt-3 border ${tone}`}>
      <div className="flex items-center gap-1.5 mb-2">
        <FileText size={14} className="text-plum-700" />
        <h3 className="text-sm font-semibold text-gray-800">Consent</h3>
        {envelopes.length > 0 && (
          <span className="text-[10px] text-gray-500">
            · {envelopes.filter(e => e.status === 'signed').length}/{envelopes.length} signed
          </span>
        )}
      </div>

      {/* Top-level status timeline */}
      {(surgery.consent_sent_at || surgery.consent_signed_at) && (
        <div className="text-[11px] text-gray-700 space-y-0.5 mb-3">
          {surgery.consent_sent_at && (
            <div>
              <span className="text-gray-500">Consent sent:</span>{' '}
              <span className="font-medium">{fmtDateTime(surgery.consent_sent_at)}</span>
            </div>
          )}
          {surgery.consent_signed_at && (
            <div>
              <span className="text-gray-500">Signed:</span>{' '}
              <span className="font-medium text-green-700">{fmtDateTime(surgery.consent_signed_at)}</span>
            </div>
          )}
        </div>
      )}

      {/* Per-envelope rows when any have been sent */}
      {envelopes.length > 0 && (
        <div className="space-y-1 mb-3">
          {envelopes.map(e => (
            <div key={e.id}
                 className={`px-2 py-1.5 rounded text-[11px] border ${envelopeStatusTone(e.status)}`}>
              <div className="flex items-center gap-2">
                <span className="font-medium flex-1">
                  {e.template_name || 'Unknown template'}
                  {e.is_supplemental && (
                    <span className="ml-1 text-[9px] bg-amber-100 text-amber-700 px-1 rounded">SUPPL</span>
                  )}
                </span>
                <span className="text-[10px] uppercase tracking-wide font-medium">{e.status}</span>
                {e.envelope_id && (
                  <span className="text-[10px] font-mono text-gray-500">{e.envelope_id.slice(0, 8)}…</span>
                )}
              </div>
              {(e.sent_at || e.signed_at) && (
                <div className="text-[10px] text-gray-600 mt-0.5 flex gap-3">
                  {e.sent_at && <span>Sent {fmtDateTime(e.sent_at)}</span>}
                  {e.signed_at && <span className="text-green-700">Signed {fmtDateTime(e.signed_at)}</span>}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Matched-template preview (shown before any envelope is sent) */}
      {envelopes.length === 0 && matchData && (
        <div className="text-[11px] text-gray-700 mb-3">
          {matchData.matches.length === 0 ? (
            <div className="text-amber-700">
              <AlertTriangle size={11} className="inline mr-1" />
              No consent templates match this surgery. Register one in&nbsp;
              <Link to="/admin/consent-templates" className="text-plum-700 underline">
                Settings → Consent Templates
              </Link>.
            </div>
          ) : (
            <>
              <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">
                Will send {matchData.matches.length} envelope{matchData.matches.length === 1 ? '' : 's'}:
              </div>
              <div className="space-y-0.5">
                {matchData.matches.map(m => (
                  <div key={m.template_id} className="flex items-center gap-2">
                    <Check size={11} className="text-green-600" />
                    <span className="font-medium">{m.template_name}</span>
                    {m.is_supplemental && <span className="text-[9px] bg-amber-100 text-amber-700 px-1 rounded">SUPPL</span>}
                    {m.warning && (
                      <span className="text-[10px] text-red-700">⚠️ {m.warning}</span>
                    )}
                  </div>
                ))}
              </div>
              {unmatched.length > 0 && (
                <div className="text-amber-700 mt-1">
                  <AlertTriangle size={11} className="inline mr-1" />
                  No template registered for: {unmatched.join(', ')}
                </div>
              )}
            </>
          )}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {!isSigned && envelopes.length === 0 && (
          <>
            <button className="btn-primary text-xs flex items-center gap-1"
                    onClick={() => handleSend('boldsign')}
                    disabled={!canSend || boldsignSend.isPending}
                    title={!canSend ? 'Resolve unmatched procedures first' : ''}>
              <Send size={11} /> {boldsignSend.isPending ? 'Sending…' : 'Send via BoldSign'}
            </button>
            <button className="btn-secondary text-xs flex items-center gap-1"
                    onClick={() => sentManual.mutate()}
                    disabled={sentManual.isPending}
                    title="Use this if you sent on paper / fax">
              <FileText size={11} /> {sentManual.isPending ? 'Saving…' : 'Mark sent (paper)'}
            </button>
          </>
        )}
        {!isSigned && envelopes.length > 0 && (
          <>
            <button className="btn-secondary text-xs flex items-center gap-1"
                    onClick={() => boldsignSync.mutate()}
                    disabled={boldsignSync.isPending}
                    title="Pull latest status from BoldSign">
              <RefreshCw size={11} className={boldsignSync.isPending ? 'animate-spin' : ''} />
              {boldsignSync.isPending ? 'Checking…' : 'Refresh from BoldSign'}
            </button>
            <button className="btn-secondary text-xs flex items-center gap-1"
                    onClick={() => docusignSync.mutate()}
                    disabled={docusignSync.isPending}
                    title="Pull latest status from DocuSign (legacy envelopes)">
              <RefreshCw size={11} className={docusignSync.isPending ? 'animate-spin' : ''} />
              {docusignSync.isPending ? 'Checking…' : 'Refresh from DocuSign'}
            </button>
          </>
        )}
        {!isSigned && (
          <button className="btn-secondary text-xs flex items-center gap-1"
                  onClick={() => signedManual.mutate()}
                  disabled={signedManual.isPending}
                  title="Manual override — patient signed in person">
            <Check size={11} /> {signedManual.isPending ? 'Saving…' : 'Mark signed (manual)'}
          </button>
        )}
      </div>
    </div>
  )
}


function BenefitsPanel({ surgery }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({
    deductible:        surgery.deductible || '',
    deductible_met:    surgery.deductible_met || '',
    copay:             surgery.copay || '',
    coinsurance_pct:   surgery.coinsurance_pct || '',
    oop_max:           surgery.oop_max || '',
    oop_met:           surgery.oop_met || '',
    allowed_amount:    surgery.allowed_amount || '',
    secondary_deductible:       surgery.secondary_deductible || '',
    secondary_deductible_met:   surgery.secondary_deductible_met || '',
    secondary_copay:            surgery.secondary_copay || '',
    secondary_coinsurance_pct:  surgery.secondary_coinsurance_pct || '',
    secondary_oop_max:          surgery.secondary_oop_max || '',
    secondary_oop_met:          surgery.secondary_oop_met || '',
    card_on_file:               !!surgery.card_on_file,
  })
  const [manual, setManual] = useState({ method: 'modpay', amount: '', note: '' })
  const [manualError, setManualError] = useState(null)
  const hasSecondary = !!(surgery.secondary_insurance || '').trim()
  const [savedFlash, setSavedFlash] = useState(false)
  const [lastPdfUrl, setLastPdfUrl] = useState(null)

  function num(k) {
    const v = form[k]
    if (v === '' || v === null || v === undefined) return 0
    const n = parseFloat(v)
    return Number.isFinite(n) ? n : 0
  }

  // Live calculation — primary first, then secondary if present.
  function _payerShare(base, deductible, ded_met, copay, coins_pct, oop_max, oop_met) {
    const ded_remaining = Math.max(0, deductible - ded_met)
    const oop_remaining = oop_max > 0 ? Math.max(0, oop_max - oop_met) : Infinity
    const ded_portion   = Math.min(base, ded_remaining)
    const after_ded     = Math.max(0, base - ded_portion)
    const coins_portion = round2(after_ded * (coins_pct / 100))
    const raw           = ded_portion + coins_portion + copay
    const final         = round2(Math.min(raw, oop_remaining))
    return {
      ded_remaining: round2(ded_remaining),
      ded_portion:   round2(ded_portion),
      after_ded:     round2(after_ded),
      coins_portion,
      copay:         round2(copay),
      raw:           round2(raw),
      patient_owed:  final,
      capped:        raw > oop_remaining,
      oop_remaining: oop_remaining === Infinity ? null : round2(oop_remaining),
    }
  }
  const calc = useMemo(() => {
    const primary = _payerShare(
      num('allowed_amount'),
      num('deductible'), num('deductible_met'),
      num('copay'),
      num('coinsurance_pct'),
      num('oop_max'), num('oop_met'),
    )
    let secondary = null
    let patient_final = primary.patient_owed
    if (hasSecondary) {
      secondary = _payerShare(
        primary.patient_owed,
        num('secondary_deductible'), num('secondary_deductible_met'),
        num('secondary_copay'),
        num('secondary_coinsurance_pct'),
        num('secondary_oop_max'), num('secondary_oop_met'),
      )
      patient_final = secondary.patient_owed
    }
    return {
      // back-compat fields for the existing breakdown UI
      ded_remaining: primary.ded_remaining,
      ded_portion:   primary.ded_portion,
      after_ded:     primary.after_ded,
      coins_portion: primary.coins_portion,
      copay:         primary.copay,
      raw:           primary.raw,
      final:         patient_final,
      capped:        primary.capped,
      oop_remaining: primary.oop_remaining,
      primary, secondary,
    }
  }, [form, hasSecondary])

  const save = useMutation({
    mutationFn: () => api.post(`/surgery/${surgery.id}/benefits`, {
      deductible: numOrNull(form.deductible),
      deductible_met: numOrNull(form.deductible_met),
      copay: numOrNull(form.copay),
      coinsurance_pct: numOrNull(form.coinsurance_pct),
      oop_max: numOrNull(form.oop_max),
      oop_met: numOrNull(form.oop_met),
      allowed_amount: numOrNull(form.allowed_amount),
      secondary_deductible:       numOrNull(form.secondary_deductible),
      secondary_deductible_met:   numOrNull(form.secondary_deductible_met),
      secondary_copay:            numOrNull(form.secondary_copay),
      secondary_coinsurance_pct:  numOrNull(form.secondary_coinsurance_pct),
      secondary_oop_max:          numOrNull(form.secondary_oop_max),
      secondary_oop_met:          numOrNull(form.secondary_oop_met),
      card_on_file:               !!form.card_on_file,
      save: true,
    }).then(r => r.data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
      qc.invalidateQueries({ queryKey: ['surgery-files', surgery.id] })
      if (data?.pdf_download_url) {
        setLastPdfUrl(data.pdf_download_url)
      }
      setSavedFlash(true)
      setTimeout(() => setSavedFlash(false), 6000)
    },
  })

  function update(k, v) {
    setForm(prev => ({ ...prev, [k]: v }))
  }

  const recordManual = useMutation({
    mutationFn: () => api.post(`/surgery/${surgery.id}/payments/manual`, {
      method: manual.method,
      amount: Number(manual.amount),
      note:   manual.note || null,
    }).then(r => r.data),
    onSuccess: () => {
      setManual({ method: 'modpay', amount: '', note: '' })
      setManualError(null)
      qc.invalidateQueries({ queryKey: ['surgery', surgery.id] })
      qc.invalidateQueries({ queryKey: ['surgery-payments', surgery.id] })
    },
    onError: (e) => {
      const d = e?.response?.data?.detail
      setManualError(typeof d === 'string' ? d : (e?.message || 'Save failed'))
    },
  })

  return (
    <div className="card !p-3 mt-3">
      <div className="flex items-center gap-1.5 mb-2 flex-wrap">
        <Calculator size={14} className="text-emerald-700" />
        <h3 className="text-sm font-semibold text-gray-800">Benefits calculator</h3>
        <span className="text-[11px] text-gray-500">Patient responsibility for this surgery</span>
        <span className="flex-1" />
        <FeeScheduleButton
          surgeryId={surgery.id}
          onApplied={(allowed) => update('allowed_amount', String(allowed))} />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs mb-3">
        <DollarInput label="Allowed amount"
                      value={form.allowed_amount}
                      onChange={v => update('allowed_amount', v)}
                      hint="What insurance considers reasonable for this procedure" />
        <DollarInput label="Deductible (annual)"
                      value={form.deductible}
                      onChange={v => update('deductible', v)} />
        <DollarInput label="Deductible met"
                      value={form.deductible_met}
                      onChange={v => update('deductible_met', v)}
                      hint="What patient has paid toward deductible YTD" />
        <PercentInput label="Coinsurance %"
                       value={form.coinsurance_pct}
                       onChange={v => update('coinsurance_pct', v)} />
        <DollarInput label="Copay"
                      value={form.copay}
                      onChange={v => update('copay', v)}
                      hint="Fixed copay, if any" />
        <DollarInput label="OOP max (annual)"
                      value={form.oop_max}
                      onChange={v => update('oop_max', v)}
                      hint="Annual out-of-pocket max" />
        <DollarInput label="OOP met"
                      value={form.oop_met}
                      onChange={v => update('oop_met', v)} />
      </div>

      {hasSecondary && (
        <>
          <div className="text-[11px] uppercase tracking-wide text-plum-700 font-semibold mb-2">
            Secondary insurance · {surgery.secondary_insurance}
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs mb-3">
            <DollarInput label="Secondary deductible (annual)"
                          value={form.secondary_deductible}
                          onChange={v => update('secondary_deductible', v)} />
            <DollarInput label="Secondary deductible met"
                          value={form.secondary_deductible_met}
                          onChange={v => update('secondary_deductible_met', v)} />
            <PercentInput label="Secondary coinsurance %"
                           value={form.secondary_coinsurance_pct}
                           onChange={v => update('secondary_coinsurance_pct', v)} />
            <DollarInput label="Secondary copay"
                          value={form.secondary_copay}
                          onChange={v => update('secondary_copay', v)} />
            <DollarInput label="Secondary OOP max (annual)"
                          value={form.secondary_oop_max}
                          onChange={v => update('secondary_oop_max', v)} />
            <DollarInput label="Secondary OOP met"
                          value={form.secondary_oop_met}
                          onChange={v => update('secondary_oop_met', v)} />
          </div>
        </>
      )}

      {/* Card-on-file + manual payment row */}
      <div className="bg-gray-50 border border-gray-200 rounded p-3 mb-3 space-y-2">
        <label className="inline-flex items-center gap-2 text-[12px] text-gray-800">
          <input type="checkbox"
                  checked={!!form.card_on_file}
                  onChange={e => update('card_on_file', e.target.checked)} />
          Patient has a card on file
        </label>
        <div>
          <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">
            Record a payment already received (reduces the estimate)
          </div>
          <div className="grid grid-cols-1 md:grid-cols-[140px_120px_1fr_auto] gap-2 items-end">
            <select className="input text-xs"
                     value={manual.method}
                     onChange={e => setManual({ ...manual, method: e.target.value })}>
              <option value="modpay">ModMed Pay</option>
              <option value="check">Check</option>
              <option value="cash">Cash</option>
              <option value="other">Other</option>
            </select>
            <input type="number" min="0" step="0.01"
                    placeholder="Amount"
                    className="input text-xs font-mono"
                    value={manual.amount}
                    onChange={e => setManual({ ...manual, amount: e.target.value })} />
            <input type="text" placeholder="Note (optional — check #, reference, etc.)"
                    className="input text-xs"
                    value={manual.note}
                    onChange={e => setManual({ ...manual, note: e.target.value })} />
            <button className="btn-secondary text-xs flex items-center gap-1"
                    onClick={() => recordManual.mutate()}
                    disabled={recordManual.isPending || !manual.amount || Number(manual.amount) <= 0}>
              <DollarSign size={11} />
              {recordManual.isPending ? 'Saving…' : 'Record payment'}
            </button>
          </div>
          {manualError && <div className="text-[11px] text-red-700 mt-1">{manualError}</div>}
        </div>
      </div>

      {/* Live breakdown */}
      <div className="bg-plum-50/40 border border-plum-100 rounded p-3 text-xs mb-3">
        <div className="text-[10px] uppercase tracking-wide text-plum-700 font-semibold mb-1">
          Live preview
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <Stat label="Deductible portion"   val={`$${calc.ded_portion.toFixed(2)}`} />
          <Stat label="Coinsurance portion"  val={`$${calc.coins_portion.toFixed(2)}`}
                  sub={calc.after_ded > 0 ? `${form.coinsurance_pct || 0}% of $${calc.after_ded.toFixed(2)}` : null} />
          <Stat label="Copay"                val={`$${(calc.copay || 0).toFixed(2)}`} />
          <Stat label="Patient owes"         val={`$${calc.final.toFixed(2)}`}
                  big tone={calc.capped ? 'amber' : 'green'}
                  sub={calc.capped ? '⚠ Capped by OOP max' : null} />
        </div>
        {calc.oop_remaining !== null && (
          <div className="text-[10px] text-gray-500 mt-2">
            OOP remaining for the year: ${calc.oop_remaining.toFixed(2)}
            {calc.ded_remaining > 0 && ` · Deductible remaining: $${calc.ded_remaining.toFixed(2)}`}
          </div>
        )}
      </div>

      <div className="flex flex-wrap justify-between items-center gap-2">
        <div className="text-[11px] text-gray-600">
          {savedFlash
            ? <span className="text-green-700">✓ Saved · milestone advanced · PDF estimate ready</span>
            : (surgery.benefits_verified_at
                ? <>Last saved: <strong>{fmt.date(surgery.benefits_verified_at)}</strong></>
                : <>Not saved yet</>)}
        </div>
        <div className="flex items-center gap-2">
          {lastPdfUrl && (
            <a href={lastPdfUrl} download
                className="text-xs text-plum-700 hover:underline flex items-center gap-1">
              <Download size={11} /> Download estimate PDF
            </a>
          )}
          <button className="btn-primary text-xs flex items-center gap-1"
                  onClick={() => save.mutate()}
                  disabled={save.isPending}>
            <Save size={11} /> {save.isPending ? 'Saving…' : 'Save + generate PDF'}
          </button>
        </div>
      </div>
      {savedFlash && lastPdfUrl && (
        <div className="mt-2 text-[11px] text-gray-600 bg-green-50 border border-green-200 rounded p-2">
          A patient-facing estimate PDF was generated. <a href={lastPdfUrl} download className="text-plum-700 hover:underline font-semibold">Download it here</a> — you can email or print it for the patient.
        </div>
      )}
    </div>
  )
}


function DollarInput({ label, value, onChange, hint }) {
  return (
    <div>
      <label className="text-[10px] uppercase tracking-wide text-gray-500 block mb-1">
        {label}
      </label>
      <div className="relative">
        <span className="absolute left-2 top-1.5 text-gray-400">$</span>
        <input type="number" min="0" step="0.01"
                className="input text-xs font-mono pl-5 w-full"
                value={value}
                onChange={e => onChange(e.target.value)} />
      </div>
      {hint && <div className="text-[9px] text-gray-400 mt-0.5">{hint}</div>}
    </div>
  )
}


function PercentInput({ label, value, onChange }) {
  return (
    <div>
      <label className="text-[10px] uppercase tracking-wide text-gray-500 block mb-1">
        {label}
      </label>
      <div className="relative">
        <input type="number" min="0" max="100" step="1"
                className="input text-xs font-mono pr-5 w-full"
                value={value}
                onChange={e => onChange(e.target.value)} />
        <span className="absolute right-2 top-1.5 text-gray-400">%</span>
      </div>
    </div>
  )
}


function Stat({ label, val, sub, big, tone }) {
  const tones = {
    green: 'text-green-700',
    amber: 'text-amber-700',
  }
  return (
    <div>
      <div className="text-[9px] uppercase tracking-wide text-gray-400">{label}</div>
      <div className={`${big ? 'text-lg' : 'text-sm'} font-bold ${tones[tone] || 'text-gray-800'}`}>{val}</div>
      {sub && <div className="text-[9px] text-gray-500">{sub}</div>}
    </div>
  )
}


function round2(n) {
  return Math.round(n * 100) / 100
}


function numOrNull(v) {
  if (v === '' || v === null || v === undefined) return null
  const n = parseFloat(v)
  return Number.isFinite(n) ? n : null
}


// ─── LARC office-procedure device picker ──────────────────────────────
//
// Surfaces a card on the surgery page when the procedure list looks like
// it needs an office-procedure device (NovaSure / Bensta). Hidden when no
// match unless an assignment already exists. Once a device is picked, the
// card shows the device + a deep link into the LARC assignment.

const OP_PROCEDURE_HINTS = [
  { needle: 'ablation',   device: 'NovaSure' },
  { needle: 'novasure',   device: 'NovaSure' },
  { needle: 'endometrial', device: 'NovaSure' },
  { needle: 'polyp',      device: 'Benesta'   },
  { needle: 'polypectomy', device: 'Benesta'  },
  { needle: 'benesta',    device: 'Benesta'   },
  { needle: 'bensta',     device: 'Benesta'   },
  { needle: 'd&c',        device: 'Benesta'   },
  { needle: 'hysteroscopy', device: 'Benesta' },
]

function inferOpDeviceHint(surgery) {
  const procText = (surgery.procedures || [])
    .map(p => `${p.cpt || ''} ${p.description || ''}`)
    .join(' ')
    .toLowerCase()
  const dxText = (surgery.diagnoses || [])
    .map(d => `${d.icd10 || ''} ${d.description || ''}`)
    .join(' ')
    .toLowerCase()
  const all = `${procText} ${dxText}`
  for (const h of OP_PROCEDURE_HINTS) {
    if (all.includes(h.needle)) return h.device
  }
  return null
}


function RequestDevicePanel({ surgery }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [typeId, setTypeId] = useState('')
  const [notes, setNotes] = useState('')

  const { data: types } = useQuery({
    queryKey: ['larc-device-types'],
    queryFn: () => api.get('/larc/device-types').then(r => r.data),
    staleTime: 60_000,
  })

  // Existing pharmacy_order assignments already attached to this surgery
  const { data: existing } = useQuery({
    queryKey: ['larc-assignments-by-surgery', surgery.id],
    queryFn: () => api.get('/larc/assignments', {
      params: { linked_surgery_id: surgery.id, include_completed: true },
    }).then(r => r.data),
    staleTime: 30_000,
  })
  const pendingOrders = (existing?.assignments || []).filter(
    a => a.source_flow === 'pharmacy_order' && a.device_id == null
  )

  const create = useMutation({
    mutationFn: () => api.post('/larc/assignments', {
      chart_number:       surgery.chart_number || '',
      patient_name:       surgery.patient_name || '',
      patient_dob:        surgery.dob || null,
      patient_email:      surgery.email || null,
      patient_phone:      surgery.cell_phone || surgery.phone || null,
      primary_insurance:  surgery.primary_insurance || null,
      source_flow:        'pharmacy_order',
      device_type_id:     typeId,
      linked_surgery_id:  surgery.id,
      notes:              notes || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['larc-assignments-by-surgery', surgery.id] })
      qc.invalidateQueries({ queryKey: ['larc-dashboard'] })
      qc.invalidateQueries({ queryKey: ['larc-assignments'] })
      setOpen(false); setTypeId(''); setNotes('')
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Request failed'),
  })

  return (
    <div className="space-y-2 text-[12px]">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-1.5">
          <Package size={14} className="text-teal-700" />
          Device Request
        </h3>
      </div>

      {pendingOrders.length > 0 && (
        <div className="space-y-1">
          {pendingOrders.map(a => (
            <div key={a.id}
                 className="flex items-baseline justify-between border-l-2 border-teal-300 pl-2 py-1 bg-teal-50/50 rounded">
              <div>
                <Link to={`/larc/assignments/${a.id}`}
                      className="text-plum-700 hover:underline font-medium">
                  {a.device_type_name || 'Device requested'}
                </Link>
                <div className="text-[10px] text-gray-500">
                  Pharmacy order · status: {a.status.replace(/_/g, ' ')}
                </div>
              </div>
              <span className="text-[10px] uppercase tracking-wide bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">
                pending
              </span>
            </div>
          ))}
        </div>
      )}

      {!open ? (
        <button type="button"
                className="btn-secondary text-xs flex items-center gap-1"
                onClick={() => setOpen(true)}>
          <Package size={11} /> Request Device
        </button>
      ) : (
        <div className="border border-teal-200 bg-white rounded p-2 space-y-2">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Device type</div>
            <select className="input text-[12px] w-full"
                    value={typeId}
                    onChange={e => setTypeId(e.target.value)}>
              <option value="">— pick a device type —</option>
              {(types || []).map(t => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </select>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Notes (optional)</div>
            <textarea className="input text-[12px] w-full" rows={2}
                      value={notes}
                      onChange={e => setNotes(e.target.value)}
                      placeholder="Anything the pharmacy / rep needs to know" />
          </div>
          <div className="flex gap-1">
            <button className="btn-primary text-[11px]"
                    onClick={() => create.mutate()}
                    disabled={!typeId || create.isPending}>
              {create.isPending ? 'Requesting…' : 'Submit request'}
            </button>
            <button className="text-[11px] text-muted hover:underline"
                    onClick={() => { setOpen(false); setTypeId(''); setNotes('') }}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}


function LarcDevicePickerCard({ surgery, flat = false }) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const inferred = inferOpDeviceHint(surgery)

  const { data: existing } = useQuery({
    queryKey: ['larc-assignments-by-surgery', surgery.id],
    queryFn: () => api.get('/larc/assignments', {
      params: { linked_surgery_id: surgery.id, include_completed: true },
    }).then(r => r.data),
    staleTime: 30_000,
  })
  const assignments = existing?.assignments || []

  // Only render the card when there's already an assignment OR the procedure
  // text suggests an office-procedure device is needed. Otherwise it would
  // be noise on every surgery page.
  if (assignments.length === 0 && !inferred) return null

  const Wrap = flat ? 'div' : 'div'
  const wrapClass = flat ? '' : 'card mt-3'
  return (
    <Wrap className={wrapClass}>
      <div className="flex items-center gap-2 mb-2">
        <Package size={14} className="text-teal-700" />
        <h3 className="text-sm font-semibold text-gray-800">Office-procedure device</h3>
        <span className="text-[10px] uppercase tracking-wide bg-teal-100 text-teal-700 px-2 py-0.5 rounded">
          LARC inventory
        </span>
      </div>

      {assignments.length === 0 ? (
        <div className="text-[12px] space-y-2">
          <div className="text-gray-600">
            This surgery looks like it needs a{' '}
            <strong className="text-teal-700">{inferred}</strong> device.
            Pick one from inventory before the procedure.
          </div>
          <button type="button" className="btn-secondary text-[11px] inline-flex items-center gap-1"
                  onClick={() => setPickerOpen(true)}>
            <Package size={11} /> Pick {inferred} device
          </button>
        </div>
      ) : (
        <ul className="space-y-2 text-[12px]">
          {assignments.map(a => (
            <li key={a.id} className="flex items-baseline justify-between border-l-2 border-teal-300 pl-2 py-1">
              <div>
                <Link to={`/larc/assignments/${a.id}`} className="text-plum-700 hover:underline font-medium">
                  {a.device_type_name} #{a.device_our_id || '—'}
                </Link>
                <div className="text-[10px] text-gray-500">
                  Status: {a.status.replace(/_/g, ' ')}
                  {a.claim_number && ` · claim #${a.claim_number}`}
                </div>
              </div>
              <span className={`text-[10px] uppercase tracking-wide px-2 py-0.5 rounded ${
                a.status === 'billed' ? 'bg-green-100 text-green-700' :
                a.status === 'inserted' ? 'bg-blue-100 text-blue-700' :
                'bg-amber-100 text-amber-700'
              }`}>
                {a.status.replace(/_/g, ' ')}
              </span>
            </li>
          ))}
          {assignments.every(a => ['billed', 'cancelled'].includes(a.status)) && inferred && (
            <li>
              <button type="button" className="btn-secondary text-[11px] inline-flex items-center gap-1"
                      onClick={() => setPickerOpen(true)}>
                <Package size={11} /> Pick another {inferred} device
              </button>
            </li>
          )}
        </ul>
      )}

      {pickerOpen && (
        <LarcDevicePickerDrawer surgery={surgery} preferred={inferred}
                                 onClose={() => setPickerOpen(false)} />
      )}
    </Wrap>
  )
}


function LarcDevicePickerDrawer({ surgery, preferred, onClose }) {
  const qc = useQueryClient()
  const [deviceId, setDeviceId] = useState('')

  const { data: devices, isLoading } = useQuery({
    queryKey: ['larc-unallocated-op'],
    queryFn: () => api.get('/larc/devices/unallocated', {
      params: { category: 'office_procedure' },
    }).then(r => r.data),
  })

  // Prefer devices of the matching type, sort to top
  const sorted = useMemo(() => {
    const list = devices || []
    if (!preferred) return list
    return [...list].sort((a, b) => {
      const am = a.device_type_name === preferred ? 0 : 1
      const bm = b.device_type_name === preferred ? 0 : 1
      return am - bm
    })
  }, [devices, preferred])

  const create = useMutation({
    mutationFn: () => api.post('/larc/assignments/office-procedure', {
      device_id: deviceId,
      chart_number: surgery.chart_number || '',
      patient_name: surgery.patient_name || '',
      patient_dob: surgery.dob || null,
      primary_insurance: surgery.primary_insurance || null,
      linked_surgery_id: surgery.id,
      appt_date: surgery.scheduled_date || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['larc-assignments-by-surgery', surgery.id] })
      qc.invalidateQueries({ queryKey: ['larc-unallocated-op'] })
      qc.invalidateQueries({ queryKey: ['larc-dashboard'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Could not pick device'),
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">
            Pick office-procedure device
          </h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <div className="text-[11px] text-gray-600">
            For <strong>{surgery.patient_name}</strong> (chart #{surgery.chart_number}).
            Linking this device to surgery <span className="font-mono">{surgery.id.slice(0, 8)}</span>.
          </div>

          {isLoading && <div className="text-gray-400 italic">Loading unallocated devices…</div>}

          {!isLoading && sorted.length === 0 && (
            <div className="text-[12px] bg-amber-50 border border-amber-200 px-2 py-2 rounded">
              No unallocated office-procedure devices on hand. Add some on the{' '}
              <Link to="/larc/devices" className="underline">devices page</Link>.
            </div>
          )}

          {!isLoading && sorted.length > 0 && (
            <ul className="space-y-1.5">
              {sorted.map(d => (
                <li key={d.id}>
                  <label className={`flex items-center gap-2 text-[12px] border rounded px-2 py-1.5 cursor-pointer hover:bg-teal-50 ${
                    deviceId === d.id ? 'bg-teal-50 border-teal-400' : 'border-gray-200'
                  }`}>
                    <input type="radio" name="device" value={d.id}
                           checked={deviceId === d.id}
                           onChange={() => setDeviceId(d.id)} />
                    <span className="font-mono font-semibold">{d.our_id}</span>
                    <span className="text-gray-600">{d.device_type_name}</span>
                    <span className="ml-auto text-[10px] text-gray-500">
                      {d.location_label}
                      {d.expiration_date && ` · exp ${d.expiration_date}`}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
          <button className="text-sm text-muted hover:underline" onClick={onClose}>Cancel</button>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => create.mutate()}
                  disabled={!deviceId || create.isPending}>
            <Save size={12} /> {create.isPending ? 'Picking…' : 'Pick device'}
          </button>
        </div>
      </div>
    </div>
  )
}


// ─── Schedule-for-patient modal ───────────────────────────────────
// Coordinator picks a block day + start time + duration (with override
// reason if >10% off the template default), then calls POST /surgery/:id/schedule.

function _hhmm(timeStr) {
  // Normalize "HH:MM:SS" or "HH:MM" to "HH:MM"
  return (timeStr || '').slice(0, 5)
}

function _blockAvailableStarts(bd) {
  // Generate 15-min increments from block start to 60 min before end,
  // skipping already-booked slot start times.
  const booked = new Set((bd.slots || []).map(sl => _hhmm(sl.start_time)))
  const [sh, sm] = _hhmm(bd.start_time).split(':').map(Number)
  const [eh, em] = _hhmm(bd.end_time).split(':').map(Number)
  const startMin = sh * 60 + sm
  const endMin = eh * 60 + em - 60   // leave 60 min gap at end
  const results = []
  for (let t = startMin; t <= endMin; t += 15) {
    const hh = String(Math.floor(t / 60)).padStart(2, '0')
    const mm = String(t % 60).padStart(2, '0')
    const label = `${hh}:${mm}`
    if (!booked.has(label)) results.push(label)
  }
  return results
}

const PROCEDURE_KIND_FALLBACK_DURATION = {
  office: 30, minor: 60, major: 120,
  robotic_180: 180, robotic_240: 240,
}

function ScheduleForPatientModal({ surgery, templates, onClose, onSaved }) {
  const [selected, setSelected] = useState(null)   // { block_day_id, start_time }
  const [duration, setDuration] = useState(null)
  const [overrideReason, setOverrideReason] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['surgery-block-days-for-schedule', surgery.selected_facility],
    queryFn: () => api.get('/surgery/admin/block-days', {
      params: { facility: surgery.selected_facility || undefined, days: 60 },
    }).then(r => r.data),
    staleTime: 30_000,
  })

  const templateDefault = useMemo(() => {
    const kind = surgery.procedure_classification
    const t = templates.find(t => t.procedure_kind === kind && t.is_active !== false)
    if (t) return t.default_duration_minutes
    return PROCEDURE_KIND_FALLBACK_DURATION[kind] || 60
  }, [surgery.procedure_classification, templates])

  useEffect(() => {
    setDuration(templateDefault)
  }, [templateDefault])

  const schedule = useMutation({
    mutationFn: () =>
      api.post(`/surgery/${surgery.id}/schedule`, {
        block_day_id: selected.block_day_id,
        start_time:   selected.start_time,
        duration_minutes: duration,
        override_reason: overrideReason.trim() || undefined,
      }).then(r => r.data),
    onSuccess: () => { onSaved(); onClose() },
    onError: (e) => alert(e?.response?.data?.detail || 'Schedule failed'),
  })

  const durationOff = duration != null && Math.abs(duration - templateDefault) > templateDefault * 0.10
  const needsReason = durationOff && !overrideReason.trim()

  const days = data?.days || []

  return (
    <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center"
         onClick={onClose}>
      <div className="bg-white rounded-lg w-full max-w-2xl shadow-xl overflow-hidden"
           onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-border-subtle">
          <div>
            <h2 className="font-serif font-semibold text-ink text-[16px]">
              Schedule for patient — {surgery.patient_name}
            </h2>
            <div className="text-muted text-[11px] mt-0.5">
              {(surgery.procedures || []).map(p => p.description || p).join(', ')}
              {surgery.procedure_classification && ` · ${surgery.procedure_classification}`}
            </div>
          </div>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        {/* Block day list */}
        <div className="p-5 max-h-96 overflow-y-auto space-y-3">
          {isLoading && (
            <div className="text-gray-500 text-sm">Loading block days…</div>
          )}
          {!isLoading && days.length === 0 && (
            <div className="bg-amber-50 border border-amber-200 text-amber-900 p-3 rounded text-sm">
              No block days found in the next 60 days.
            </div>
          )}
          {!isLoading && days.map(bd => {
            const starts = _blockAvailableStarts(bd)
            return (
              <div key={bd.id}
                   className="border border-border-subtle rounded p-3">
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="text-sm font-medium">{bd.block_date}</span>
                  <span className="text-[11px] text-gray-500">·</span>
                  <span className="text-[11px] text-gray-600">{bd.facility}</span>
                  <span className="text-[11px] text-gray-500">·</span>
                  <span className="text-[11px] text-gray-600">{bd.block_kind}</span>
                  <span className="text-[11px] text-gray-400 ml-auto">
                    {_hhmm(bd.start_time)}–{_hhmm(bd.end_time)}
                  </span>
                </div>
                {starts.length === 0 ? (
                  <div className="text-[11px] text-gray-400 italic">No available slots</div>
                ) : (
                  <div className="flex flex-wrap gap-1">
                    {starts.map(t => {
                      const isSelected = selected?.block_day_id === bd.id && selected?.start_time === t
                      return (
                        <button key={t}
                                type="button"
                                onClick={() => setSelected({ block_day_id: bd.id, start_time: t })}
                                className={`text-[12px] px-2 py-0.5 rounded border ${
                                  isSelected
                                    ? 'border-plum-700 bg-plum-50 text-plum-800 font-medium'
                                    : 'border-gray-200 hover:bg-plum-50 hover:border-plum-300'
                                }`}>
                          {t}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
        </div>

        {/* Duration + override + confirm */}
        {selected && (
          <div className="border-t border-border-subtle px-5 py-4 space-y-3 bg-gray-50">
            <div className="flex items-center gap-2">
              <label className="text-[11px] uppercase text-gray-500 w-36">Duration (min)</label>
              <input type="number" min={5} step={5}
                     className="input text-sm w-24"
                     value={duration ?? ''}
                     onChange={e => setDuration(Number(e.target.value))} />
              <span className="text-[11px] text-gray-400">
                template default: {templateDefault} min
              </span>
            </div>
            {durationOff && (
              <div className="flex items-center gap-2">
                <label className="text-[11px] uppercase text-gray-500 w-36">Override reason</label>
                <input className="input text-sm flex-1"
                       value={overrideReason}
                       onChange={e => setOverrideReason(e.target.value)}
                       placeholder="Required — duration differs >10% from template default" />
              </div>
            )}
            <div className="flex items-center gap-2 pt-1">
              <button type="button"
                      className="btn-primary text-sm"
                      disabled={needsReason || schedule.isPending}
                      onClick={() => schedule.mutate()}>
                {schedule.isPending ? 'Scheduling…' : `Confirm — ${selected.start_time} on ${selected.block_day_id ? days.find(d => d.id === selected.block_day_id)?.block_date || '' : ''}`}
              </button>
              <button type="button" className="btn-secondary text-sm" onClick={onClose}>
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}


// ─── I7: Ad-hoc patient email composer + audit history ──────────────

function PatientEmailsSection({ surgery, flat = false }) {
  const qc = useQueryClient()
  const [composing, setComposing] = useState(false)
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [toOverride, setToOverride] = useState('')

  const { data } = useQuery({
    queryKey: ['patient-emails', surgery.id],
    queryFn: () => api.get(`/surgery/${surgery.id}/patient-emails`).then(r => r.data),
  })

  const sendMut = useMutation({
    mutationFn: (body) =>
      api.post(`/surgery/${surgery.id}/send-patient-email`, body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['patient-emails', surgery.id] })
      setComposing(false); setSubject(''); setBody(''); setToOverride('')
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Send failed'),
  })

  const emails = data?.emails || []
  const fmtDate = (iso) => (iso || '').slice(0, 16).replace('T', ' ')

  const Outer = ({ children }) => flat
    ? <>{children}</>
    : <div className="bg-white border border-border-subtle rounded-lg p-5 mb-4">{children}</div>
  return (
    <Outer>
      <div className="flex items-center justify-between mb-3">
        <h3 className={`flex items-center gap-1.5 ${flat ? "text-sm font-semibold text-gray-800" : "text-lg font-semibold"}`}>
          <Mail size={14} className="text-plum-700" /> Patient emails
        </h3>
        {!composing && (
          <button className="btn-secondary text-sm" onClick={() => setComposing(true)}>
            Compose email
          </button>
        )}
      </div>

      {composing && (
        <div className="border border-plum-200 rounded p-3 mb-3 space-y-2 bg-plum-50/40">
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-0.5">To</label>
            <input className="input text-sm w-full"
                   placeholder={surgery.email || 'patient email…'}
                   value={toOverride}
                   onChange={e => setToOverride(e.target.value)} />
            <div className="text-[11px] text-gray-500 mt-0.5">
              Defaults to {surgery.email || '(no patient email on file)'} if blank.
            </div>
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-0.5">Subject</label>
            <input className="input text-sm w-full"
                   value={subject}
                   onChange={e => setSubject(e.target.value)} />
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-0.5">
              Body (HTML allowed)
            </label>
            <textarea className="input text-sm w-full font-mono"
                      rows={6}
                      placeholder="<p>Hi {{patient_name}},</p><p>…</p>"
                      value={body}
                      onChange={e => setBody(e.target.value)} />
          </div>
          <div className="flex items-center gap-2">
            <button className="btn-primary text-sm"
                    onClick={() => sendMut.mutate({
                      subject, body_html: body,
                      to_email: toOverride.trim() || undefined,
                    })}
                    disabled={sendMut.isPending || !subject.trim() || !body.trim()}>
              {sendMut.isPending ? 'Sending…' : 'Send email'}
            </button>
            <button className="btn-secondary text-sm"
                    onClick={() => { setComposing(false); setSubject(''); setBody('') }}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {emails.length === 0 ? (
        <div className="text-[12px] text-gray-400 italic">No patient emails yet.</div>
      ) : (
        <table className="w-full text-[12px]">
          <thead className="text-[11px] uppercase text-gray-500">
            <tr>
              <th className="text-left py-1">When</th>
              <th className="text-left py-1">To</th>
              <th className="text-left py-1">Subject</th>
              <th className="text-left py-1">Kind</th>
              <th className="text-left py-1">Status</th>
            </tr>
          </thead>
          <tbody>
            {emails.map(e => (
              <tr key={e.id} className="border-t border-border-subtle">
                <td className="py-1.5 text-gray-500">{fmtDate(e.sent_at)}</td>
                <td className="py-1.5">{e.to_email}</td>
                <td className="py-1.5">{e.rendered_subject}</td>
                <td className="py-1.5 text-gray-500">{e.template_kind || 'ad-hoc'}</td>
                <td className="py-1.5">
                  <span className={`px-2 py-0.5 rounded text-[11px] ${
                    e.status === 'sent'    ? 'bg-green-100 text-green-700' :
                    e.status === 'failed'  ? 'bg-red-100 text-red-700' :
                                              'bg-amber-100 text-amber-700'
                  }`}>{e.status}</span>
                  {e.failure_reason && (
                    <div className="text-[10px] text-red-600 mt-0.5">{e.failure_reason}</div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Outer>
  )
}


// ─── J5: Per-surgery SMS audit history ──────────────────────────────

function PatientSmsSection({ surgery, flat = false }) {
  const { data } = useQuery({
    queryKey: ['patient-sms', surgery.id],
    queryFn: () => api.get(`/surgery/${surgery.id}/patient-sms`).then(r => r.data),
  })
  const messages = data?.messages || []
  const fmtDate = (iso) => (iso || '').slice(0, 16).replace('T', ' ')

  const Outer = ({ children }) => flat
    ? <>{children}</>
    : <div className="bg-white border border-border-subtle rounded-lg p-5 mb-4">{children}</div>
  return (
    <Outer>
      <h3 className={`flex items-center gap-1.5 mb-3 ${flat ? "text-sm font-semibold text-gray-800" : "text-lg font-semibold"}`}>
        <Phone size={14} className="text-plum-700" /> Patient SMS history
      </h3>
      {messages.length === 0 ? (
        <div className="text-[12px] text-gray-400 italic">No SMS activity.</div>
      ) : (
        <table className="w-full text-[12px]">
          <thead className="text-[11px] uppercase text-gray-500">
            <tr>
              <th className="text-left py-1">When</th>
              <th className="text-left py-1">To</th>
              <th className="text-left py-1">Kind</th>
              <th className="text-left py-1">Body</th>
              <th className="text-left py-1">Status</th>
            </tr>
          </thead>
          <tbody>
            {messages.map(m => (
              <tr key={m.id} className="border-t border-border-subtle">
                <td className="py-1.5 text-gray-500">{fmtDate(m.sent_at)}</td>
                <td className="py-1.5 font-mono">{m.to_phone}</td>
                <td className="py-1.5 text-gray-500">{m.template_kind || 'ad-hoc'}</td>
                <td className="py-1.5 max-w-md truncate" title={m.rendered_body}>
                  {m.rendered_body}
                </td>
                <td className="py-1.5">
                  <span className={`px-2 py-0.5 rounded text-[11px] ${
                    m.status === 'sent'    ? 'bg-green-100 text-green-700' :
                    m.status === 'failed'  ? 'bg-red-100 text-red-700' :
                                              'bg-amber-100 text-amber-700'
                  }`}>{m.status}</span>
                  {m.failure_reason && (
                    <div className="text-[10px] text-red-600 mt-0.5">{m.failure_reason}</div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Outer>
  )
}


// ─── Phase D6: Slot duration inline edit ────────────────────────────

function SlotDurationEdit({ slotId, currentMinutes, onSaved }) {
  const [editing, setEditing] = useState(false)
  const [draftMin, setDraftMin] = useState(currentMinutes)
  const [reason, setReason] = useState('')
  const save = useMutation({
    mutationFn: () => api.patch(`/surgery/slots/${slotId}`, {
      duration_minutes: draftMin,
      override_reason: reason.trim(),
    }).then(r => r.data),
    onSuccess: () => { setEditing(false); setReason(''); onSaved() },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  if (!editing) {
    return (
      <button className="text-[11px] text-plum-700 hover:underline"
              onClick={() => { setDraftMin(currentMinutes); setEditing(true) }}>
        Adjust duration ({currentMinutes}m)
      </button>
    )
  }
  return (
    <div className="flex items-center gap-1 text-[11px]">
      <input type="number" className="input text-[11px] w-16"
             value={draftMin} onChange={e => setDraftMin(Number(e.target.value))} />
      <input className="input text-[11px] flex-1"
             placeholder="Reason (required)"
             value={reason} onChange={e => setReason(e.target.value)} />
      <button className="text-plum-700 hover:underline"
              disabled={!reason.trim() || save.isPending}
              onClick={() => save.mutate()}>Save</button>
      <button className="text-gray-500 hover:underline"
              onClick={() => { setEditing(false); setReason(''); setDraftMin(currentMinutes) }}>
        Cancel
      </button>
    </div>
  )
}
