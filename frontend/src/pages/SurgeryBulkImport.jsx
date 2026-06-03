import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import {
  ArrowLeft, Upload, FileSpreadsheet, CheckCircle2, AlertTriangle, X,
} from 'lucide-react'
import api from '../utils/api'


export default function SurgeryBulkImport() {
  const [file, setFile] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const upload = useMutation({
    mutationFn: ({ file, dryRun }) => {
      const form = new FormData()
      form.append('file', file)
      return api.post(`/surgery/candidates/bulk-import?dry_run=${dryRun}`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      }).then(r => r.data)
    },
    onSuccess: (d) => { setResult(d); setError(null) },
    onError: (e) => {
      setError(e?.response?.data?.detail || e?.message || 'Upload failed')
      setResult(null)
    },
  })

  function onPickFile(f) {
    setFile(f); setResult(null); setError(null)
  }

  function clearFile() {
    setFile(null); setResult(null); setError(null)
  }

  function preview() {
    if (file) upload.mutate({ file, dryRun: true })
  }
  function commit() {
    if (file) upload.mutate({ file, dryRun: false })
  }

  return (
    <div>
      <Link to="/surgery" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
        <ArrowLeft size={12} /> Surgery dashboard
      </Link>
      <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2 mb-1">
        <FileSpreadsheet size={22} className="text-plum-700" />
        Bulk import surgery candidates
      </h1>
      <p className="text-sm text-gray-600 mb-4 max-w-2xl">
        Upload a ModMed-style patient roster Excel (.xlsx). Each row creates
        a Surgery in <strong>incomplete</strong> status — coordinators can then
        triage who needs benefits checks, prior-auth outreach, etc. Existing
        active surgeries for the same chart number are skipped automatically.
      </p>

      <div className="card mb-4">
        <h2 className="text-sm font-semibold text-gray-800 mb-2">Expected columns</h2>
        <div className="text-[11px] text-gray-600 grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-1">
          <span>Patient MRN <span className="text-red-600">*</span></span>
          <span>Patient First Name</span>
          <span>Patient Last Name</span>
          <span>Patient DOB</span>
          <span>Patient Mobile Phone</span>
          <span>Patient Email Address</span>
          <span>Patient Address Line 1 / 2</span>
          <span>Patient City / State / Zip Code</span>
          <span>Payer · Payer Plan Name · Payer Policy Number</span>
          <span>Secondary Payer</span>
          <span>Tertiary Payer</span>
          <span>Primary Care Provider</span>
        </div>
        <div className="text-[10px] text-gray-500 mt-2 italic">
          Column order doesn't matter; case + spacing are normalized. Cells
          containing "None" / "-" are treated as empty.
        </div>
      </div>

      <div className="card mb-4">
        <h2 className="text-sm font-semibold text-gray-800 mb-3">1 · Choose file</h2>
        {!file ? (
          <label className="block border-2 border-dashed border-plum-200 rounded-lg p-8
                              text-center cursor-pointer hover:bg-plum-50/50 transition">
            <Upload size={20} className="text-plum-700 mx-auto mb-2" />
            <div className="text-sm text-gray-700 font-medium">
              Click to select an .xlsx file
            </div>
            <div className="text-[11px] text-gray-500 mt-1">
              Drag-and-drop also works
            </div>
            <input type="file" accept=".xlsx" className="hidden"
                   onChange={e => onPickFile(e.target.files?.[0] || null)} />
          </label>
        ) : (
          <div className="flex items-center justify-between gap-2 bg-plum-50/50
                            border border-plum-200 rounded-lg p-3">
            <div className="flex items-center gap-2 min-w-0">
              <FileSpreadsheet size={16} className="text-plum-700 shrink-0" />
              <div className="min-w-0">
                <div className="text-sm font-medium text-gray-900 truncate">{file.name}</div>
                <div className="text-[10px] text-gray-500">
                  {(file.size / 1024).toFixed(1)} KB
                </div>
              </div>
            </div>
            <button onClick={clearFile}
                    className="text-gray-500 hover:text-red-700 p-1">
              <X size={14} />
            </button>
          </div>
        )}
      </div>

      {file && (
        <div className="card mb-4">
          <h2 className="text-sm font-semibold text-gray-800 mb-3">2 · Preview</h2>
          <div className="flex gap-2 flex-wrap">
            <button onClick={preview}
                    disabled={upload.isPending}
                    className="btn-secondary text-sm">
              {upload.isPending && upload.variables?.dryRun
                ? 'Parsing…' : 'Preview (dry-run)'}
            </button>
            <button onClick={commit}
                    disabled={upload.isPending || !result}
                    className="btn-primary text-sm">
              {upload.isPending && upload.variables?.dryRun === false
                ? 'Importing…' : 'Import all rows'}
            </button>
            {result?.dry_run === false && result?.created > 0 && (
              <Link to="/surgery" className="btn-secondary text-sm">
                Go to dashboard →
              </Link>
            )}
          </div>
          <div className="text-[10px] text-gray-500 mt-2 italic">
            Run preview first to see how many rows will be created / skipped /
            errored. Then click Import to commit.
          </div>
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-800 mb-4">
          ⚠ {error}
        </div>
      )}

      {result && <ResultPanel result={result} />}
    </div>
  )
}


