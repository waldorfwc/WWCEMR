import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import axios from 'axios'
import {
  Calendar, CheckCircle2, Clock, AlertCircle, Hospital, Building2, Lock,
  CreditCard, Phone, RotateCcw, XCircle, ArrowLeft,
} from 'lucide-react'
import logoMark from '../assets/wwc-logo.png'


// Public patient page — no app auth, no TopNav. Mobile-first layout
// since most patients click in from a Klara link on their phone.

const FACILITY_LABEL = {
  medstar: 'MedStar Southern Maryland Hospital',
  crmc:    'University of MD Charles Regional',
  office:  'Waldorf Women\'s Care — White Plains office',
}

const FACILITY_SHORT = {
  medstar: 'MedStar',
  crmc:    'Charles Regional',
  office:  'Office',
}


// Use a separate axios instance so the public page never sends the
// staff session cookie (and the backend treats it as truly public).
const publicApi = axios.create({
  baseURL: '/api',
  withCredentials: false,
})


export default function PatientSurgery() {
  const { id } = useParams()
  const [token, setToken] = useState(null)

  // Read any stashed token (so reload doesn't force re-auth within the hour)
  useEffect(() => {
    const stored = sessionStorage.getItem(`patient-token-${id}`)
    if (stored) setToken(stored)
  }, [id])

  function onAuthed(t) {
    sessionStorage.setItem(`patient-token-${id}`, t)
    setToken(t)
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-plum-50 to-white">
      <header className="bg-white border-b border-border-subtle py-4">
        <div className="max-w-2xl mx-auto px-4 flex items-center gap-3">
          <img src={logoMark} alt="WWC" className="w-9 h-9 object-contain" />
          <div className="leading-tight">
            <div className="font-serif font-semibold text-plum-700 text-[14px] tracking-wordmark">
              WWC GYNECOLOGY
            </div>
            <div className="font-serif italic text-plum-600 text-[12px] -mt-0.5">
              &amp; Aesthetics
            </div>
          </div>
        </div>
      </header>

      <main className="max-w-2xl mx-auto px-4 py-6">
        {!token
          ? <AuthScreen surgeryId={id} onAuthed={onAuthed} />
          : <ScheduleFlow surgeryId={id} token={token} onLogout={() => {
              sessionStorage.removeItem(`patient-token-${id}`)
              setToken(null)
            }} />
        }
      </main>

      <footer className="text-center text-[11px] text-gray-500 py-6">
        Need help? Call us at <a href="tel:+12402522140" className="text-plum-700">240-252-2140</a>
        <span className="mx-2">·</span>
        <span>Waldorf Women's Care</span>
      </footer>
    </div>
  )
}


function AuthScreen({ surgeryId, onAuthed }) {
  const [dob, setDob] = useState('')
  const [last4, setLast4] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function submit(e) {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      const r = await publicApi.post(`/p/surgery/${surgeryId}/auth`, {
        dob, phone_last4: last4,
      })
      onAuthed(r.data.token)
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not sign in. Please try again.')
    } finally { setBusy(false) }
  }

  return (
    <div className="bg-white rounded-lg border border-border-subtle shadow-sm p-6">
      <div className="flex items-center gap-2 mb-1 text-plum-700">
        <Lock size={16} />
        <h1 className="text-lg font-serif font-semibold">Verify your identity</h1>
      </div>
      <p className="text-xs text-gray-600 mb-4">
        We just need your date of birth and the last 4 digits of the phone number we have on file.
      </p>

      <form onSubmit={submit} className="space-y-3">
        <div>
          <label className="text-[10px] uppercase tracking-wide text-gray-500 mb-1 block">
            Date of birth
          </label>
          <input
            type="date"
            className="input text-base font-mono w-full"
            required
            value={dob}
            onChange={e => setDob(e.target.value)}
          />
        </div>
        <div>
          <label className="text-[10px] uppercase tracking-wide text-gray-500 mb-1 block">
            Last 4 digits of your phone number
          </label>
          <input
            type="tel"
            inputMode="numeric"
            pattern="\d{4}"
            maxLength={4}
            placeholder="0000"
            required
            className="input text-base font-mono w-full tracking-[0.4em] text-center"
            value={last4}
            onChange={e => setLast4(e.target.value.replace(/\D/g, ''))}
          />
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-800 text-sm p-2 rounded flex items-start gap-2">
            <AlertCircle size={14} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <button
          type="submit"
          className="btn-primary w-full text-base py-2.5 disabled:opacity-60"
          disabled={busy || !dob || last4.length !== 4}
        >
          {busy ? 'Verifying…' : 'Continue'}
        </button>

        <p className="text-[10px] text-center text-gray-500 pt-2">
          Trouble signing in? Call us at <a href="tel:+12402522140" className="text-plum-700">240-252-2140</a>.
        </p>
      </form>
    </div>
  )
}


