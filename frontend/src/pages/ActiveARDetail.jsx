import { useState, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft, Send, Upload, Download, Trash2, Save,
  RefreshCw, ExternalLink, Clock, AlertTriangle, ChevronDown, ChevronRight,
  FileText, Sparkles, X, Mail, Printer, IdCard,
} from 'lucide-react'
import api, { fmt } from '../utils/api'

// ─────────────────────────────────────────────────────────────────────
// Constants

const WORKFLOW_OPTIONS = [
  { value: 'new',              label: 'New' },
  { value: 'in_progress',      label: 'In progress' },
  { value: 'waiting_payer',    label: 'Waiting on payer' },
  { value: 'waiting_patient',  label: 'Waiting on patient' },
  { value: 'denied',           label: 'Denied' },
  { value: 'appealed',         label: 'Appealed' },
  { value: 'paid',             label: 'Paid' },
  { value: 'rebilled_modmed',  label: 'Rebilled in ModMed' },
  { value: 'written_off',      label: 'Written off' },
  { value: 'closed',           label: 'Closed' },
]

const ACTION_TYPES = [
  { value: 'note',              label: 'Note' },
  { value: 'phone_call',        label: 'Phone call' },
  { value: 'fax_sent',          label: 'Fax sent' },
  { value: 'status_check',      label: 'Status check' },
  { value: 'appeal_submitted',  label: 'Appeal submitted' },
  { value: 'other',             label: 'Other' },
]

const PRIORITY_BADGE = {
  Primary:   { label: 'P', cls: 'bg-emerald-100 text-emerald-700' },
  Secondary: { label: 'S', cls: 'bg-amber-100 text-amber-700' },
  Tertiary:  { label: 'T', cls: 'bg-gray-100 text-gray-600' },
}

// ─────────────────────────────────────────────────────────────────────
// Page

