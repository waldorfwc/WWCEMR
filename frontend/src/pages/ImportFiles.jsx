import { useState, useRef, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Upload, FileText, CheckCircle, AlertCircle, Clock, Database, Link2 } from 'lucide-react'
import api, { fmt } from '../utils/api'

export default function ImportFiles() {
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const inputRef = useRef()

  // Charge Analysis (Phase 2b) state machine:
  // null                                  → drop zone
  // { uploading: true, filename }         → uploading
  // { preview: {...} }                    → preview card
  // { preview, committing: true }         → preview + spinner
  // { success: {...} }                    → success card
  // { preview?, error: {...} }            → error card
  const [chargeState, setChargeState] = useState(null)
  const chargeInputRef = useRef()

  const [bootstrapState, setBootstrapState] = useState(null)
  const bootstrapInputRef = useRef()

  const handleBootstrapFile = async (file) => {
    setBootstrapState({ uploading: true, filename: file.name })
    const form = new FormData()
    form.append('file', file)
    try {
      const res = await api.post('/imports/claim-id-bootstrap', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setBootstrapState({ preview: res.data })
    } catch (e) {
      setBootstrapState({ error: { message: e.response?.data?.detail || e.message } })
    }
  }

  const handleChargeFile = async (file) => {
    setChargeState({ uploading: true, filename: file.name })
    const form = new FormData()
    form.append('file', file)
    try {
      const res = await api.post('/imports/charge-analysis', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setChargeState({ preview: res.data })
    } catch (e) {
      setChargeState({ error: { message: e.response?.data?.detail || e.message } })
    }
  }

  const { data: eraFiles, refetch } = useQuery({
    queryKey: ['era-files'],
    queryFn: () => api.get('/imports/era-files').then(r => r.data),
  })

  const handleFile = async (file) => {
    setUploading(true)
    setResult(null)
    setError(null)
    const form = new FormData()
    form.append('file', file)
    try {
      const res = await api.post('/imports/upload', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setResult(res.data)
      refetch()
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    }
    setUploading(false)
  }

  const onDrop = (e) => {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }

  const formatIcon = (fmt) => {
    const icons = { era835: '📋', csv: '📊', xlsx: '📗', xls: '📗', pdf: '📄' }
    return icons[fmt] || '📁'
  }

  return (
    <div className="p-6 max-w-4xl">
      <h1 className="text-2xl font-bold text-gray-900 mb-2">Import Files</h1>
      <p className="text-gray-500 text-sm mb-6">
        Supported: ERA 835 (X12 EDI), CSV, XLS/XLSX, PDF · ERA files are auto-imported · Others show a preview for review
      </p>

      {/* Drop Zone */}
      <div
        className={`border-2 border-dashed rounded-xl p-10 text-center mb-6 transition-colors cursor-pointer ${
          dragging ? 'border-primary-500 bg-primary-50' : 'border-gray-300 hover:border-primary-400 hover:bg-gray-50'
        }`}
        onDragOver={e => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
      >
        <input
          ref={inputRef}
          type="file"
          className="hidden"
          accept=".835,.x12,.edi,.csv,.xls,.xlsx,.pdf"
          onChange={e => e.target.files[0] && handleFile(e.target.files[0])}
        />
        {uploading ? (
          <div className="text-primary-500">
            <div className="animate-spin text-4xl mb-3">⟳</div>
            <p className="font-medium">Processing file…</p>
            <p className="text-sm text-gray-400">Parsing and importing</p>
          </div>
        ) : (
          <>
            <Upload size={40} className="mx-auto mb-3 text-gray-400" />
            <p className="font-semibold text-gray-700">Drop file here or click to browse</p>
            <p className="text-sm text-gray-400 mt-1">ERA 835, CSV, XLS/XLSX, PDF</p>
            <p className="text-xs text-gray-400 mt-2">Files from PrimeSuite or Waystar — any format</p>
          </>
        )}
      </div>

      {/* Result */}
      {result && (
        <div className="card border border-green-200 bg-green-50 mb-6">
          <div className="flex items-center gap-2 mb-3">
            <CheckCircle size={18} className="text-green-600" />
            <span className="font-semibold text-green-800">File imported successfully!</span>
          </div>
          <dl className="text-sm grid grid-cols-2 gap-2">
            <div><dt className="text-gray-500">File:</dt><dd className="font-medium">{result.filename}</dd></div>
            <div><dt className="text-gray-500">Format:</dt><dd>{formatIcon(result.format)} {result.format.toUpperCase()}</dd></div>
            <div><dt className="text-gray-500">Detected Type:</dt><dd>{result.detected_type}</dd></div>
            <div><dt className="text-gray-500">Records:</dt><dd className="font-medium">{result.row_count?.toLocaleString()}</dd></div>
            {result.claims_imported != null && (
              <div><dt className="text-gray-500">Claims Imported:</dt><dd className="font-bold text-green-700">{result.claims_imported}</dd></div>
            )}
            {result.payer && (
              <div><dt className="text-gray-500">Payer:</dt><dd>{result.payer}</dd></div>
            )}
            {result.check_number && (
              <div><dt className="text-gray-500">Check #:</dt><dd className="font-mono">{result.check_number}</dd></div>
            )}
            {result.check_amount > 0 && (
              <div><dt className="text-gray-500">Check Amount:</dt><dd className="font-mono text-green-700">{fmt.currency(result.check_amount)}</dd></div>
            )}
          </dl>

          {/* Preview for non-ERA */}
          {result.data_preview?.length > 0 && (
            <div className="mt-3">
              <div className="text-xs text-gray-500 mb-1">Preview (first 20 rows):</div>
              <div className="overflow-x-auto">
                <table className="text-xs border-collapse">
                  <thead>
                    <tr>
                      {Object.keys(result.data_preview[0]).filter(k => k !== '__sheet__').map(k => (
                        <th key={k} className="border border-gray-200 px-2 py-1 bg-gray-100 text-left whitespace-nowrap">{k}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.data_preview.map((row, i) => (
                      <tr key={i}>
                        {Object.entries(row).filter(([k]) => k !== '__sheet__').map(([k, v]) => (
                          <td key={k} className="border border-gray-100 px-2 py-1 whitespace-nowrap text-gray-600">{String(v ?? '').substring(0, 40)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {result.text_preview && (
            <div className="mt-3">
              <div className="text-xs text-gray-500 mb-1">PDF Text Preview:</div>
              <pre className="text-xs bg-white p-3 rounded border border-green-100 max-h-40 overflow-y-auto text-gray-600 whitespace-pre-wrap">
                {result.text_preview}
              </pre>
            </div>
          )}

          <div className="mt-3 flex gap-2">
            {result.format === 'era835' && (
              <a href="/claims" className="btn-primary text-xs">View Imported Claims →</a>
            )}
            <button className="btn-secondary text-xs" onClick={() => setResult(null)}>Import Another</button>
          </div>
        </div>
      )}

      {error && (
        <div className="card border border-red-200 bg-red-50 mb-6">
          <div className="flex items-center gap-2">
            <AlertCircle size={18} className="text-red-600" />
            <span className="font-semibold text-red-700">Import failed</span>
          </div>
          <pre className="text-xs text-red-600 mt-2 whitespace-pre-wrap">{typeof error === 'string' ? error : JSON.stringify(error, null, 2)}</pre>
        </div>
      )}

      {/* Phase 2c banner */}
      <div className="card border border-amber-300 bg-amber-50 mb-6">
        <div className="flex items-center gap-2 text-amber-800 text-sm">
          <AlertCircle size={16} />
          <strong>Legacy ERA auto-posting is disabled.</strong>
          <span>Use the ERA 835 Payment Posting card below.</span>
        </div>
      </div>

      {/* Claim ID Bootstrap (Phase 2c Part 1) */}
      <div className="card mb-6">
        <div className="flex items-center gap-2 mb-1">
          <Link2 size={16} className="text-primary-600" />
          <h2 className="text-sm font-semibold text-gray-800">Link Claim IDs (PrimeSuite Claims Analysis)</h2>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          Upload the Claims Analysis <code>.xls</code> export to link each claim to its PrimeSuite Claim ID.
          Enables ERA payment posting. Secondary/tertiary claim records are created when Claims Analysis shows them.
        </p>

        {!bootstrapState && (
          <div
            className="border-2 border-dashed rounded-lg p-6 text-center cursor-pointer border-gray-300 hover:border-primary-400 hover:bg-gray-50"
            onClick={() => bootstrapInputRef.current?.click()}
            onDragOver={e => e.preventDefault()}
            onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) handleBootstrapFile(f) }}
          >
            <input ref={bootstrapInputRef} type="file" accept=".xls,.xlsx" className="hidden"
                   onChange={e => e.target.files[0] && handleBootstrapFile(e.target.files[0])} />
            <p className="text-sm text-gray-700">📄 Drop <code>.xls</code> here or click to browse</p>
          </div>
        )}

        {bootstrapState?.uploading && (
          <div className="border-2 border-dashed rounded-lg p-6 text-center border-gray-300 text-gray-500">
            <div className="animate-spin inline-block text-lg mr-2">⟳</div>
            Parsing <code>{bootstrapState.filename}</code>…
          </div>
        )}

        {bootstrapState?.error && (
          <div className="card border border-red-200 bg-red-50">
            <div className="flex items-center gap-2">
              <AlertCircle size={16} className="text-red-600" />
              <span className="font-semibold text-red-700 text-sm">Upload failed</span>
            </div>
            <pre className="text-xs text-red-600 mt-2 whitespace-pre-wrap">{typeof bootstrapState.error.message === 'string' ? bootstrapState.error.message : JSON.stringify(bootstrapState.error.message)}</pre>
            <button className="btn-secondary text-xs mt-2" onClick={() => setBootstrapState(null)}>Try another file</button>
          </div>
        )}

        {bootstrapState?.preview && !bootstrapState.success && (
          <BootstrapPreview
            preview={bootstrapState.preview}
            committing={bootstrapState.committing}
            onCancel={() => setBootstrapState(null)}
            onCommit={async () => {
              setBootstrapState(s => ({ ...s, committing: true }))
              try {
                const res = await api.post(`/imports/claim-id-bootstrap/${bootstrapState.preview.session_id}/commit`)
                setBootstrapState({ success: res.data })
              } catch (e) {
                setBootstrapState(s => ({
                  preview: s.preview,
                  error: { message: e.response?.data?.detail || e.message },
                }))
              }
            }}
          />
        )}
        {bootstrapState?.success && (
          <BootstrapSuccess result={bootstrapState.success}
                            onAgain={() => setBootstrapState(null)} />
        )}
      </div>

      {/* Charge Analysis Import (Phase 2b) */}
      <div className="card mb-6">
        <div className="flex items-center gap-2 mb-1">
          <Database size={16} className="text-primary-600" />
          <h2 className="text-sm font-semibold text-gray-800">Charge Analysis Import (PrimeSuite)</h2>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          Upload the monthly or quarterly Charge Analysis <code>.xls</code> export.
          Voided charges skipped. Existing claims (by VisitID) skipped.
        </p>

        {!chargeState && (
          <div
            className="border-2 border-dashed rounded-lg p-6 text-center cursor-pointer border-gray-300 hover:border-primary-400 hover:bg-gray-50"
            onClick={() => chargeInputRef.current?.click()}
            onDragOver={e => e.preventDefault()}
            onDrop={e => {
              e.preventDefault()
              const f = e.dataTransfer.files[0]
              if (f) handleChargeFile(f)
            }}
          >
            <input
              ref={chargeInputRef}
              type="file"
              accept=".xls,.xlsx"
              className="hidden"
              onChange={e => e.target.files[0] && handleChargeFile(e.target.files[0])}
            />
            <p className="text-sm text-gray-700">📊 Drop <code>.xls</code> here or click to browse</p>
          </div>
        )}

        {chargeState?.uploading && (
          <div className="border-2 border-dashed rounded-lg p-6 text-center border-gray-300 text-gray-500">
            <div className="animate-spin inline-block text-lg mr-2">⟳</div>
            Parsing <code>{chargeState.filename}</code>…
          </div>
        )}

        {chargeState?.error && (
          <div className="card border border-red-200 bg-red-50">
            <div className="flex items-center gap-2">
              <AlertCircle size={16} className="text-red-600" />
              <span className="font-semibold text-red-700 text-sm">Upload failed</span>
            </div>
            <pre className="text-xs text-red-600 mt-2 whitespace-pre-wrap">{chargeState.error.message}</pre>
            <button className="btn-secondary text-xs mt-2" onClick={() => setChargeState(null)}>Try another file</button>
          </div>
        )}

        {chargeState?.preview && !chargeState.success && (
          <ChargeAnalysisPreview
            preview={chargeState.preview}
            committing={chargeState.committing}
            onCancel={() => setChargeState(null)}
            onCommit={async () => {
              setChargeState(s => ({ ...s, committing: true }))
              try {
                const res = await api.post(`/imports/charge-analysis/${chargeState.preview.session_id}/commit`)
                setChargeState({ success: res.data })
              } catch (e) {
                setChargeState(s => ({
                  preview: s.preview,
                  error: { message: e.response?.data?.detail || e.message },
                }))
              }
            }}
          />
        )}

        {chargeState?.success && (
          <ChargeAnalysisSuccess result={chargeState.success} onAgain={() => setChargeState(null)} />
        )}
      </div>

      {/* ERA File History */}
      <div className="card">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">ERA File Import History</h2>
        {eraFiles?.length === 0 && (
          <p className="text-gray-400 text-sm">No ERA files imported yet.</p>
        )}
        <div className="space-y-2">
          {eraFiles?.map(f => (
            <div key={f.id} className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg text-sm">
              <FileText size={16} className="text-gray-400 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="font-medium text-gray-800 truncate">{f.filename}</div>
                <div className="text-xs text-gray-500">{f.payer_name} · Check #{f.check_number} · {fmt.date(f.check_date)}</div>
              </div>
              <div className="text-right shrink-0">
                <div className="font-mono text-green-700">{fmt.currency(f.check_amount)}</div>
                <div className="text-xs text-gray-400">{f.transaction_count} claims</div>
              </div>
              <div>
                {f.status === 'processed' ? (
                  <CheckCircle size={16} className="text-green-500" />
                ) : (
                  <AlertCircle size={16} className="text-yellow-500" />
                )}
              </div>
              <div className="text-xs text-gray-400 shrink-0">{fmt.dateTime(f.imported_at)}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function ChargeAnalysisPreview({ preview, committing, onCancel, onCommit }) {
  const [showIssues, setShowIssues] = useState(false)
  const [remaining, setRemaining] = useState(() => secondsUntil(preview.expires_at))

  useEffect(() => {
    const id = setInterval(() => setRemaining(secondsUntil(preview.expires_at)), 1000)
    return () => clearInterval(id)
  }, [preview.expires_at])

  const expired = remaining <= 0

  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white">
      <div className="flex items-center justify-between mb-2">
        <div className="text-sm font-semibold text-gray-800">Preview · {preview.source_filename}</div>
        <div className="text-xs text-gray-500 font-mono">
          {expired ? 'Session expired' : `Expires in ${formatRemaining(remaining)}`}
        </div>
      </div>
      <div className="text-xs text-gray-500 mb-3">
        {preview.parsed_claims} claims parsed · {preview.total_rows} rows
      </div>

      <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Claims</div>
      <div className="text-sm space-y-0.5 mb-3">
        <div><span className="text-green-600 mr-1">✓</span>{preview.will_create} new claims will be created</div>
        <div><span className="text-gray-400 mr-1">⊘</span>{preview.will_skip_existing} existing (by VisitID) skipped</div>
        <div><span className="text-gray-400 mr-1">⊘</span>{preview.skipped_voids} voided rows skipped</div>
        <div><span className="text-gray-400 mr-1">⊘</span>{preview.skipped_non_clinical} finance-charge rows skipped</div>
      </div>

      <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Patients</div>
      <div className="text-sm space-y-0.5 mb-3">
        <div><span className="text-green-600 mr-1">✓</span>{preview.will_match_patients} matched to existing charts</div>
        <div><span className="text-primary-600 mr-1">+</span>{preview.will_create_patients} new patients will be created</div>
      </div>

      <div className="text-xs text-gray-600 mb-2">
        <strong>{preview.errors} errors · {preview.warnings} warnings</strong>
        {(preview.errors + preview.warnings) > 0 && (
          <button className="ml-2 text-primary-600 underline" onClick={() => setShowIssues(v => !v)}>
            {showIssues ? 'Hide details ▴' : 'Show details ▾'}
          </button>
        )}
      </div>

      {showIssues && (
        <div className="max-h-40 overflow-y-auto border border-gray-100 rounded p-2 bg-gray-50 text-xs mb-3">
          {preview.issues.map((i, idx) => (
            <div key={idx} className="py-0.5">
              <span className={i.severity === 'error' ? 'text-red-600 font-semibold' : 'text-amber-600 font-semibold'}>
                {i.severity.toUpperCase()}
              </span>
              {' · row '}{i.row_index}
              {i.visit_id && <> · VisitID <code>{i.visit_id}</code></>}
              {' · '}{i.message}
            </div>
          ))}
        </div>
      )}

      <div className="flex justify-end gap-2 mt-2">
        <button className="btn-secondary text-xs" disabled={committing} onClick={onCancel}>Cancel</button>
        <button
          className="btn-primary text-xs"
          disabled={committing || expired}
          onClick={onCommit}
        >
          {committing ? 'Committing…' : expired ? 'Session expired' : 'Commit import'}
        </button>
      </div>
    </div>
  )
}

function ChargeAnalysisSuccess({ result, onAgain }) {
  const hasErrors = result.errors.length > 0

  const stats = (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm mb-3">
      <div><dt className="text-gray-500 inline">Claims created:</dt>{' '}<dd className="inline font-semibold">{result.claims_created}</dd></div>
      <div><dt className="text-gray-500 inline">Service lines:</dt>{' '}<dd className="inline font-semibold">{result.service_lines_created}</dd></div>
      <div><dt className="text-gray-500 inline">Patients created:</dt>{' '}<dd className="inline font-semibold">{result.patients_created}</dd></div>
      <div><dt className="text-gray-500 inline">Patients matched:</dt>{' '}<dd className="inline font-semibold">{result.patients_matched}</dd></div>
      <div className="col-span-2"><dt className="text-gray-500 inline">Skipped (existing VisitID):</dt>{' '}<dd className="inline font-semibold">{result.claims_skipped_existing}</dd></div>
    </dl>
  )

  if (hasErrors) {
    return (
      <div className="card border border-amber-200 bg-amber-50">
        <div className="flex items-center gap-2 mb-2">
          <AlertCircle size={18} className="text-amber-600" />
          <span className="font-semibold text-amber-800 text-sm">
            Import completed with {result.errors.length} error{result.errors.length === 1 ? '' : 's'}
          </span>
        </div>
        <div className="text-xs text-gray-600 mb-2">
          Source: <code>{result.source_filename}</code>
        </div>
        {stats}
        <div className="text-[11px] uppercase tracking-wide text-gray-500 mb-1">Failed claims</div>
        <div className="max-h-40 overflow-y-auto border border-amber-100 rounded p-2 bg-white text-xs mb-3">
          {result.errors.map((err, idx) => (
            <div key={idx} className="py-0.5">
              {err.visit_id && <>VisitID <code>{err.visit_id}</code> · </>}
              <span className="text-red-600">{err.message}</span>
            </div>
          ))}
        </div>
        <div className="flex justify-end gap-2">
          <button className="btn-secondary text-xs" onClick={onAgain}>Dismiss</button>
        </div>
      </div>
    )
  }

  return (
    <div className="card border border-green-200 bg-green-50">
      <div className="flex items-center gap-2 mb-2">
        <CheckCircle size={18} className="text-green-600" />
        <span className="font-semibold text-green-800 text-sm">Import complete</span>
      </div>
      <div className="text-xs text-gray-600 mb-2">
        Source: <code>{result.source_filename}</code>
      </div>
      {stats}
      <div className="flex justify-end gap-2">
        <a href="/claims" className="btn-primary text-xs">View claims →</a>
        <button className="btn-secondary text-xs" onClick={onAgain}>Import another file</button>
      </div>
    </div>
  )
}

function BootstrapPreview({ preview, committing, onCancel, onCommit }) {
  const [showIssues, setShowIssues] = useState(false)
  const [remaining, setRemaining] = useState(() => secondsUntil(preview.expires_at))
  useEffect(() => {
    const id = setInterval(() => setRemaining(secondsUntil(preview.expires_at)), 1000)
    return () => clearInterval(id)
  }, [preview.expires_at])
  const expired = remaining <= 0

  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white">
      <div className="flex items-center justify-between mb-2">
        <div className="text-sm font-semibold text-gray-800">Preview · {preview.source_filename}</div>
        <div className="text-xs text-gray-500 font-mono">
          {expired ? 'Session expired' : `Expires in ${formatRemaining(remaining)}`}
        </div>
      </div>
      <div className="text-xs text-gray-500 mb-3">
        {preview.unique_claims} unique claims · {preview.total_rows} rows
      </div>

      <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Claim IDs</div>
      <div className="text-sm space-y-0.5 mb-3">
        <div><span className="text-green-600 mr-1">✓</span>{preview.will_patch} will be linked</div>
        <div><span className="text-primary-600 mr-1">+</span>{preview.will_create_secondary} secondary claims will be created</div>
        <div><span className="text-gray-400 mr-1">⊘</span>{preview.already_set} already linked</div>
        <div><span className="text-gray-400 mr-1">⊘</span>{preview.no_patient + preview.no_claim} not found in system</div>
        <div><span className="text-amber-600 mr-1">⚠</span>{preview.ambiguous} ambiguous</div>
        <div><span className="text-amber-600 mr-1">⚠</span>{preview.conflicts} conflicts</div>
      </div>

      {preview.issues && preview.issues.length > 0 && (
        <div className="text-xs text-gray-600 mb-2">
          <strong>{preview.issues.length} issues</strong>
          <button className="ml-2 text-primary-600 underline" onClick={() => setShowIssues(v => !v)}>
            {showIssues ? 'Hide ▴' : 'Show ▾'}
          </button>
        </div>
      )}
      {showIssues && (
        <div className="max-h-40 overflow-y-auto border border-gray-100 rounded p-2 bg-gray-50 text-xs mb-3">
          {preview.issues.map((i, idx) => (
            <div key={idx} className="py-0.5">
              <span className={i.severity === 'error' ? 'text-red-600 font-semibold' : 'text-amber-600 font-semibold'}>
                {i.severity.toUpperCase()}
              </span>
              {i.claim_id && <> · Claim <code>{i.claim_id}</code></>}
              {' · '}{i.message}
            </div>
          ))}
        </div>
      )}

      <div className="flex justify-end gap-2 mt-2">
        <button className="btn-secondary text-xs" disabled={committing} onClick={onCancel}>Cancel</button>
        <button className="btn-primary text-xs" disabled={committing || expired} onClick={onCommit}>
          {committing ? 'Committing…' : expired ? 'Session expired' : 'Commit'}
        </button>
      </div>
    </div>
  )
}

function BootstrapSuccess({ result, onAgain }) {
  return (
    <div className="card border border-green-200 bg-green-50">
      <div className="flex items-center gap-2 mb-2">
        <CheckCircle size={16} className="text-green-700" />
        <span className="font-semibold text-green-800 text-sm">Claim IDs linked</span>
      </div>
      <div className="text-xs text-green-900 mb-3">{result.source_filename}</div>
      <div className="grid grid-cols-2 gap-1 text-xs mb-3">
        <div>Claims patched: <span className="font-mono font-semibold">{result.claims_patched}</span></div>
        <div>Secondary created: <span className="font-mono font-semibold">{result.secondary_claims_created}</span></div>
        <div>Already set: <span className="font-mono">{result.already_set}</span></div>
        <div>Unmatched: <span className="font-mono">{result.unmatched}</span></div>
        <div>Ambiguous: <span className="font-mono">{result.ambiguous}</span></div>
        <div>Conflicts: <span className="font-mono">{result.conflicts}</span></div>
      </div>
      <div className="flex gap-2">
        <a href="/claims" className="btn-primary text-xs">View claims →</a>
        <button className="btn-secondary text-xs" onClick={onAgain}>Upload another file</button>
      </div>
    </div>
  )
}

function secondsUntil(isoString) {
  const diffMs = new Date(isoString).getTime() - Date.now()
  return Math.max(0, Math.floor(diffMs / 1000))
}
function formatRemaining(seconds) {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${String(s).padStart(2, '0')}`
}
