import { useState, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Upload, Receipt, Search, X, Check, MessageSquare, ExternalLink,
  Calendar, DollarSign, Trash2, AlertCircle, Send, Link as LinkIcon,
  Copy, ArrowUp, ArrowDown, ArrowUpDown,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import { useCurrentUser } from '../hooks/useCurrentUser'


function SortableTh({ label, k, sort, onClick }) {
  const active = sort.key === k
  const icon = !active
    ? <ArrowUpDown size={10} className="text-gray-300" />
    : sort.dir === 'asc'
      ? <ArrowUp size={11} className="text-plum-700" />
      : <ArrowDown size={11} className="text-plum-700" />
  return (
    <th className="table-th cursor-pointer select-none hover:bg-plum-100"
        onClick={() => onClick(k)}>
      <span className="inline-flex items-center gap-1">
        {label}
        {icon}
      </span>
    </th>
  )
}


// Show relative time since last provider email; '—' if never emailed.
function EmailedCell({ when }) {
  if (!when) return <span className="text-gray-300">—</span>
  const ms = Date.now() - new Date(when).getTime()
  const days = Math.floor(ms / 86_400_000)
  let label
  if (days <= 0) label = 'today'
  else if (days === 1) label = '1d ago'
  else if (days < 14) label = `${days}d ago`
  else label = `${Math.floor(days / 7)}w ago`
  // Yellow flag if it's been a while (>10 days = past the weekly cadence)
  const tone = days > 10 ? 'text-amber-700' : 'text-gray-500'
  return <span className={tone} title={new Date(when).toLocaleString()}>{label}</span>
}


// Status → chip tone
const STATUS_TONES = {
  new:                'bg-gray-100 text-gray-700  border-gray-300',
  needs_to_be_billed: 'bg-amber-100 text-amber-800 border-amber-300',
  provider_billed:    'bg-blue-100 text-blue-800   border-blue-300',
  provider_error:     'bg-red-100  text-red-800    border-red-300',
  billed:             'bg-green-100 text-green-800 border-green-300',
  no_show:            'bg-gray-200 text-gray-600  border-gray-400',
  canceled:           'bg-gray-200 text-gray-600  border-gray-400',
}


export default function MissingCharges() {
  const { isAdmin } = useCurrentUser()
  const [filters, setFilters] = useState({
    status: '', provider: '', payer: '', appointment: '', patient: '',
    mrn: '', date_from: '', date_to: '',
    open_only: true, search: '',
  })
  const [sort, setSort] = useState({ key: '', dir: '' })
  function toggleSort(key) {
    setSort(prev => {
      if (prev.key !== key)    return { key, dir: 'asc' }
      if (prev.dir === 'asc')  return { key, dir: 'desc' }
      return { key: '', dir: '' }   // third click clears
    })
  }
  const [uploading, setUploading] = useState(false)
  const [emailingProviders, setEmailingProviders] = useState(false)
  const [openId, setOpenId] = useState(null)

  const { data: picks } = useQuery({
    queryKey: ['mc-picklists'],
    queryFn: () => api.get('/billing/missing-charges/picklists').then(r => r.data),
    staleTime: 300_000,
  })
  const { data: dash } = useQuery({
    queryKey: ['mc-dashboard'],
    queryFn: () => api.get('/billing/missing-charges/dashboard').then(r => r.data),
  })
  const { data, isLoading } = useQuery({
    queryKey: ['mc-list', filters, sort],
    queryFn: () => api.get('/billing/missing-charges', {
      params: {
        ...Object.fromEntries(
          Object.entries(filters).filter(([_, v]) => v !== '' && v !== false)
        ),
        ...(sort.key ? { sort: sort.key, sort_dir: sort.dir } : {}),
      },
    }).then(r => r.data),
  })

  const charges = data?.charges || []
  const statusLabel = (v) => picks?.statuses?.find(s => s.v === v)?.l || v

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <Receipt size={18} className="text-plum-700" />
          <h2 className="text-base font-semibold text-gray-800">Missing Charges</h2>
          <span className="text-[11px] text-gray-500">({data?.total ?? 0})</span>
        </div>
        <div className="flex gap-2">
          <button className="btn-secondary text-sm flex items-center gap-1"
                  onClick={() => setEmailingProviders(true)}>
            <Send size={13} /> Email providers
          </button>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => setUploading(true)}>
            <Upload size={13} /> Upload Report
          </button>
        </div>
      </div>

      {/* Status counters */}
      <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-7 gap-2 mb-3">
        {(picks?.statuses || []).map(s => {
          const count = dash?.by_status?.[s.v] ?? 0
          const active = filters.status === s.v
          return (
            <button key={s.v} type="button"
                    onClick={() => setFilters({
                      ...filters, status: active ? '' : s.v,
                      open_only: false,
                    })}
                    className={`card !p-2 text-left border transition ${
                      active ? 'border-plum-600 ring-2 ring-plum-200' :
                      'border-border-subtle hover:border-plum-300'
                    }`}>
              <div className="text-[11px] uppercase text-gray-500 truncate">{s.l}</div>
              <div className="text-xl font-bold mt-0.5">{count}</div>
            </button>
          )
        })}
      </div>

      {/* Filter bar */}
      <div className="card mb-3">
        <div className="grid grid-cols-2 md:grid-cols-6 gap-2 text-sm">
          <div className="md:col-span-2">
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Search</label>
            <div className="relative">
              <Search size={11} className="absolute left-2 top-2 text-muted" />
              <input className="input text-sm pl-7 w-full"
                     placeholder="Patient name / MRN / claim #"
                     value={filters.search}
                     onChange={e => setFilters({ ...filters, search: e.target.value })} />
            </div>
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Patient</label>
            <input className="input text-sm w-full" placeholder="Name contains…"
                   value={filters.patient}
                   onChange={e => setFilters({ ...filters, patient: e.target.value })} />
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">MRN</label>
            <input className="input text-sm w-full font-mono" placeholder="MRN contains…"
                   value={filters.mrn}
                   onChange={e => setFilters({ ...filters, mrn: e.target.value })} />
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Appointment</label>
            <select className="input text-sm w-full" aria-label="Appointment type"
                    value={filters.appointment}
                    onChange={e => setFilters({ ...filters, appointment: e.target.value })}>
              <option value="">All appointment types</option>
              {(picks?.appointment_types || []).map(v => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Provider</label>
            <select className="input text-sm w-full" aria-label="Provider"
                    value={filters.provider}
                    onChange={e => setFilters({ ...filters, provider: e.target.value })}>
              <option value="">All providers</option>
              {(picks?.providers || []).map(v => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Payer</label>
            <select className="input text-sm w-full" aria-label="Payer"
                    value={filters.payer}
                    onChange={e => setFilters({ ...filters, payer: e.target.value })}>
              <option value="">All payers</option>
              {(picks?.payers || []).map(v => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Status</label>
            <select className="input text-sm w-full" aria-label="Status"
                    value={filters.status}
                    onChange={e => setFilters({ ...filters, status: e.target.value, open_only: false })}>
              <option value="">All statuses</option>
              {(picks?.statuses || []).map(s => (
                <option key={s.v} value={s.v}>{s.l}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">DOS from</label>
            <input type="date" className="input text-sm w-full" aria-label="DOS from"
                   value={filters.date_from}
                   onChange={e => setFilters({ ...filters, date_from: e.target.value })} />
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">DOS to</label>
            <input type="date" className="input text-sm w-full" aria-label="DOS to"
                   value={filters.date_to}
                   onChange={e => setFilters({ ...filters, date_to: e.target.value })} />
          </div>
        </div>
        <div className="flex items-center gap-3 mt-2">
          <label className="flex items-center gap-1 text-[12px] cursor-pointer">
            <input type="checkbox" checked={filters.open_only}
                   onChange={e => setFilters({ ...filters, open_only: e.target.checked, status: '' })} />
            Open only (hide billed / no-show / canceled)
          </label>
          <button type="button" className="text-[11px] text-plum-700 hover:underline"
                  onClick={() => {
                    setFilters({
                      status: '', provider: '', payer: '', appointment: '',
                      patient: '', mrn: '', date_from: '', date_to: '',
                      open_only: true, search: '',
                    })
                    setSort({ key: '', dir: '' })
                  }}>
            Clear filters
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="card !p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-plum-50">
            <tr>
              <SortableTh label="DOS"         k="dos"         sort={sort} onClick={toggleSort} />
              <SortableTh label="Patient"     k="patient"     sort={sort} onClick={toggleSort} />
              <SortableTh label="MRN"         k="mrn"         sort={sort} onClick={toggleSort} />
              <SortableTh label="Appointment" k="appointment" sort={sort} onClick={toggleSort} />
              <SortableTh label="Provider"    k="provider"    sort={sort} onClick={toggleSort} />
              <SortableTh label="Payer"       k="payer"       sort={sort} onClick={toggleSort} />
              <SortableTh label="Status"      k="status"      sort={sort} onClick={toggleSort} />
              <th className="table-th">Emailed</th>
              <th className="table-th">Claim #</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {isLoading && (
              <tr><td colSpan={9} className="table-td text-center py-6 text-gray-400">Loading…</td></tr>
            )}
            {!isLoading && charges.length === 0 && (
              <tr><td colSpan={9} className="table-td text-center py-6 text-gray-400 italic">
                No charges match.
              </td></tr>
            )}
            {charges.map(c => (
              <tr key={c.id} className="hover:bg-plum-50/40 cursor-pointer"
                  onClick={() => setOpenId(c.id)}>
                <td className="table-td text-[11px] whitespace-nowrap">{fmt.date(c.appointment_date)}</td>
                <td className="table-td truncate max-w-[180px]">{c.patient_name}</td>
                <td className="table-td font-mono text-[11px]">{c.patient_mrn}</td>
                <td className="table-td text-[11px] truncate max-w-[160px]">{c.appointment_type}</td>
                <td className="table-td text-[11px] truncate max-w-[140px]">{c.primary_provider}</td>
                <td className="table-td text-[11px] truncate max-w-[160px]">{c.payer}</td>
                <td className="table-td">
                  <span className={`text-[11px] uppercase px-1.5 py-0.5 rounded border ${STATUS_TONES[c.status] || ''}`}>
                    {statusLabel(c.status).split(' — ')[0]}
                  </span>
                </td>
                <td className="table-td text-[10px]">
                  <EmailedCell when={c.last_emailed_at} />
                </td>
                <td className="table-td font-mono text-[11px]">{c.claim_number || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {uploading && (
        <UploadDrawer onClose={() => setUploading(false)} />
      )}
      {emailingProviders && (
        <EmailProvidersDrawer onClose={() => setEmailingProviders(false)} />
      )}
      {openId && (
        <DetailDrawer id={openId} picks={picks} isAdmin={isAdmin}
                       onClose={() => setOpenId(null)} />
      )}
    </div>
  )
}


// ─── Email providers drawer (send weekly OR copy per-provider link) ──

function EmailProvidersDrawer({ onClose }) {
  const qc = useQueryClient()
  const [report, setReport] = useState(null)
  const [copied, setCopied] = useState(null)

  // Mapping data — load alongside the drawer so the biller sees gaps first
  const { data: mapData, isLoading: mapLoading, error: mapError, refetch: refetchMap } = useQuery({
    queryKey: ['mc-provider-mappings'],
    queryFn: () => api.get('/billing/missing-charges/provider-mappings').then(r => r.data),
  })
  const { data: workforce = [] } = useQuery({
    queryKey: ['billing-doc-workforce'],
    queryFn: () => api.get('/billing/documents/workforce/assignable').then(r => r.data),
    staleTime: 60_000,
  })

  const sendAll = useMutation({
    mutationFn: () => api.post('/billing/missing-charges/email-providers').then(r => r.data),
    onSuccess: (data) => {
      setReport(data)
      qc.invalidateQueries({ queryKey: ['mc-list'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Send failed'),
  })

  function copyLink(url) {
    const fullUrl = window.location.origin + url
    navigator.clipboard.writeText(fullUrl)
    setCopied(url)
    setTimeout(() => setCopied(null), 2000)
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-2xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">Email Providers</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <div className="text-[12px] text-gray-600 bg-plum-50/50 border border-plum-100 rounded p-2">
            Sends one email per provider with open <em>Needs to be billed</em> rows.
            Each email contains a 60-day signed link to a self-service portal
            where the provider marks each row <strong>Billed</strong> or <strong>Error</strong>.
            <br />
            <span className="text-[11px] text-gray-500">
              Runs automatically every Monday 8 AM. Trigger here for an ad-hoc run.
            </span>
          </div>

          {/* Provider-to-email mapping panel */}
          <ProviderMappingsPanel mapData={mapData} workforce={workforce}
                                  loading={mapLoading} error={mapError}
                                  onRefresh={refetchMap} />

          {!report ? (
            <button className="btn-primary text-sm flex items-center gap-1"
                    onClick={() => sendAll.mutate()}
                    disabled={sendAll.isPending}>
              <Send size={12} /> {sendAll.isPending ? 'Sending…' : 'Send weekly emails now'}
            </button>
          ) : (
            <div className="space-y-2">
              <div className="text-[12px] bg-green-50 border border-green-200 rounded p-2">
                <div className="font-semibold text-green-800">
                  Run complete · {report.sent_count} sent · {report.skipped_count} skipped
                </div>
                <div className="text-[11px] text-gray-700">
                  Total rows included: <strong>{report.total_rows}</strong>
                </div>
              </div>
              <div className="border border-border-subtle rounded overflow-hidden">
                <table className="w-full text-[12px]">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-2 py-1.5 text-left">Provider</th>
                      <th className="px-2 py-1.5 text-left">Rows</th>
                      <th className="px-2 py-1.5 text-left">Status</th>
                      <th className="px-2 py-1.5 text-left">Link</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {report.providers.map(p => (
                      <tr key={p.provider}>
                        <td className="px-2 py-1.5">{p.provider}</td>
                        <td className="px-2 py-1.5">{p.row_count}</td>
                        <td className="px-2 py-1.5">
                          {p.status === 'sent' && (
                            <span className="text-green-700">✓ sent</span>
                          )}
                          {p.status === 'logged_only' && (
                            <span className="text-amber-700" title="SMTP not configured — email body logged to console">
                              logged only
                            </span>
                          )}
                          {p.status === 'skipped_no_email' && (
                            <span className="text-gray-500">no user email</span>
                          )}
                          {p.status === 'ignored' && (
                            <span className="text-gray-400 italic">ignored</span>
                          )}
                        </td>
                        <td className="px-2 py-1.5">
                          <button className="text-plum-700 hover:underline flex items-center gap-1 text-[11px]"
                                  onClick={() => copyLink(p.portal_url)}
                                  title="Copy portal URL">
                            {copied === p.portal_url
                              ? <><Check size={11} /> copied</>
                              : <><Copy size={11} /> copy</>}
                          </button>
                        </td>
                      </tr>
                    ))}
                    {report.providers.length === 0 && (
                      <tr><td colSpan={4} className="px-2 py-3 text-center text-gray-400 italic">
                        No providers had open rows.
                      </td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              <div className="text-[11px] text-gray-500">
                For providers without a user account email, copy the portal link and email it manually.
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}


// ─── Upload drawer ─────────────────────────────────────────────────

function UploadDrawer({ onClose }) {
  const qc = useQueryClient()
  const fileRef = useRef(null)
  const [file, setFile] = useState(null)
  const [result, setResult] = useState(null)

  const upload = useMutation({
    mutationFn: async () => {
      const fd = new FormData()
      fd.append('file', file)
      return api.post('/billing/missing-charges/upload', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      }).then(r => r.data)
    },
    onSuccess: (data) => {
      setResult(data)
      qc.invalidateQueries({ queryKey: ['mc-list'] })
      qc.invalidateQueries({ queryKey: ['mc-dashboard'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Upload failed'),
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">Upload Missing-Charges Report</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <div className="text-[12px] text-gray-600 bg-plum-50/50 border border-plum-100 rounded p-2">
            Drop the ModMed <strong>Appointment Missing Charges</strong> Excel.
            Rows already in the system (matched on patient MRN + DOS) are
            skipped automatically — no duplicates.
          </div>
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Excel file (.xlsx)</label>
            <input ref={fileRef} type="file" accept=".xlsx,.xls"
                   className="text-[12px] w-full"
                   onChange={e => { setFile(e.target.files?.[0] || null); setResult(null) }} />
            {file && (
              <div className="text-[11px] text-gray-500 mt-1">
                {file.name} — {(file.size / 1024).toFixed(1)} KB
              </div>
            )}
          </div>
          {result && (
            <div className="text-[12px] bg-green-50 border border-green-200 rounded p-2 space-y-0.5">
              <div className="font-semibold text-green-800">Imported {result.filename}</div>
              <div>Total rows: <strong>{result.total_rows}</strong></div>
              <div>New: <strong>{result.new_rows}</strong></div>
              <div>Duplicates skipped: <strong>{result.duplicate_rows}</strong></div>
              {result.error_rows > 0 && (
                <div className="text-amber-700">
                  Rows skipped — bad data: {result.error_rows}
                </div>
              )}
            </div>
          )}
        </div>
        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
          <button className="text-sm text-muted hover:underline" onClick={onClose}>
            {result ? 'Close' : 'Cancel'}
          </button>
          {!result && (
            <button className="btn-primary text-sm flex items-center gap-1"
                    onClick={() => upload.mutate()}
                    disabled={!file || upload.isPending}>
              <Upload size={12} /> {upload.isPending ? 'Uploading…' : 'Upload'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}


// ─── Detail drawer (workflow + notes) ──────────────────────────────

function DetailDrawer({ id, picks, isAdmin, onClose }) {
  const qc = useQueryClient()
  const [claimDraft, setClaimDraft] = useState('')

  const { data: c } = useQuery({
    queryKey: ['mc', id],
    queryFn: () => api.get(`/billing/missing-charges/${id}`).then(r => r.data),
  })

  const patchMut = useMutation({
    mutationFn: (body) => api.patch(`/billing/missing-charges/${id}`, body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mc', id] })
      qc.invalidateQueries({ queryKey: ['mc-list'] })
      qc.invalidateQueries({ queryKey: ['mc-dashboard'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Update failed'),
  })

  const deleteMut = useMutation({
    mutationFn: () => api.delete(`/billing/missing-charges/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mc-list'] })
      qc.invalidateQueries({ queryKey: ['mc-dashboard'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Delete failed'),
  })

  if (!c) {
    return (
      <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
        <div className="absolute inset-0 bg-black/30" />
        <div className="relative w-full max-w-lg bg-white shadow-xl p-6"
             onClick={e => e.stopPropagation()}>
          <div className="text-gray-400">Loading…</div>
        </div>
      </div>
    )
  }

  const isTerminal = picks?.terminal_statuses?.includes(c.status)
  const statusLabel = (v) => picks?.statuses?.find(s => s.v === v)?.l || v

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between gap-2">
          <div className="min-w-0">
            <h2 className="font-serif font-semibold text-ink text-[15px] truncate">
              {c.patient_name} <span className="text-[12px] text-gray-500 font-normal">· MRN {c.patient_mrn}</span>
            </h2>
            <div className="text-[11px] text-gray-500">
              {fmt.date(c.appointment_date)} · {c.appointment_type}
              {c.primary_provider && <> · {c.primary_provider}</>}
            </div>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {isAdmin && (
              <button onClick={() => {
                        if (window.confirm(`Delete row for ${c.patient_name} on ${fmt.date(c.appointment_date)}?`))
                          deleteMut.mutate()
                      }}
                      disabled={deleteMut.isPending}
                      className="text-red-600 hover:bg-red-50 p-1.5 rounded flex items-center gap-1 text-[11px]"
                      title="Delete (admin)">
                <Trash2 size={13} />
              </button>
            )}
            <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
          </div>
        </div>

        <div className="p-5 space-y-4 text-sm">
          {/* Current status */}
          <div>
            <label className="text-[11px] uppercase text-gray-500 block mb-1">Current status</label>
            <span className={`text-[12px] uppercase px-2 py-1 rounded border ${STATUS_TONES[c.status] || ''}`}>
              {statusLabel(c.status)}
            </span>
            {c.resolved_at && (
              <div className="text-[10px] text-gray-500 mt-1">
                Resolved {fmt.date(c.resolved_at)} by {c.resolved_by?.split('@')[0]}
              </div>
            )}
          </div>

          {/* Visit context */}
          <div className="bg-gray-50 border border-gray-200 rounded p-2 text-[11px] space-y-1">
            <div><strong>Appointment status:</strong> {c.appointment_status || '—'}</div>
            <div><strong>Visit status:</strong> {c.visit_status || '—'}</div>
            <div><strong>Payer:</strong> {c.payer || '—'}</div>
            {c.patient_dob && <div><strong>DOB:</strong> {fmt.date(c.patient_dob)}</div>}
            {c.patient_link && (
              <a href={c.patient_link} target="_blank" rel="noopener noreferrer"
                 className="text-plum-700 hover:underline inline-flex items-center gap-1">
                Open in ModMed <ExternalLink size={10} />
              </a>
            )}
          </div>

          {/* Triage actions (when in 'new' state) */}
          {c.status === 'new' && (
            <div className="space-y-2">
              <div className="text-[11px] text-gray-700 font-medium">Triage:</div>
              <div className="flex flex-wrap gap-2">
                <button className="btn-primary text-[12px] flex items-center gap-1"
                        onClick={() => patchMut.mutate({ status: 'needs_to_be_billed' })}>
                  <Check size={12} /> Seen — needs billing
                </button>
                <button className="btn-secondary text-[12px]"
                        onClick={() => patchMut.mutate({ status: 'no_show' })}>
                  No show
                </button>
                <button className="btn-secondary text-[12px]"
                        onClick={() => patchMut.mutate({ status: 'canceled' })}>
                  Canceled
                </button>
              </div>
            </div>
          )}

          {/* Provider error explanation (read-only) */}
          {c.status === 'provider_error' && c.provider_response_note && (
            <div className="border-l-2 border-red-300 bg-red-50 pl-2 py-1.5 text-[12px]">
              <div className="flex items-center gap-1 text-red-800 font-medium">
                <AlertCircle size={12} /> Provider response
              </div>
              <div className="whitespace-pre-wrap text-gray-800 mt-0.5">
                {c.provider_response_note}
              </div>
            </div>
          )}

          {/* Claim # entry (when waiting on biller) */}
          {(c.status === 'needs_to_be_billed' || c.status === 'provider_billed' ||
            c.status === 'provider_error' || c.status === 'billed') && (
            <div>
              <label className="text-[11px] uppercase text-gray-500 flex items-center gap-1 mb-1">
                <DollarSign size={11} /> Claim # {c.status === 'billed' && '(billed)'}
              </label>
              <div className="flex gap-2">
                <input className="input text-sm flex-1 font-mono"
                       placeholder="ModMed claim #"
                       value={claimDraft !== '' ? claimDraft : (c.claim_number || '')}
                       onChange={e => setClaimDraft(e.target.value)} />
                <button className="btn-primary text-[12px]"
                        onClick={() => patchMut.mutate({ claim_number: claimDraft || c.claim_number })}
                        disabled={(!claimDraft && !c.claim_number) || patchMut.isPending}>
                  Save & close
                </button>
              </div>
              {c.status === 'billed' && (
                <button className="text-[11px] text-amber-700 hover:underline mt-1"
                        onClick={() => patchMut.mutate({ status: 'needs_to_be_billed' })}>
                  Reopen
                </button>
              )}
            </div>
          )}

          {/* Status override (any status → any status) */}
          <details className="text-[11px]">
            <summary className="cursor-pointer text-gray-500 hover:text-plum-700">
              Change status directly
            </summary>
            <div className="mt-2 flex items-center gap-2">
              <select className="input text-[12px] flex-1"
                      value={c.status}
                      onChange={e => patchMut.mutate({ status: e.target.value })}>
                {picks?.statuses?.map(s => (
                  <option key={s.v} value={s.v}>{s.l}</option>
                ))}
              </select>
            </div>
          </details>

          {/* Notes */}
          <NotesSection chargeId={id} notes={c.notes || []} />
        </div>
      </div>
    </div>
  )
}


function NotesSection({ chargeId, notes }) {
  const qc = useQueryClient()
  const [body, setBody] = useState('')
  const add = useMutation({
    mutationFn: () => api.post(`/billing/missing-charges/${chargeId}/notes`,
                                { body }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mc', chargeId] })
      setBody('')
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })
  return (
    <section>
      <label className="text-[11px] uppercase text-gray-500 flex items-center gap-1 mb-1">
        <MessageSquare size={11} /> Notes ({notes.length})
      </label>
      <div className="space-y-2 max-h-48 overflow-y-auto mb-2">
        {notes.length === 0 && (
          <div className="text-[11px] text-gray-400 italic">No notes yet.</div>
        )}
        {notes.map(n => (
          <div key={n.id} className="border-l-2 border-plum-200 pl-2 py-0.5">
            <div className="text-[10px] text-gray-500">
              {n.author?.split('@')[0]} · {fmt.date(n.created_at)}{' '}
              {fmt.time(n.created_at)}
            </div>
            <div className="text-[12px] text-gray-800 whitespace-pre-wrap">{n.body}</div>
          </div>
        ))}
      </div>
      <textarea className="input text-[12px] w-full" rows={2}
                placeholder="Add a note…"
                value={body} onChange={e => setBody(e.target.value)} />
      <button className="btn-secondary text-[11px] mt-1"
              onClick={() => add.mutate()}
              disabled={!body.trim() || add.isPending}>
        {add.isPending ? 'Saving…' : 'Add note'}
      </button>
    </section>
  )
}


// ─── Provider → user-email mapping panel ───────────────────────────

function ProviderMappingsPanel({ mapData, workforce, loading, error, onRefresh }) {
  const qc = useQueryClient()
  const [addingFor, setAddingFor] = useState(null)
  const [emailDraft, setEmailDraft] = useState('')
  const [showAll, setShowAll] = useState(false)

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['mc-provider-mappings'] })
  }

  const create = useMutation({
    mutationFn: (body) =>
      api.post('/billing/missing-charges/provider-mappings', body).then(r => r.data),
    onSuccess: () => { refresh(); setAddingFor(null); setEmailDraft('') },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  const patch = useMutation({
    mutationFn: ({ id, body }) =>
      api.patch(`/billing/missing-charges/provider-mappings/${id}`, body).then(r => r.data),
    onSuccess: refresh,
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  const del = useMutation({
    mutationFn: (id) => api.delete(`/billing/missing-charges/provider-mappings/${id}`),
    onSuccess: refresh,
    onError: (e) => alert(e?.response?.data?.detail || 'Delete failed'),
  })

  const [autoReport, setAutoReport] = useState(null)
  const autoMatch = useMutation({
    mutationFn: () => api.post('/billing/missing-charges/provider-mappings/auto-match')
                       .then(r => r.data),
    onSuccess: (data) => { setAutoReport(data); refresh() },
    onError: (e) => alert(e?.response?.data?.detail || 'Auto-match failed'),
  })

  const mappings = mapData?.mappings || []
  const unmapped = mapData?.unmapped_providers || []

  // Skip filtering unhelpful "providers" like "-" or "Nurse, Schedule"
  // We still show them so the biller can decide; just nudge by ordering
  // unmapped to the top.

  return (
    <div className="border border-border-subtle rounded">
      <details open>
        <summary className="cursor-pointer px-3 py-2 bg-gray-50 text-[12px] font-medium text-gray-700 flex items-center justify-between rounded-t">
          <span>Provider → email mappings</span>
          <span className="text-[11px] text-gray-500 flex items-center gap-2">
            {loading
              ? <span className="text-gray-400">loading…</span>
              : error
                ? <span className="text-red-600">error</span>
                : (
                  <>
                    {mappings.length} mapped
                    {unmapped.length > 0 && (
                      <span className="ml-2 text-amber-700">
                        · {unmapped.length} unmapped
                      </span>
                    )}
                  </>
                )}
            {onRefresh && (
              <button type="button"
                       onClick={e => { e.preventDefault(); onRefresh() }}
                       className="text-[10px] text-plum-700 hover:underline">
                Refresh
              </button>
            )}
          </span>
        </summary>

        <div className="p-3 space-y-2">
          {loading && (
            <div className="text-[11px] text-gray-500 italic">Loading mappings…</div>
          )}
          {error && (
            <div className="text-[11px] text-red-700 bg-red-50 border border-red-200 rounded p-2">
              Failed to load mappings: {error?.response?.status} {error?.response?.data?.detail || error?.message}
            </div>
          )}

          {/* Auto-match against the Google-sync'd workforce — always visible */}
          <div className="flex items-center gap-2 bg-plum-50/40 border border-plum-100 rounded p-2">
            <button type="button"
                     className="btn-primary text-[11px]"
                     disabled={autoMatch.isPending}
                     onClick={() => autoMatch.mutate()}>
              {autoMatch.isPending ? 'Matching…' : 'Auto-match from Google directory'}
            </button>
            <span className="text-[11px] text-gray-600">
              Pairs each unmapped provider with the matching workforce email by name.
            </span>
          </div>
          {autoReport && (
            <div className="text-[11px] bg-green-50 border border-green-200 rounded p-2 space-y-1">
              <div>
                <strong>{autoReport.matched}</strong> matched ·{' '}
                <strong>{autoReport.unmatched}</strong> still need attention
              </div>
              {autoReport.results.filter(r => !r.matched).length > 0 && (
                <ul className="list-disc ml-5 text-gray-700">
                  {autoReport.results.filter(r => !r.matched).map(r => (
                    <li key={r.provider_name}>
                      <strong>{r.provider_name}</strong> — {r.reason}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* Unmapped first — actionable */}
          {unmapped.length > 0 && (
            <div className="space-y-1">
              <div className="text-[11px] uppercase text-amber-700 font-semibold">
                Unmapped providers with open rows
              </div>
              {unmapped.map(name => (
                <div key={name} className="border border-amber-200 bg-amber-50/50 rounded p-2">
                  <div className="flex items-baseline justify-between gap-2 flex-wrap">
                    <span className="font-medium text-[12px]">{name || '(blank)'}</span>
                    {addingFor === name ? null : (
                      <div className="flex items-center gap-2">
                        <button className="text-[11px] text-plum-700 hover:underline"
                                onClick={() => { setAddingFor(name); setEmailDraft('') }}>
                          + Add mapping
                        </button>
                        <button className="text-[11px] text-gray-500 hover:text-red-700 hover:underline"
                                title="Mark as a non-person (won't appear here again)"
                                onClick={() => {
                                  if (window.confirm(`Ignore "${name}"? This name will be silently skipped by the weekly email cron.`))
                                    create.mutate({ provider_name: name, is_ignored: true })
                                }}>
                          Ignore
                        </button>
                      </div>
                    )}
                  </div>
                  {addingFor === name && (
                    <div className="mt-2 flex gap-1 items-center">
                      <select className="input text-[12px] flex-1"
                              value={emailDraft}
                              onChange={e => setEmailDraft(e.target.value)}>
                        <option value="">— choose user —</option>
                        {workforce.map(u => (
                          <option key={u.email} value={u.email}>
                            {u.name} ({u.email})
                          </option>
                        ))}
                      </select>
                      <button className="btn-primary text-[11px]"
                              disabled={!emailDraft || create.isPending}
                              onClick={() => create.mutate({
                                provider_name: name, user_email: emailDraft,
                              })}>
                        Save
                      </button>
                      <button className="text-[11px] text-gray-500 hover:text-ink px-1"
                              onClick={() => { setAddingFor(null); setEmailDraft('') }}>
                        <X size={11} />
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Existing mappings */}
          {mappings.length > 0 && (
            <div className="space-y-1">
              <button type="button"
                       className="text-[11px] uppercase text-gray-500 font-semibold hover:text-plum-700"
                       onClick={() => setShowAll(v => !v)}>
                Existing mappings ({mappings.length}) {showAll ? '▾' : '▸'}
              </button>
              {showAll && (
                <table className="w-full text-[12px]">
                  <tbody className="divide-y divide-gray-100">
                    {mappings.map(m => (
                      <tr key={m.id} className={m.is_ignored ? 'opacity-60' : ''}>
                        <td className="py-1 pr-2 truncate">
                          {m.provider_name}
                          {m.is_ignored && (
                            <span className="ml-1 text-[11px] uppercase bg-gray-200 text-gray-600 px-1 rounded">ignored</span>
                          )}
                        </td>
                        <td className="py-1 pr-2">
                          {m.is_ignored ? (
                            <span className="text-[11px] text-gray-400 italic">— no email (silenced)</span>
                          ) : (
                            <select className="input text-[11px] w-full"
                                    value={m.user_email || ''}
                                    onChange={e => patch.mutate({
                                      id: m.id, body: { user_email: e.target.value },
                                    })}>
                              {m.user_email && !workforce.some(u => u.email === m.user_email) && (
                                <option value={m.user_email}>{m.user_email}</option>
                              )}
                              {workforce.map(u => (
                                <option key={u.email} value={u.email}>{u.email}</option>
                              ))}
                            </select>
                          )}
                        </td>
                        <td className="py-1 pr-1 text-right">
                          {m.is_ignored ? (
                            <button className="text-[10px] text-plum-700 hover:underline"
                                    onClick={() => patch.mutate({
                                      id: m.id, body: { is_ignored: false, user_email: '' },
                                    })}>
                              un-ignore
                            </button>
                          ) : (
                            <label className="text-[10px] text-gray-500 cursor-pointer">
                              <input type="checkbox" checked={m.is_active}
                                     onChange={e => patch.mutate({
                                       id: m.id, body: { is_active: e.target.checked },
                                     })} /> active
                            </label>
                          )}
                        </td>
                        <td className="py-1 text-right">
                          <button className="text-red-600 hover:bg-red-50 p-0.5 rounded"
                                  title="Delete mapping"
                                  onClick={() => {
                                    if (window.confirm(`Delete mapping for ${m.provider_name}?`))
                                      del.mutate(m.id)
                                  }}>
                            <Trash2 size={11} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}

          {!loading && !error && mappings.length === 0 && unmapped.length === 0 && (
            <div className="text-[11px] text-gray-400 italic">
              No mappings needed yet — no providers with open rows.
            </div>
          )}
        </div>
      </details>
    </div>
  )
}
