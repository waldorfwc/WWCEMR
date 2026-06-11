import { useState, useMemo, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft, AlertTriangle, Check, CheckCircle2, Circle, Clock,
  ChevronDown, ChevronUp, Edit3, Package, RotateCcw, X, SkipForward, FileText,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import { OWNERSHIP_TONES, OWNERSHIP_LABELS } from './LarcDevices'


const MILESTONE_ICON = {
  pending:        <Circle size={14} className="text-gray-400" />,
  in_progress:    <Clock size={14} className="text-amber-600" />,
  done:           <CheckCircle2 size={14} className="text-green-600" />,
  skipped:        <SkipForward size={14} className="text-gray-400" />,
  not_applicable: <X size={14} className="text-gray-400" />,
}


/**
 * Invalidate every list query that could be showing this assignment.
 * Any mutation that flips a.status OR completes a milestone changes
 * which bucket the row lives in — without invalidating these four
 * keys, the user sees the row "stuck" on the dashboard or Owed list
 * until a hard refresh. Keep this in lockstep with the queries used
 * by Larc.jsx (`larc-dashboard`, `larc-assignments`) and
 * LarcOwed.jsx (`larc-owed`).
 */
function invalidateLarcLists(qc, assignmentId) {
  qc.invalidateQueries({ queryKey: ['larc-assignment', assignmentId] })
  qc.invalidateQueries({ queryKey: ['larc-dashboard'] })
  qc.invalidateQueries({ queryKey: ['larc-assignments'] })
  qc.invalidateQueries({ queryKey: ['larc-owed'] })
}


export default function LarcAssignment() {
  const { id } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const { data: a, isLoading, error } = useQuery({
    queryKey: ['larc-assignment', id],
    queryFn: () => api.get(`/larc/assignments/${id}`).then(r => r.data),
  })

  if (isLoading) return <div className="p-6 text-gray-400">Loading…</div>
  if (error) return <div className="p-6 text-red-600">{error?.response?.data?.detail || error.message}</div>
  if (!a) return null

  const milestones = a.milestones || []

  return (
    <div>
      <Link to="/larc" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
        <ArrowLeft size={12} /> LARC dashboard
      </Link>

      {/* Patient header */}
      <div className="card mb-4">
        <div className="flex items-baseline justify-between gap-3 mb-2">
          <div>
            <h1 className="text-xl font-bold text-gray-900">{a.patient_name}</h1>
            <div className="text-xs text-gray-500 mt-0.5">
              Chart #{a.chart_number}
              {a.patient_dob && <> · DOB {fmt.date(a.patient_dob)}</>}
              {a.patient_phone && <> · {a.patient_phone}</>}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className={`text-[10px] uppercase tracking-wide px-2 py-1 rounded ${
              a.source_flow === 'office_procedure'
                ? 'bg-teal-100 text-teal-700'
                : 'bg-plum-100 text-plum-700'
            }`}>
              {a.source_flow.replace('_', ' ')}
            </span>
            <span className={`text-[10px] uppercase tracking-wide px-2 py-1 rounded ${
              a.status === 'billed' ? 'bg-green-100 text-green-700' :
              a.status === 'inserted' ? 'bg-blue-100 text-blue-700' :
              a.status.startsWith('failed') ? 'bg-red-100 text-red-700' :
              'bg-amber-100 text-amber-700'
            }`}>
              {a.status.replace(/_/g, ' ')}
            </span>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs mt-3">
          <Field label="Device">
            {a.device_our_id ? (
              <>
                <Link to={`/larc/devices/${a.device_id}`} className="font-mono text-plum-700 hover:underline">
                  {a.device_our_id}
                </Link>
                <div className="text-[10px] text-gray-500">{a.device_type_name}</div>
              </>
            ) : <span className="text-amber-700 italic">not yet assigned</span>}
          </Field>
          <Field label="Ownership">
            {a.device_ownership ? (
              <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${OWNERSHIP_TONES[a.device_ownership] || 'bg-gray-100 text-gray-700'}`}
                    title={a.device_ownership === 'patient_owned'
                      ? 'Patient Owned — WWC does NOT bill insurance.'
                      : a.device_ownership === 'wwc_claimed'
                        ? 'WWC Claimed (originally patient-owned).'
                        : 'WWC Owned — billable to insurance.'}>
                {OWNERSHIP_LABELS[a.device_ownership] || a.device_ownership}
              </span>
            ) : <span className="text-gray-400">—</span>}
          </Field>
          <Field label="Date received">
            {a.device_received_date ? fmt.date(a.device_received_date) : <span className="text-gray-400">—</span>}
          </Field>
          <Field label="Insurance">
            {a.primary_insurance || <span className="text-gray-400">—</span>}
          </Field>
          <Field label="Patient responsibility">
            {a.patient_responsibility != null
              ? <span className="font-mono">${a.patient_responsibility}</span>
              : <span className="text-gray-400">TBD</span>}
          </Field>
          <Field label="Claim #">
            {a.claim_number ? <span className="font-mono">{a.claim_number}</span>
              : <span className="text-gray-400">—</span>}
          </Field>
        </div>
      </div>

      {/* Replacement chain banner (failed_used assignments only) */}
      {a.status === 'failed_used' && <ReplacementChainCard a={a} />}

      <AllocateInventoryCard a={a} />
      <InsuranceCardCard a={a} />

      {/* Benefits calculator — always-visible card. The benefits_verified
          milestone still uses BenefitsBody under the hood, but this surfaces
          it without requiring the user to expand a milestone first. */}
      {(a.device_ownership || 'wwc_owned') !== 'patient_owned' && (
        <div className="card mb-4">
          <div className="flex items-center gap-1.5 mb-2">
            <span className="text-emerald-700 text-base">$</span>
            <h2 className="text-sm font-semibold text-gray-800">Benefits Calculator</h2>
            <span className="text-[11px] text-gray-500">
              Patient responsibility for this device
            </span>
          </div>
          <BenefitsBody a={a} />
        </div>
      )}
      {a.device_ownership === 'patient_owned' && (
        <div className="card mb-4 bg-sky-50/60 border-sky-200 text-[12px] text-gray-700">
          <div className="font-semibold text-sky-800 mb-1">
            Patient-Owned device — no benefits calculation
          </div>
          The patient (or their pharmacy plan) paid for this device.
          WWC does not bill insurance for it, so the benefits calculator
          is skipped.
        </div>
      )}

      {/* Milestone cards */}
      <div className="space-y-3">
        {milestones.map(m => <LarcMilestoneCard key={m.id} m={m} assignment={a} />)}
        {milestones.length === 0 && (
          <div className="card text-xs text-gray-500 italic">No milestones yet.</div>
        )}
      </div>
    </div>
  )
}


function LarcMilestoneCard({ m, assignment }) {
  const isResolved = ['done', 'skipped', 'not_applicable'].includes(m.status)
  const [open, setOpen] = useState(!isResolved)
  const body = milestoneInline(m, assignment)
  return (
    <div className={`card !p-3 ${isResolved ? 'bg-green-50/30' : ''}`}>
      <div className="flex items-start gap-2">
        <div className="mt-0.5 shrink-0">{MILESTONE_ICON[m.status] || MILESTONE_ICON.pending}</div>
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="text-sm font-medium">{m.title}</span>
            <span className="text-[10px] text-gray-500 capitalize">{m.status.replace(/_/g, ' ')}</span>
            {m.expected_duration_days && !isResolved && (
              <span className="text-[10px] text-gray-400">expected within {m.expected_duration_days}d</span>
            )}
          </div>
          {m.completed_at && (
            <div className="text-[10px] text-gray-500 mt-0.5">
              ✓ {fmt.date(m.completed_at.slice(0, 10))}
              {m.completed_by && ` by ${m.completed_by.split('@')[0]}`}
            </div>
          )}
        </div>
        {body && (
          <button type="button" onClick={() => setOpen(o => !o)}
                  className="text-gray-400 hover:text-plum-700 p-1">
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


function milestoneInline(m, a) {
  switch (m.kind) {
    case 'benefits_verified':              return <BenefitsBody a={a} />
    case 'patient_responsibility_modmed':  return <ResponsibilityModmedBody a={a} />
    case 'enrollment_sent':                return <EnrollmentSentBody a={a} />
    case 'enrollment_signed':              return <EnrollmentSignedBody a={a} />
    case 'request_faxed':                  return <FaxPharmacyBody a={a} />
    case 'device_received':                return <ReceiveDeviceBody a={a} />
    case 'patient_notified':               return <NotifyBody a={a} />
    case 'appt_scheduled':                 return <ApptBody a={a} />
    case 'device_checked_out':             return <CheckoutPlaceholderBody a={a} />
    case 'device_inserted':                return <OutcomeBody a={a} />
    case 'billed':                         return <BilledBody a={a} />
    // Office-procedure (NovaSure, Bensta) ─────────────────────────────
    case 'device_assigned':                return <DeviceAssignedBody a={a} />
    case 'device_consumed':                return <ConsumeBody a={a} />
    default:                                return null
  }
}


/* ── Milestone bodies ───────────────────────────────────────────── */

function BenefitsBody({ a }) {
  const qc = useQueryClient()
  const { data: picklists } = useQuery({
    queryKey: ['larc-picklists'],
    queryFn: () => api.get('/larc/picklists').then(r => r.data),
    staleTime: 60_000,
  })

  const [ins, setIns]     = useState(a.primary_insurance || '')
  const [notes, setNotes] = useState('')
  // Default allowed amount to what the device cost — purchase price wins,
  // typical cost is the fallback. Coordinator can override either way.
  const _defaultAllowed = a.allowed_amount
                          || a.device_purchase_price
                          || a.device_typical_cost
                          || ''
  const [form, setForm]   = useState({
    allowed_amount:  _defaultAllowed,
    deductible:      a.deductible      || '',
    deductible_met:  a.deductible_met  || '',
    copay:           a.copay           || '',
    coinsurance_pct: a.coinsurance_pct || '',
    oop_max:         a.oop_max         || '',
    oop_met:         a.oop_met         || '',
  })
  const [savedFlash, setSavedFlash] = useState(false)
  const update = (k, v) => setForm(prev => ({ ...prev, [k]: v }))

  function num(k) {
    const v = form[k]
    if (v === '' || v === null || v === undefined) return 0
    const n = parseFloat(v)
    return Number.isFinite(n) ? n : 0
  }

  // Live calc — same math as backend `_calc_patient_responsibility`
  const calc = useMemo(() => {
    const allowed   = num('allowed_amount')
    const ded       = num('deductible')
    const ded_met   = num('deductible_met')
    const copay     = num('copay')
    const coins_pct = num('coinsurance_pct')
    const oop_max   = num('oop_max')
    const oop_met   = num('oop_met')

    const ded_remaining = Math.max(0, ded - ded_met)
    const oop_remaining = oop_max > 0 ? Math.max(0, oop_max - oop_met) : Infinity
    const ded_portion   = Math.min(allowed, ded_remaining)
    const after_ded     = allowed - ded_portion
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
      final,
      capped:        raw > oop_remaining,
      oop_remaining: oop_remaining === Infinity ? null : round2(oop_remaining),
    }
  }, [form])

  const save = useMutation({
    mutationFn: () => api.post(`/larc/assignments/${a.id}/benefits`, {
      primary_insurance: ins || null,
      allowed_amount:  numOrNull(form.allowed_amount),
      deductible:      numOrNull(form.deductible),
      deductible_met:  numOrNull(form.deductible_met),
      copay:           numOrNull(form.copay),
      coinsurance_pct: numOrNull(form.coinsurance_pct),
      oop_max:         numOrNull(form.oop_max),
      oop_met:         numOrNull(form.oop_met),
      notes: notes || null,
      save: true,
    }).then(r => r.data),
    onSuccess: () => {
      invalidateLarcLists(qc, a.id)
      setSavedFlash(true)
      setTimeout(() => setSavedFlash(false), 4000)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  return (
    <div className="space-y-3 text-[12px]">
      <div>
        <div className="text-[10px] uppercase text-gray-500 mb-1">Primary insurance</div>
        <select className="input text-[12px] w-full" value={ins}
                onChange={e => setIns(e.target.value)}>
          <option value="">— select insurance —</option>
          {(picklists?.insurance_companies || []).map(name => (
            <option key={name} value={name}>{name}</option>
          ))}
        </select>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <DollarInput label="Allowed amount" value={form.allowed_amount}
                      onChange={v => update('allowed_amount', v)}
                      hint={a.device_purchase_price
                        ? `Default: device cost $${a.device_purchase_price}`
                        : a.device_typical_cost
                          ? `Default: typical cost $${a.device_typical_cost}`
                          : "Insurance's allowed amount for the device + visit"} />
        <DollarInput label="Deductible (annual)" value={form.deductible}
                      onChange={v => update('deductible', v)} />
        <DollarInput label="Deductible met" value={form.deductible_met}
                      onChange={v => update('deductible_met', v)}
                      hint="Paid toward deductible YTD" />
        <PercentInput label="Coinsurance %" value={form.coinsurance_pct}
                       onChange={v => update('coinsurance_pct', v)} />
        <DollarInput label="Copay" value={form.copay}
                      onChange={v => update('copay', v)}
                      hint="Fixed copay, if any" />
        <DollarInput label="OOP max (annual)" value={form.oop_max}
                      onChange={v => update('oop_max', v)} />
        <DollarInput label="OOP met" value={form.oop_met}
                      onChange={v => update('oop_met', v)} />
      </div>

      <div className="bg-plum-50/40 border border-plum-100 rounded p-3">
        <div className="text-[10px] uppercase tracking-wide text-plum-700 font-semibold mb-1">
          Live preview
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <Stat label="Deductible portion"  val={`$${calc.ded_portion.toFixed(2)}`} />
          <Stat label="Coinsurance portion" val={`$${calc.coins_portion.toFixed(2)}`}
                  sub={calc.after_ded > 0 ? `${form.coinsurance_pct || 0}% of $${calc.after_ded.toFixed(2)}` : null} />
          <Stat label="Copay"               val={`$${calc.copay.toFixed(2)}`} />
          <Stat label="Patient owes"        val={`$${calc.final.toFixed(2)}`}
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

      <textarea className="input text-[11px] w-full" rows={2}
                placeholder="Benefits notes (prior auth needed, special terms, etc.)"
                value={notes} onChange={e => setNotes(e.target.value)} />

      <div className="flex flex-wrap justify-between items-center gap-2">
        <div className="text-[11px] text-gray-600">
          {savedFlash
            ? <span className="text-green-700">✓ Saved · milestone advanced</span>
            : (a.benefits_verified_at
                ? <>Last saved: <strong>{fmt.date(a.benefits_verified_at)}</strong></>
                : <>Not saved yet</>)}
        </div>
        <button className="btn-primary text-[11px]"
                onClick={() => save.mutate()}
                disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save & mark verified'}
        </button>
      </div>
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
      {hint && <div className="text-[11px] text-gray-400 mt-0.5">{hint}</div>}
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
  const tones = { green: 'text-green-700', amber: 'text-amber-700' }
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-gray-400">{label}</div>
      <div className={`${big ? 'text-lg' : 'text-sm'} font-bold ${tones[tone] || 'text-gray-800'}`}>{val}</div>
      {sub && <div className="text-[11px] text-gray-500">{sub}</div>}
    </div>
  )
}


function round2(n) { return Math.round(n * 100) / 100 }
function numOrNull(v) {
  if (v === '' || v === null || v === undefined) return null
  const n = parseFloat(v)
  return Number.isFinite(n) ? n : null
}


function ResponsibilityModmedBody({ a }) {
  const qc = useQueryClient()
  const done = !!a.milestones?.find(m => m.kind === 'patient_responsibility_modmed' && m.status === 'done')
  const toggle = useMutation({
    mutationFn: (confirmed) => api.post(`/larc/assignments/${a.id}/responsibility-in-modmed`,
                                         { confirmed }).then(r => r.data),
    onSuccess: () => invalidateLarcLists(qc, a.id),
  })
  return (
    <label className="flex items-start gap-2 text-[12px] cursor-pointer">
      <input type="checkbox" className="mt-0.5" checked={done}
             onChange={e => toggle.mutate(e.target.checked)} />
      <div>
        <div className="font-medium text-gray-800">Patient responsibility entered in ModMed</div>
        <div className="text-[10px] text-gray-500">
          Manual step (ModMed has no integration). Once entered, the patient sees the cost on their balance.
        </div>
      </div>
    </label>
  )
}


function EnrollmentSentBody({ a }) {
  const qc = useQueryClient()
  const [dispense, setDispense] = useState(false)
  const [providerContact, setProviderContact] = useState(false)
  const [error, setError] = useState(null)

  const send = useMutation({
    mutationFn: () => api.post(`/larc/assignments/${a.id}/send-enrollment`, {
      dispense, provider_contact_preference: providerContact,
    }).then(r => r.data),
    onMutate: () => setError(null),
    onSuccess: () => invalidateLarcLists(qc, a.id),
    onError: (e) => setError(e?.response?.data?.detail || 'Send failed'),
  })

  const env = a.latest_envelope

  // Already sent — show the BoldSign envelope status panel.
  if (env) {
    return <EnrollmentEnvelopeStatus a={a} env={env} />
  }

  return (
    <div className="space-y-2 text-[12px]">
      <div className="text-[11px] text-gray-600">
        Sends a 3-signer BoldSign envelope (Reception → Patient → Provider).
        Once all three sign, the form auto-faxes to the pharmacy.
      </div>

      <ClinicianPicker a={a} kind="provider" />
      <ClinicianPicker a={a} kind="app" />

      <details className="text-[11px]">
        <summary className="cursor-pointer text-plum-700 hover:underline">
          Form options
        </summary>
        <div className="mt-1 space-y-1 pl-3 border-l border-gray-200">
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={dispense}
                   onChange={e => setDispense(e.target.checked)} />
            <span>Dispense (check this if applicable)</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={providerContact}
                   onChange={e => setProviderContact(e.target.checked)} />
            <span>Provider contact preference</span>
          </label>
        </div>
      </details>

      <div className="flex gap-2">
        <button className="btn-primary text-[11px]"
                onClick={() => send.mutate()}
                disabled={send.isPending}>
          {send.isPending ? 'Sending…' : 'Send Enrollment via BoldSign'}
        </button>
      </div>
      {error && (
        <div className="text-[11px] text-danger bg-red-50 border border-red-200 rounded px-2 py-1.5">
          {error}
        </div>
      )}
    </div>
  )
}


function ClinicianPicker({ a, kind }) {
  // `kind` is 'provider' or 'app'. Each picks from the same /admin/users/
  // clinicians endpoint but writes to a different per-assignment override.
  const qc = useQueryClient()
  const isProvider = kind === 'provider'
  const endpoint = isProvider
    ? `/larc/assignments/${a.id}/inserting-provider`
    : `/larc/assignments/${a.id}/app`
  const fields = isProvider
    ? ['inserting_provider_email', 'inserting_provider_name', 'inserting_provider_npi']
    : ['app_email', 'app_name', 'app_npi']
  const label = isProvider ? 'Inserting provider' : 'APP (Advanced Practice Provider)'
  const current = {
    email: a[fields[0]] || '',
    name:  a[fields[1]] || '',
    npi:   a[fields[2]] || '',
  }
  const [saved, setSaved] = useState(false)

  const { data: clinicians = [] } = useQuery({
    queryKey: ['larc-clinicians'],
    queryFn: () => api.get('/admin/users/clinicians').then(r => r.data),
    staleTime: 60_000,
  })

  const save = useMutation({
    mutationFn: (body) => api.post(endpoint, body).then(r => r.data),
    onSuccess: () => {
      invalidateLarcLists(qc, a.id)
      setSaved(true); setTimeout(() => setSaved(false), 1200)
    },
  })

  function applyClinician(email) {
    if (!email) {
      // "Use practice default" → clear all three fields
      save.mutate({ email: '', name: '', npi: '' })
      return
    }
    const c = clinicians.find(x => x.email === email)
    if (!c) return
    save.mutate({ email: c.email, name: c.display_name, npi: c.npi })
  }

  // Pre-filter the dropdown by clinician_role. Show all clinicians as a
  // fallback so a misroled user can still be picked.
  const matchKind = isProvider ? 'provider' : 'app'
  const primary   = clinicians.filter(c => c.clinician_role === matchKind)
  const others    = clinicians.filter(c => c.clinician_role !== matchKind)
  const matchedCurrent = clinicians.find(c => c.email === current.email)

  return (
    <details className="text-[11px]">
      <summary className="cursor-pointer text-plum-700 hover:underline">
        {label}
        {current.email && (
          <span className="ml-2 text-gray-700">
            — {current.name || current.email}{current.npi && <> · <span className="font-mono">{current.npi}</span></>}
          </span>
        )}
        {saved && <span className="text-success ml-1">✓ saved</span>}
      </summary>
      <div className="mt-1 pl-3 border-l border-gray-200 space-y-1.5">
        <div className="text-[10px] text-gray-500">
          {isProvider
            ? 'Prescribing physician on the enrollment form. Falls back to the practice provider if blank.'
            : 'APP printed on the form. Falls back to the practice APP if blank.'}
          {' '}Pick from the clinician catalog (set NPIs on the Admin page).
        </div>
        <select className="input py-0.5 px-1 text-[11px] w-full"
                value={current.email}
                onChange={e => applyClinician(e.target.value)}
                disabled={save.isPending}>
          <option value="">— use practice default —</option>
          {primary.length > 0 && (
            <optgroup label={isProvider ? 'Providers' : 'APPs'}>
              {primary.map(c => (
                <option key={c.email} value={c.email}>
                  {c.display_name} · {c.npi}
                </option>
              ))}
            </optgroup>
          )}
          {others.length > 0 && (
            <optgroup label="Other clinicians">
              {others.map(c => (
                <option key={c.email} value={c.email}>
                  {c.display_name} · {c.npi}
                  {c.clinician_role && ` (${c.clinician_role})`}
                </option>
              ))}
            </optgroup>
          )}
          {current.email && !matchedCurrent && (
            // Custom override that doesn't match any catalog entry — keep
            // the value pickable so it shows as selected.
            <option value={current.email}>
              ⚠ {current.name || current.email} · {current.npi} (not in catalog)
            </option>
          )}
        </select>
      </div>
    </details>
  )
}


function EnrollmentEnvelopeStatus({ a, env }) {
  // Step states: 'pending' | 'signed' | 'declined' | 'voided'
  const steps = [
    { label: 'Reception', at: env.receptionist_signed_at },
    { label: 'Patient',   at: env.patient_signed_at },
    { label: 'Provider',  at: env.provider_signed_at },
  ]
  const allSigned = !!env.signed_at
  const fax = env.faxed_at
        ? { kind: 'done', text: `Faxed ${fmt.date(env.faxed_at.slice(0, 10))} — ${env.fax_status || 'sent'}` }
        : env.fax_status === 'SendingFailed' || env.last_fax_error
          ? { kind: 'err', text: `Fax failed — ${env.last_fax_error || env.fax_status}` }
          : allSigned
            ? { kind: 'pending', text: 'Signed — fax queued' }
            : null

  return (
    <div className="space-y-1.5 text-[11px]">
      <div className="text-gray-700">
        <span className="font-mono text-[10px] text-gray-500">
          BoldSign {env.boldsign_envelope_id ? env.boldsign_envelope_id.slice(0, 8) + '…' : ''}
        </span>
        {' '}sent {env.sent_at ? fmt.date(env.sent_at.slice(0, 10)) : '—'}
        {env.sent_by && <> by <span className="text-gray-600">{env.sent_by}</span></>}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {steps.map(s => (
          <span key={s.label}
                className={
                  s.at
                    ? 'text-[10px] bg-green-100 text-green-800 px-1.5 py-0.5 rounded'
                    : 'text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded'
                }
                title={s.at || 'pending'}>
            {s.at ? '✓ ' : '○ '}{s.label}
          </span>
        ))}
      </div>
      {fax && (
        <div className={
          fax.kind === 'done' ? 'text-green-700'
            : fax.kind === 'err' ? 'text-danger'
            : 'text-amber-700'
        }>
          {fax.text}
        </div>
      )}
      {env.declined_at && (
        <div className="text-danger">Declined {fmt.date(env.declined_at.slice(0, 10))}</div>
      )}
      {env.voided_at && (
        <div className="text-gray-500 italic">Voided {fmt.date(env.voided_at.slice(0, 10))}</div>
      )}
    </div>
  )
}


function EnrollmentSignedBody({ a }) {
  const qc = useQueryClient()
  const toggle = useMutation({
    mutationFn: (confirmed) => api.post(`/larc/assignments/${a.id}/enrollment-signed`,
                                          { confirmed }).then(r => r.data),
    onSuccess: () => invalidateLarcLists(qc, a.id),
  })
  const done = !!a.enrollment_signed_at
  return (
    <label className="flex items-start gap-2 text-[12px] cursor-pointer">
      <input type="checkbox" className="mt-0.5" checked={done}
             onChange={e => toggle.mutate(e.target.checked)} />
      <div>
        <div className="font-medium text-gray-800">Enrollment form signed</div>
        <div className="text-[10px] text-gray-500">
          BoldSign will auto-mark this when all three signers complete (Phase 4 webhook).
          For now, check this manually once you confirm the signed PDF arrived.
        </div>
        {done && (
          <div className="text-[11px] text-green-700 mt-0.5">
            ✓ Signed {fmt.date(a.enrollment_signed_at.slice(0, 10))}
          </div>
        )}
      </div>
    </label>
  )
}


function FaxPharmacyBody({ a }) {
  const qc = useQueryClient()
  // Filter pharmacies by the assignment's device type — staff only sees
  // pharmacies that ship the right device family. Legacy rows with no
  // device_names list still show up (server-side: empty = "any device").
  const device = a.device_type_name || ''
  const { data: pharmacies } = useQuery({
    queryKey: ['larc-pharmacies', device || 'all'],
    queryFn: () => api.get('/larc/pharmacies',
                            device ? { params: { device_name: device } } : {})
                       .then(r => r.data),
    staleTime: 60_000,
  })
  const [pharmacyId, setPharmacyId] = useState(a.pharmacy_id || '')
  const [notes, setNotes] = useState('')
  const save = useMutation({
    mutationFn: () => api.post(`/larc/assignments/${a.id}/fax-pharmacy`, {
      pharmacy_id: pharmacyId || null,
      notes: notes || null,
    }).then(r => r.data),
    onSuccess: () => invalidateLarcLists(qc, a.id),
  })

  if (a.request_faxed_at) {
    return (
      <div className="text-[11px] text-green-700 space-y-0.5">
        <div>✓ Faxed {fmt.date(a.request_faxed_at.slice(0, 10))}</div>
        {a.expected_received_by && (
          <div className="text-gray-600">Expecting device by {fmt.date(a.expected_received_by)} (14-day SLA)</div>
        )}
      </div>
    )
  }

  const pharmacy = (pharmacies || []).find(p => p.id === pharmacyId)

  return (
    <div className="space-y-2 text-[12px]">
      <div>
        <div className="text-[10px] uppercase text-gray-500">Pharmacy</div>
        <select className="input text-[12px] w-full"
                value={pharmacyId}
                onChange={e => setPharmacyId(e.target.value)}>
          <option value="">— pick a pharmacy —</option>
          {(pharmacies || []).map(p => (
            <option key={p.id} value={p.id}>{p.name}{p.fax && ` (fax ${p.fax})`}</option>
          ))}
        </select>
        {(pharmacies || []).length === 0 && (
          <div className="text-[10px] text-amber-700 mt-0.5">
            No pharmacies configured. An admin needs to add some in Settings.
          </div>
        )}
      </div>
      {pharmacy?.fax && (
        <div className="bg-plum-50/40 border border-plum-100 rounded px-2 py-1 text-[11px]">
          Send fax to: <span className="font-mono font-medium">{pharmacy.fax}</span>
        </div>
      )}
      <input className="input text-[11px] w-full" placeholder="Notes (optional)"
             value={notes} onChange={e => setNotes(e.target.value)} />
      <button className="btn-primary text-[11px]"
              onClick={() => save.mutate()}
              disabled={save.isPending}>
        {save.isPending ? 'Saving…' : 'Mark fax sent'}
      </button>
    </div>
  )
}


function ReceiveDeviceBody({ a }) {
  const qc = useQueryClient()
  const { data: types } = useQuery({
    queryKey: ['larc-device-types'],
    queryFn: () => api.get('/larc/device-types').then(r => r.data),
    staleTime: 60_000,
  })
  const [ourId, setOurId] = useState('')
  const [lot, setLot] = useState('')
  const [serial, setSerial] = useState('')
  const [exp, setExp] = useState('')
  const [location, setLocation] = useState('white_plains')
  const [price, setPrice] = useState('')
  const [typeId, setTypeId] = useState(a.device_id ? '' :
    (types?.find(t => t.name?.toLowerCase() === (a.device_type_name || '').toLowerCase())?.id || ''))
  // The initializer above runs before the device-types query resolves
  // on first mount, leaving typeId blank and the Receive button
  // disabled until Reception manually re-picks. Once types arrive,
  // default to the assignment's known type (Mirena/Paragard/etc.) so
  // the form is one click closer to submit.
  useEffect(() => {
    if (typeId || !types || !a.device_type_name) return
    const match = types.find(
      t => t.name?.toLowerCase() === a.device_type_name.toLowerCase())
    if (match) setTypeId(match.id)
  }, [types, a.device_type_name, typeId])

  const save = useMutation({
    mutationFn: () => api.post(`/larc/assignments/${a.id}/receive-device`, {
      our_id: ourId.trim(),
      manufacturer_lot: lot || null,
      manufacturer_serial: serial || null,
      expiration_date: exp || null,
      location,
      purchase_price: price === '' ? null : Number(price),
      device_type_id: typeId || null,
    }).then(r => r.data),
    onSuccess: () => invalidateLarcLists(qc, a.id),
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  if (a.device_received_at) {
    return (
      <div className="text-[11px] text-green-700">
        ✓ Received {fmt.date(a.device_received_at.slice(0, 10))} — device <strong>{a.device_our_id}</strong>
      </div>
    )
  }

  return (
    <div className="space-y-2 text-[12px]">
      <div className="text-[11px] text-gray-600">
        When the device arrives, mint a label ID + record the lot # from the box. This binds the
        physical device to this assignment.
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <div className="text-[10px] uppercase text-gray-500">Our label ID *</div>
          <input className="input text-[12px] w-full font-mono" required
                 placeholder="e.g. LARC-2026-0042"
                 value={ourId} onChange={e => setOurId(e.target.value)} />
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500">Device type</div>
          <select className="input text-[12px] w-full"
                  value={typeId} onChange={e => setTypeId(e.target.value)}>
            <option value="">— pick —</option>
            {(types || []).map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500">Manufacturer lot</div>
          <input className="input text-[12px] w-full font-mono"
                 value={lot} onChange={e => setLot(e.target.value)} />
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500">Manufacturer serial</div>
          <input className="input text-[12px] w-full font-mono"
                 value={serial} onChange={e => setSerial(e.target.value)} />
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500">Expiration date</div>
          <input type="date" className="input text-[12px] w-full"
                 value={exp} onChange={e => setExp(e.target.value)} />
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500">Location</div>
          <select className="input text-[12px] w-full"
                  value={location} onChange={e => setLocation(e.target.value)}>
            <option value="white_plains">White Plains</option>
            <option value="arlington">Arlington</option>
            <option value="brandywine">Brandywine</option>
          </select>
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500">Purchase price ($)</div>
          <input type="number" step="0.01" className="input text-[12px] w-full font-mono"
                 value={price} onChange={e => setPrice(e.target.value)} />
        </div>
      </div>
      <button className="btn-primary text-[11px]"
              onClick={() => save.mutate()}
              disabled={!ourId.trim() || !typeId || save.isPending}>
        {save.isPending ? 'Saving…' : 'Receive device & bind to patient'}
      </button>
    </div>
  )
}


function NotifyBody({ a }) {
  const qc = useQueryClient()
  const cost = a.patient_responsibility || 0
  const facility = '' // could be derived per location later
  const defaultMsg = `Hi ${a.patient_name?.split(',')[1]?.trim() || a.patient_name?.split(' ')[0] || 'there'} — great news, your LARC device is ready! ` +
    `Please schedule your insertion appointment with us at your earliest convenience. ` +
    `Your total cost for the visit will be $${Number(cost).toFixed(2)}. ` +
    `Reply to this Klara to grab a slot.`
  const [msg, setMsg] = useState(defaultMsg)
  const [copied, setCopied] = useState(false)

  const send = useMutation({
    mutationFn: () => api.post(`/larc/assignments/${a.id}/notify`,
                                { message_body: msg }).then(r => r.data),
    onSuccess: () => invalidateLarcLists(qc, a.id),
  })

  function copy() {
    navigator.clipboard.writeText(msg).then(() => {
      setCopied(true); setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <div className="space-y-2 text-[12px]">
      <div className="text-[11px] text-gray-600">
        Copy this Klara message to send to the patient. Total cost auto-fills from "Patient responsibility".
      </div>
      <textarea className="input text-[11px] w-full font-mono" rows={4}
                value={msg} onChange={e => setMsg(e.target.value)} />
      <div className="flex items-center gap-2">
        <button className="btn-secondary text-[11px]" onClick={copy}>
          Copy {copied && <span className="ml-1 text-green-700">✓</span>}
        </button>
        <button className="btn-primary text-[11px]"
                onClick={() => send.mutate()}
                disabled={send.isPending}>
          {send.isPending ? 'Marking…' : 'Mark sent on Klara'}
        </button>
      </div>
    </div>
  )
}


function ApptBody({ a }) {
  const qc = useQueryClient()
  const [date, setDate] = useState(a.appt_date || '')
  const save = useMutation({
    mutationFn: () => api.post(`/larc/assignments/${a.id}/schedule-appt`,
                                { appt_date: date }).then(r => r.data),
    onSuccess: () => invalidateLarcLists(qc, a.id),
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  return (
    <div className="space-y-2 text-[12px]">
      <div className="flex items-center gap-2">
        <input type="date" className="input text-[12px]" value={date}
               onChange={e => setDate(e.target.value)} />
        <button className="btn-primary text-[11px]"
                onClick={() => save.mutate()}
                disabled={!date || save.isPending}>
          {save.isPending ? 'Saving…' : 'Save appt date'}
        </button>
      </div>
      {a.appt_date && (
        <div className="text-[10px] text-green-700">Appt scheduled for {fmt.date(a.appt_date)}</div>
      )}
    </div>
  )
}


function CheckoutPlaceholderBody({ a }) {
  const qc = useQueryClient()
  const [dob, setDob] = useState(a.patient_dob || '')
  const [givenTo, setGivenTo] = useState('')
  const [result, setResult] = useState(null)
  const request = useMutation({
    mutationFn: () => api.post(`/larc/assignments/${a.id}/checkout-request`, {
      patient_dob: dob, given_to: givenTo || null,
    }).then(r => r.data),
    onSuccess: (data) => {
      setResult(data)
      invalidateLarcLists(qc, a.id)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Request failed'),
  })

  const done = !!a.milestones?.find(m => m.kind === 'device_checked_out' && m.status === 'done')
  if (done) {
    return (
      <div className="text-[11px] text-green-700">
        ✓ Device checked out — awaiting insertion outcome below.
      </div>
    )
  }

  return (
    <div className="space-y-2 text-[12px]">
      <div className="text-[11px] text-gray-600">
        Pulling the device from the cabinet for insertion. MA enters patient DOB to verify
        identity. Auto-approved when all gates pass (DOB match, today's appt, benefits done,
        device available); otherwise flagged for manager approval.
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <div className="text-[10px] uppercase text-gray-500">Patient DOB (identity check)</div>
          <input type="date" className="input text-[12px] w-full" value={dob}
                 onChange={e => setDob(e.target.value)} />
        </div>
        <div>
          <div className="text-[10px] uppercase text-gray-500">Given to (provider/MA)</div>
          <input className="input text-[12px] w-full" value={givenTo}
                 onChange={e => setGivenTo(e.target.value)}
                 placeholder="e.g. Dr. Cooke" />
        </div>
      </div>
      <button className="btn-primary text-[11px]"
              onClick={() => request.mutate()}
              disabled={!dob || request.isPending}>
        {request.isPending ? 'Requesting…' : 'Request check-out'}
      </button>
      {result && (
        <div className={`text-[11px] p-2 rounded ${
          result.approval_status === 'approved' ? 'bg-green-50 text-green-800 border border-green-200'
            : 'bg-amber-50 text-amber-800 border border-amber-200'
        }`}>
          {result.approval_status === 'approved'
            ? '✓ Auto-approved — device is yours, head to the cabinet.'
            : <>
                ⚠ Flagged for manager approval. Gates that failed:
                <ul className="list-disc pl-5 mt-0.5 text-[10px]">
                  {(result.gate_failures || []).map((g, i) => <li key={i}>{g}</li>)}
                </ul>
              </>}
        </div>
      )}
    </div>
  )
}


function OutcomeBody({ a }) {
  const qc = useQueryClient()
  const [outcome, setOutcome] = useState('inserted')
  const [notes, setNotes] = useState('')
  const [loss, setLoss] = useState('')

  const save = useMutation({
    mutationFn: () => api.post(`/larc/assignments/${a.id}/outcome`, {
      outcome,
      notes: notes || null,
      loss_value: loss === '' ? null : Number(loss),
    }).then(r => r.data),
    onSuccess: () => invalidateLarcLists(qc, a.id),
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  if (a.status === 'inserted' || a.status === 'billed') {
    return (
      <div className="text-[11px] text-green-700">
        ✓ Inserted on {a.inserted_at && fmt.date(a.inserted_at.slice(0, 10))}
        {a.inserted_at && ` by ${(a.milestones?.find(m=>m.kind==='device_inserted')?.completed_by || '').split('@')[0]}`}
      </div>
    )
  }

  return (
    <div className="space-y-2 text-[12px]">
      <select className="input text-[12px] w-full" value={outcome}
              onChange={e => setOutcome(e.target.value)}>
        <option value="inserted">Inserted (success)</option>
        <option value="failed_unused">Failed insertion — device unused (returns to stock)</option>
        <option value="failed_used">Failed insertion — device used (defective → return to manufacturer)</option>
        <option value="patient_no_show">Patient no-show</option>
        <option value="patient_canceled">Patient canceled</option>
        <option value="office_canceled">Office canceled</option>
        <option value="lost">Device lost</option>
        <option value="other">Other (notes required)</option>
      </select>
      {(outcome === 'other' || outcome === 'lost' || outcome === 'failed_used') && (
        <textarea className="input text-[11px] w-full" rows={2}
                  placeholder={outcome === 'other' ? 'Required — what happened?' : 'Notes (optional)'}
                  value={notes} onChange={e => setNotes(e.target.value)} />
      )}
      {(outcome === 'lost' || outcome === 'failed_used') && (
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-gray-600">Loss value ($)</span>
          <input type="number" step="0.01" className="input text-[12px] w-24 font-mono"
                 value={loss} onChange={e => setLoss(e.target.value)} placeholder="0.00" />
        </div>
      )}
      <button className="btn-primary text-[11px]"
              onClick={() => save.mutate()}
              disabled={save.isPending || (outcome === 'other' && !notes.trim())}>
        {save.isPending ? 'Saving…' : 'Record outcome'}
      </button>
    </div>
  )
}


function BilledBody({ a }) {
  const qc = useQueryClient()
  const [claim, setClaim] = useState(a.claim_number || '')
  const save = useMutation({
    mutationFn: () => api.post(`/larc/assignments/${a.id}/bill`,
                                { claim_number: claim }).then(r => r.data),
    onSuccess: () => invalidateLarcLists(qc, a.id),
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  if (a.status === 'billed') {
    return (
      <div className="text-[11px] text-green-700">
        ✓ Billed under claim #{a.claim_number} on {a.billed_at && fmt.date(a.billed_at.slice(0,10))}
        {a.billed_by && ` by ${a.billed_by.split('@')[0]}`}
      </div>
    )
  }

  return (
    <div className="space-y-2 text-[12px]">
      <div className="text-[11px] text-gray-600">
        Once the claim is submitted in ModMed, enter the claim # here to close the assignment.
      </div>
      <div className="flex items-center gap-2">
        <input className="input text-[12px] flex-1 font-mono" placeholder="ModMed claim #"
               value={claim} onChange={e => setClaim(e.target.value)} />
        <button className="btn-primary text-[11px]"
                onClick={() => save.mutate()}
                disabled={!claim.trim() || save.isPending}>
          {save.isPending ? 'Saving…' : 'Save & close'}
        </button>
      </div>
    </div>
  )
}


function DeviceAssignedBody({ a }) {
  // Office-procedure flow: device was picked at surgery scheduling. The
  // milestone is auto-marked done at assignment creation; nothing to do
  // here except show the chain.
  return (
    <div className="text-[11px] text-gray-600 space-y-1">
      <div>
        Device <span className="font-mono font-medium">{a.device_our_id}</span>
        {' '}({a.device_type_name}) was picked from inventory when this
        assignment was created.
      </div>
      {a.linked_surgery_id && (
        <Link to={`/surgeries/${a.linked_surgery_id}`}
              className="text-plum-700 hover:underline inline-flex items-center gap-1">
          View linked surgery →
        </Link>
      )}
    </div>
  )
}


function ConsumeBody({ a }) {
  const qc = useQueryClient()
  const [notes, setNotes] = useState('')
  const save = useMutation({
    mutationFn: () => api.post(`/larc/assignments/${a.id}/consume`,
                                { notes: notes || null }).then(r => r.data),
    onSuccess: () => invalidateLarcLists(qc, a.id),
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  if (a.inserted_at) {
    return (
      <div className="text-[11px] text-green-700">
        ✓ Consumed {fmt.date(a.inserted_at.slice(0, 10))}
      </div>
    )
  }
  return (
    <div className="space-y-2 text-[12px]">
      <div className="text-[11px] text-gray-600">
        Mark the device as used during the procedure. After this, record the
        ModMed claim # to close the assignment.
      </div>
      <textarea className="input text-[11px] w-full" rows={2}
                placeholder="Procedure notes (optional)"
                value={notes} onChange={e => setNotes(e.target.value)} />
      <button className="btn-primary text-[11px]"
              onClick={() => save.mutate()}
              disabled={save.isPending}>
        {save.isPending ? 'Saving…' : 'Mark device consumed'}
      </button>
    </div>
  )
}


function AllocateInventoryCard({ a }) {
  // Only renders for in-stock assignments where no device has been
  // allocated yet. Pharmacy-order assignments use a different
  // workflow (receive-device endpoint) wired up elsewhere.
  if (a.source_flow !== 'in_stock' || a.device_id) return null

  const qc = useQueryClient()
  // payment-received → patient_paid_at flips, device allocation moves
  // the row out of "Awaiting payment" bucket; both need the list views
  // refreshed, not just the detail.
  const refetch = () => invalidateLarcLists(qc, a.id)
  const benefitsDone = !!a.benefits_verified_at
  const paidDone     = !!a.patient_paid_at
  const ready        = benefitsDone && paidDone

  const recordPayment = useMutation({
    mutationFn: (amount) => api.post(`/larc/assignments/${a.id}/payment-received`,
                                       { amount: amount || null }).then(r => r.data),
    onSuccess: refetch,
    onError: (e) => alert(e?.response?.data?.detail || 'Failed'),
  })

  const { data: stock } = useQuery({
    queryKey: ['larc-unassigned', a.device_type_id],
    queryFn: () => api.get('/larc/devices', { params: { status: 'unassigned' } })
                       .then(r => r.data),
    enabled: ready,
  })
  const matchingDevices = (stock?.devices || []).filter(
    d => d.device_type_id === a.device_type_id && d.status === 'unassigned'
  )

  const [devId, setDevId] = useState('')
  const allocate = useMutation({
    mutationFn: () => api.post(`/larc/assignments/${a.id}/allocate-device`,
                                 { device_id: devId }).then(r => r.data),
    onSuccess: refetch,
    onError: (e) => alert(e?.response?.data?.detail || 'Allocation failed'),
  })

  return (
    <div className="card mb-4 border-amber-200 bg-amber-50/40">
      <div className="flex items-center gap-2 mb-2">
        <Package size={14} className="text-amber-700" />
        <h2 className="text-sm font-semibold text-gray-800">
          Allocate Device from Inventory
        </h2>
        <span className="text-[11px] text-gray-500">
          ({a.device_type_name || 'unknown device type'})
        </span>
      </div>
      <p className="text-[11px] text-gray-600 mb-2">
        Reserved patient — device picked from inventory only after benefits
        are verified <em>and</em> the patient has paid their responsibility.
      </p>

      <ol className="text-[12px] space-y-1.5 mb-2">
        <li className="flex items-baseline gap-2">
          <span className={benefitsDone ? 'text-success' : 'text-gray-400'}>
            {benefitsDone ? '✓' : '○'}
          </span>
          <span className={benefitsDone ? 'text-gray-700' : 'text-gray-500'}>
            Benefits verified
            {benefitsDone && <span className="text-[10px] text-gray-500 ml-1">
              {fmt.date(a.benefits_verified_at?.slice(0, 10))}
            </span>}
          </span>
          {!benefitsDone && (
            <span className="text-[10px] text-gray-500">
              — fill in the Benefits Calculator above
            </span>
          )}
        </li>
        <li className="flex items-baseline gap-2">
          <span className={paidDone ? 'text-success' : 'text-gray-400'}>
            {paidDone ? '✓' : '○'}
          </span>
          <span className={paidDone ? 'text-gray-700' : 'text-gray-500'}>
            Patient paid responsibility
            {paidDone && <span className="text-[10px] text-gray-500 ml-1">
              {fmt.date(a.patient_paid_at?.slice(0, 10))}
              {a.patient_paid_amount && <> · ${parseFloat(a.patient_paid_amount).toFixed(2)}</>}
              {a.patient_paid_by && <> · {a.patient_paid_by}</>}
            </span>}
          </span>
          {!paidDone && (
            <button className="btn-secondary py-0.5 px-2 text-[11px] ml-2"
                    onClick={() => {
                      const amt = window.prompt(
                        'Amount paid (USD, optional — blank if unknown):',
                        a.patient_responsibility || '')
                      if (amt === null) return  // cancelled
                      const parsed = amt.trim() ? parseFloat(amt) : null
                      if (amt.trim() && Number.isNaN(parsed)) {
                        alert('Not a number'); return
                      }
                      recordPayment.mutate(parsed)
                    }}
                    disabled={recordPayment.isPending}>
              {recordPayment.isPending ? '…' : 'Record payment'}
            </button>
          )}
        </li>
      </ol>

      {ready && (
        <div className="border-t border-amber-200 pt-2">
          <label className="text-[10px] uppercase text-gray-500 block mb-1">
            Pick device from inventory
          </label>
          <div className="flex gap-2 items-center">
            <select className="input text-[12px] flex-1"
                    value={devId}
                    onChange={e => setDevId(e.target.value)}>
              <option value="">
                {matchingDevices.length === 0
                  ? `— no unassigned ${a.device_type_name || ''} devices in inventory —`
                  : '— pick a device —'}
              </option>
              {matchingDevices.map(d => (
                <option key={d.id} value={d.id}>
                  {d.our_id} · expires {d.expiration_date || 'unknown'}
                  {d.location_label && ` · ${d.location_label}`}
                  {d.manufacturer_lot && ` · lot ${d.manufacturer_lot}`}
                </option>
              ))}
            </select>
            <button className="btn-primary text-[11px] whitespace-nowrap"
                    disabled={!devId || allocate.isPending}
                    onClick={() => allocate.mutate()}>
              {allocate.isPending ? 'Allocating…' : 'Allocate'}
            </button>
          </div>
          {matchingDevices.length === 0 && (
            <div className="text-[10px] text-amber-700 mt-1">
              Receive more {a.device_type_name || ''} into inventory before allocating.
            </div>
          )}
        </div>
      )}
    </div>
  )
}


function InsuranceCardCard({ a }) {
  const qc = useQueryClient()
  const [previewUrl, setPreviewUrl] = useState(null)
  const [previewError, setPreviewError] = useState(null)
  const fileInputRef = useState(() => ({ current: null }))[0]

  // Load the card image as an object URL so the <img> tag can render
  // it without separate auth handling. Re-load when the filename
  // changes (after a re-upload).
  useEffect(() => {
    if (!a.has_insurance_card) { setPreviewUrl(null); return }
    let revoked = false
    api.get(`/larc/assignments/${a.id}/insurance-card`,
            { responseType: 'blob' })
      .then(r => {
        if (revoked) return
        const url = URL.createObjectURL(r.data)
        setPreviewUrl(url)
        setPreviewError(null)
      })
      .catch(e => setPreviewError(e?.response?.status === 404
        ? 'File missing on storage'
        : (e?.response?.data?.detail || e.message)))
    return () => {
      revoked = true
      if (previewUrl) URL.revokeObjectURL(previewUrl)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a.id, a.has_insurance_card, a.insurance_card_filename])

  const upload = useMutation({
    mutationFn: (file) => {
      const fd = new FormData()
      fd.append('file', file)
      return api.post(`/larc/assignments/${a.id}/insurance-card`, fd,
                      { headers: { 'Content-Type': 'multipart/form-data' } })
                 .then(r => r.data)
    },
    onSuccess: () => invalidateLarcLists(qc, a.id),
    onError: (e) => alert(e?.response?.data?.detail || 'Upload failed'),
  })

  function pickFile() {
    fileInputRef.current?.click()
  }

  const hasCard = !!a.has_insurance_card

  return (
    <div className="card mb-4">
      <div className="flex items-center gap-2 mb-2">
        <FileText size={14} className="text-plum-600" />
        <h2 className="text-sm font-semibold text-gray-800">Insurance Card</h2>
        <span className="text-[11px] text-gray-500">
          Attached to every pharmacy enrollment envelope
        </span>
        <div className="ml-auto flex items-center gap-2">
          <input type="file" ref={el => fileInputRef.current = el}
                 accept="image/*,application/pdf"
                 className="hidden"
                 onChange={e => {
                   const f = e.target.files?.[0]
                   if (f) upload.mutate(f)
                 }} />
          <button className="btn-secondary text-[11px]"
                  onClick={pickFile}
                  disabled={upload.isPending}>
            {upload.isPending ? 'Uploading…' : hasCard ? 'Replace' : 'Upload'}
          </button>
        </div>
      </div>

      {!hasCard && (
        <div className="text-[11px] text-amber-700 italic">
          No insurance card on file. Pharmacies typically want to see it
          along with the enrollment form — upload one before sending.
        </div>
      )}
      {hasCard && previewError && (
        <div className="text-[11px] text-danger">
          Couldn't load preview: {previewError}
        </div>
      )}
      {hasCard && !previewError && (
        <div className="flex items-start gap-3">
          <a href={previewUrl || '#'} target="_blank" rel="noopener noreferrer"
             title="Open full size in a new tab">
            {previewUrl ? (
              <img src={previewUrl} alt="Insurance card"
                   className="max-h-40 border border-gray-200 rounded"
                   onError={() => setPreviewError('Could not render image')} />
            ) : (
              <div className="text-[10px] text-gray-400 italic">Loading…</div>
            )}
          </a>
          <div className="text-[11px] text-gray-600 space-y-0.5">
            <div className="font-mono">{a.insurance_card_filename || '(no filename)'}</div>
            <a className="text-plum-700 hover:underline"
               href={previewUrl || '#'} target="_blank" rel="noopener noreferrer">
              Open full size →
            </a>
          </div>
        </div>
      )}
    </div>
  )
}


function ReplacementChainCard({ a }) {
  const qc = useQueryClient()
  const { data: device } = useQuery({
    queryKey: ['larc-device', a.device_id],
    queryFn: () => api.get(`/larc/devices/${a.device_id}`).then(r => r.data),
    enabled: !!a.device_id,
  })

  const [step, setStep] = useState(device?.status === 'returned' ? 'receive' : 'return')
  // Return form
  const [rma, setRma] = useState('')
  const [method, setMethod] = useState('fedex')
  const [tracking, setTracking] = useState('')
  // Receive form
  const [newOurId, setNewOurId] = useState('')
  const [newLot, setNewLot] = useState('')
  const [newSerial, setNewSerial] = useState('')
  const [newExp, setNewExp] = useState('')
  const [newPrice, setNewPrice] = useState('0')
  const [newLocation, setNewLocation] = useState(device?.location || 'white_plains')

  const ret = useMutation({
    mutationFn: () => api.post(`/larc/devices/${a.device_id}/return-to-manufacturer`, {
      rma_number: rma || null,
      return_method: method,
      tracking_number: tracking || null,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['larc-device', a.device_id] })
      invalidateLarcLists(qc, a.id)
      setStep('receive')
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Return failed'),
  })

  const recv = useMutation({
    mutationFn: () => api.post(`/larc/devices/${a.device_id}/receive-replacement`, {
      new_our_id: newOurId.trim(),
      new_manufacturer_lot: newLot || null,
      new_manufacturer_serial: newSerial || null,
      new_expiration_date: newExp || null,
      new_purchase_price: newPrice === '' ? null : Number(newPrice),
      new_location: newLocation,
    }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['larc-device', a.device_id] })
      invalidateLarcLists(qc, a.id)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Receive failed'),
  })

  const isReturned = device?.status === 'returned'
  const isDefective = device?.status === 'defective'

  return (
    <div className="card !p-3 bg-red-50/40 border border-red-200 mb-3">
      <div className="flex items-center gap-1.5 mb-2">
        <AlertTriangle size={14} className="text-red-700" />
        <h2 className="text-sm font-semibold text-red-800">Defective Device — Replacement Chain</h2>
      </div>
      <div className="text-[11px] text-gray-700 mb-2">
        Device <span className="font-mono">{a.device_our_id}</span> failed insertion and is presumed defective.
        Return it to the manufacturer and bind the replacement when it arrives so {a.patient_name}'s workflow can continue.
      </div>

      {/* Step 1: return */}
      <div className="border-l-2 border-red-300 pl-3 mb-3">
        <div className="text-[10px] uppercase tracking-wide text-red-700 mb-1">
          1 · Return to manufacturer {isReturned && <span className="text-green-700 ml-1">✓ done</span>}
        </div>
        {isDefective && !isReturned && (
          <div className="grid grid-cols-3 gap-2 text-[12px]">
            <div>
              <div className="text-[11px] text-gray-500">RMA #</div>
              <input className="input text-[11px] w-full font-mono" value={rma}
                     onChange={e => setRma(e.target.value)} />
            </div>
            <div>
              <div className="text-[11px] text-gray-500">Method</div>
              <select className="input text-[11px] w-full" value={method}
                      onChange={e => setMethod(e.target.value)}>
                <option value="fedex">FedEx</option>
                <option value="ups">UPS</option>
                <option value="usps">USPS</option>
                <option value="manufacturer_pickup">Manufacturer pickup</option>
              </select>
            </div>
            <div>
              <div className="text-[11px] text-gray-500">Tracking #</div>
              <input className="input text-[11px] w-full font-mono" value={tracking}
                     onChange={e => setTracking(e.target.value)} />
            </div>
            <div className="col-span-3">
              <button className="btn-primary text-[11px]"
                      onClick={() => ret.mutate()}
                      disabled={ret.isPending}>
                {ret.isPending ? 'Saving…' : 'Mark returned to manufacturer'}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Step 2: receive replacement */}
      {isReturned && (
        <div className="border-l-2 border-amber-300 pl-3">
          <div className="text-[10px] uppercase tracking-wide text-amber-700 mb-1">
            2 · Receive replacement from manufacturer
          </div>
          <div className="grid grid-cols-2 gap-2 text-[12px]">
            <div>
              <div className="text-[11px] text-gray-500">New label ID *</div>
              <input className="input text-[11px] w-full font-mono" required
                     placeholder="WWC0700" value={newOurId}
                     onChange={e => setNewOurId(e.target.value)} />
            </div>
            <div>
              <div className="text-[11px] text-gray-500">New lot #</div>
              <input className="input text-[11px] w-full font-mono" value={newLot}
                     onChange={e => setNewLot(e.target.value)} />
            </div>
            <div>
              <div className="text-[11px] text-gray-500">New serial #</div>
              <input className="input text-[11px] w-full font-mono" value={newSerial}
                     onChange={e => setNewSerial(e.target.value)} />
            </div>
            <div>
              <div className="text-[11px] text-gray-500">New expiration</div>
              <input type="date" className="input text-[11px] w-full" value={newExp}
                     onChange={e => setNewExp(e.target.value)} />
            </div>
            <div>
              <div className="text-[11px] text-gray-500">Price ($) — usually $0 for replacement</div>
              <input type="number" step="0.01" className="input text-[11px] w-full font-mono"
                     value={newPrice} onChange={e => setNewPrice(e.target.value)} />
            </div>
            <div>
              <div className="text-[11px] text-gray-500">Location</div>
              <select className="input text-[11px] w-full" value={newLocation}
                      onChange={e => setNewLocation(e.target.value)}>
                <option value="white_plains">White Plains</option>
                <option value="arlington">Arlington</option>
                <option value="brandywine">Brandywine</option>
              </select>
            </div>
            <div className="col-span-2">
              <button className="btn-primary text-[11px]"
                      onClick={() => recv.mutate()}
                      disabled={!newOurId.trim() || recv.isPending}>
                {recv.isPending ? 'Saving…' : 'Receive & bind to new assignment'}
              </button>
              <span className="text-[10px] text-gray-500 ml-2">
                Closes this assignment, opens a fresh one on the new device with prior milestones carried over.
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}


function Field({ label, children }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-0.5">{label}</div>
      <div className="text-gray-800">{children}</div>
    </div>
  )
}
