import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  User, Heart, Pill, AlertTriangle, Activity, Shield,
  FileText, Scissors, Users, Cigarette, ChevronLeft,
  CreditCard, Eye, Download, ChevronDown, ChevronRight,
  Search, Filter, Phone, X, Check, Loader2,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import FaxBatchModal from '../components/FaxBatchModal'
import FaxStatusChip from '../components/FaxStatusChip'
import ChartPatientList from '../components/ChartPatientList'
import { useFaxByChart, faxByDocId } from '../hooks/useFaxByChart'

function FaxModal({ doc, docType, onClose, patient }) {
  const [faxNumber, setFaxNumber] = useState('2402522141')

  const patientInfo = patient ? [
    `Patient: ${patient.patient_name || ''}`,
    patient.dob ? `DOB: ${fmt.date(patient.dob)}` : '',
    patient.chart_number ? `Chart #: ${patient.chart_number}` : '',
    `Document Type: ${doc.doc_type || doc.doc_category || doc.filename || ''}`,
    doc.doc_date ? `Document Date: ${fmt.date(doc.doc_date)}` : '',
  ].filter(Boolean).join('\n') : ''

  const [coverText, setCoverText] = useState(patientInfo)
  const [sending, setSending] = useState(false)
  const [result, setResult] = useState(null)

  async function handleSend() {
    if (!faxNumber.replace(/\D/g, '').length >= 10) return
    setSending(true)
    setResult(null)
    try {
      const r = await api.post('/fax/send', {
        fax_number: faxNumber,
        doc_type: docType,
        doc_id: doc.id,
        cover_text: coverText,
      })
      setResult({ success: true, data: r.data })
    } catch (err) {
      setResult({ success: false, error: err.response?.data?.detail || err.message })
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-white rounded-lg shadow-xl w-[420px] p-5">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold text-gray-800 flex items-center gap-2">
            <Phone size={16} /> Send Fax
          </h3>
          <button onClick={onClose} className="p-1 hover:bg-gray-100 rounded"><X size={16} /></button>
        </div>

        <div className="text-xs text-gray-500 mb-3 bg-gray-50 rounded p-2">
          {doc.filename || doc.doc_type || 'Document'}
        </div>

        <label className="block text-xs font-medium text-gray-700 mb-1">Fax Number</label>
        <input
          className="w-full border rounded px-3 py-2 text-sm mb-3 focus:outline-none focus:ring-1 focus:ring-primary-400"
          placeholder="(240) 555-1234"
          value={faxNumber}
          onChange={e => setFaxNumber(e.target.value)}
        />

        <label className="block text-xs font-medium text-gray-700 mb-1">Cover Page Note (optional)</label>
        <textarea
          className="w-full border rounded px-3 py-2 text-sm mb-4 focus:outline-none focus:ring-1 focus:ring-primary-400"
          rows={2}
          placeholder="Please add to patient chart..."
          value={coverText}
          onChange={e => setCoverText(e.target.value)}
        />

        {result && (
          <div className={`text-xs rounded p-2 mb-3 ${result.success ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
            {result.success ? (
              <div>
                <span className="flex items-center gap-1"><Check size={12} /> Fax {result.data.status || 'Queued'} — ID: {result.data.message_id}</span>
                <span className="text-[10px] block mt-0.5">To: {result.data.to} {result.data.pages ? `| ${result.data.pages} pages` : ''}</span>
              </div>
            ) : (
              result.error
            )}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="btn-secondary text-xs">Cancel</button>
          <button
            onClick={handleSend}
            disabled={sending || faxNumber.replace(/\D/g, '').length < 10}
            className="btn-primary text-xs flex items-center gap-1 disabled:opacity-50"
          >
            {sending ? <Loader2 size={12} className="animate-spin" /> : <Phone size={12} />}
            {sending ? 'Sending…' : 'Send Fax'}
          </button>
        </div>
      </div>
    </div>
  )
}

function Accordion({ title, icon: Icon, color = 'text-gray-700', badge, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border rounded-lg mb-3 bg-white overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-4 py-3 hover:bg-gray-50 transition-colors"
      >
        {open ? <ChevronDown size={16} className="text-gray-400 shrink-0" /> : <ChevronRight size={16} className="text-gray-400 shrink-0" />}
        <Icon size={16} className={`${color} shrink-0`} />
        <span className="text-sm font-semibold text-gray-800">{title}</span>
        {badge > 0 && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-500">{badge}</span>
        )}
      </button>
      {open && <div className="px-4 pb-4 border-t">{children}</div>}
    </div>
  )
}

function EmptyState({ text }) {
  return <div className="text-xs text-gray-400 italic py-3">{text}</div>
}

function HistoryTable({ items, columns }) {
  if (!items?.length) return <EmptyState text="No records" />
  return (
    <div className="overflow-x-auto mt-2">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b text-gray-500">
            {columns.map(c => (
              <th key={c.key} className="text-left py-1.5 pr-3 font-medium">{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((item, i) => (
            <tr key={i} className="border-b last:border-0">
              {columns.map(c => (
                <td key={c.key} className={`py-1.5 pr-3 ${c.className || 'text-gray-700'}`}>
                  {c.render ? c.render(item) : (item[c.key] || '—')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function IntakeSection({ docs, onFax }) {
  if (!docs?.length) return <EmptyState text="No intake documents" />
  const byCategory = {}
  for (const doc of docs) {
    const cat = doc.doc_category || 'Other'
    if (!byCategory[cat]) byCategory[cat] = []
    byCategory[cat].push(doc)
  }
  // Sort categories: ID&Insurance first
  const sortedCats = Object.keys(byCategory).sort((a, b) => {
    if (a.includes('Insurance')) return -1
    if (b.includes('Insurance')) return 1
    return a.localeCompare(b)
  })
  return (
    <div className="space-y-4 mt-2">
      {sortedCats.map(cat => (
        <div key={cat}>
          <div className="text-xs font-semibold text-gray-600 mb-2">{cat}</div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-2">
            {byCategory[cat].map(doc => (
              <div key={doc.id} className="border rounded-lg p-2 hover:bg-gray-50 group relative">
                {['jpg', 'jpeg', 'png'].includes(doc.file_type) ? (
                  <img
                    src={`/api/intake/view/${doc.id}`}
                    alt={doc.filename}
                    className="rounded border w-full h-24 object-contain bg-gray-50 cursor-pointer"
                    onClick={() => window.open(`/api/intake/view/${doc.id}`, '_blank')}
                  />
                ) : (
                  <div
                    className="rounded border w-full h-24 bg-gray-50 flex items-center justify-center cursor-pointer hover:bg-blue-50"
                    onClick={() => window.open(`/api/intake/view/${doc.id}`, '_blank')}
                  >
                    <FileText size={24} className="text-gray-300" />
                  </div>
                )}
                <div className="mt-1.5">
                  <div className="text-[10px] text-gray-700 font-medium truncate" title={doc.filename}>
                    {doc.filename}
                  </div>
                  <div className="text-[9px] text-gray-400 flex items-center gap-1">
                    <span className="uppercase">{doc.file_type}</span>
                    {doc.file_size_kb > 0 && <span>· {doc.file_size_kb} KB</span>}
                  </div>
                </div>
                <div className="absolute top-1 right-1 flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button
                    onClick={() => onFax && onFax(doc)}
                    className="p-1 bg-white/90 rounded shadow-sm hover:bg-orange-50 text-orange-600"
                    title="Fax"
                  >
                    <Phone size={11} />
                  </button>
                  <button
                    onClick={() => window.open(`/api/intake/download/${doc.id}`, '_blank')}
                    className="p-1 bg-white/90 rounded shadow-sm hover:bg-green-50 text-green-600"
                    title="Download"
                  >
                    <Download size={11} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

function DocumentsSection({ chartNumber, onBatchFax }) {
  const [docType, setDocType] = useState('')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState(new Set())
  const PER_PAGE = 30

  const { data: docs } = useQuery({
    queryKey: ['chart-docs', chartNumber, docType, page],
    queryFn: () => api.get('/documents', {
      params: { chart_number: chartNumber, doc_type: docType || undefined, page, per_page: PER_PAGE },
    }).then(r => r.data),
  })

  const { data: types } = useQuery({
    queryKey: ['chart-doc-types', chartNumber],
    queryFn: () => api.get('/documents/types').then(r => r.data),
  })

  const faxQuery = useFaxByChart(chartNumber)
  const byDoc = faxByDocId(faxQuery.data)

  if (!docs?.documents?.length && !docType) return <EmptyState text="No PrimeSuite documents for this chart" />

  const allDocsOnPage = docs?.documents || []

  function toggleDoc(docId) {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(docId)) next.delete(docId)
      else next.add(docId)
      return next
    })
  }

  function selectUnsent() {
    const unsent = allDocsOnPage
      .filter(d => !byDoc[d.id] || byDoc[d.id].status === 'failed')
      .map(d => d.id)
    setSelected(prev => new Set([...prev, ...unsent]))
  }

  function clearSelection() { setSelected(new Set()) }

  function handleBatchClick() {
    if (onBatchFax) onBatchFax(Array.from(selected), clearSelection)
  }

  async function handleRetry(row) {
    try {
      await api.post(`/fax/retry/${row.id}`)
      faxQuery.refetch()
    } catch (e) {
      console.error('Retry failed', e)
    }
  }

  // Group by date
  const groups = []
  const groupMap = new Map()
  for (const d of allDocsOnPage) {
    const key = d.doc_date || 'unknown'
    if (!groupMap.has(key)) {
      const g = { key, doc_date: d.doc_date, docs: [] }
      groupMap.set(key, g)
      groups.push(g)
    }
    groupMap.get(key).docs.push(d)
  }

  return (
    <div className="mt-2">
      <div className="flex items-center gap-2 mb-3">
        <select
          className="border rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-plum-700"
          value={docType}
          onChange={e => { setDocType(e.target.value); setPage(1) }}
        >
          <option value="">All Types</option>
          {types?.types?.map(t => (
            <option key={t.type} value={t.type}>{t.type} ({t.count})</option>
          ))}
        </select>
        <span className="text-xs text-muted">{docs?.total || 0} documents</span>
        <div className="ml-auto flex items-center gap-3 text-xs">
          <button onClick={selectUnsent} className="text-plum-700 underline">Select Unsent</button>
          {selected.size > 0 && (
            <>
              <button onClick={clearSelection} className="text-muted underline">
                Clear ({selected.size})
              </button>
              <button onClick={handleBatchClick} className="btn-primary">
                Fax {selected.size} {selected.size === 1 ? 'doc' : 'docs'} to EMA →
              </button>
            </>
          )}
        </div>
      </div>

      <div className="space-y-2">
        {groups.map(g => (
          <div key={g.key} className="border rounded overflow-hidden">
            <div className="bg-plum-50 px-3 py-1.5 text-xs flex items-center gap-2 text-muted">
              <span className="font-medium">
                {g.doc_date
                  ? new Date(g.doc_date + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
                  : 'Unknown Date'}
              </span>
              <span className="text-muted">· {g.docs.length} doc{g.docs.length !== 1 ? 's' : ''}</span>
            </div>
            {g.docs.map(doc => {
              const faxRow = byDoc[doc.id]
              return (
                <div key={doc.id} className="flex items-center px-3 py-1.5 text-xs border-t hover:bg-plum-50/40">
                  <input
                    type="checkbox"
                    className="mr-3"
                    checked={selected.has(doc.id)}
                    onChange={() => toggleDoc(doc.id)}
                  />
                  <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium mr-3 ${docTypeColor(doc.doc_type)}`}>
                    {doc.doc_type}
                  </span>
                  <span className="text-muted font-mono text-[10px] mr-3">ID {doc.doc_id || '—'}</span>
                  <span className="text-muted mr-3">{doc.file_size_kb > 0 ? `${doc.file_size_kb} KB` : ''}</span>
                  <span className="mr-auto">
                    {faxRow && <FaxStatusChip row={faxRow} onRetry={handleRetry} />}
                  </span>
                  <div className="flex gap-1">
                    <button
                      onClick={() => window.open(`/api/documents/view/${doc.id}`, '_blank')}
                      className="p-1 hover:bg-plum-100 rounded text-plum-700"
                      title="View"
                    ><Eye size={13} /></button>
                    <button
                      onClick={() => window.open(`/api/documents/download/${doc.id}`, '_blank')}
                      className="p-1 hover:bg-plum-100 rounded text-plum-700"
                      title="Download"
                    ><Download size={13} /></button>
                  </div>
                </div>
              )
            })}
          </div>
        ))}
      </div>

      {docs && docs.total > PER_PAGE && (
        <div className="flex items-center justify-center gap-3 mt-3 text-xs text-muted">
          <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1} className="px-2 py-1 border rounded disabled:opacity-40">Prev</button>
          <span>Page {page} of {Math.ceil(docs.total / PER_PAGE)}</span>
          <button onClick={() => setPage(p => p + 1)} disabled={page >= Math.ceil(docs.total / PER_PAGE)} className="px-2 py-1 border rounded disabled:opacity-40">Next</button>
        </div>
      )}
    </div>
  )
}