export default function ActiveARDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const { data: claim, isLoading } = useQuery({
    queryKey: ['active-ar-claim', id],
    queryFn: () => api.get(`/active-ar/claims/${id}`).then(r => r.data),
  })

  const { data: relatedData } = useQuery({
    queryKey: ['active-ar-claim-related', id],
    queryFn: () => api.get(`/active-ar/claims/${id}/related`).then(r => r.data),
    enabled: !!claim,
  })

  const { data: docsData } = useQuery({
    queryKey: ['active-ar-claim-docs', id],
    queryFn: () => api.get(`/active-ar/claims/${id}/documents`).then(r => r.data),
    enabled: !!claim,
  })

  const { data: assigneesData } = useQuery({
    queryKey: ['active-ar-assignees'],
    queryFn: () => api.get('/active-ar/assignees').then(r => r.data),
    staleTime: 5 * 60 * 1000,
  })

  const updateMutation = useMutation({
    mutationFn: payload => api.patch(`/active-ar/claims/${id}`, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['active-ar-claim', id] })
      qc.invalidateQueries({ queryKey: ['active-ar-summary'] })
    },
  })

  const syncMutation = useMutation({
    mutationFn: () => api.post(`/active-ar/claims/${id}/sync-status`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['active-ar-claim', id] })
      qc.invalidateQueries({ queryKey: ['active-ar-claim-docs', id] })
    },
  })

  if (isLoading) return <div className="p-6 text-gray-400">Loading…</div>
  if (!claim) return <div className="p-6 text-gray-400">Claim not found</div>

  return (
    <div>
      <ClaimHeader
        claim={claim}
        onBack={() => navigate('/active-ar')}
        onSync={() => syncMutation.mutate()}
        syncing={syncMutation.isPending}
        syncResult={syncMutation.isSuccess ? syncMutation.data?.data : null}
        syncError={syncMutation.isError ? (syncMutation.error?.response?.data?.detail || syncMutation.error?.message) : null}
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mt-4 px-1">
        {/* MAIN CONTENT (2/3) */}
        <div className="lg:col-span-2 space-y-3">
          <MoneySummary claim={claim} />
          {claim.denial_summary?.has_appealable_denials && (
            <DenialBanner claim={claim} />
          )}
          <IdInsuranceCardsCard claimId={id} />
          <ServiceLinesCard claim={claim} qc={qc} />
          <NotesCard claim={claim} qc={qc} claimId={id} />
          <AppealLettersCard claimId={id} claim={claim} qc={qc} />
          <DocumentsCard claimId={id} docs={docsData?.documents || []} qc={qc} />
          {claim.allocations?.length > 0 && <PaymentHistoryCard claim={claim} />}
          {relatedData?.related?.length > 0 && (
            <RelatedClaimsCard related={relatedData.related} claim={claim} navigate={navigate} />
          )}
        </div>

        {/* SIDEBAR (1/3) */}
        <div className="space-y-3">
          <PatientCard claim={claim} />
          <InsuranceCard claim={claim} />
          <ProviderCard claim={claim} />
          <WorkflowCard
            claim={claim}
            assignees={assigneesData?.assignees || []}
            onUpdate={payload => updateMutation.mutate(payload)}
          />
          <ActivityLogSidebar claim={claim} qc={qc} claimId={id} />
        </div>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────
// Header (sticky-ish, prominent)

function ClaimHeader({ claim, onBack, onSync, syncing, syncResult, syncError }) {
  const pri = PRIORITY_BADGE[claim.insurance_priority] || PRIORITY_BADGE.Primary
  const tf = claim.tf_status
  const tfDays = claim.days_until_tf_deadline
  const tfBanner = tfBannerSpec(tf, tfDays)

  return (
    <div className="bg-white border-b border-gray-200 px-2 py-3 -mx-6 -mt-6 mb-1">
      <div className="px-6">
        <div className="flex items-start gap-3 mb-2">
          <button onClick={onBack} className="text-gray-400 hover:text-gray-600 mt-1.5">
            <ArrowLeft size={20} />
          </button>
          <div className="flex-1 min-w-0">
            <div className="flex items-baseline gap-2 flex-wrap">
              <h1 className="text-2xl font-bold text-gray-900 leading-none">
                Claim <span className="font-mono text-primary-500">{claim.claim_number}</span>
              </h1>
              <span className={`px-1.5 py-0.5 text-[10px] font-bold rounded ${pri.cls}`}>{pri.label}</span>
              <span className="text-sm text-gray-500">·</span>
              <span className="text-base text-gray-700">
                {claim.patient_name}
                {claim.patient_external_id && (
                  <a
                    href={`/chart/${claim.patient_external_id}`}
                    className="ml-1.5 text-primary-500 hover:underline text-sm"
                  >
                    (chart {claim.patient_external_id} ↗)
                  </a>
                )}
              </span>
            </div>
            <div className="text-xs text-gray-500 mt-1">
              DOS {fmt.date(claim.dos)} · Age {claim.age_days != null ? `${claim.age_days}d` : '—'} ·
              {' '}{claim.insurance_company}
              {claim.last_status_check_at && (
                <span className="ml-2 text-gray-400">
                  Last Waystar check: {fmt.date(claim.last_status_check_at?.slice(0, 10))} {claim.last_status_check_at?.slice(11, 16)}
                </span>
              )}
            </div>
          </div>
          <div className="flex gap-2 shrink-0">
            <button
              className="btn-secondary flex items-center gap-1 text-sm"
              onClick={onSync} disabled={syncing}
              title="Query Waystar for current claim status"
            >
              <RefreshCw size={14} className={syncing ? 'animate-spin' : ''} />
              {syncing ? 'Syncing…' : 'Sync Waystar'}
            </button>
          </div>
        </div>

        {tfBanner && (
          <div className={`flex items-center gap-2 text-xs px-3 py-2 rounded ${tfBanner.cls}`}>
            <AlertTriangle size={14} />
            <span className="font-semibold">{tfBanner.label}</span>
            <span className="opacity-80">
              · TF deadline {fmt.date(claim.tf_deadline_date)} ({claim.tf_days_allowed}-day window for this payer)
            </span>
          </div>
        )}

        {syncResult?.summary && (
          <div className="text-xs bg-green-50 border border-green-200 rounded p-2 mt-2">
            <span className="font-medium">Sync result:</span> {syncResult.summary}
            {syncResult.era_attached && (
              <span className="ml-2 text-green-700">· ERA auto-attached: {syncResult.era_attached}</span>
            )}
          </div>
        )}
        {syncError && (
          <div className="text-xs text-red-600 bg-red-50 rounded p-2 mt-2">
            Sync failed: {syncError}
          </div>
        )}
      </div>
    </div>
  )
}

function tfBannerSpec(tf, days) {
  if (tf === 'past')   return { cls: 'bg-red-50 border border-red-200 text-red-800',     label: `⚠ TF Past Deadline · ${Math.abs(days)} days overdue — appeal or write-off` }
  if (tf === 'urgent') return { cls: 'bg-red-50 border border-red-200 text-red-800',     label: `⚠ TF Urgent · only ${days} days remaining — submit immediately` }
  if (tf === 'soon')   return { cls: 'bg-amber-50 border border-amber-200 text-amber-800', label: `TF Soon · ${days} days remaining` }
  return null
}

// ─────────────────────────────────────────────────────────────────────
// Money summary (3-stat compact row)

function DenialBanner({ claim }) {
  const ds = claim.denial_summary
  const appealable = (ds?.denial_codes || []).filter(c => c.appealable)
  if (appealable.length === 0) return null
  return (
    <div className="card border-l-4 border-l-red-500 bg-red-50/30">
      <div className="flex items-start gap-3">
        <AlertTriangle className="text-red-600 mt-0.5 shrink-0" size={20} />
        <div className="flex-1">
          <h3 className="text-sm font-semibold text-red-800">
            This claim has appealable denial code{appealable.length > 1 ? 's' : ''}
            {ds.total_denied_amount > 0 && (
              <span className="ml-2 font-mono text-xs">· ${ds.total_denied_amount.toFixed(2)} denied</span>
            )}
          </h3>
          <div className="mt-2 space-y-2">
            {appealable.map(c => (
              <div key={`${c.group_code}-${c.reason_code}`} className="bg-white border border-red-100 rounded p-2">
                <div className="flex items-baseline gap-2 text-sm">
                  <span className="font-mono font-semibold text-red-700">{c.group_code}-{c.reason_code}</span>
                  <span className="font-medium text-gray-900">{c.issue}</span>
                  <span className="ml-auto text-xs font-mono text-gray-600">
                    ${c.total_amount.toFixed(2)}
                    {c.lines_affected > 1 && <span className="text-gray-400"> · {c.lines_affected} lines</span>}
                  </span>
                </div>
                <div className="text-xs text-gray-700 mt-1">
                  <span className="font-semibold">Resolution: </span>{c.resolution}
                </div>
              </div>
            ))}
          </div>
          {ds.suggested_template && (
            <div className="mt-3 text-xs text-gray-600">
              Suggested appeal template: <strong className="capitalize">{ds.suggested_template.replace(/_/g, ' ')}</strong>
              <span className="text-gray-400"> — open the Appeals card below to draft</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}


function MoneySummary({ claim }) {
  const billed = claim.total_charges || claim.claim_amount || 0
  const paid = claim.paid_amount || 0
  const insBal = claim.insurance_balance || 0
  const ptBal = claim.patient_balance || 0
  const pctPaid = billed > 0 ? Math.min(100, (paid / billed) * 100) : 0
  return (
    <div className="card">
      <div className="grid grid-cols-4 gap-4">
        <MoneyStat label="Billed"             val={billed} tone="gray" />
        <MoneyStat label="Insurance Paid"     val={paid}   tone="green" />
        <MoneyStat label="Insurance Balance"  val={insBal} tone={insBal > 0 ? 'red' : 'gray'} />
        <MoneyStat label="Patient Balance"    val={ptBal}  tone={ptBal > 0 ? 'amber' : 'gray'} />
      </div>
      <div className="mt-2 h-1 bg-gray-100 rounded-full overflow-hidden">
        <div className="h-full bg-green-500" style={{ width: `${pctPaid}%` }}></div>
      </div>
    </div>
  )
}

function MoneyStat({ label, val, tone }) {
  const tones = {
    gray:  'text-gray-700',
    green: 'text-green-700',
    red:   'text-red-600',
    amber: 'text-amber-600',
  }
  return (
    <div>
      <div className="text-[10px] uppercase text-gray-400 tracking-wide">{label}</div>
      <div className={`text-2xl font-bold mt-0.5 ${tones[tone]}`}>{fmt.currency(val)}</div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────
// Service Lines (the hero card)

function ServiceLinesCard({ claim, qc }) {
  const lines = claim.service_lines || []
  if (lines.length === 0) {
    return (
      <div className="card">
        <h2 className="text-sm font-semibold text-gray-700 mb-2">Service Lines</h2>
        <p className="text-xs text-gray-400 italic">
          No line detail yet. Upload a Charge Analysis covering this DOS to populate CPTs/dx/charges per line.
        </p>
      </div>
    )
  }
  return (
    <div className="card p-0 overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-gray-700">Service Lines</h2>
          <div className="text-[11px] text-gray-500">
            Click <strong>Settle</strong> on each line to enter EOB detail
          </div>
        </div>
        {claim.enriched_at && (
          <div className="text-[10px] text-gray-400">enriched {fmt.date(claim.enriched_at?.slice(0,10))}</div>
        )}
      </div>
      <ServiceLinesTable claim={claim} qc={qc} />
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────
// Notes — user-authored notes for this claim (phone calls, follow-ups, etc.).
// System-generated activity (line_settled, payment_applied, status_changed,
// eob_updated…) is filtered out and remains visible in the right-side
// Activity Log sidebar.

const USER_NOTE_TYPES = new Set(ACTION_TYPES.map(a => a.value))

// ─────────────────────────────────────────────────────────────────────
// ID & Insurance cards — pulled from intake documents matched to this
// claim's chart number. JPG/PNG render as image thumbnails; PDFs render
// as a labeled file tile. Click any thumbnail to open inline in a new tab.

function IdInsuranceCardsCard({ claimId }) {
  const [open, setOpen] = useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ['ar-id-insurance', claimId],
    queryFn: () => api.get(`/active-ar/claims/${claimId}/id-insurance-cards`).then(r => r.data),
    staleTime: 60_000,
  })
  const docs = data?.documents || []

  return (
    <div className="card p-0 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-plum-50/40 transition-colors"
      >
        <div className="flex items-center gap-2">
          <IdCard size={14} className="text-plum-600" />
          <h2 className="text-sm font-semibold text-gray-700">ID & Insurance Cards</h2>
          <span className="text-[11px] text-gray-400">{docs.length}</span>
        </div>
        {open ? <ChevronDown size={14} className="text-gray-400" />
              : <ChevronRight size={14} className="text-gray-400" />}
      </button>
      {open && (
        <div className="px-4 pb-3">
          {isLoading && <div className="text-xs text-gray-400 italic">Loading…</div>}
          {!isLoading && docs.length === 0 && (
            <div className="text-xs text-gray-400 italic">
              No ID/Insurance images on file for this patient.
            </div>
          )}
          {!isLoading && docs.length > 0 && (
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
              {docs.map(d => <CardThumbnail key={d.id} doc={d} />)}
            </div>
          )}
        </div>
      )}
    </div>
  )
}


function CardThumbnail({ doc }) {
  const isImage = ['jpg', 'jpeg'].includes(doc.file_type)
  const yearLabel = doc.doc_year ? String(doc.doc_year) : ''
  return (
    <a
      href={doc.view_url}
      target="_blank"
      rel="noopener noreferrer"
      className="group block border border-gray-200 rounded overflow-hidden bg-white hover:border-plum-300 hover:shadow-sm transition-all"
      title={doc.filename}
    >
      <div className="aspect-[4/3] bg-gray-50 overflow-hidden flex items-center justify-center">
        {isImage ? (
          <img
            src={doc.view_url}
            alt={doc.filename}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform"
            loading="lazy"
          />
        ) : (
          <div className="flex flex-col items-center gap-1.5 text-gray-400 group-hover:text-plum-600">
            <FileText size={32} />
            <span className="text-[10px] font-mono uppercase">{doc.file_type || 'file'}</span>
          </div>
        )}
      </div>
      <div className="px-2 py-1.5 border-t border-gray-100">
        <div className="text-[10px] text-gray-500 truncate">{doc.doc_category}</div>
        {yearLabel && (
          <div className="text-[9px] font-mono text-gray-400 mt-0.5">{yearLabel}</div>
        )}
      </div>
    </a>
  )
}


function NotesCard({ claim, qc, claimId }) {
  const [noteText, setNoteText] = useState('')
  const [actionType, setActionType] = useState('note')

  const noteMutation = useMutation({
    mutationFn: () => api.post(`/active-ar/claims/${claimId}/notes`, {
      action_type: actionType, note: noteText,
    }),
    onSuccess: () => {
      setNoteText('')
      qc.invalidateQueries({ queryKey: ['active-ar-claim', claimId] })
    },
  })

  const userNotes = (claim.notes || [])
    .filter(n => USER_NOTE_TYPES.has(n.action_type))
    // Newest first
    .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-sm font-semibold text-gray-700">Notes</h2>
        <span className="text-[10px] text-gray-400">{userNotes.length} note{userNotes.length === 1 ? '' : 's'}</span>
      </div>

      <div className="flex gap-2 mb-3">
        <select
          className="input text-xs py-1 w-32 shrink-0"
          value={actionType}
          onChange={e => setActionType(e.target.value)}
        >
          {ACTION_TYPES.map(a => <option key={a.value} value={a.value}>{a.label}</option>)}
        </select>
        <textarea
          className="input text-xs flex-1"
          rows={2}
          placeholder="Add a note (e.g. called payer, spoke with patient, follow-up needed)…"
          value={noteText}
          onChange={e => setNoteText(e.target.value)}
        />
        <button
          className="btn-primary text-xs flex items-center gap-1 self-start shrink-0"
          disabled={!noteText.trim() || noteMutation.isPending}
          onClick={() => noteMutation.mutate()}
        >
          <Send size={11} /> {noteMutation.isPending ? 'Saving…' : 'Add Note'}
        </button>
      </div>

      {userNotes.length === 0 ? (
        <div className="text-xs text-gray-400 italic">No notes yet.</div>
      ) : (
        <div className="space-y-2 border-t border-gray-100 pt-2">
          {userNotes.map(n => (
            <div key={n.id} className="border-l-2 border-primary-200 pl-3 py-0.5">
              <div className="flex items-baseline gap-2 text-[11px]">
                <span className="font-medium text-gray-800">{(n.user || 'system').split('@')[0]}</span>
                <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 text-[9px] uppercase tracking-wide">
                  {n.action_type.replace(/_/g, ' ')}
                </span>
                <span className="text-gray-400 ml-auto font-mono">{formatStamp(n.created_at)}</span>
              </div>
              <div className="text-xs text-gray-700 mt-1 whitespace-pre-wrap leading-snug">{n.note}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function formatStamp(iso) {
  if (!iso) return ''
  // Backend returns naive UTC like '2026-05-06 11:11:16.815911' — treat as UTC.
  const d = new Date(iso.replace(' ', 'T') + 'Z')
  if (isNaN(d.getTime())) return iso
  return d.toLocaleString('en-US', {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit',
  })
}

// ─────────────────────────────────────────────────────────────────────
// Sidebar: Patient

function PatientCard({ claim }) {
  return (
    <div className="card">
      <h3 className="text-[10px] uppercase tracking-wide text-gray-400 mb-2">Patient</h3>
      <div className="font-semibold text-gray-900 text-sm">{claim.patient_name}</div>
      <div className="text-xs text-gray-500 mt-1">
        Chart #{claim.patient_external_id}
        {claim.patient_dob && <> · DOB {fmt.date(claim.patient_dob)}</>}
      </div>
      {claim.patient_external_id && (
        <a href={`/chart/${claim.patient_external_id}`} className="text-xs text-primary-500 hover:underline flex items-center gap-1 mt-1">
          <ExternalLink size={11} /> Open chart
        </a>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────
// Sidebar: Insurance

function InsuranceCard({ claim }) {
  return (
    <div className="card">
      <h3 className="text-[10px] uppercase tracking-wide text-gray-400 mb-2">Insurance</h3>
      <div className="text-sm font-medium text-gray-900 leading-snug">
        {claim.insurance_company || '—'}
      </div>
      <div className="text-xs text-gray-600 mt-1">
        {claim.plan_name && <div>Plan: {claim.plan_name}</div>}
        {claim.primary_plan_detail && claim.primary_plan_detail !== claim.plan_name && (
          <div className="text-[11px] text-gray-500">Plan detail: {claim.primary_plan_detail}</div>
        )}
        {claim.policy_number && <div className="font-mono">Policy: {claim.policy_number}</div>}
        {claim.payor_id && <div className="text-[10px] text-gray-400">Payor ID {claim.payor_id}</div>}
        <div className="text-[10px] text-gray-400 mt-1">
          {claim.insurance_priority} · {claim.tf_days_allowed}d TF window
        </div>
      </div>
      {claim.secondary_insurance_company && (
        <div className="mt-2 pt-2 border-t border-gray-100">
          <div className="text-[10px] uppercase tracking-wide text-gray-400">Secondary</div>
          <div className="text-xs text-gray-700">{claim.secondary_insurance_company}</div>
          {claim.secondary_plan_name && (
            <div className="text-[11px] text-gray-500">Plan: {claim.secondary_plan_name}</div>
          )}
          {claim.secondary_policy_number && (
            <div className="text-[10px] text-gray-500 font-mono">{claim.secondary_policy_number}</div>
          )}
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────
// Sidebar: Provider

function ProviderCard({ claim }) {
  if (!claim.care_provider && !claim.rendering_provider_name_full && !claim.billable_provider_npi) return null
  return (
    <div className="card">
      <h3 className="text-[10px] uppercase tracking-wide text-gray-400 mb-2">Provider</h3>
      {claim.care_provider && (
        <div className="text-sm text-gray-900">{claim.care_provider}</div>
      )}
      {claim.rendering_provider_name_full && claim.rendering_provider_name_full !== claim.care_provider && (
        <div className="text-xs text-gray-600 mt-0.5">Rendering: {claim.rendering_provider_name_full}</div>
      )}
      {claim.billable_provider_npi && (
        <div className="text-[10px] text-gray-400 font-mono mt-1">
          Billable NPI {claim.billable_provider_npi}
        </div>
      )}
      {claim.rendering_provider_npi && claim.rendering_provider_npi !== claim.billable_provider_npi && (
        <div className="text-[10px] text-gray-400 font-mono">
          Rendering NPI {claim.rendering_provider_npi}
        </div>
      )}
      {claim.service_location && (
        <div className="text-[10px] text-gray-500 mt-1">📍 {claim.service_location}</div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────
// Sidebar: Workflow + Assignee

function WorkflowCard({ claim, assignees, onUpdate }) {
  const me = (() => {
    try { return JSON.parse(localStorage.getItem('user') || '{}').email } catch { return '' }
  })()
  return (
    <div className="card space-y-3">
      <div>
        <div className="flex items-center justify-between mb-1">
          <h3 className="text-[10px] uppercase tracking-wide text-gray-400">Workflow</h3>
        </div>
        <select
          className="input text-sm"
          value={claim.workflow_state}
          onChange={e => onUpdate({ workflow_state: e.target.value })}
        >
          {WORKFLOW_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>
      <div>
        <div className="flex items-center justify-between mb-1">
          <h3 className="text-[10px] uppercase tracking-wide text-gray-400">Assigned To</h3>
          {me && (
            <button
              type="button"
              className="text-[10px] text-primary-500 hover:underline"
              onClick={() => onUpdate({ assigned_to: me })}
            >
              ← me
            </button>
          )}
        </div>
        <select
          className="input text-sm"
          value={claim.assigned_to || ''}
          onChange={e => onUpdate({ assigned_to: e.target.value })}
        >
          <option value="">— Unassigned —</option>
          {assignees.map(a => (
            <option key={a.email} value={a.email}>
              {a.display_name || a.email}{a.group ? ` (${a.group})` : ''}
            </option>
          ))}
        </select>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────
// Sidebar: Activity Log (sticky, scrollable)

function ActivityLogSidebar({ claim, qc, claimId }) {
  const [noteText, setNoteText] = useState('')
  const [actionType, setActionType] = useState('note')

  const noteMutation = useMutation({
    mutationFn: () => api.post(`/active-ar/claims/${claimId}/notes`, { action_type: actionType, note: noteText }),
    onSuccess: () => { setNoteText(''); qc.invalidateQueries({ queryKey: ['active-ar-claim', claimId] }) },
  })

  return (
    <div className="card flex flex-col" style={{ maxHeight: 'calc(100vh - 100px)' }}>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-[10px] uppercase tracking-wide text-gray-400 flex items-center gap-1">
          <Clock size={11} /> Activity Log
        </h3>
        <span className="text-[10px] text-gray-400">{claim.notes?.length || 0} entries</span>
      </div>
      <div className="space-y-2 mb-3">
        <select className="input text-xs py-1" value={actionType} onChange={e => setActionType(e.target.value)}>
          {ACTION_TYPES.map(a => <option key={a.value} value={a.value}>{a.label}</option>)}
        </select>
        <textarea
          className="input text-xs" rows={2}
          placeholder="Log a follow-up action…"
          value={noteText} onChange={e => setNoteText(e.target.value)}
        />
        <button
          className="btn-primary text-xs flex items-center gap-1 w-full justify-center"
          disabled={!noteText.trim() || noteMutation.isPending}
          onClick={() => noteMutation.mutate()}
        >
          <Send size={11} /> {noteMutation.isPending ? 'Saving…' : 'Log Action'}
        </button>
      </div>
      <div className="flex-1 overflow-y-auto pr-1 -mr-1 space-y-2 border-t border-gray-100 pt-2">
        {claim.notes?.length === 0 && (
          <div className="text-[11px] text-gray-400 italic">No activity yet.</div>
        )}
        {claim.notes?.map(n => (
          <div key={n.id} className="border-l-2 border-primary-200 pl-2">
            <div className="flex items-baseline gap-1.5 text-[10px] text-gray-500">
              <span className="font-medium text-gray-700">{(n.user || 'system').split('@')[0]}</span>
              <span className="text-gray-400">·</span>
              <span>{n.action_type.replace(/_/g, ' ')}</span>
              <span className="text-gray-400 ml-auto">{relativeTime(n.created_at)}</span>
            </div>
            <div className="text-[11px] mt-0.5 whitespace-pre-wrap leading-snug">{n.note}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function relativeTime(iso) {
  if (!iso) return ''
  const d = new Date(iso.replace(' ', 'T') + 'Z')
  const diffMs = Date.now() - d.getTime()
  const mins = Math.floor(diffMs / 60000)
  if (mins < 1) return 'now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days < 7) return `${days}d ago`
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

// ─────────────────────────────────────────────────────────────────────
// Misc cards (collapsibles for less-used sections)

function PaymentHistoryCard({ claim }) {
  return (
    <Collapsible title={`Payment History (${claim.allocations.length})`} defaultOpen={true}>
      <table className="w-full text-sm">
        <thead className="text-xs uppercase text-gray-500">
          <tr>
            <th className="text-left py-1">Date</th>
            <th className="text-left py-1">Payer</th>
            <th className="text-left py-1">Check #</th>
            <th className="text-right py-1">Amount</th>
            <th className="text-left py-1">Note</th>
          </tr>
        </thead>
        <tbody>
          {claim.allocations.map(a => (
            <tr key={a.id} className="border-t border-gray-100">
              <td className="py-1.5 text-xs">{fmt.date(a.check_date) || fmt.date(a.created_at?.slice(0, 10))}</td>
              <td className="py-1.5 text-xs">{a.payer_name}</td>
              <td className="py-1.5 text-xs font-mono">{a.check_number || '—'}</td>
              <td className="py-1.5 text-xs font-mono text-right text-green-700">{fmt.currency(a.amount_applied)}</td>
              <td className="py-1.5 text-xs text-gray-600">{a.allocation_note || ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Collapsible>
  )
}

function RelatedClaimsCard({ related, claim, navigate }) {
  return (
    <Collapsible title={`Related Claims (${related.length} on same DOS)`} defaultOpen={false}>
      <table className="w-full text-sm">
        <thead className="text-xs uppercase text-gray-500">
          <tr>
            <th className="text-left py-1">Claim #</th>
            <th className="text-left py-1">Pri</th>
            <th className="text-left py-1">Payer</th>
            <th className="text-right py-1">Billed</th>
            <th className="text-right py-1">Balance</th>
            <th className="text-left py-1">Workflow</th>
          </tr>
        </thead>
        <tbody>
          {related.map(r => (
            <tr key={r.id} className="border-t border-gray-100 cursor-pointer hover:bg-gray-50"
                onClick={() => navigate(`/active-ar/${r.id}`)}>
              <td className="py-1.5 font-mono text-xs text-primary-500">{r.claim_number}</td>
              <td className="py-1.5 text-xs">{r.insurance_priority?.charAt(0)}</td>
              <td className="py-1.5 text-xs">{r.insurance_company}</td>
              <td className="py-1.5 text-xs font-mono text-right">{fmt.currency(r.total_charges || r.claim_amount)}</td>
              <td className={`py-1.5 text-xs font-mono text-right ${r.insurance_balance > 0 ? 'text-red-600' : 'text-gray-500'}`}>{fmt.currency(r.insurance_balance)}</td>
              <td className="py-1.5 text-xs">{r.workflow_state?.replace(/_/g, ' ')}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Collapsible>
  )
}

// ─────────────────────────────────────────────────────────────────────
// Reusable: Collapsible card

function Collapsible({ title, defaultOpen, children }) {
  const [open, setOpen] = useState(defaultOpen ?? false)
  return (
    <div className="card">
      <button
        type="button"
        className="w-full flex items-center justify-between text-left"
        onClick={() => setOpen(o => !o)}
      >
        <h2 className="text-sm font-semibold text-gray-700">{title}</h2>
        {open ? <ChevronDown size={14} className="text-gray-400" /> : <ChevronRight size={14} className="text-gray-400" />}
      </button>
      {open && <div className="mt-3">{children}</div>}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────
// Common helpers (Stat / Field / Labeled — used by sub-components)

function Field({ label, val }) {
  return (
    <div>
      <div className="text-[10px] uppercase text-gray-400 tracking-wide">{label}</div>
      <div className="text-gray-900 text-sm">{val || <span className="text-gray-400">—</span>}</div>
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

// ─────────────────────────────────────────────────────────────────────
// Service Lines table (per-line settle) — unchanged from prior version

function ServiceLinesTable({ claim, qc }) {
  const [editingLine, setEditingLine] = useState(null)
  const lines = claim.service_lines || []

  const totals = lines.reduce((s, ln) => {
    s.charge += ln.charge || 0
    if (ln.allowed != null) {
      s.allowed += ln.allowed || 0
      s.contractual += ln.contractual || 0
      s.insurance_paid += ln.insurance_paid || 0
      s.pt_resp += (ln.copay || 0) + (ln.deductible || 0) + (ln.coinsurance || 0)
      s.pt_balance += ln.patient_balance || 0
      s.settled_count += ln.settled ? 1 : 0
    }
    return s
  }, { charge: 0, allowed: 0, contractual: 0, insurance_paid: 0, pt_resp: 0, pt_balance: 0, settled_count: 0 })
  const allSettled = lines.length > 0 && totals.settled_count === lines.length

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-xs uppercase text-gray-500 bg-gray-50">
          <tr>
            <th className="text-left py-1.5 px-2">L</th>
            <th className="text-left py-1.5 px-2">CPT</th>
            <th className="text-left py-1.5 px-2">Mod</th>
            <th className="text-left py-1.5 px-2">Dx</th>
            <th className="text-right py-1.5 px-2">U</th>
            <th className="text-right py-1.5 px-2">Charge</th>
            <th className="text-right py-1.5 px-2">Allowed</th>
            <th className="text-right py-1.5 px-2">Contract.</th>
            <th className="text-right py-1.5 px-2">Ins Paid</th>
            <th className="text-right py-1.5 px-2">Pt Resp</th>
            <th className="text-right py-1.5 px-2">Pt Bal</th>
            <th className="text-center py-1.5 px-2">Status</th>
            <th className="py-1.5 px-2"></th>
          </tr>
        </thead>
        <tbody>
          {lines.map(ln => {
            const isEditing = editingLine === ln.line
            const ptResp = ln.allowed != null ? ((ln.copay || 0) + (ln.deductible || 0) + (ln.coinsurance || 0)) : null
            return (
              <>
                <tr key={ln.line} className={`border-t border-gray-100 ${ln.settled ? 'bg-green-50/30' : ''}`}>
                  <td className="py-1.5 px-2 font-mono text-xs">{ln.line}</td>
                  <td className="py-1.5 px-2 font-mono text-xs">{ln.cpt || '—'}</td>
                  <td className="py-1.5 px-2 font-mono text-xs">{ln.modifiers || '—'}</td>
                  <td className="py-1.5 px-2 font-mono text-xs">{ln.dx || '—'}</td>
                  <td className="py-1.5 px-2 text-right text-xs">{ln.units != null ? ln.units : '—'}</td>
                  <td className="py-1.5 px-2 text-right font-mono text-xs">{ln.charge != null ? fmt.currency(ln.charge) : '—'}</td>
                  <td className="py-1.5 px-2 text-right font-mono text-xs text-blue-700">{ln.allowed != null ? fmt.currency(ln.allowed) : <span className="text-gray-300">—</span>}</td>
                  <td className="py-1.5 px-2 text-right font-mono text-xs text-gray-500">{ln.contractual != null ? fmt.currency(ln.contractual) : <span className="text-gray-300">—</span>}</td>
                  <td className="py-1.5 px-2 text-right font-mono text-xs text-green-700">{ln.insurance_paid != null ? fmt.currency(ln.insurance_paid) : <span className="text-gray-300">—</span>}</td>
                  <td className="py-1.5 px-2 text-right font-mono text-xs text-orange-600">{ptResp != null ? fmt.currency(ptResp) : <span className="text-gray-300">—</span>}</td>
                  <td className="py-1.5 px-2 text-right font-mono text-xs text-red-600">{ln.patient_balance != null ? fmt.currency(ln.patient_balance) : <span className="text-gray-300">—</span>}</td>
                  <td className="py-1.5 px-2 text-center text-xs">
                    {ln.settled ? (
                      <span className="px-2 py-0.5 rounded bg-green-100 text-green-700 text-[10px] font-medium">SETTLED</span>
                    ) : ln.allowed != null ? (
                      <span className="px-2 py-0.5 rounded bg-amber-100 text-amber-700 text-[10px] font-medium">OPEN</span>
                    ) : (
                      <span className="text-gray-400 text-[10px]">—</span>
                    )}
                  </td>
                  <td className="py-1.5 px-2 text-right">
                    <button
                      className="text-xs text-primary-500 hover:underline"
                      onClick={() => setEditingLine(isEditing ? null : ln.line)}
                    >
                      {isEditing ? 'Cancel' : ln.allowed != null ? 'Edit' : 'Settle'}
                    </button>
                  </td>
                </tr>
                {ln.adjustment_codes && ln.adjustment_codes.length > 0 && !isEditing && (
                  <tr key={`codes-${ln.line}`} className="border-t-0">
                    <td colSpan={2}></td>
                    <td colSpan={11} className="py-0 px-2 pb-1.5">
                      <div className="flex flex-wrap gap-1">
                        {ln.adjustment_codes.map((ac, idx) => (
                          <AdjustmentCodePill key={idx} code={ac} />
                        ))}
                      </div>
                    </td>
                  </tr>
                )}
                {isEditing && (
                  <LineSettleForm
                    key={`form-${ln.line}`}
                    claim={claim}
                    line={ln}
                    qc={qc}
                    onClose={() => setEditingLine(null)}
                  />
                )}
              </>
            )
          })}
        </tbody>
        <tfoot>
          <tr className="border-t-2 border-gray-300 font-medium bg-gray-50">
            <td colSpan={5} className="py-1.5 px-2 text-right text-xs uppercase text-gray-600">
              Totals {lines.length > 0 && <span className="text-[10px] text-gray-500">({totals.settled_count}/{lines.length} settled)</span>}
            </td>
            <td className="py-1.5 px-2 text-right font-mono text-xs">{fmt.currency(totals.charge)}</td>
            <td className="py-1.5 px-2 text-right font-mono text-xs text-blue-700">{fmt.currency(totals.allowed)}</td>
            <td className="py-1.5 px-2 text-right font-mono text-xs text-gray-500">{fmt.currency(totals.contractual)}</td>
            <td className="py-1.5 px-2 text-right font-mono text-xs text-green-700">{fmt.currency(totals.insurance_paid)}</td>
            <td className="py-1.5 px-2 text-right font-mono text-xs text-orange-600">{fmt.currency(totals.pt_resp)}</td>
            <td className="py-1.5 px-2 text-right font-mono text-xs text-red-600">{fmt.currency(totals.pt_balance)}</td>
            <td colSpan={2} className="py-1.5 px-2 text-center text-xs">
              {allSettled && <span className="px-2 py-0.5 rounded bg-green-100 text-green-700 text-[10px] font-medium">ALL SETTLED</span>}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  )
}

function LineSettleForm({ claim, line, qc, onClose }) {
  const [allowed, setAllowed] = useState(line.allowed ?? '')
  const [contractual, setContractual] = useState(line.contractual ?? '')
  const [copay, setCopay] = useState(line.copay ?? '')
  const [deductible, setDeductible] = useState(line.deductible ?? '')
  const [coinsurance, setCoinsurance] = useState(line.coinsurance ?? '')
  const [patientPaid, setPatientPaid] = useState(line.patient_paid ?? '')
  // '' = use auto-computed insurance paid; any number = manual override
  const [insPaidOverride, setInsPaidOverride] = useState(
    line.insurance_paid != null && line.allowed != null
      // Only treat as override if it differs from what auto would have computed
      && Math.abs(
        line.insurance_paid -
        Math.max(0, (line.allowed || 0) - ((line.copay || 0) + (line.deductible || 0) + (line.coinsurance || 0))
          - ((line.adjustment_codes || []).filter(c =>
              !((c.group_code || '').toUpperCase() === 'CO' && String(c.reason_code) === '45')
            ).reduce((s, c) => s + (parseFloat(c.amount) || 0), 0)
          )
        )
      ) > 0.01
      ? line.insurance_paid : ''
  )
  const [notes, setNotes] = useState(line.notes ?? '')
  // Adjustment codes — empty array means "use default CO-45" on save
  const [adjCodes, setAdjCodes] = useState(line.adjustment_codes || [])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  // CARC reference lookup — cached for the session, used to auto-fill
  // descriptions when the user types a reason code (e.g. 253 → sequestration).
  const { data: carcData } = useQuery({
    queryKey: ['adjustment-codes', 'CARC', 'all'],
    queryFn: () => api.get('/adjustment-codes', {
      params: { code_type: 'CARC', per_page: 200 },
    }).then(r => r.data),
    staleTime: 1000 * 60 * 60,  // 1h
  })
  const carcMap = (carcData?.items || []).reduce((m, item) => {
    m[item.code] = item.official_verbiage
    return m
  }, {})

  const charge = line.charge || 0
  const allowedNum = parseFloat(allowed || 0) || 0
  const contractualNum = parseFloat(contractual || 0) || 0
  const copayNum = parseFloat(copay || 0) || 0
  const dedNum = parseFloat(deductible || 0) || 0
  const coinsNum = parseFloat(coinsurance || 0) || 0
  const ptPaidNum = parseFloat(patientPaid || 0) || 0
  const ptResp = copayNum + dedNum + coinsNum
  // Sum of adjustment codes that AREN'T the contractual CO-45 (which is
  // already represented by the Contractual input). Sequestration (CO-253),
  // bundling (CO-97), denials, etc. all live here.
  const otherAdjsTotal = adjCodes
    .filter(c => !((c.group_code || '').toUpperCase() === 'CO' && String(c.reason_code) === '45'))
    .reduce((s, c) => s + (parseFloat(c.amount) || 0), 0)
  const insPaidAuto = allowed === '' ? 0 : Math.max(0, allowedNum - ptResp - otherAdjsTotal)
  const insPaidValue = insPaidOverride === '' ? insPaidAuto : (parseFloat(insPaidOverride) || 0)
  const ptBalance = Math.max(0, ptResp - ptPaidNum)
  // Balance equation: charge = contractual + insurance_paid + pt_resp + other_adjs
  const sum = contractualNum + insPaidValue + ptResp + otherAdjsTotal
  const balanced = (allowedNum > 0 || contractualNum > 0 || otherAdjsTotal > 0)
                   && Math.abs(charge - sum) < 0.01

  function setAllowedSync(v) {
    setAllowed(v)
    const n = parseFloat(v || 0) || 0
    setContractual(v === '' ? '' : Math.max(0, charge - n).toFixed(2))
  }
  function setContractualSync(v) {
    setContractual(v)
    const n = parseFloat(v || 0) || 0
    setAllowed(v === '' ? '' : Math.max(0, charge - n).toFixed(2))
  }

  async function save() {
    setBusy(true); setError(null)
    try {
      const payload = {
        allowed: parseFloat(allowed || 0) || 0,
        copay: copayNum,
        deductible: dedNum,
        coinsurance: coinsNum,
        patient_paid: ptPaidNum,
        notes: notes || null,
      }
      if (insPaidOverride !== '') {
        payload.insurance_paid_override = parseFloat(insPaidOverride) || 0
      }
      if (adjCodes.length > 0) {
        payload.adjustment_codes = adjCodes.map(ac => ({
          group_code: ac.group_code || 'CO',
          reason_code: String(ac.reason_code || ''),
          amount: parseFloat(ac.amount || 0) || 0,
          description: ac.description || null,
        }))
      }
      await api.patch(`/active-ar/claims/${claim.id}/service-lines/${line.line}`, payload)
      qc.invalidateQueries({ queryKey: ['active-ar-claim', claim.id] })
      onClose()
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally { setBusy(false) }
  }

  function addCode() {
    const contractualAmt = parseFloat(contractual || 0) || 0
    setAdjCodes([
      ...adjCodes,
      {
        group_code: 'CO',
        reason_code: '45',
        amount: contractualAmt.toFixed(2),
        description: carcMap['45'] || 'Charges exceed fee schedule',
      },
    ])
  }
  function updateCode(idx, field, val) {
    setAdjCodes(adjCodes.map((c, i) => {
      if (i !== idx) return c
      const next = { ...c, [field]: val }
      // When reason_code changes and matches a known CARC, auto-fill description.
      // We always overwrite — user can manually edit after if they want a custom note.
      if (field === 'reason_code') {
        const lookup = carcMap[String(val || '').trim()]
        if (lookup) next.description = lookup
      }
      return next
    }))
  }
  function removeCode(idx) {
    setAdjCodes(adjCodes.filter((_, i) => i !== idx))
  }

  return (
    <tr className="bg-blue-50/40 border-t border-blue-100">
      <td colSpan={13} className="p-3">
        <div className="text-xs font-semibold text-gray-700 mb-2">
          Settle Line {line.line} · CPT {line.cpt} · Charge {fmt.currency(charge)}
        </div>
        <div className="grid grid-cols-3 md:grid-cols-7 gap-2 mb-2">
          <Labeled label="Allowed">
            <input className="input font-mono text-xs" type="number" step="0.01"
                   value={allowed} onChange={e => setAllowedSync(e.target.value)} />
          </Labeled>
          <Labeled label="Contractual">
            <input className="input font-mono text-xs" type="number" step="0.01"
                   value={contractual} onChange={e => setContractualSync(e.target.value)} />
          </Labeled>
          <Labeled label={
            <span title="Defaults to Allowed − patient resp − other adjustments. Override for sequestration, takebacks, etc.">
              Ins Paid {insPaidOverride === '' && <span className="text-gray-400 normal-case">(auto)</span>}
            </span>
          }>
            <input className="input font-mono text-xs" type="number" step="0.01"
                   placeholder={insPaidAuto.toFixed(2)}
                   value={insPaidOverride}
                   onChange={e => setInsPaidOverride(e.target.value)} />
          </Labeled>
          <Labeled label="Copay">
            <input className="input font-mono text-xs" type="number" step="0.01"
                   value={copay} onChange={e => setCopay(e.target.value)} />
          </Labeled>
          <Labeled label="Deductible">
            <input className="input font-mono text-xs" type="number" step="0.01"
                   value={deductible} onChange={e => setDeductible(e.target.value)} />
          </Labeled>
          <Labeled label="Coinsurance">
            <input className="input font-mono text-xs" type="number" step="0.01"
                   value={coinsurance} onChange={e => setCoinsurance(e.target.value)} />
          </Labeled>
          <Labeled label="Patient Paid">
            <input className="input font-mono text-xs" type="number" step="0.01"
                   value={patientPaid} onChange={e => setPatientPaid(e.target.value)} />
          </Labeled>
        </div>
        <div className="grid grid-cols-3 md:grid-cols-6 gap-2 mb-2 text-xs">
          <div className="bg-white border border-gray-100 rounded p-1.5"><div className="text-gray-400 uppercase text-[9px]">Charge</div><div className="font-mono">{fmt.currency(charge)}</div></div>
          <div className="bg-white border border-gray-100 rounded p-1.5"><div className="text-gray-400 uppercase text-[9px]">Pt Resp (auto)</div><div className="font-mono text-orange-600">{fmt.currency(ptResp)}</div></div>
          <div className="bg-white border border-gray-100 rounded p-1.5"><div className="text-gray-400 uppercase text-[9px]">Ins Paid {insPaidOverride === '' ? '(auto)' : '(override)'}</div><div className="font-mono text-green-700">{fmt.currency(insPaidValue)}</div></div>
          <div className="bg-white border border-gray-100 rounded p-1.5"><div className="text-gray-400 uppercase text-[9px]">Pt Balance (auto)</div><div className="font-mono text-red-600">{fmt.currency(ptBalance)}</div></div>
          <div className="col-span-2 bg-white border border-gray-100 rounded p-1.5">
            <div className="text-gray-400 uppercase text-[9px]">Balance Check</div>
            <div className={`text-xs ${balanced ? 'text-green-700 font-medium' : (allowedNum > 0 || contractualNum > 0 ? 'text-amber-700' : 'text-gray-400')}`}>
              {balanced ? '✓ Balanced' : (allowedNum > 0 || contractualNum > 0)
                ? `⚠ Off by ${fmt.currency(Math.abs(charge - sum))}`
                : 'Enter Allowed to check'}
            </div>
          </div>
        </div>
        {/* Adjustment codes editor */}
        <div className="mb-2">
          <div className="flex items-center justify-between mb-1">
            <div className="text-[10px] uppercase text-gray-500 tracking-wide">
              Adjustment Codes (CARC/RARC)
              <span className="ml-2 text-gray-400 normal-case">
                — defaults to CO-45 if you leave empty and contractual &gt; 0
              </span>
            </div>
            <button type="button" className="text-xs text-primary-500 hover:underline" onClick={addCode}>
              + Add Code
            </button>
          </div>
          {adjCodes.length === 0 ? (
            <div className="text-xs text-gray-400 italic">No codes — CO-45 will auto-add for the contractual amount</div>
          ) : (
            <div className="space-y-1">
              {adjCodes.map((c, idx) => (
                <div key={idx} className="flex gap-1.5 items-center">
                  <select
                    className="input text-xs py-1 w-16"
                    value={c.group_code || 'CO'}
                    onChange={e => updateCode(idx, 'group_code', e.target.value)}
                  >
                    <option>CO</option><option>PR</option><option>OA</option><option>PI</option><option>CR</option>
                  </select>
                  <input
                    className="input text-xs py-1 w-20 font-mono"
                    placeholder="45"
                    value={c.reason_code || ''}
                    onChange={e => updateCode(idx, 'reason_code', e.target.value)}
                  />
                  <input
                    className="input text-xs py-1 w-24 font-mono"
                    type="number" step="0.01" placeholder="amount"
                    value={c.amount || ''}
                    onChange={e => updateCode(idx, 'amount', e.target.value)}
                  />
                  <input
                    className="input text-xs py-1 flex-1"
                    placeholder="Description (e.g. Not medically necessary)"
                    value={c.description || ''}
                    onChange={e => updateCode(idx, 'description', e.target.value)}
                  />
                  <button type="button" onClick={() => removeCode(idx)} className="text-gray-400 hover:text-red-600">
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <Labeled label="Line Notes (optional)">
          <input className="input text-xs" value={notes} onChange={e => setNotes(e.target.value)}
                 placeholder="e.g. Denial reason, appeal status, partial payment context…" />
        </Labeled>
        {error && <div className="text-red-600 text-xs mt-2">{error}</div>}
        <div className="flex gap-2 justify-end mt-2">
          <button className="btn-secondary text-xs" onClick={onClose}>Cancel</button>
          <button className="btn-primary text-xs flex items-center gap-1" onClick={save}
                  disabled={busy || (allowed === '' && adjCodes.length === 0)}
                  title={allowed === '' && adjCodes.length === 0
                    ? 'Enter an allowed amount (use 0 for a full denial) or add an adjustment code'
                    : ''}>
            <Save size={12} /> {busy ? 'Saving…' : 'Save Line'}
          </button>
        </div>
      </td>
    </tr>
  )
}


// Pill that shows a single CARC/RARC code on a service line row.
// Color-coded: red = appealable denial, gray = informational/contractual.
const _NON_APPEALABLE_RC = new Set(['1', '2', '3', '45', '253'])

function AdjustmentCodePill({ code }) {
  const gc = (code.group_code || '').toUpperCase()
  const rc = String(code.reason_code || '').trim()
  const isAppealable = gc === 'CO' && rc && !_NON_APPEALABLE_RC.has(rc)
  const cls = isAppealable
    ? 'bg-red-50 border-red-200 text-red-700'
    : 'bg-gray-50 border-gray-200 text-gray-600'
  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] ${cls}`}
      title={code.description || ''}
    >
      <span className="font-mono font-semibold">{gc}-{rc}</span>
      {code.amount > 0 && <span className="font-mono">${Number(code.amount).toFixed(2)}</span>}
      {code.description && <span className="opacity-75 max-w-[180px] truncate">· {code.description}</span>}
    </span>
  )
}

// ─────────────────────────────────────────────────────────────────────
// Documents card

const DOC_TYPES = ['EOB', 'Denial Letter', 'Appeal', 'Correspondence',
                   'Medical Records', 'Insurance Card', 'Other']

function DocumentsCard({ claimId, docs, qc }) {
  const fileRef = useRef(null)
  const [docType, setDocType] = useState('EOB')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function handleFile(e) {
    const file = e.target.files?.[0]
    if (!file) return
    setBusy(true); setError(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const params = new URLSearchParams({ document_type: docType })
      if (description) params.append('description', description)
      await api.post(`/active-ar/claims/${claimId}/documents?${params}`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      qc.invalidateQueries({ queryKey: ['active-ar-claim-docs', claimId] })
      qc.invalidateQueries({ queryKey: ['active-ar-claim', claimId] })
      setDescription('')
      if (fileRef.current) fileRef.current.value = ''
    } catch (err) {
      setError(err?.response?.data?.detail || err.message)
    } finally { setBusy(false) }
  }

  async function handleDelete(docId, filename) {
    if (!window.confirm(`Delete ${filename}?`)) return
    try {
      await api.delete(`/active-ar/claims/${claimId}/documents/${docId}`)
      qc.invalidateQueries({ queryKey: ['active-ar-claim-docs', claimId] })
      qc.invalidateQueries({ queryKey: ['active-ar-claim', claimId] })
    } catch (e) { alert(e?.response?.data?.detail || e.message) }
  }

  function fmtSize(bytes) {
    if (!bytes) return ''
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }

  return (
    <Collapsible title={`Documents (${docs.length})`} defaultOpen={docs.length > 0}>
      <div className="flex gap-2 items-center mb-3 flex-wrap">
        <select className="input w-36 text-xs" value={docType} onChange={e => setDocType(e.target.value)}>
          {DOC_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        <input
          className="input flex-1 min-w-[180px] text-xs"
          placeholder="Description (optional)"
          value={description} onChange={e => setDescription(e.target.value)}
        />
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.gif,.webp,.heic,.doc,.docx,.txt,.rtf"
          onChange={handleFile}
          className="hidden"
        />
        <button
          className="btn-secondary text-xs flex items-center gap-1"
          onClick={() => fileRef.current?.click()} disabled={busy}
        >
          <Upload size={12} /> {busy ? 'Uploading…' : 'Upload'}
        </button>
      </div>
      {error && <div className="text-red-600 text-xs mb-2">{error}</div>}
      {docs.length === 0 ? (
        <div className="text-xs text-gray-400 italic">No documents yet.</div>
      ) : (
        <ul className="divide-y divide-gray-100">
          {docs.map(d => (
            <li key={d.id} className="flex items-center gap-2 py-1.5 text-xs">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-gray-900 truncate">{d.filename}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 uppercase tracking-wide">{d.document_type}</span>
                </div>
                <div className="text-[10px] text-gray-500">
                  {fmtSize(d.file_size)} · {(d.uploaded_by || '').split('@')[0]} · {fmt.date(d.uploaded_at?.slice(0, 10))}
                  {d.description && <> · {d.description}</>}
                </div>
              </div>
              <a
                href={`/api${d.download_url.startsWith('/api') ? d.download_url.slice(4) : d.download_url}`}
                target="_blank" rel="noopener noreferrer"
                className="text-primary-500 hover:underline flex items-center gap-1"
              >
                <Download size={12} /> View
              </a>
              <button onClick={() => handleDelete(d.id, d.filename)} className="text-gray-400 hover:text-red-600">
                <Trash2 size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </Collapsible>
  )
}

// ─────────────────────────────────────────────────────────────────────
// Appeal Letters

function AppealLettersCard({ claimId, claim, qc }) {
  const [showDraft, setShowDraft] = useState(false)
  const [editingId, setEditingId] = useState(null)

  const { data } = useQuery({
    queryKey: ['active-ar-claim-appeals', claimId],
    queryFn: () => api.get(`/active-ar/claims/${claimId}/appeals`).then(r => r.data),
    enabled: !!claimId,
  })

  const appeals = data?.appeals || []
  const editing = appeals.find(a => a.id === editingId)

  return (
    <Collapsible
      title={`Appeals (${appeals.length})`}
      defaultOpen={appeals.length > 0 || claim.workflow_state === 'denied'}
    >
      <div className="flex justify-between items-center mb-3">
        <div className="text-xs text-gray-500">
          {claim.workflow_state === 'denied' && (
            <span className="inline-flex items-center gap-1 text-red-600">
              <AlertTriangle size={12} /> Claim is denied — generate a Level 1 appeal
            </span>
          )}
        </div>
        <button
          className="btn-primary text-xs flex items-center gap-1"
          onClick={() => setShowDraft(true)}
        >
          <FileText size={12} /> New Appeal Letter
        </button>
      </div>

      {appeals.length === 0 ? (
        <div className="text-xs text-gray-400 italic">No appeals drafted yet.</div>
      ) : (
        <ul className="divide-y divide-gray-100">
          {appeals.map(a => (
            <li key={a.id} className="py-2 text-sm">
              <div className="flex items-center gap-2">
                <span className="text-[10px] uppercase tracking-wide bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">
                  L{a.level}
                </span>
                <span className="font-medium text-gray-900">{a.template_type.replace(/_/g, ' ')}</span>
                <StatusBadge status={a.status} />
                {a.used_ai_drafting && (
                  <span className="text-[10px] text-purple-600 flex items-center gap-0.5">
                    <Sparkles size={10} /> AI-drafted
                  </span>
                )}
                <span className="ml-auto flex gap-1">
                  <button
                    className="text-xs text-primary-500 hover:underline"
                    onClick={() => setEditingId(a.id)}
                  >Edit</button>
                  {a.pdf_path && (
                    <a
                      href={`/api/active-ar/appeals/${a.id}/pdf`}
                      target="_blank" rel="noopener noreferrer"
                      className="text-xs text-primary-500 hover:underline flex items-center gap-0.5"
                    ><Download size={11} /> PDF</a>
                  )}
                </span>
              </div>
              <div className="text-[11px] text-gray-500 mt-0.5">
                {a.recipient_name}
                {a.recipient_fax && <> · Fax {a.recipient_fax}</>}
                {a.sent_at && <> · Sent {fmt.date(a.sent_at?.slice(0, 10))} via {a.sent_via}</>}
                {a.response_outcome && <> · Response: <strong>{a.response_outcome}</strong></>}
              </div>
            </li>
          ))}
        </ul>
      )}

      {showDraft && (
        <AppealDraftModal
          claimId={claimId}
          claim={claim}
          onClose={() => setShowDraft(false)}
          onCreated={(letter) => { setShowDraft(false); setEditingId(letter.id); qc.invalidateQueries() }}
        />
      )}

      {editing && (
        <AppealEditor
          appeal={editing}
          claim={claim}
          onClose={() => setEditingId(null)}
          onChanged={() => qc.invalidateQueries()}
        />
      )}
    </Collapsible>
  )
}


function StatusBadge({ status }) {
  const map = {
    draft:     'bg-gray-100 text-gray-600',
    generated: 'bg-blue-100 text-blue-700',
    sent:      'bg-amber-100 text-amber-700',
    responded: 'bg-purple-100 text-purple-700',
    approved:  'bg-green-100 text-green-700',
    denied:    'bg-red-100 text-red-700',
    withdrawn: 'bg-gray-100 text-gray-500',
  }
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded ${map[status] || 'bg-gray-100'}`}>
      {status}
    </span>
  )
}


function AppealDraftModal({ claimId, claim, onClose, onCreated }) {
  const [templateType, setTemplateType] = useState('medical_necessity')
  const [level, setLevel] = useState(1)
  const [useAi, setUseAi] = useState(true)
  const [signerName, setSignerName] = useState('')
  const [signerTitle, setSignerTitle] = useState('')
  const [verbiage, setVerbiage] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const { data: tmplData } = useQuery({
    queryKey: ['appeal-templates'],
    queryFn: () => api.get('/active-ar/appeal-templates').then(r => r.data),
  })

  async function draft() {
    setBusy(true); setError(null)
    try {
      const res = await api.post(`/active-ar/claims/${claimId}/appeals/draft`, {
        template_type: templateType,
        level: level,
        use_ai: useAi,
        additional_verbiage: verbiage || null,
        signer_name: signerName || null,
        signer_title: signerTitle || null,
      })
      onCreated?.(res.data)
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">New Appeal Letter</h2>
          <button onClick={onClose}><X size={18} className="text-gray-500" /></button>
        </div>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Labeled label="Template Type">
              <select className="input text-sm" value={templateType} onChange={e => setTemplateType(e.target.value)}>
                {tmplData?.template_types?.map(t => (
                  <option key={t.key} value={t.key}>{t.label}</option>
                ))}
              </select>
            </Labeled>
            <Labeled label="Level">
              <select className="input text-sm" value={level} onChange={e => setLevel(parseInt(e.target.value))}>
                <option value={1}>Level 1 (Reconsideration)</option>
                <option value={2}>Level 2 (Formal Appeal)</option>
                <option value={3}>External Review (IRO/IDR)</option>
              </select>
            </Labeled>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Labeled label="Signer Name (optional, defaults to practice manager)">
              <input className="input text-sm" placeholder="—" value={signerName} onChange={e => setSignerName(e.target.value)} />
            </Labeled>
            <Labeled label="Signer Title">
              <input className="input text-sm" placeholder="Practice Manager" value={signerTitle} onChange={e => setSignerTitle(e.target.value)} />
            </Labeled>
          </div>
          <Labeled label="Additional Custom Verbiage (optional, included verbatim in body)">
            <textarea
              className="input text-xs" rows={4}
              placeholder="Standard language WWC always wants in appeals (HIPAA, reservation of rights, etc.)"
              value={verbiage} onChange={e => setVerbiage(e.target.value)}
            />
          </Labeled>
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={useAi} onChange={e => setUseAi(e.target.checked)} />
            <Sparkles size={12} className="text-purple-500" />
            <span>Use Claude AI to draft a tailored argument body (you can edit after)</span>
          </label>
          <div className="text-[11px] text-gray-500 bg-gray-50 rounded p-2 leading-relaxed">
            Drafting for Claim <strong>{claim.claim_number}</strong> · {claim.patient_name}<br/>
            Payer: {claim.insurance_company}<br/>
            DOS: {fmt.date(claim.dos)}  ·  Billed: {fmt.currency(claim.total_charges || claim.claim_amount)}
          </div>
        </div>
        {error && <div className="text-red-600 text-xs mt-3">{error}</div>}
        <div className="flex gap-2 justify-end mt-4">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary flex items-center gap-1" onClick={draft} disabled={busy}>
            <FileText size={14} /> {busy ? (useAi ? 'Drafting with Claude…' : 'Drafting…') : 'Draft Letter'}
          </button>
        </div>
      </div>
    </div>
  )
}


function AppealEditor({ appeal, claim, onClose, onChanged }) {
  const [subject, setSubject] = useState(appeal.subject || '')
  const [body, setBody] = useState(appeal.body || '')
  const [verbiage, setVerbiage] = useState(appeal.additional_verbiage || '')
  const [recipientName, setRecipientName] = useState(appeal.recipient_name || '')
  const [recipientAddress, setRecipientAddress] = useState(appeal.recipient_address || '')
  const [recipientFax, setRecipientFax] = useState(appeal.recipient_fax || '')
  const [signerName, setSignerName] = useState(appeal.signer_name || '')
  const [signerTitle, setSignerTitle] = useState(appeal.signer_title || '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [pdfUrl, setPdfUrl] = useState(appeal.pdf_path ? `/api/active-ar/appeals/${appeal.id}/pdf` : null)

  async function save() {
    setBusy(true); setError(null)
    try {
      await api.patch(`/active-ar/appeals/${appeal.id}`, {
        subject, body, additional_verbiage: verbiage,
        recipient_name: recipientName, recipient_address: recipientAddress,
        recipient_fax: recipientFax,
        signer_name: signerName, signer_title: signerTitle,
      })
      onChanged?.()
    } catch (e) { setError(e?.response?.data?.detail || e.message) }
    finally { setBusy(false) }
  }

  async function generatePdf() {
    await save()
    setBusy(true); setError(null)
    try {
      await api.post(`/active-ar/appeals/${appeal.id}/generate-pdf`)
      setPdfUrl(`/api/active-ar/appeals/${appeal.id}/pdf?ts=${Date.now()}`)
      onChanged?.()
    } catch (e) { setError(e?.response?.data?.detail || e.message) }
    finally { setBusy(false) }
  }

  async function sendFax() {
    if (!pdfUrl) { await generatePdf() }
    if (!recipientFax) { setError('No recipient fax number'); return }
    if (!window.confirm(`Send to fax ${recipientFax}?`)) return
    setBusy(true); setError(null)
    try {
      await api.post(`/active-ar/appeals/${appeal.id}/send-fax`)
      onChanged?.(); onClose()
    } catch (e) { setError(e?.response?.data?.detail || e.message) }
    finally { setBusy(false) }
  }

  async function markSent(via) {
    setBusy(true); setError(null)
    try {
      await api.post(`/active-ar/appeals/${appeal.id}/mark-sent?sent_via=${via}`)
      onChanged?.(); onClose()
    } catch (e) { setError(e?.response?.data?.detail || e.message) }
    finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-5xl max-h-[90vh] overflow-y-auto p-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-lg font-semibold">Edit Appeal Letter</h2>
            <p className="text-xs text-gray-500">
              Claim {claim.claim_number} · L{appeal.level} {appeal.template_type.replace(/_/g, ' ')} ·
              <StatusBadge status={appeal.status} />
              {appeal.used_ai_drafting && <span className="ml-1 text-purple-600">✦ AI</span>}
            </p>
          </div>
          <button onClick={onClose}><X size={18} className="text-gray-500" /></button>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="space-y-3">
            <Labeled label="Subject">
              <input className="input text-sm" value={subject} onChange={e => setSubject(e.target.value)} />
            </Labeled>
            <div className="grid grid-cols-2 gap-3">
              <Labeled label="Recipient Name">
                <input className="input text-sm" value={recipientName} onChange={e => setRecipientName(e.target.value)} />
              </Labeled>
              <Labeled label="Recipient Fax">
                <input className="input text-sm font-mono" value={recipientFax} onChange={e => setRecipientFax(e.target.value)} />
              </Labeled>
            </div>
            <Labeled label="Recipient Address">
              <textarea className="input text-xs" rows={4} value={recipientAddress} onChange={e => setRecipientAddress(e.target.value)} />
            </Labeled>
            <div className="grid grid-cols-2 gap-3">
              <Labeled label="Signer Name">
                <input className="input text-sm" value={signerName} onChange={e => setSignerName(e.target.value)} />
              </Labeled>
              <Labeled label="Signer Title">
                <input className="input text-sm" value={signerTitle} onChange={e => setSignerTitle(e.target.value)} />
              </Labeled>
            </div>
            <Labeled label="Additional Custom Verbiage">
              <textarea className="input text-xs" rows={3} value={verbiage} onChange={e => setVerbiage(e.target.value)} />
            </Labeled>
          </div>
          <div>
            <Labeled label="Letter Body (edit before generating PDF)">
              <textarea
                className="input text-xs font-mono"
                rows={28}
                value={body}
                onChange={e => setBody(e.target.value)}
              />
            </Labeled>
          </div>
        </div>

        {error && <div className="text-red-600 text-xs mt-3">{error}</div>}

        <div className="flex gap-2 justify-end mt-4 flex-wrap">
          <button className="btn-secondary text-sm" onClick={onClose}>Close</button>
          <button className="btn-secondary text-sm flex items-center gap-1" onClick={save} disabled={busy}>
            <Save size={14} /> Save Draft
          </button>
          <button className="btn-secondary text-sm flex items-center gap-1" onClick={generatePdf} disabled={busy}>
            <FileText size={14} /> Generate PDF
          </button>
          {pdfUrl && (
            <a href={pdfUrl} target="_blank" rel="noopener noreferrer"
               className="btn-secondary text-sm flex items-center gap-1">
              <Download size={14} /> Download
            </a>
          )}
          {recipientFax && (
            <button className="btn-primary text-sm flex items-center gap-1" onClick={sendFax} disabled={busy}>
              <Send size={14} /> Send Fax
            </button>
          )}
          <button className="btn-secondary text-sm flex items-center gap-1" onClick={() => markSent('mail')} disabled={busy}>
            <Mail size={14} /> Mark Mailed
          </button>
        </div>
      </div>
    </div>
  )
}