function ScheduleFlow({ surgeryId, token, onLogout }) {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const [confirmation, setConfirmation] = useState(null)
  // mode: 'view' | 'reschedule' | 'cancel' | 'cancelled'
  const [mode, setMode] = useState('view')
  const [cancelResult, setCancelResult] = useState(null)

  const headers = { Authorization: `Bearer ${token}` }

  async function loadStatus() {
    try {
      const r = await publicApi.get(`/p/surgery/${surgeryId}/status`, { headers })
      setStatus(r.data)
    } catch (err) {
      if (err?.response?.status === 401) {
        onLogout()
        return
      }
      setError(err?.response?.data?.detail || 'Could not load your information.')
    }
  }

  useEffect(() => { loadStatus() }, [token])

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 text-red-800 text-sm p-4 rounded">
        {error}
      </div>
    )
  }

  if (!status) {
    return <div className="text-center text-gray-500 py-8">Loading…</div>
  }

  if (confirmation) {
    return <ConfirmationScreen status={status} confirmation={confirmation} />
  }

  if (cancelResult) {
    return <CancelledScreen status={status} result={cancelResult} />
  }

  // Patient just clicked Cancel — confirm screen
  if (mode === 'cancel' && status.scheduled_date) {
    return (
      <CancelConfirmScreen
        surgeryId={surgeryId}
        headers={headers}
        status={status}
        onBack={() => setMode('view')}
        onCancelled={(r) => setCancelResult(r)}
      />
    )
  }

  // Patient clicked Reschedule — show slot picker that calls /reschedule
  if (mode === 'reschedule' && status.scheduled_date) {
    return (
      <SlotPicker
        surgeryId={surgeryId}
        headers={headers}
        status={status}
        endpoint="reschedule"
        onPicked={(c) => setConfirmation(c)}
        onBack={() => setMode('view')}
      />
    )
  }

  // Already scheduled — view-only with Reschedule / Cancel actions
  if (status.scheduled_date) {
    return (
      <AlreadyScheduledScreen
        surgeryId={surgeryId}
        headers={headers}
        status={status}
        onReschedule={() => setMode('reschedule')}
        onCancel={() => setMode('cancel')}
      />
    )
  }

  // Has balance due → show payment screen
  if (!status.balance_clear) {
    return <BalanceDueScreen status={status} />
  }

  // Clearance is required and we don't yet have cardiologist info — collect it.
  // Only ask once: clearance_status starts at 'required' for clearance-needed
  // surgeries; once they've replied we move to 'request_sent'.
  if (status.clearance_required && status.clearance_status === 'required') {
    return <CardiologistAskScreen
      surgeryId={surgeryId}
      headers={headers}
      status={status}
      onUpdated={() => loadStatus()}
    />
  }

  // Locked out by other status
  if (!status.can_pick_date) {
    return (
      <div className="bg-amber-50 border border-amber-200 text-amber-900 p-4 rounded">
        <div className="font-semibold mb-1">We can't take a date pick right now.</div>
        <p className="text-sm">
          Please call our office at 240-252-2140 — we'll get this sorted with you.
        </p>
      </div>
    )
  }

  return (
    <SlotPicker
      surgeryId={surgeryId}
      headers={headers}
      status={status}
      endpoint="pick"
      onPicked={(c) => setConfirmation(c)}
    />
  )
}