const DOC_TYPE_COLORS = {
  'Insurance Card': 'bg-blue-100 text-blue-700',
  'Driver': 'bg-green-100 text-green-700',
  'HIPAA': 'bg-purple-100 text-purple-700',
  'Progress Note': 'bg-gray-100 text-gray-700',
  'Lab': 'bg-yellow-100 text-yellow-700',
  'Imaging': 'bg-orange-100 text-orange-700',
  'Pathology': 'bg-red-100 text-red-700',
  'Hospital': 'bg-indigo-100 text-indigo-700',
  'Prenatal': 'bg-pink-100 text-pink-700',
}

function docTypeColor(docType) {
  for (const [key, cls] of Object.entries(DOC_TYPE_COLORS)) {
    if (docType.toLowerCase().includes(key.toLowerCase())) return cls
  }
  return 'bg-gray-100 text-gray-600'
}

export default function PatientChart() {
  const { chartNumber } = useParams()

  const [faxDoc, setFaxDoc] = useState(null) // {doc, docType}
  const [batchDocIds, setBatchDocIds] = useState(null)  // array when open, null when closed
  const [batchClearFn, setBatchClearFn] = useState(null)  // () => void, called after send

  const { data: chart, isLoading, error } = useQuery({
    queryKey: ['chart', chartNumber],
    queryFn: () => api.get(`/chart/${chartNumber}`).then(r => r.data),
  })

  const d = chart?.demographics
  const activeMeds = chart?.medications?.filter(m => m.active) || []
  const inactiveMeds = chart?.medications?.filter(m => !m.active) || []
  const activeAllergies = chart?.allergies?.filter(a => a.active) || []
  const latestVital = chart?.vitals?.[0]

  return (
    <div className="p-6">
      <div className="grid gap-3" style={{ gridTemplateColumns: '280px 1fr', minHeight: 'calc(100vh - 120px)' }}>
        {/* Persistent patient list */}
        <div className="sticky top-4" style={{ alignSelf: 'start', maxHeight: 'calc(100vh - 80px)' }}>
          <ChartPatientList activeChartNumber={chartNumber} />
        </div>

        {/* Chart detail */}
        <div>
        {isLoading && <div className="text-gray-400">Loading chart…</div>}
        {error && <div className="text-red-600">Error loading chart: {error.message}</div>}
        {!isLoading && !error && chart && (<>
      {/* Header */}
      <div className="mb-4">
        <Link to="/documents" className="text-xs text-primary-600 hover:underline flex items-center gap-1 mb-2">
          <ChevronLeft size={12} /> All Patients
        </Link>
        <h1 className="text-2xl font-bold text-gray-900">{d.patient_name}</h1>
        <div className="flex items-center gap-4 text-sm text-gray-500 mt-1">
          <span className="font-mono">Chart #{d.chart_number}</span>
          {d.dob && <span>DOB: {fmt.date(d.dob)}</span>}
          {d.gender && <span>{d.gender}</span>}
        </div>
        <div className="mt-1.5 text-xs text-gray-500 space-y-0.5">
          {d.address && (
            <div className="flex items-center gap-1.5">
              <span className="text-gray-400 w-14 shrink-0">Address:</span>
              <span>{d.address}</span>
            </div>
          )}
          {d.phone && (
            <div className="flex items-center gap-1.5">
              <span className="text-gray-400 w-14 shrink-0">Phone:</span>
              <span>{d.phone}</span>
            </div>
          )}
          {d.email && (
            <div className="flex items-center gap-1.5">
              <span className="text-gray-400 w-14 shrink-0">Email:</span>
              <a href={`mailto:${d.email}`} className="text-primary-600 hover:underline">{d.email}</a>
            </div>
          )}
        </div>
      </div>

      {/* Vitals Bar — always visible */}
      {latestVital && (
        <div className="bg-blue-50 border border-blue-100 rounded-lg p-3 mb-4 flex flex-wrap gap-5 text-xs">
          <span className="font-semibold text-blue-700">Latest Vitals</span>
          {latestVital.systolic && (
            <span className={latestVital.systolic > 140 ? 'text-red-600 font-semibold' : ''}>
              BP: <b>{latestVital.systolic}/{latestVital.diastolic}</b>
            </span>
          )}
          {latestVital.heart_rate && <span>HR: <b>{latestVital.heart_rate}</b></span>}
          {latestVital.weight_kg && <span>Wt: <b>{latestVital.weight_kg} kg</b></span>}
          {latestVital.height_cm && <span>Ht: <b>{latestVital.height_cm} cm</b></span>}
          {latestVital.temp_c && <span>Temp: <b>{latestVital.temp_c}°C</b></span>}
          {latestVital.spo2 && <span>SpO₂: <b>{latestVital.spo2}%</b></span>}
          {latestVital.date && (
            <span className="text-gray-400 ml-auto">
              {new Date(latestVital.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
            </span>
          )}
        </div>
      )}

      {/* Allergies — always visible */}
      {activeAllergies.length > 0 && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4">
          <div className="flex items-center gap-2 text-xs font-semibold text-red-700 mb-1">
            <AlertTriangle size={14} /> Allergies ({activeAllergies.length})
          </div>
          <div className="flex flex-wrap gap-2">
            {activeAllergies.map((a, i) => (
              <span key={i} className="px-2 py-1 bg-red-100 text-red-800 rounded text-xs">
                <b>{a.allergy}</b>
                {a.reaction && <span className="text-red-600 ml-1">— {a.reaction}</span>}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Accordion Sections */}
      <Accordion title="ID & Insurance Cards" icon={CreditCard} color="text-blue-600" badge={chart.intake_documents?.length}>
        <IntakeSection docs={chart.intake_documents} onFax={doc => setFaxDoc({ doc, docType: 'intake' })} />
      </Accordion>

      <Accordion title="PrimeSuite Documents" icon={FileText} color="text-gray-600" badge={chart.document_count}>
        <DocumentsSection
          chartNumber={chartNumber}
          onBatchFax={(docIds, clearFn) => {
            setBatchDocIds(docIds)
            setBatchClearFn(() => clearFn)
          }}
        />
      </Accordion>

      <Accordion title="Active Medications" icon={Pill} color="text-green-600" badge={activeMeds.length}>
        <HistoryTable
          items={activeMeds}
          columns={[
            { key: 'name', label: 'Medication', className: 'font-medium text-gray-800' },
            { key: 'strength', label: 'Strength', render: m => m.strength ? `${m.strength} ${m.unit || ''}`.trim() : '—' },
            { key: 'frequency', label: 'Frequency' },
            { key: 'sig', label: 'SIG', className: 'text-gray-500 max-w-[200px] truncate' },
            { key: 'start_date', label: 'Started' },
          ]}
        />
        {inactiveMeds.length > 0 && (
          <details className="mt-3">
            <summary className="text-[10px] text-gray-400 cursor-pointer hover:text-gray-600">
              {inactiveMeds.length} inactive medications
            </summary>
            <HistoryTable
              items={inactiveMeds}
              columns={[
                { key: 'name', label: 'Medication', className: 'text-gray-500' },
                { key: 'strength', label: 'Strength', render: m => m.strength ? `${m.strength} ${m.unit || ''}`.trim() : '—' },
                { key: 'start_date', label: 'Started' },
              ]}
            />
          </details>
        )}
      </Accordion>

      <Accordion title="Past Medical History" icon={Heart} color="text-red-500" badge={chart.medical_history?.length}>
        <HistoryTable
          items={chart.medical_history}
          columns={[
            { key: 'description', label: 'Condition', className: 'font-medium text-gray-800' },
            { key: 'category', label: 'Category' },
            { key: 'icd10', label: 'ICD-10', className: 'font-mono text-gray-500' },
            { key: 'date_of_onset', label: 'Onset' },
          ]}
        />
      </Accordion>

      <Accordion title="Surgical History" icon={Scissors} color="text-purple-600" badge={chart.surgical_history?.length}>
        <HistoryTable
          items={chart.surgical_history}
          columns={[
            { key: 'description', label: 'Procedure', className: 'font-medium text-gray-800' },
            { key: 'category', label: 'Category' },
            { key: 'date', label: 'Date' },
            { key: 'note', label: 'Notes', className: 'text-gray-500 max-w-[200px] truncate' },
          ]}
        />
      </Accordion>

      <Accordion title="Family History" icon={Users} color="text-blue-600" badge={chart.family_history?.length}>
        <HistoryTable
          items={chart.family_history}
          columns={[
            { key: 'description', label: 'Condition', className: 'font-medium text-gray-800' },
            { key: 'relation', label: 'Relation' },
            { key: 'age_of_onset', label: 'Age' },
            { key: 'category', label: 'Category' },
          ]}
        />
      </Accordion>

      <Accordion title="Social History" icon={Cigarette} color="text-orange-500" badge={chart.social_history?.length}>
        <HistoryTable
          items={chart.social_history}
          columns={[
            { key: 'description', label: 'Item', className: 'font-medium text-gray-800' },
            { key: 'category', label: 'Category' },
            { key: 'quantity', label: 'Quantity' },
            { key: 'note', label: 'Notes', className: 'text-gray-500' },
          ]}
        />
      </Accordion>

      <Accordion title="Problem List" icon={Activity} badge={chart.problem_list?.length}>
        <HistoryTable
          items={chart.problem_list}
          columns={[
            { key: 'description', label: 'Problem', className: 'font-medium text-gray-800' },
            { key: 'category', label: 'Category' },
            { key: 'icd10', label: 'ICD-10', className: 'font-mono text-gray-500' },
            { key: 'resolved', label: 'Resolved' },
          ]}
        />
      </Accordion>

      <Accordion title="Vitals History" icon={Activity} color="text-cyan-600" badge={chart.vitals?.length}>
        {chart.vitals?.length > 0 ? (
          <div className="overflow-x-auto mt-2">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b text-gray-500">
                  <th className="text-left py-1.5 pr-3 font-medium">Date</th>
                  <th className="text-left py-1.5 pr-3 font-medium">BP</th>
                  <th className="text-left py-1.5 pr-3 font-medium">HR</th>
                  <th className="text-left py-1.5 pr-3 font-medium">Wt (kg)</th>
                  <th className="text-left py-1.5 pr-3 font-medium">Ht (cm)</th>
                  <th className="text-left py-1.5 pr-3 font-medium">Temp</th>
                  <th className="text-left py-1.5 pr-3 font-medium">SpO₂</th>
                </tr>
              </thead>
              <tbody>
                {chart.vitals.map((v, i) => (
                  <tr key={i} className="border-b last:border-0">
                    <td className="py-1.5 pr-3 text-gray-600">
                      {v.date ? new Date(v.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—'}
                    </td>
                    <td className={`py-1.5 pr-3 font-medium ${v.systolic > 140 ? 'text-red-600' : 'text-gray-800'}`}>
                      {v.systolic ? `${v.systolic}/${v.diastolic}` : '—'}
                    </td>
                    <td className="py-1.5 pr-3">{v.heart_rate || '—'}</td>
                    <td className="py-1.5 pr-3">{v.weight_kg || '—'}</td>
                    <td className="py-1.5 pr-3">{v.height_cm || '—'}</td>
                    <td className="py-1.5 pr-3">{v.temp_c || '—'}</td>
                    <td className="py-1.5 pr-3">{v.spo2 ? `${v.spo2}%` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <EmptyState text="No vitals recorded" />}
      </Accordion>

      {/* Fax Modal */}
      {faxDoc && (
        <FaxModal
          doc={faxDoc.doc}
          docType={faxDoc.docType}
          patient={d}
          onClose={() => setFaxDoc(null)}
        />
      )}

      {batchDocIds && (
        <FaxBatchModal
          chartNumber={chartNumber}
          docIds={batchDocIds}
          defaultDestFax="2402522141"
          defaultCover={chart?.demographics ? `Patient: ${chart.demographics.patient_name || ''}\nDOB: ${chart.demographics.dob || ''}\nChart #${chartNumber}` : `Chart #${chartNumber}`}
          onClose={() => {
            setBatchDocIds(null)
            if (batchClearFn) batchClearFn()
            setBatchClearFn(null)
          }}
        />
      )}
        </>)}
        </div>
      </div>
    </div>
  )
}