function ResultPanel({ result }) {
  const isDry = result.dry_run
  const banner = isDry
    ? <div className="text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 text-sm">
        Dry-run · no changes committed yet
      </div>
    : <div className="text-green-800 bg-green-50 border border-green-200 rounded-lg px-3 py-2 text-sm flex items-center gap-1">
        <CheckCircle2 size={14} /> Imported · {result.created} surgeries created
      </div>

  return (
    <div className="space-y-3">
      {banner}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label={isDry ? 'Would create' : 'Created'} value={result.created} tone="green" />
        <Stat label="Skipped (dup)"   value={result.skipped} tone="gray" />
        <Stat label="Errored"         value={result.errors}  tone={result.errors > 0 ? 'red' : 'gray'} />
        <Stat label="Total rows"      value={result.total}   tone="gray" />
      </div>

      {result.created_rows?.length > 0 && (
        <RowTable title={isDry ? 'Will create' : 'Created'} rows={result.created_rows} kind="created" />
      )}
      {result.skipped_rows?.length > 0 && (
        <RowTable title="Skipped" rows={result.skipped_rows} kind="skipped" />
      )}
      {result.error_rows?.length > 0 && (
        <RowTable title="Errored" rows={result.error_rows} kind="errors" />
      )}
    </div>
  )
}


function Stat({ label, value, tone }) {
  const tones = {
    green: 'text-green-700',
    red:   'text-red-700',
    gray:  'text-gray-700',
  }
  return (
    <div className="card !p-3">
      <div className="text-[10px] uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`text-2xl font-semibold ${tones[tone] || tones.gray}`}>{value}</div>
    </div>
  )
}


function RowTable({ title, rows, kind }) {
  return (
    <div className="card !p-0 overflow-hidden">
      <div className="px-4 py-2 bg-plum-50/40 border-b border-plum-100 flex items-center gap-2">
        {kind === 'errors' && <AlertTriangle size={13} className="text-red-700" />}
        <h3 className="text-sm font-semibold text-gray-800">{title}</h3>
        <span className="text-[11px] text-gray-500">({rows.length})</span>
      </div>
      <div className="max-h-96 overflow-y-auto">
        <table className="w-full text-[12px]">
          <thead className="bg-gray-50 sticky top-0">
            <tr className="text-left text-[10px] uppercase text-gray-500">
              <th className="px-3 py-1.5">Chart</th>
              <th className="px-3 py-1.5">Patient</th>
              {kind !== 'errors' && <th className="px-3 py-1.5">DOB</th>}
              {kind === 'created' && <th className="px-3 py-1.5">Insurance</th>}
              {kind === 'skipped' && <th className="px-3 py-1.5">Reason</th>}
              {kind === 'errors'  && <th className="px-3 py-1.5">Error</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((r, i) => (
              <tr key={i}>
                <td className="px-3 py-1.5 font-mono">{r.chart_number}</td>
                <td className="px-3 py-1.5">{r.patient_name || '—'}</td>
                {kind !== 'errors' && (
                  <td className="px-3 py-1.5 text-gray-600">{r.dob || '—'}</td>
                )}
                {kind === 'created' && (
                  <td className="px-3 py-1.5 text-gray-700 truncate max-w-xs">{r.primary_insurance || '—'}</td>
                )}
                {kind === 'skipped' && (
                  <td className="px-3 py-1.5 text-amber-700">{r.reason}</td>
                )}
                {kind === 'errors' && (
                  <td className="px-3 py-1.5 text-red-700">{r.error}</td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