function AlreadyScheduledScreen({ surgeryId, headers, status, onReschedule, onCancel }) {
  // Compute days until surgery to disable patient-facing reschedule within 14 days
  const daysUntil = (() => {
    if (!status.scheduled_date) return null
    const d = new Date(status.scheduled_date + 'T00:00:00')
    const now = new Date()
    now.setHours(0, 0, 0, 0)
    return Math.round((d - now) / 86_400_000)
  })()
  const within14Days = daysUntil !== null && daysUntil >= 0 && daysUntil <= 14

  return (
    <div className="bg-white rounded-lg border border-border-subtle shadow-sm p-6">
      <div className="flex items-center gap-2 mb-3 text-green-700">
        <CheckCircle2 size={20} />
        <h1 className="text-lg font-serif font-semibold">You're scheduled!</h1>
      </div>
      <p className="text-sm text-gray-700 mb-3">
        Hi {status.patient_first_name}, your surgery is on the books:
      </p>
      <div className="bg-plum-50 border border-plum-100 rounded p-3 text-sm space-y-1.5">
        <div><strong>Date:</strong> {fmt(status.scheduled_date)}</div>
        {status.scheduled_start_time && <div><strong>Start time:</strong> {status.scheduled_start_time}</div>}
        {status.selected_facility && (
          <div><strong>Where:</strong> {FACILITY_LABEL[status.selected_facility]}</div>
        )}
        <div><strong>Procedure:</strong> {status.procedure_descriptions?.join(', ')}</div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-2">
        <button type="button"
                onClick={onReschedule}
                disabled={within14Days}
                className="btn-secondary text-sm py-2 flex items-center justify-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                title={within14Days ? 'Within 14 days of surgery — please call us to reschedule' : ''}>
          <RotateCcw size={14} /> Reschedule
        </button>
        <button type="button"
                onClick={onCancel}
                className="text-sm py-2 flex items-center justify-center gap-1.5 border border-red-200 text-red-700 hover:bg-red-50 rounded">
          <XCircle size={14} /> Cancel surgery
        </button>
      </div>

      {within14Days && (
        <p className="text-[11px] text-amber-700 mt-2 text-center">
          Reschedules within 14 days require a call to the office. Cancellations within
          14 days may incur a $351 fee.
        </p>
      )}

      <FmlaUploadPanel surgeryId={surgeryId} headers={headers} />

      <p className="text-xs text-gray-500 mt-3 text-center">
        Questions? Call us at <a href="tel:+12402522140" className="text-plum-700">240-252-2140</a>.
      </p>
    </div>
  )
}


