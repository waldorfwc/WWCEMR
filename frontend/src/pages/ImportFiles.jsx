import { useState, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Upload, FileText, CheckCircle, AlertCircle, Clock } from 'lucide-react'
import api, { fmt } from '../utils/api'

export default function ImportFiles() {
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const inputRef = useRef()

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
