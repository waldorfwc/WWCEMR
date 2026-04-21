import { useState, useRef, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { FileText, CheckCircle, AlertCircle, Database, Link2 } from 'lucide-react'
import api, { fmt } from '../utils/api'

export default function ImportFiles() {
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

  const [eraState, setEraState] = useState(null)
  const eraInputRef = useRef()

  const handleEraFiles = async (fileList) => {
    const files = Array.from(fileList).filter(Boolean)
    if (!files.length) return
    setEraState({ uploading: true, filenames: files.map(f => f.name) })
    const form = new FormData()
    for (const f of files) form.append('file', f)
    try {
      const res = await api.post('/imports/era-posting', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setEraState({ preview: res.data })
    } catch (e) {
      setEraState({ error: { message: e.response?.data?.detail || e.message } })
    }
  }

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

  const { data: eraFiles } = useQuery({
    queryKey: ['era-files'],
    queryFn: () => api.get('/imports/era-files').then(r => r.data),
  })

  return (
    <div className="p-6 max-w-4xl">
      <h1 className="text-2xl font-bold text-gray-900 mb-2">Import</h1>
      <p className="text-gray-500 text-sm mb-6">
        Three upload flows below: <strong>Charge Analysis</strong> creates claims from PrimeSuite,
        <strong>Claims Analysis</strong> links Claim IDs + workflow fields, <strong>ERA 835</strong>
        posts payments. Uploaded file history is at the bottom.
      </p>

      {/* Claim ID Bootstrap (Phase 2c Part 1) */}
      <div className="card mb-6">
        <div className="flex items-center gap-2 mb-1">
          <Link2 size={16} className="text-primary-600" />
          <h2 className="text-sm font-semibold text-gray-800">Claims Analysis Import</h2>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          Upload the Claims Analysis <code>.xls</code> export to link PrimeSuite Claim IDs, set claim status,
          follow-up dates, and filing info. Secondary/tertiary claim records are created when Claims Analysis
          shows them. Re-upload any time — Claims Analysis always wins.
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

      {/* ERA 835 Payment Posting (Phase 2c Part 2) */}
      <div className="card mb-6">
        <div className="flex items-center gap-2 mb-1">
          <FileText size={16} className="text-primary-600" />
          <h2 className="text-sm font-semibold text-gray-800">ERA 835 Payment Posting</h2>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          Upload one or more ERA <code>.835</code> files to post payments to existing claims.
          Strict match on linked Claim ID. Reversals and unmatched claims are flagged.
        </p>

        {!eraState && (
          <div
            className="border-2 border-dashed rounded-lg p-6 text-center cursor-pointer border-gray-300 hover:border-primary-400 hover:bg-gray-50"
            onClick={() => eraInputRef.current?.click()}
            onDragOver={e => e.preventDefault()}
            onDrop={e => { e.preventDefault(); handleEraFiles(e.dataTransfer.files) }}
          >
            <input ref={eraInputRef} type="file" accept=".835,.x12,.edi" multiple className="hidden"
                   onChange={e => handleEraFiles(e.target.files)} />
            <p className="text-sm text-gray-700">📋 Drop one or more <code>.835</code> files here or click to browse</p>
          </div>
        )}

        {eraState?.uploading && (
          <div className="border-2 border-dashed rounded-lg p-6 text-center border-gray-300 text-gray-500">
            <div className="animate-spin inline-block text-lg mr-2">⟳</div>
            Parsing {eraState.filenames.length} file{eraState.filenames.length > 1 ? 's' : ''}…
          </div>
        )}

        {eraState?.error && (
          <div className="card border border-red-200 bg-red-50">
            <div className="flex items-center gap-2">
              <AlertCircle size={16} className="text-red-600" />
              <span className="font-semibold text-red-700 text-sm">Upload failed</span>
            </div>
            <pre className="text-xs text-red-600 mt-2 whitespace-pre-wrap">{typeof eraState.error.message === 'string' ? eraState.error.message : JSON.stringify(eraState.error.message)}</pre>
            <button className="btn-secondary text-xs mt-2" onClick={() => setEraState(null)}>Try again</button>
          </div>
        )}

        {eraState?.preview && !eraState.success && (
          <EraPreview
            preview={eraState.preview}
            committing={eraState.committing}
            onCancel={() => setEraState(null)}
            onCommit={async () => {
              setEraState(s => ({ ...s, committing: true }))
              try {
                const res = await api.post(`/imports/era-posting/${eraState.preview.session_id}/commit`)
                setEraState({ success: res.data })
              } catch (e) {
                setEraState(s => ({
                  preview: s.preview,
                  error: { message: e.response?.data?.detail || e.message },
                }))
              }
            }}
          />
        )}
        {eraState?.success && (
          <EraSuccess result={eraState.success} onAgain={() => setEraState(null)} />
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

function EraPreview({ preview, committing, onCancel, onCommit }) {
  const [showIssues, setShowIssues] = useState(false)
  const [remaining, setRemaining] = useState(() => secondsUntil(preview.expires_at))
  useEffect(() => {
    const id = setInterval(() => setRemaining(secondsUntil(preview.expires_at)), 1000)
    return () => clearInterval(id)
  }, [preview.expires_at])
  const expired = remaining <= 0
  const t = preview.totals

  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white">
      <div className="flex items-center justify-between mb-2">
        <div className="text-sm font-semibold text-gray-800">
          Preview · {t.n_files} ERA file{t.n_files > 1 ? 's' : ''}
        </div>
        <div className="text-xs text-gray-500 font-mono">
          {expired ? 'Session expired' : `Expires in ${formatRemaining(remaining)}`}
        </div>
      </div>
      <div className="text-xs text-gray-500 mb-3">
        Combined check total: ${t.combined_check_amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </div>

      <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Totals</div>
      <div className="text-sm space-y-0.5 mb-3">
        <div><span className="text-green-600 mr-1">✓</span>{t.n_matched} will be posted</div>
        <div><span className="text-gray-400 mr-1">⊘</span>{t.n_already_posted} already posted (skipped)</div>
        <div><span className="text-amber-600 mr-1">⚠</span>{t.n_unmatched} unmatched (no linked Claim ID)</div>
        <div><span className="text-amber-600 mr-1">⚠</span>{t.n_reversals} reversals flagged</div>
        <div><span className="text-gray-400 mr-1">⊘</span>{t.n_cb_skipped} CB-prefix ModMed claims</div>
        <div><span className="text-gray-400 mr-1">⊘</span>{t.n_malformed} malformed CLP01</div>
      </div>

      <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-1">Per file</div>
      <div className="text-xs space-y-1 mb-3">
        {preview.files.map((f, idx) => (
          <div key={idx} className="border border-gray-100 rounded p-2">
            <div className="font-mono truncate">{f.source_filename}</div>
            <div className="text-gray-500">
              Check #{f.check_number} · ${f.check_amount.toLocaleString(undefined, { minimumFractionDigits: 2 })} ·
              {' '}{f.n_matched} matched / {f.n_unmatched} unmatched / {f.n_reversals} reversals
            </div>
          </div>
        ))}
      </div>

      {preview.issues && preview.issues.length > 0 && (
        <div className="text-xs text-gray-600 mb-2">
          <strong>{preview.issues.length} flagged</strong>
          <button className="ml-2 text-primary-600 underline" onClick={() => setShowIssues(v => !v)}>
            {showIssues ? 'Hide ▴' : 'Show ▾'}
          </button>
        </div>
      )}
      {showIssues && (
        <div className="max-h-40 overflow-y-auto border border-gray-100 rounded p-2 bg-gray-50 text-xs mb-3">
          {preview.issues.map((i, idx) => (
            <div key={idx} className="py-0.5">
              <span className="text-amber-700 font-semibold">{i.status.toUpperCase()}</span>
              {' · '}<code>{i.internal_claim_id || '—'}</code>
              {i.billed_amount > 0 && <> · billed ${i.billed_amount.toFixed(2)}</>}
              {i.reason && <> · {i.reason}</>}
              <span className="text-gray-400"> ({i.source_filename})</span>
            </div>
          ))}
        </div>
      )}

      <div className="flex justify-end gap-2 mt-2">
        <button className="btn-secondary text-xs" disabled={committing} onClick={onCancel}>Cancel</button>
        <button className="btn-primary text-xs" disabled={committing || expired || t.n_matched === 0} onClick={onCommit}>
          {committing ? 'Committing…' : expired ? 'Session expired' : 'Post payments'}
        </button>
      </div>
    </div>
  )
}

function EraSuccess({ result, onAgain }) {
  return (
    <div className="card border border-green-200 bg-green-50">
      <div className="flex items-center gap-2 mb-2">
        <CheckCircle size={16} className="text-green-700" />
        <span className="font-semibold text-green-800 text-sm">Payments posted</span>
      </div>
      <div className="grid grid-cols-2 gap-1 text-xs mb-3">
        <div>Files processed: <span className="font-mono">{result.files_processed}</span></div>
        <div>Claims posted: <span className="font-mono font-semibold">{result.claims_posted}</span></div>
        <div>Payments created: <span className="font-mono">{result.payments_created}</span></div>
        <div>Denials created: <span className="font-mono">{result.denials_created}</span></div>
        <div>Unmatched: <span className="font-mono">{result.claims_unmatched}</span></div>
        <div>Reversals flagged: <span className="font-mono">{result.claims_reversal_flagged}</span></div>
      </div>
      {result.errors && result.errors.length > 0 && (
        <div className="text-xs text-red-700 border-t border-green-200 pt-2 mt-2">
          {result.errors.length} errors: {result.errors.map(e => e.internal_claim_id).join(', ')}
        </div>
      )}
      <div className="flex gap-2 mt-2">
        <a href="/claims" className="btn-primary text-xs">View claims →</a>
        <button className="btn-secondary text-xs" onClick={onAgain}>Upload more ERAs</button>
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