function FmlaUploadPanel({ surgeryId, headers }) {
  const [file, setFile] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  async function upload() {
    if (!file) return
    setSubmitting(true); setError(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await publicApi.post(`/p/surgery/${surgeryId}/upload-fmla`, fd, {
        headers: { ...headers, 'Content-Type': 'multipart/form-data' },
      })
      setResult(r.data)
      setFile(null)
    } catch (err) {
      setError(err?.response?.data?.detail || 'Upload failed. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="mt-4 border-t border-gray-100 pt-3">
      <div className="text-xs font-semibold text-gray-700 mb-1">
        Need FMLA paperwork filled out?
      </div>
      <p className="text-[11px] text-gray-600 mb-2">
        Upload your FMLA forms here and our office will fill them out and return them
        to you. Accepted: PDF or image (JPG / PNG / HEIC), up to 10 MB.
      </p>
      <input type="file"
             className="text-[11px] mb-1"
             accept="application/pdf,image/jpeg,image/png,image/heic"
             onChange={e => { setFile(e.target.files?.[0] || null); setError(null); setResult(null) }} />
      <div className="flex items-center gap-2 mt-1">
        <button type="button"
                onClick={upload}
                disabled={!file || submitting}
                className="btn-secondary text-xs">
          {submitting ? 'Uploading…' : 'Upload FMLA'}
        </button>
        {result && (
          <span className="text-[11px] text-green-700">✓ {result.message || 'Received'}</span>
        )}
      </div>
      {error && (
        <div className="text-[11px] text-red-700 mt-1">{error}</div>
      )}
    </div>
  )
}


function CancelConfirmScreen({ surgeryId, headers, status, onBack, onCancelled }) {
  const [reasonText, setReasonText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  const daysUntil = (() => {
    if (!status.scheduled_date) return null
    const d = new Date(status.scheduled_date + 'T00:00:00')
    const now = new Date()
    now.setHours(0, 0, 0, 0)
    return Math.round((d - now) / 86_400_000)
  })()
  const feeLikely = daysUntil !== null && daysUntil >= 0 && daysUntil <= 14

  async function submit() {
    setSubmitting(true); setError(null)
    try {
      const r = await publicApi.post(`/p/surgery/${surgeryId}/cancel`,
        { reason_text: reasonText || null }, { headers })
      onCancelled(r.data)
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not cancel — please call our office.')
      setSubmitting(false)
    }
  }

  return (
    <div className="bg-white rounded-lg border border-red-200 shadow-sm p-6">
      <div className="flex items-center gap-2 mb-3 text-red-700">
        <XCircle size={20} />
        <h1 className="text-lg font-serif font-semibold">Cancel your surgery?</h1>
      </div>
      <p className="text-sm text-gray-700 mb-3">
        Hi {status.patient_first_name}, you're about to cancel your surgery scheduled
        for <strong>{fmt(status.scheduled_date)}</strong>. This cannot be undone from
        this page.
      </p>

      {feeLikely && (
        <div className="bg-amber-50 border border-amber-200 text-amber-900 text-sm p-3 rounded mb-3">
          <div className="font-semibold mb-1">Important — possible cancellation fee</div>
          <p className="text-xs">
            Cancellations within 14 days of surgery are subject to a <strong>$351 fee</strong>
            {' '}per practice policy. Our office will be in touch with you about the fee.
          </p>
        </div>
      )}

      <label className="block text-[11px] uppercase tracking-wide text-gray-500 mb-1">
        Reason (optional)
      </label>
      <textarea className="input w-full text-sm" rows={3}
                placeholder="Tell us briefly why — this helps us plan and may waive the fee in some cases."
                value={reasonText}
                onChange={e => setReasonText(e.target.value)} />

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-800 text-sm p-2 rounded mt-2">
          {error}
        </div>
      )}

      <div className="flex gap-2 mt-4">
        <button type="button"
                onClick={onBack}
                disabled={submitting}
                className="btn-secondary flex-1 text-base py-2 flex items-center justify-center gap-1.5">
          <ArrowLeft size={14} /> Don't cancel
        </button>
        <button type="button"
                onClick={submit}
                disabled={submitting}
                className="flex-1 text-base py-2 bg-red-600 hover:bg-red-700 text-white rounded font-medium disabled:opacity-60">
          {submitting ? 'Cancelling…' : 'Yes, cancel my surgery'}
        </button>
      </div>
    </div>
  )
}


function CancelledScreen({ status, result }) {
  return (
    <div className="bg-white rounded-lg border border-border-subtle shadow-sm p-6">
      <div className="flex items-center gap-2 mb-3 text-gray-700">
        <XCircle size={20} />
        <h1 className="text-lg font-serif font-semibold">Surgery cancelled</h1>
      </div>
      <p className="text-sm text-gray-700 mb-3">
        Your surgery has been cancelled. We're sorry we won't be seeing you on
        the previously scheduled date.
      </p>
      {result.fee_required && (
        <div className="bg-amber-50 border border-amber-200 text-amber-900 text-sm p-3 rounded mb-3">
          A <strong>$351 cancellation fee</strong> applies per practice policy.
          Our office will contact you about the fee.
        </div>
      )}
      {result.refund_required && (
        <div className="bg-blue-50 border border-blue-200 text-blue-900 text-sm p-3 rounded mb-3">
          Any amount you've already paid will be refunded — typically within 5–7
          business days.
        </div>
      )}
      <p className="text-xs text-gray-600">
        Questions? Call our office at <a href="tel:+12402522140" className="text-plum-700">240-252-2140</a>.
      </p>
    </div>
  )
}


function BalanceDueScreen({ status }) {
  return (
    <div className="bg-white rounded-lg border border-border-subtle shadow-sm p-6">
      <div className="flex items-center gap-2 mb-3 text-amber-700">
        <CreditCard size={20} />
        <h1 className="text-lg font-serif font-semibold">Balance due — please pay first</h1>
      </div>
      <p className="text-sm text-gray-700 mb-3">
        Hi {status.patient_first_name},
      </p>
      <p className="text-sm text-gray-700 mb-3">
        Before you can pick a surgery date, please pay your portion through ModMed Pay.
      </p>
      <div className="bg-amber-50 border border-amber-200 rounded p-3 text-sm space-y-1">
        <div className="flex justify-between"><span>Procedure</span>
          <span className="text-right">{status.procedure_descriptions?.join(', ')}</span>
        </div>
        <div className="flex justify-between"><span>Total responsibility</span>
          <span className="font-mono">${status.patient_responsibility?.toFixed(2)}</span>
        </div>
        <div className="flex justify-between"><span>Already paid</span>
          <span className="font-mono">${status.amount_paid?.toFixed(2)}</span>
        </div>
        <div className="flex justify-between border-t border-amber-300 pt-1 mt-1 font-semibold">
          <span>Balance due</span>
          <span className="font-mono">${status.balance_due?.toFixed(2)}</span>
        </div>
      </div>
      <p className="text-xs text-gray-600 mt-3">
        Pay through your ModMed patient portal or call <a href="tel:+12402522140" className="text-plum-700">240-252-2140</a> for a payment plan.
        Once your balance is $0, refresh this page to pick a date.
      </p>
    </div>
  )
}


function SlotPicker({ surgeryId, headers, status, onPicked, onBack, endpoint = 'pick' }) {
  const isReschedule = endpoint === 'reschedule'
  const [days, setDays] = useState(null)
  const [selected, setSelected] = useState(null)   // a day object the patient is reviewing
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState(null)

  async function loadSlots() {
    try {
      const r = await publicApi.get(`/p/surgery/${surgeryId}/slots`, { headers })
      setDays(r.data.days)
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not load available dates.')
    }
  }
  useEffect(() => { loadSlots() }, [])

  // Pre-select the earliest available slot on first load (new bookings only).
  useEffect(() => {
    if (isReschedule) return
    if (selected) return
    if (!days?.length) return
    setSelected(days[0])
  }, [days])

  async function confirmPick() {
    if (!selected) return
    setConfirming(true); setError(null)
    try {
      const r = await publicApi.post(`/p/surgery/${surgeryId}/${endpoint}`,
        { block_day_id: selected.block_day_id }, { headers })
      onPicked(r.data)
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not book that date — try another.')
      setConfirming(false)
    }
  }

  async function confirmSelectSlot() {
    if (!selected) return
    setConfirming(true); setError(null)
    try {
      const r = await publicApi.post(
        `/p/surgery/${surgeryId}/select-slot`,
        { block_day_id: selected.block_day_id, start_time: selected.proposed_start_time },
        { headers }
      )
      onPicked(r.data)
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not book that slot — try another.')
      setConfirming(false)
    }
  }

  if (error && !days) {
    return <div className="bg-red-50 border border-red-200 text-red-800 text-sm p-4 rounded">{error}</div>
  }

  if (!days) {
    return <div className="text-center text-gray-500 py-8">Loading available dates…</div>
  }

  if (days.length === 0) {
    return (
      <div className="bg-amber-50 border border-amber-200 text-amber-900 p-4 rounded">
        <div className="font-semibold mb-1">No openings in the next 6 months.</div>
        <p className="text-sm">Please call our office at 240-252-2140 and we'll work with you.</p>
      </div>
    )
  }

  // Group by facility for clarity
  const byFacility = {}
  for (const d of days) {
    if (!byFacility[d.facility]) byFacility[d.facility] = []
    byFacility[d.facility].push(d)
  }

  // Step 2: review + confirm a selected date
  if (selected) {
    return (
      <div>
        <div className="bg-white rounded-lg border border-plum-300 shadow p-5">
          <div className="flex items-center gap-2 mb-2 text-plum-700">
            <Calendar size={20} />
            <h1 className="text-lg font-serif font-semibold">Confirm your surgery date</h1>
          </div>
          <p className="text-sm text-gray-700 mb-3">
            Hi {status.patient_first_name}, please review the details below before confirming.
          </p>
          <div className="bg-plum-50 border border-plum-100 rounded p-3 text-sm space-y-1.5 mb-4">
            <div><strong>Date:</strong> {selected.weekday}, {fmt(selected.block_date)}</div>
            <div><strong>Start time:</strong> {selected.proposed_start_time} (please plan to arrive earlier)</div>
            <div><strong>Where:</strong> {FACILITY_LABEL[selected.facility]}</div>
            <div><strong>Procedure:</strong> {status.procedure_descriptions?.join(', ')}</div>
            <div><strong>Estimated time:</strong> {selected.duration_minutes} minutes</div>
            {selected.cases_already_booked > 0 && (
              <div className="text-xs text-gray-600 pt-1 border-t border-plum-200">
                Note: {selected.cases_already_booked} other patient
                {selected.cases_already_booked === 1 ? ' is' : 's are'} also scheduled
                for {selected.weekday.toLowerCase()}; your time may shift slightly to accommodate.
              </div>
            )}
          </div>

          {status.clearance_required && (
            <div className="bg-amber-50 border border-amber-200 rounded p-2 text-xs text-amber-900 mb-3">
              <strong>Don't forget:</strong> Medical clearance must be obtained <strong>2–4 weeks before</strong> this date.
            </div>
          )}

          {error && (
            <div className="bg-red-50 border border-red-200 text-red-800 text-sm p-2 rounded mb-3">{error}</div>
          )}

          <button
            type="button"
            onClick={confirmSelectSlot}
            className="btn-primary w-full text-base py-3 mb-2 disabled:opacity-60"
            disabled={confirming}
          >
            {confirming ? 'Booking…' : `Confirm this time (${selected.proposed_start_time})`}
          </button>
          <button
            type="button"
            onClick={() => { setSelected(null); setError(null) }}
            className="btn-secondary w-full text-base py-2 disabled:opacity-50"
            disabled={confirming}
          >
            Pick a different date
          </button>
        </div>
      </div>
    )
  }

  // Step 1: pick a date
  return (
    <div>
      {isReschedule && onBack && (
        <button type="button"
                onClick={onBack}
                className="flex items-center gap-1 text-[12px] text-plum-700 hover:underline mb-2">
          <ArrowLeft size={12} /> Back to current date
        </button>
      )}
      <div className="bg-white rounded-lg border border-border-subtle shadow-sm p-5 mb-4">
        <h1 className="text-lg font-serif font-semibold text-plum-700 mb-1">
          {isReschedule
            ? <>Hi {status.patient_first_name} — pick a new date</>
            : <>Hi {status.patient_first_name} — pick your surgery date</>}
        </h1>
        {isReschedule && (
          <div className="bg-amber-50 border border-amber-200 text-amber-900 text-[12px] p-2 rounded mb-2">
            Your current date is <strong>{fmt(status.scheduled_date)}</strong>.
            Picking a new date will release your current slot.
          </div>
        )}
        <p className="text-[11px] text-gray-500 mb-1">Showing all available dates in the next 6 months. Tap one to review the details before confirming.</p>
        <div className="text-sm text-gray-700 mb-2">
          <strong>Procedure:</strong> {status.procedure_descriptions?.join(', ')}
          {status.is_robotic && <span className="ml-2 text-blue-700">🤖 Robotic</span>}
        </div>
        {(status.eligible_facilities || []).length > 1 && (
          <p className="text-xs text-gray-600">
            Your procedure can be done at either location. Pick whichever works best for you.
          </p>
        )}
        {status.clearance_required && (
          <div className="mt-3 bg-amber-50 border border-amber-200 rounded p-2 text-xs text-amber-900">
            <strong>Reminder:</strong> Medical clearance is required for your surgery.
            Please schedule with your primary care doctor (or cardiologist if recommended)
            <strong> 2–4 weeks before</strong> your surgery date.
          </div>
        )}
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-800 text-sm p-2 rounded mb-3">{error}</div>
      )}

      <div className="space-y-4">
        {Object.entries(byFacility).map(([facility, items]) => (
          <div key={facility}>
            <h2 className="text-sm font-serif font-semibold text-plum-700 mb-2 flex items-center gap-1.5">
              <Hospital size={14} /> {FACILITY_LABEL[facility]}
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {items.map(d => {
                const isEarliest = !isReschedule && d.block_day_id === days[0]?.block_day_id
                return (
                  <button
                    key={d.block_day_id}
                    onClick={() => setSelected(d)}
                    className={`border rounded p-3 text-left transition-colors ${
                      isEarliest
                        ? 'bg-plum-50 border-plum-400 hover:border-plum-600'
                        : 'bg-white border-border-subtle hover:border-plum-300 hover:bg-plum-50'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-gray-900">
                        {d.weekday}, {fmt(d.block_date)}
                      </span>
                      {isEarliest && (
                        <span className="text-[10px] text-plum-700 font-semibold bg-plum-100 border border-plum-300 rounded px-1.5 py-0.5">
                          Recommended
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-gray-600 mt-0.5 flex items-center gap-1">
                      <Clock size={10} /> {d.proposed_start_time} ({d.duration_minutes} min)
                    </div>
                    {d.cases_already_booked > 0 && (
                      <div className="text-[10px] text-gray-500 mt-0.5">
                        {d.cases_already_booked} other case{d.cases_already_booked > 1 ? 's' : ''} that day
                      </div>
                    )}
                  </button>
                )
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}


function ConfirmationScreen({ status, confirmation }) {
  return (
    <div className="bg-white rounded-lg border border-green-300 shadow-sm p-6">
      <div className="flex items-center gap-2 mb-3 text-green-700">
        <CheckCircle2 size={24} />
        <h1 className="text-lg font-serif font-semibold">Surgery date confirmed!</h1>
      </div>
      <p className="text-sm text-gray-700 mb-3">
        Thank you, {status.patient_first_name}. Your surgery is scheduled:
      </p>
      <div className="bg-plum-50 border border-plum-100 rounded p-3 text-sm space-y-1.5">
        <div><strong>Date:</strong> {fmt(confirmation.scheduled_date)}</div>
        <div><strong>Start time:</strong> {confirmation.scheduled_start_time}</div>
        <div><strong>Where:</strong> {FACILITY_LABEL[confirmation.facility]}</div>
        <div><strong>Procedure:</strong> {status.procedure_descriptions?.join(', ')}</div>
      </div>

      <div className="mt-4 text-sm text-gray-700 space-y-2">
        <h2 className="font-serif font-semibold text-plum-700">What's next:</h2>
        <ol className="list-decimal pl-5 space-y-1.5 text-xs">
          <li>You'll receive a Klara message with consent forms to sign electronically.</li>
          <li>If your surgery is at a hospital, we'll send a boarding-slip confirmation request.</li>
          {status.clearance_required && (
            <li className="text-amber-700">
              <strong>Schedule your medical clearance now</strong> — call your PCP this week.
            </li>
          )}
          <li>You'll get a reminder 1 week before, plus pre-op instructions.</li>
          <li>Day of surgery: come in fasting, with a driver, and your insurance card.</li>
        </ol>
      </div>

      <p className="text-xs text-gray-500 mt-5 pt-3 border-t border-gray-100">
        Questions? Call us at <a href="tel:+12402522140" className="text-plum-700">240-252-2140</a>.
      </p>
    </div>
  )
}


function fmt(iso) {
  if (!iso) return ''
  const [y, m, d] = iso.split('-')
  const date = new Date(parseInt(y), parseInt(m) - 1, parseInt(d))
  return date.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })
}


function CardiologistAskScreen({ surgeryId, headers, status, onUpdated }) {
  const [hasCardio, setHasCardio] = useState(null)   // null/'yes'/'no'
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [fax, setFax] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [confirmation, setConfirmation] = useState(null)

  async function submit() {
    setBusy(true); setError(null)
    try {
      const r = await publicApi.post(`/p/surgery/${surgeryId}/cardiologist`, {
        has_cardiologist: hasCardio === 'yes',
        cardiologist_name: name || null,
        cardiologist_phone: phone || null,
        cardiologist_fax: fax || null,
      }, { headers })
      setConfirmation(r.data.message)
      setTimeout(() => onUpdated(), 1500)
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not save your answer.')
    } finally { setBusy(false) }
  }

  if (confirmation) {
    return (
      <div className="bg-green-50 border border-green-200 rounded p-4 text-sm text-green-900">
        ✓ {confirmation}
      </div>
    )
  }

  return (
    <div className="bg-white rounded-lg border border-border-subtle shadow-sm p-5">
      <div className="flex items-center gap-2 mb-2 text-amber-700">
        <AlertCircle size={20} />
        <h1 className="text-lg font-serif font-semibold">Medical clearance needed</h1>
      </div>
      <p className="text-sm text-gray-700 mb-3">
        Hi {status.patient_first_name} — your surgery requires medical clearance before
        we can finalize your date. <strong>Please obtain clearance as early as possible</strong>;
        ideally 2–4 weeks before surgery, since clearance offices are often booked.
      </p>
      <p className="text-sm text-gray-700 mb-4">
        <strong>Do you currently see a cardiologist?</strong>
      </p>

      <div className="flex gap-2 mb-4">
        <button
          type="button"
          onClick={() => setHasCardio('yes')}
          className={`flex-1 py-2 rounded border text-sm font-semibold ${
            hasCardio === 'yes'
              ? 'bg-plum-100 border-plum-400 text-plum-800'
              : 'bg-white border-gray-300 text-gray-700 hover:border-plum-300'
          }`}
        >
          Yes — I have a cardiologist
        </button>
        <button
          type="button"
          onClick={() => setHasCardio('no')}
          className={`flex-1 py-2 rounded border text-sm font-semibold ${
            hasCardio === 'no'
              ? 'bg-plum-100 border-plum-400 text-plum-800'
              : 'bg-white border-gray-300 text-gray-700 hover:border-plum-300'
          }`}
        >
          No — I'll go through my PCP
        </button>
      </div>

      {hasCardio === 'yes' && (
        <div className="space-y-3 mb-3">
          <p className="text-xs text-gray-600">
            We'll fax your cardiologist's office a clearance request. Please provide:
          </p>
          <div>
            <label className="text-[10px] uppercase tracking-wide text-gray-500 mb-1 block">
              Cardiologist's name *
            </label>
            <input className="input text-sm w-full" value={name}
                    placeholder="Dr. Jane Smith / Smith Cardiology"
                    onChange={e => setName(e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] uppercase tracking-wide text-gray-500 mb-1 block">
                Phone
              </label>
              <input className="input text-sm w-full font-mono"
                      type="tel" value={phone}
                      placeholder="(301) 555-1234"
                      onChange={e => setPhone(e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wide text-gray-500 mb-1 block">
                Fax (preferred)
              </label>
              <input className="input text-sm w-full font-mono"
                      type="tel" value={fax}
                      placeholder="(301) 555-1235"
                      onChange={e => setFax(e.target.value)} />
            </div>
          </div>
        </div>
      )}

      {hasCardio === 'no' && (
        <div className="bg-amber-50 border border-amber-200 rounded p-3 text-xs text-amber-900 mb-3">
          No problem — please call your primary care doctor right away to schedule
          a clearance appointment. Their office may be booked, so the earlier the better.
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-800 text-sm p-2 rounded mb-3">{error}</div>
      )}

      <button
        type="button"
        onClick={submit}
        className="btn-primary w-full text-base py-2 disabled:opacity-60"
        disabled={busy || !hasCardio || (hasCardio === 'yes' && !name.trim())}
      >
        {busy ? 'Saving…' : 'Continue'}
      </button>
    </div>
  )
}
