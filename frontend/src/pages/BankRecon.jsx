import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Upload, Download, Trash2, ChevronDown, ChevronRight, Building2,
  FileText, AlertTriangle, CheckCircle2, X,
} from 'lucide-react'
import api, { fmt } from '../utils/api'


// ─────────────────────────────────────────────────────────────────────
// Page

export default function BankRecon() {
  const qc = useQueryClient()
  const [bankName, setBankName] = useState(() => localStorage.getItem('bai2_bank') || 'PNC x395')
  const [skipWithdrawals, setSkipWithdrawals] = useState(true)
  const [skipModmed, setSkipModmed] = useState(true)
  const [skipStripe, setSkipStripe] = useState(true)
  const [skipZero, setSkipZero] = useState(true)

  const [preview, setPreview] = useState(null)             // result of /preview
  const [excludedKeys, setExcludedKeys] = useState(new Set())
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState(null)

  const fileRef = useRef(null)

  useEffect(() => { localStorage.setItem('bai2_bank', bankName) }, [bankName])

  const { data: history } = useQuery({
    queryKey: ['bai2-imports'],
    queryFn: () => api.get('/bank-recon/imports').then(r => r.data),
  })

  async function handlePreview(e) {
    const f = e.target.files?.[0]
    if (!f) return
    setError(null)
    const fd = new FormData()
    fd.append('file', f)
    fd.append('bank_name', bankName)
    fd.append('skip_withdrawals', skipWithdrawals)
    fd.append('skip_modmed', skipModmed)
    fd.append('skip_stripe', skipStripe)
    fd.append('skip_zero', skipZero)
    try {
      const res = await api.post('/bank-recon/preview', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setPreview(res.data)
      // Default-exclude rows that are already-imported
      const auto = new Set(res.data.transactions.filter(t => t.already_imported).map(t => t.dedup_key))
      setExcludedKeys(auto)
    } catch (err) {
      setError(err?.response?.data?.detail || err.message)
    } finally {
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  function toggleExclude(key) {
    setExcludedKeys(prev => {
      const n = new Set(prev)
      if (n.has(key)) n.delete(key); else n.add(key)
      return n
    })
  }
  function selectAll() { setExcludedKeys(new Set()) }
  function selectNone() {
    if (!preview) return
    setExcludedKeys(new Set(preview.transactions.map(t => t.dedup_key)))
  }
  function selectOnlyNew() {
    if (!preview) return
    const auto = new Set(preview.transactions.filter(t => t.already_imported).map(t => t.dedup_key))
    setExcludedKeys(auto)
  }

  async function generate() {
    if (!preview) return
    setGenerating(true); setError(null)
    try {
      const res = await api.post('/bank-recon/generate', {
        preview_id: preview.preview_id,
        csv_filename: preview.csv_filename,
        ext: preview.ext,
        bank_name: bankName,
        excluded_keys: Array.from(excludedKeys),
        skip_withdrawals: skipWithdrawals,
        skip_modmed: skipModmed,
        skip_stripe: skipStripe,
        skip_zero: skipZero,
      })
      qc.invalidateQueries({ queryKey: ['bai2-imports'] })
      setPreview(null); setExcludedKeys(new Set())
      // Auto-download
      if (res.data.downloadable && res.data.id) {
        window.location.href = `/api/bank-recon/imports/${res.data.id}/download`
      }
    } catch (err) {
      setError(err?.response?.data?.detail || err.message)
    } finally {
      setGenerating(false)
    }
  }

  function cancelPreview() { setPreview(null); setExcludedKeys(new Set()); setError(null) }

  // Compute live totals
  const includedTxns = (preview?.transactions || []).filter(t => !excludedKeys.has(t.dedup_key))
  const includedTotal = includedTxns.reduce((s, t) => s + t.amount, 0)

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Bank Reconciliation</h1>
          <p className="text-sm text-gray-500 mt-0.5">Upload bank CSV → review → generate BAI2 file for ModMed import</p>
        </div>
      </div>

      {/* Upload + filter card — only when no preview active */}
      {!preview && (
        <div className="card">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
            <Labeled label="Bank / Account Label (used as the BAI2 filename prefix)">
              <input className="input text-sm" value={bankName} onChange={e => setBankName(e.target.value)} placeholder="PNC x395" />
            </Labeled>
            <div className="flex flex-wrap gap-3 items-center text-xs text-gray-700">
              <ToggleCheckbox label="Skip withdrawals" checked={skipWithdrawals} onChange={setSkipWithdrawals} />
              <ToggleCheckbox label="Skip ModMed" checked={skipModmed} onChange={setSkipModmed} />
              <ToggleCheckbox label="Skip Stripe" checked={skipStripe} onChange={setSkipStripe} />
              <ToggleCheckbox label="Skip zero-amount" checked={skipZero} onChange={setSkipZero} />
            </div>
          </div>
          <div className="text-[10px] text-gray-400 italic mb-2">
            MERCHANT BNKCD is always dropped (not a payer). Filename example: <span className="font-mono">{bankName} 26.05.01 - 26.05.05.bai</span>
          </div>
          <div className="flex items-center gap-3">
            <input
              ref={fileRef} type="file" accept=".csv,.txt"
              onChange={handlePreview} className="hidden"
            />
            <button
              className="btn-primary flex items-center gap-1"
              onClick={() => fileRef.current?.click()}
              disabled={!bankName}
            >
              <Upload size={14} /> Upload Bank CSV
            </button>
            {!bankName && <span className="text-xs text-amber-600">Set the bank label first</span>}
            {error && <span className="text-xs text-red-600 ml-2">{error}</span>}
          </div>
        </div>
      )}

      {/* PREVIEW: review screen with checkbox per row */}
      {preview && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h2 className="text-sm font-semibold text-gray-700">Review Transactions Before Generating BAI2</h2>
              <p className="text-xs text-gray-500 mt-0.5">
                Source: <strong>{preview.csv_filename}</strong> · {preview.csv_row_count} rows · uncheck any rows you don't want in the BAI2
              </p>
            </div>
            <button className="text-gray-400 hover:text-gray-600" onClick={cancelPreview}>
              <X size={16} />
            </button>
          </div>

          {/* Filter stats */}
          <div className="flex flex-wrap gap-3 mb-3 text-[11px] text-gray-600">
            <Stat label="To review" val={preview.stats.transactions_to_review} />
            <Stat label="Already imported" val={preview.stats.already_imported_count} cls="text-amber-700" />
            <Stat label="Withdrawals" val={preview.stats.skipped_withdrawal} cls="text-gray-500" />
            <Stat label="ModMed" val={preview.stats.skipped_modmed} cls="text-gray-500" />
            <Stat label="Stripe" val={preview.stats.skipped_stripe} cls="text-gray-500" />
            <Stat label="Always dropped" val={preview.stats.skipped_always_drop} cls="text-gray-500" />
            <Stat label="Dups in file" val={preview.stats.skipped_duplicate_in_file} cls="text-gray-500" />
          </div>

          <div className="flex gap-2 mb-3">
            <button className="text-xs text-primary-500 hover:underline" onClick={selectAll}>Select all</button>
            <span className="text-gray-300">·</span>
            <button className="text-xs text-primary-500 hover:underline" onClick={selectNone}>Select none</button>
            <span className="text-gray-300">·</span>
            <button className="text-xs text-primary-500 hover:underline" onClick={selectOnlyNew}>Select only new (default)</button>
          </div>

          <div className="overflow-x-auto max-h-[60vh] overflow-y-auto border border-gray-100 rounded">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 sticky top-0">
                <tr>
                  <th className="px-2 py-1.5 w-8"></th>
                  <th className="px-2 py-1.5 text-left text-[10px] uppercase text-gray-500">Date</th>
                  <th className="px-2 py-1.5 text-left text-[10px] uppercase text-gray-500">Reformatted (BAI2 text)</th>
                  <th className="px-2 py-1.5 text-right text-[10px] uppercase text-gray-500">Amount</th>
                  <th className="px-2 py-1.5 text-left text-[10px] uppercase text-gray-500">Method</th>
                  <th className="px-2 py-1.5 text-left text-[10px] uppercase text-gray-500">Status</th>
                  <th className="px-2 py-1.5 text-left text-[10px] uppercase text-gray-500">Original</th>
                </tr>
              </thead>
              <tbody>
                {preview.transactions.map(t => {
                  const excluded = excludedKeys.has(t.dedup_key)
                  return (
                    <tr key={t.dedup_key}
                        className={`border-t border-gray-100 ${t.already_imported ? 'bg-amber-50/40' : ''} ${excluded ? 'opacity-50' : ''}`}>
                      <td className="px-2 py-1 text-center">
                        <input
                          type="checkbox"
                          checked={!excluded}
                          onChange={() => toggleExclude(t.dedup_key)}
                        />
                      </td>
                      <td className="px-2 py-1 text-xs whitespace-nowrap">{fmt.date(t.date)}</td>
                      <td className="px-2 py-1 text-xs">{t.formatted_text}</td>
                      <td className={`px-2 py-1 text-xs font-mono text-right ${excluded ? '' : 'text-green-700'}`}>
                        {fmt.currency(t.amount)}
                      </td>
                      <td className="px-2 py-1 text-xs">
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-700">{t.method}</span>
                      </td>
                      <td className="px-2 py-1 text-xs">
                        {t.already_imported ? (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">already imported</span>
                        ) : (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-100 text-green-700">new</span>
                        )}
                      </td>
                      <td className="px-2 py-1 text-[10px] text-gray-400 truncate max-w-[300px]">{t.description}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Live totals + generate */}
          <div className="mt-4 flex items-center justify-between gap-4 flex-wrap">
            <div className="text-sm text-gray-700">
              Will include <strong>{includedTxns.length}</strong> of {preview.transactions.length} transactions ·
              Total <strong className="font-mono">{fmt.currency(includedTotal)}</strong>
              {excludedKeys.size > 0 && <span className="text-gray-500"> · {excludedKeys.size} excluded</span>}
            </div>
            {error && <div className="text-xs text-red-600">{error}</div>}
            <div className="flex gap-2">
              <button className="btn-secondary text-sm" onClick={cancelPreview}>Cancel</button>
              <button
                className="btn-primary text-sm flex items-center gap-1"
                onClick={generate}
                disabled={generating || includedTxns.length === 0}
              >
                <FileText size={14} />
                {generating ? 'Generating…' : `Generate BAI2 (${includedTxns.length} txns)`}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* HISTORY */}
      <HistoryList imports={history?.imports || []} />
    </div>
  )
}


// ─────────────────────────────────────────────────────────────────────
// History list — last 3 visible, rest under chevron

function HistoryList({ imports }) {
  const [showOlder, setShowOlder] = useState(false)
  const recent = imports.slice(0, 3)
  const older = imports.slice(3)

  if (imports.length === 0) {
    return (
      <div className="card text-xs text-gray-400 italic">
        No BAI2 files generated yet.
      </div>
    )
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-700 flex items-center gap-1">
          <Building2 size={14} /> Generated BAI2 Files
        </h2>
        <span className="text-[10px] text-gray-400">{imports.length} total</span>
      </div>

      <ul className="divide-y divide-gray-100">
        {recent.map(imp => <ImportRow key={imp.id} imp={imp} />)}
      </ul>

      {older.length > 0 && (
        <>
          <button
            className="mt-2 text-xs text-primary-500 hover:underline flex items-center gap-1"
            onClick={() => setShowOlder(o => !o)}
          >
            {showOlder ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            {showOlder ? 'Hide older' : `Show ${older.length} older file${older.length !== 1 ? 's' : ''}`}
          </button>
          {showOlder && (
            <ul className="divide-y divide-gray-100 mt-2 border-t border-gray-100 pt-2">
              {older.map(imp => <ImportRow key={imp.id} imp={imp} />)}
            </ul>
          )}
        </>
      )}
    </div>
  )
}


function ImportRow({ imp }) {
  const qc = useQueryClient()
  const [expanded, setExpanded] = useState(false)

  const range = imp.date_range_start && imp.date_range_end
    ? (imp.date_range_start === imp.date_range_end
        ? fmt.date(imp.date_range_start)
        : `${fmt.date(imp.date_range_start)} – ${fmt.date(imp.date_range_end)}`)
    : '—'

  async function handleDelete() {
    if (!window.confirm(`Delete ${imp.bai2_filename || 'this import'} and its transactions?`)) return
    try {
      await api.delete(`/bank-recon/imports/${imp.id}`)
      qc.invalidateQueries({ queryKey: ['bai2-imports'] })
    } catch (e) { alert(e?.response?.data?.detail || e.message) }
  }

  return (
    <li className="py-2">
      <div className="flex items-center gap-3 text-sm">
        <button onClick={() => setExpanded(o => !o)} className="text-gray-400 hover:text-gray-600">
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </button>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            {imp.bai2_filename ? (
              <span className="font-mono text-xs text-gray-900 truncate">{imp.bai2_filename}</span>
            ) : (
              <span className="text-xs text-gray-500 italic">{imp.notes || 'No file generated'}</span>
            )}
          </div>
          <div className="text-[11px] text-gray-500 flex flex-wrap gap-2 mt-0.5">
            <span>{range}</span>
            <span>·</span>
            <span>{imp.transactions_included} txns</span>
            <span>·</span>
            <span className="font-mono">{fmt.currency(imp.total_amount)}</span>
            <span>·</span>
            <span>{fmt.date(imp.generated_at?.slice(0, 10))} {imp.generated_at?.slice(11, 16)}</span>
            {imp.generated_by && <><span>·</span><span>by {imp.generated_by.split('@')[0]}</span></>}
          </div>
        </div>
        {imp.downloadable && (
          <a
            href={`/api/bank-recon/imports/${imp.id}/download`}
            className="btn-secondary text-xs flex items-center gap-1"
          >
            <Download size={12} /> Download
          </a>
        )}
        <button onClick={handleDelete} className="text-gray-400 hover:text-red-600">
          <Trash2 size={12} />
        </button>
      </div>

      {expanded && (
        <div className="ml-5 mt-2 grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px] text-gray-600 bg-gray-50 rounded p-2">
          <Stat label="CSV rows" val={imp.csv_row_count} />
          <Stat label="Included" val={imp.transactions_included} />
          <Stat label="Withdrawals" val={imp.skipped_withdrawal} />
          <Stat label="ModMed" val={imp.skipped_modmed} />
          <Stat label="Stripe" val={imp.skipped_stripe} />
          <Stat label="Dups in file" val={imp.skipped_duplicate_in_file} />
          <Stat label="Prior dups" val={imp.skipped_prior_imports} />
          <Stat label="Source CSV" val={imp.csv_filename} cls="text-[10px]" />
        </div>
      )}
    </li>
  )
}


// ─────────────────────────────────────────────────────────────────────
// Helpers

function Labeled({ label, children }) {
  return (
    <div>
      <div className="text-[10px] uppercase text-gray-500 tracking-wide mb-1">{label}</div>
      {children}
    </div>
  )
}

function ToggleCheckbox({ label, checked, onChange }) {
  return (
    <label className="flex items-center gap-1 cursor-pointer">
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  )
}

function Stat({ label, val, cls }) {
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className="text-gray-400">{label}:</span>
      <span className={`font-medium ${cls || ''}`}>{val}</span>
    </span>
  )
}
