import { useState, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Upload, FileScan, Search, X, Check, ChevronLeft, ChevronRight,
  Users, MessageSquare, History, Lock, FileText, Edit3, Trash2, Save,
  ZoomIn, ZoomOut, RotateCcw, RotateCw, Maximize,
} from 'lucide-react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'
import api, { fmt } from '../utils/api'
import { useCurrentUser } from '../hooks/useCurrentUser'

// react-pdf needs a worker. Point at the bundled one.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()


const CLASSIFICATION_TONES = {
  paper_eob:        'bg-blue-100 text-blue-800',
  patient_payment:  'bg-green-100 text-green-800',
  insurance_letter: 'bg-violet-100 text-violet-800',
  denial:           'bg-red-100 text-red-800',
  other:            'bg-gray-100 text-gray-700',
}


const STATUS_TONES = {
  new:         'bg-amber-100 text-amber-700',
  in_progress: 'bg-blue-100 text-blue-700',
  worked:      'bg-green-100 text-green-700',
}


export default function InsuranceDocuments() {
  const [filters, setFilters] = useState({
    status: ['new', 'in_progress'],   // multi-select; default = active work
    classification: '',
    assigned_to_me: false,
    unassigned_only: false,
  })
  const [uploading, setUploading] = useState(false)
  const [openDocId, setOpenDocId] = useState(null)

  // Inline rename in the list (avoid having to open the drawer).
  const qcList = useQueryClient()
  const [renamingId, setRenamingId] = useState(null)
  const [renameValue, setRenameValue] = useState('')
  const renameMut = useMutation({
    mutationFn: ({ id, name }) =>
      api.patch(`/billing/documents/${id}`, { original_filename: name }).then(r => r.data),
    onSuccess: () => {
      qcList.invalidateQueries({ queryKey: ['billing-docs'] })
      setRenamingId(null)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Rename failed'),
  })
  const startRename = (d, e) => {
    e.stopPropagation()
    setRenameValue(d.original_filename)
    setRenamingId(d.id)
  }
  const saveRename = (e) => {
    if (e) e.stopPropagation()
    const name = renameValue.trim()
    if (!name || !renamingId) { setRenamingId(null); return }
    renameMut.mutate({ id: renamingId, name })
  }
  const cancelRename = (e) => {
    if (e) e.stopPropagation()
    setRenamingId(null)
  }

  const { data: picks } = useQuery({
    queryKey: ['billing-doc-picklists'],
    queryFn: () => api.get('/billing/documents/picklists').then(r => r.data),
    staleTime: 300_000,
  })
  const { data, isLoading } = useQuery({
    queryKey: ['billing-docs', filters],
    queryFn: () => api.get('/billing/documents', {
      params: Object.fromEntries(
        Object.entries(filters)
          // Arrays are serialized as comma-joined for the backend
          .map(([k, v]) => Array.isArray(v) ? [k, v.join(',')] : [k, v])
          .filter(([_, v]) => v !== '' && v !== false)
      ),
    }).then(r => r.data),
  })

  const rawDocs = data?.documents || []
  const classLabel = (v) => picks?.classifications?.find(c => c.v === v)?.l || v

  // Sort state — defaults to newest uploaded first. Click a header to toggle.
  const [sortKey, setSortKey] = useState('uploaded_at')   // 'uploaded_at' | 'classification'
  const [sortDir, setSortDir] = useState('desc')          // 'asc' | 'desc'
  function clickSort(key) {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir(key === 'uploaded_at' ? 'desc' : 'asc')
    }
  }
  const docs = (() => {
    const list = [...rawDocs]
    list.sort((a, b) => {
      let av, bv
      if (sortKey === 'classification') {
        av = (classLabel(a.classification) || '').toLowerCase()
        bv = (classLabel(b.classification) || '').toLowerCase()
      } else {
        av = a.uploaded_at || ''
        bv = b.uploaded_at || ''
      }
      const cmp = av < bv ? -1 : av > bv ? 1 : 0
      return sortDir === 'asc' ? cmp : -cmp
    })
    return list
  })()
  function SortArrow({ k }) {
    if (sortKey !== k) return <span className="text-gray-300">↕</span>
    return <span className="text-plum-700">{sortDir === 'asc' ? '▲' : '▼'}</span>
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <FileScan size={18} className="text-plum-700" />
          <h2 className="text-base font-semibold text-gray-800">Insurance Documents</h2>
          <span className="text-[11px] text-gray-500">({data?.total ?? 0})</span>
        </div>
        <button className="btn-primary text-sm flex items-center gap-1"
                onClick={() => setUploading(true)}>
          <Upload size={13} /> Upload document
        </button>
      </div>

      {/* Filters */}
      <div className="card mb-3">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">
              Status <span className="text-gray-400 normal-case">(toggle to multi-select)</span>
            </label>
            <div className="flex flex-wrap gap-1.5">
              {(picks?.statuses || []).map(s => {
                const active = (filters.status || []).includes(s.v)
                return (
                  <button key={s.v} type="button"
                          className={`text-[11px] px-2 py-1 rounded border ${
                            active
                              ? (STATUS_TONES[s.v] || 'bg-plum-100 text-plum-800') + ' border-current'
                              : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'
                          }`}
                          onClick={() => setFilters(f => {
                            const cur = new Set(f.status || [])
                            if (cur.has(s.v)) cur.delete(s.v)
                            else cur.add(s.v)
                            return { ...f, status: Array.from(cur) }
                          })}>
                    {s.l}
                  </button>
                )
              })}
              {(filters.status?.length ?? 0) > 0 && (
                <button type="button"
                        className="text-[11px] text-muted hover:underline ml-1"
                        onClick={() => setFilters(f => ({ ...f, status: [] }))}>
                  clear
                </button>
              )}
            </div>
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Classification</label>
            <select className="input text-sm w-full" value={filters.classification}
                    onChange={e => setFilters({ ...filters, classification: e.target.value })}>
              <option value="">All</option>
              {picks?.classifications?.map(c => (
                <option key={c.v} value={c.v}>{c.l}</option>
              ))}
            </select>
          </div>
          <div className="flex items-end">
            <label className="flex items-center gap-1 text-[12px] text-gray-700 cursor-pointer">
              <input type="checkbox" checked={filters.assigned_to_me}
                     onChange={e => setFilters({ ...filters, assigned_to_me: e.target.checked })} />
              Assigned to me
            </label>
          </div>
          <div className="flex items-end">
            <label className="flex items-center gap-1 text-[12px] text-gray-700 cursor-pointer">
              <input type="checkbox" checked={filters.unassigned_only}
                     onChange={e => setFilters({ ...filters, unassigned_only: e.target.checked })} />
              Unassigned only
            </label>
          </div>
        </div>
      </div>

      {/* List */}
      <div className="card !p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-plum-50">
            <tr>
              <th className="table-th">Filename</th>
              <th className="table-th cursor-pointer select-none"
                  onClick={() => clickSort('classification')}
                  title="Sort by document type">
                <span className="inline-flex items-center gap-1">Type <SortArrow k="classification" /></span>
              </th>
              <th className="table-th">Pages</th>
              <th className="table-th cursor-pointer select-none"
                  onClick={() => clickSort('uploaded_at')}
                  title="Sort by upload date">
                <span className="inline-flex items-center gap-1">Uploaded <SortArrow k="uploaded_at" /></span>
              </th>
              <th className="table-th">Assigned</th>
              <th className="table-th">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {isLoading && (
              <tr><td colSpan={6} className="table-td text-center py-6 text-gray-400">Loading…</td></tr>
            )}
            {!isLoading && docs.length === 0 && (
              <tr><td colSpan={6} className="table-td text-center py-6 text-gray-400 italic">
                No documents match.
              </td></tr>
            )}
            {docs.map(d => (
              <tr key={d.id} className="group hover:bg-plum-50/40 cursor-pointer"
                  onClick={() => renamingId !== d.id && setOpenDocId(d.id)}>
                <td className="table-td">
                  {renamingId === d.id ? (
                    <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
                      <FileText size={12} className="text-gray-400 shrink-0" />
                      <input
                        autoFocus
                        className="input text-[12px] py-0.5 flex-1 min-w-0"
                        value={renameValue}
                        onChange={e => setRenameValue(e.target.value)}
                        onKeyDown={e => {
                          if (e.key === 'Enter')  saveRename(e)
                          if (e.key === 'Escape') cancelRename(e)
                        }}
                      />
                      <button onClick={saveRename}
                              disabled={renameMut.isPending}
                              className="text-plum-700 hover:bg-plum-100 p-0.5 rounded shrink-0"
                              title="Save (Enter)"><Save size={12} /></button>
                      <button onClick={cancelRename}
                              className="text-gray-500 hover:bg-gray-100 p-0.5 rounded shrink-0"
                              title="Cancel (Esc)"><X size={12} /></button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1">
                      <FileText size={12} className="text-gray-400 shrink-0" />
                      <span className="truncate max-w-[280px]">{d.original_filename}</span>
                      <button
                        onClick={e => startRename(d, e)}
                        title="Rename"
                        className="opacity-0 group-hover:opacity-100 text-plum-700 hover:bg-plum-100 p-0.5 rounded shrink-0 transition-opacity">
                        <Edit3 size={11} />
                      </button>
                    </div>
                  )}
                </td>
                <td className="table-td">
                  <span className={`text-[10px] uppercase px-1.5 py-0.5 rounded ${CLASSIFICATION_TONES[d.classification] || ''}`}>
                    {classLabel(d.classification)}
                  </span>
                </td>
                <td className="table-td text-[11px] text-gray-500">{d.page_count ?? '—'}</td>
                <td className="table-td text-[11px]">
                  <div>{fmt.date(d.uploaded_at.slice(0, 10))}</div>
                  <div className="text-[10px] text-gray-500">{d.uploaded_by?.split('@')[0]}</div>
                </td>
                <td className="table-td text-[11px]">
                  {d.assigned_to?.length
                    ? d.assigned_to.map(a => a.split('@')[0]).join(', ')
                    : <span className="text-gray-400 italic">unassigned</span>}
                </td>
                <td className="table-td">
                  <span className={`text-[10px] uppercase px-1.5 py-0.5 rounded ${STATUS_TONES[d.status] || 'bg-gray-100 text-gray-700'}`}>
                    {(picks?.statuses?.find(s => s.v === d.status)?.l) || d.status.replace(/_/g, ' ')}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {uploading && (
        <UploadDrawer picks={picks} onClose={() => setUploading(false)} />
      )}
      {openDocId && (
        <DocumentDrawer docId={openDocId} onClose={() => setOpenDocId(null)} picks={picks} />
      )}
    </div>
  )
}


// ─── Upload drawer ─────────────────────────────────────────────────

function UploadDrawer({ picks, onClose }) {
  const qc = useQueryClient()
  const fileRef = useRef(null)
  const [file, setFile] = useState(null)
  const [classification, setClassification] = useState('other')
  const [autoClassify, setAutoClassify] = useState(true)
  const [assignedTo, setAssignedTo] = useState([])
  const [aiResult, setAiResult] = useState(null)

  const { data: workforce = [] } = useQuery({
    queryKey: ['billing-doc-workforce'],
    queryFn: () => api.get('/billing/documents/workforce/assignable').then(r => r.data),
    staleTime: 60_000,
  })

  // Holds the 'existing' info returned with a 409 dup response so the
  // user can decide whether to upload anyway.
  const [dupExisting, setDupExisting] = useState(null)

  const upload = useMutation({
    mutationFn: async ({ force = false } = {}) => {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('classification', classification)
      fd.append('auto_classify', autoClassify ? 'true' : 'false')
      fd.append('assigned_to', assignedTo.join(','))
      if (force) fd.append('force', 'true')
      return api.post('/billing/documents', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      }).then(r => r.data)
    },
    onSuccess: (data) => {
      setDupExisting(null)
      qc.invalidateQueries({ queryKey: ['billing-docs'] })
      // Show AI classification result briefly before closing if it kicked in
      if (data?.ai_classified) {
        setAiResult(data)
        setTimeout(() => onClose(), 1800)
      } else {
        onClose()
      }
    },
    onError: (e) => {
      const detail = e?.response?.data?.detail
      // Backend returns 409 with detail={error,message,existing:{...}} for dups
      if (e?.response?.status === 409 && detail?.error === 'duplicate') {
        setDupExisting(detail.existing)
        return
      }
      alert(typeof detail === 'string' ? detail : 'Upload failed')
    },
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-md bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between">
          <h2 className="font-serif font-semibold text-ink text-[16px]">Upload document</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">File (PDF preferred)</label>
            <input ref={fileRef} type="file" accept="application/pdf,image/*"
                   className="text-[12px] w-full"
                   onChange={e => setFile(e.target.files?.[0] || null)} />
            {file && (
              <div className="text-[11px] text-gray-500 mt-1">
                {file.name} — {(file.size / 1024 / 1024).toFixed(2)} MB
              </div>
            )}
          </div>
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">Classification</label>
            <select className="input text-sm w-full" value={classification}
                    onChange={e => setClassification(e.target.value)}>
              {picks?.classifications?.map(c => (
                <option key={c.v} value={c.v}>{c.l}</option>
              ))}
            </select>
            <label className="flex items-center gap-1 text-[10px] text-gray-500 mt-1 cursor-pointer">
              <input type="checkbox" checked={autoClassify}
                     onChange={e => setAutoClassify(e.target.checked)} />
              Auto-classify with AI (when left at <em>Other</em>)
            </label>
          </div>
          {aiResult && (
            <div className="text-[11px] bg-blue-50 border border-blue-200 rounded p-2 text-blue-900">
              <strong>✨ AI classified</strong> this as{' '}
              <strong>{aiResult.classification_label}</strong>. You can change it
              after the drawer closes.
            </div>
          )}
          <div>
            <label className="text-[10px] uppercase text-gray-500 block mb-1">
              Assign to (optional — leave blank for everyone)
            </label>
            <div className="border border-border-subtle rounded p-2 max-h-48 overflow-y-auto space-y-1">
              {workforce.map(u => (
                <label key={u.email} className="flex items-center gap-2 text-[12px] cursor-pointer">
                  <input type="checkbox"
                         checked={assignedTo.includes(u.email)}
                         onChange={e => {
                           if (e.target.checked) setAssignedTo([...assignedTo, u.email])
                           else setAssignedTo(assignedTo.filter(a => a !== u.email))
                         }} />
                  <span>{u.name}</span>
                  <span className="text-[10px] text-gray-500 ml-auto">{u.email}</span>
                </label>
              ))}
            </div>
          </div>
        </div>
        {dupExisting && (
          <div className="mx-5 mb-3 text-[12px] bg-amber-50 border border-amber-300 rounded p-3 text-amber-900">
            <div className="font-semibold mb-1">⚠ Possible duplicate</div>
            <div className="leading-snug">
              A document with identical contents already exists:
              <div className="mt-1 bg-white rounded border border-amber-200 px-2 py-1 text-[11px] text-ink">
                <div className="font-medium truncate">{dupExisting.original_filename}</div>
                <div className="text-gray-500">
                  Uploaded {fmt.date((dupExisting.uploaded_at || '').slice(0, 10))} by{' '}
                  {dupExisting.uploaded_by?.split('@')[0]} ·{' '}
                  <span className="uppercase">{dupExisting.status}</span>
                </div>
              </div>
            </div>
          </div>
        )}
        <div className="sticky bottom-0 bg-white border-t border-border-subtle px-5 py-3 flex justify-end gap-2">
          <button className="text-sm text-muted hover:underline" onClick={onClose}>Cancel</button>
          {dupExisting ? (
            <button className="btn-primary text-sm flex items-center gap-1 bg-amber-600 hover:bg-amber-700"
                    onClick={() => upload.mutate({ force: true })}
                    disabled={upload.isPending}>
              <Upload size={12} /> {upload.isPending ? 'Uploading…' : 'Upload anyway'}
            </button>
          ) : (
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


// ─── Document drawer (viewer + workflow) ───────────────────────────

function DocumentDrawer({ docId, onClose, picks }) {
  const qc = useQueryClient()
  const { isAdmin } = useCurrentUser()
  const [renaming, setRenaming] = useState(false)
  const [newName, setNewName] = useState('')
  const [assignFilter, setAssignFilter] = useState('')

  const { data: doc } = useQuery({
    queryKey: ['billing-doc', docId],
    queryFn: () => api.get(`/billing/documents/${docId}`).then(r => r.data),
  })
  const { data: workforce = [] } = useQuery({
    queryKey: ['billing-doc-workforce'],
    queryFn: () => api.get('/billing/documents/workforce/assignable').then(r => r.data),
    staleTime: 60_000,
  })

  const patchMut = useMutation({
    mutationFn: (body) => api.patch(`/billing/documents/${docId}`, body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['billing-doc', docId] })
      qc.invalidateQueries({ queryKey: ['billing-docs'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Update failed'),
  })

  const deleteMut = useMutation({
    mutationFn: () => api.delete(`/billing/documents/${docId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['billing-docs'] })
      onClose()
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Delete failed'),
  })

  function saveRename() {
    const name = newName.trim()
    if (!name) return
    patchMut.mutate({ original_filename: name }, {
      onSuccess: () => setRenaming(false),
    })
  }

  function confirmDelete() {
    if (!doc) return
    if (!window.confirm(
      `Delete "${doc.original_filename}"?\n\n` +
      `This permanently removes the file from disk and the DB row, ` +
      `along with all notes and access history. This cannot be undone.`
    )) return
    deleteMut.mutate()
  }

  if (!doc) {
    return (
      <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
        <div className="absolute inset-0 bg-black/30" />
        <div className="relative w-full max-w-5xl bg-white shadow-xl p-6"
             onClick={e => e.stopPropagation()}>
          <div className="text-gray-400">Loading…</div>
        </div>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-6xl bg-white shadow-xl overflow-hidden flex flex-col"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-5 py-3 flex items-center justify-between gap-2">
          <div className="min-w-0 flex-1">
            {renaming ? (
              <div className="flex items-center gap-1">
                <input className="input text-sm w-full"
                       value={newName}
                       onChange={e => setNewName(e.target.value)}
                       onKeyDown={e => {
                         if (e.key === 'Enter') saveRename()
                         else if (e.key === 'Escape') setRenaming(false)
                       }}
                       autoFocus />
                <button className="text-plum-700 hover:bg-plum-50 p-1 rounded shrink-0"
                        title="Save (Enter)"
                        onClick={saveRename}><Save size={14} /></button>
                <button className="text-gray-500 hover:bg-gray-100 p-1 rounded shrink-0"
                        title="Cancel (Esc)"
                        onClick={() => setRenaming(false)}><X size={14} /></button>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <h2 className="font-serif font-semibold text-ink text-[15px] truncate">
                  {doc.original_filename}
                </h2>
                <button className="text-plum-700 hover:bg-plum-50 px-2 py-1 rounded flex items-center gap-1 text-[11px] border border-plum-200 shrink-0"
                        title="Rename document"
                        onClick={() => { setNewName(doc.original_filename); setRenaming(true) }}>
                  <Edit3 size={12} /> Rename
                </button>
              </div>
            )}
            <div className="text-[11px] text-gray-500 mt-0.5">
              Uploaded {fmt.date(doc.uploaded_at.slice(0, 10))} by {doc.uploaded_by?.split('@')[0]}
              {doc.assigned_to?.length > 0 && (
                <> · <Lock size={10} className="inline" /> assigned to {doc.assigned_to.map(a => a.split('@')[0]).join(', ')}</>
              )}
            </div>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {isAdmin && (
              <button onClick={confirmDelete}
                       disabled={deleteMut.isPending}
                       className="text-red-600 hover:bg-red-50 p-1.5 rounded flex items-center gap-1 text-[11px]"
                       title="Delete document (admin only)">
                <Trash2 size={13} /> Delete
              </button>
            )}
            <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
          </div>
        </div>

        <div className="flex-1 overflow-hidden grid grid-cols-3 gap-0">
          {/* Left: PDF or image viewer (2/3) */}
          <div className="col-span-2 bg-gray-100 overflow-auto border-r border-border-subtle">
            {isImageFilename(doc.original_filename) ? (
              <ImageViewer docId={docId} filename={doc.original_filename} />
            ) : (
              <PdfViewer docId={docId} totalPages={doc.page_count} />
            )}
          </div>

          {/* Right: workflow (1/3) */}
          <div className="col-span-1 overflow-y-auto p-4 space-y-4">
            {/* Classification */}
            <section>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Classification</label>
              <select className="input text-sm w-full"
                      value={doc.classification}
                      onChange={e => patchMut.mutate({ classification: e.target.value })}>
                {picks?.classifications?.map(c => (
                  <option key={c.v} value={c.v}>{c.l}</option>
                ))}
              </select>
            </section>

            {/* Assignees */}
            <section>
              <label className="text-[11px] uppercase text-gray-500 flex items-center gap-1 mb-1.5 font-semibold">
                <Users size={12} /> Assigned to
                {doc.assigned_to?.length > 0 && (
                  <span className="ml-1 text-[10px] text-gray-400 font-normal">
                    ({doc.assigned_to.length})
                  </span>
                )}
              </label>

              {/* Selected chips */}
              {doc.assigned_to?.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-2">
                  {doc.assigned_to.map(email => {
                    const u = workforce.find(w => w.email === email)
                    return (
                      <span key={email}
                            className="inline-flex items-center gap-1 text-[11px] bg-plum-100 text-plum-800 px-2 py-0.5 rounded">
                        {u?.name || email.split('@')[0]}
                        <button type="button"
                                className="hover:text-red-700"
                                onClick={() => patchMut.mutate({
                                  assigned_to: (doc.assigned_to || []).filter(a => a !== email)
                                })}>
                          <X size={10} />
                        </button>
                      </span>
                    )
                  })}
                  <button type="button"
                          className="text-[10px] text-gray-500 hover:text-red-700 underline ml-1"
                          onClick={() => patchMut.mutate({ assigned_to: [] })}>
                    clear all
                  </button>
                </div>
              )}

              {/* Search box */}
              <div className="relative mb-1.5">
                <Search size={11} className="absolute left-2 top-2 text-muted" />
                <input className="input text-[12px] pl-6 w-full"
                       placeholder="Search users…"
                       value={assignFilter}
                       onChange={e => setAssignFilter(e.target.value)} />
              </div>

              {/* Larger list */}
              <div className="border border-border-subtle rounded p-2 h-64 overflow-y-auto space-y-0.5 bg-gray-50">
                {workforce
                  .filter(u => {
                    const q = assignFilter.trim().toLowerCase()
                    if (!q) return true
                    return (u.name || '').toLowerCase().includes(q)
                        || (u.email || '').toLowerCase().includes(q)
                  })
                  .map(u => {
                    const checked = doc.assigned_to?.includes(u.email)
                    return (
                      <label key={u.email}
                             className={`flex items-center gap-2 text-[12px] cursor-pointer px-1.5 py-1 rounded ${
                               checked ? 'bg-plum-100' : 'hover:bg-white'
                             }`}>
                        <input type="checkbox" checked={!!checked}
                               onChange={e => {
                                 const next = e.target.checked
                                   ? [...(doc.assigned_to || []), u.email]
                                   : (doc.assigned_to || []).filter(a => a !== u.email)
                                 patchMut.mutate({ assigned_to: next })
                               }} />
                        <span className="truncate">{u.name}</span>
                        <span className="ml-auto text-[10px] text-gray-500 truncate">
                          {u.email.split('@')[0]}
                        </span>
                      </label>
                    )
                  })}
                {workforce.length === 0 && (
                  <div className="text-[11px] text-gray-400 italic p-2">No users available.</div>
                )}
              </div>
              {(!doc.assigned_to || doc.assigned_to.length === 0) && (
                <div className="text-[10px] text-gray-500 italic mt-1">
                  Unassigned — visible to everyone with billing access.
                </div>
              )}
            </section>

            {/* Status */}
            <section>
              <label className="text-[10px] uppercase text-gray-500 block mb-1">Status</label>
              <div className="flex items-center gap-2 flex-wrap">
                <span className={`text-[10px] uppercase px-1.5 py-0.5 rounded ${STATUS_TONES[doc.status] || 'bg-gray-100 text-gray-700'}`}>
                  {(picks?.statuses?.find(s => s.v === doc.status)?.l) || doc.status.replace(/_/g, ' ')}
                </span>
                {doc.status === 'worked' && doc.worked_by && (
                  <span className="text-[11px] text-gray-500">
                    by {doc.worked_by.split('@')[0]}
                    {doc.worked_at && ` on ${fmt.date(doc.worked_at.slice(0,10))}`}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2 mt-2 flex-wrap">
                {doc.status !== 'new' && (
                  <button className="text-[11px] text-amber-700 hover:underline"
                          onClick={() => patchMut.mutate({ status: 'new' })}>
                    Mark as new
                  </button>
                )}
                {doc.status !== 'in_progress' && (
                  <button className="text-[11px] text-blue-700 hover:underline"
                          onClick={() => patchMut.mutate({ status: 'in_progress' })}>
                    Mark in progress
                  </button>
                )}
                {doc.status !== 'worked' && (
                  <button className="btn-primary text-[12px] flex items-center gap-1"
                          onClick={() => patchMut.mutate({ status: 'worked' })}>
                    <Check size={12} /> Mark as worked
                  </button>
                )}
              </div>
            </section>

            <NotesSection docId={docId} notes={doc.notes || []} />
            <AuditSection log={doc.access_log || []} />
          </div>
        </div>
      </div>
    </div>
  )
}


// ─── PDF viewer with prev/next ─────────────────────────────────────

// ─── Image viewer (with zoom + rotate) ────────────────────────────

const _IMG_EXTS = new Set(['jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'bmp', 'tiff', 'tif'])
function isImageFilename(name) {
  if (!name) return false
  const i = name.lastIndexOf('.')
  if (i < 0) return false
  return _IMG_EXTS.has(name.slice(i + 1).toLowerCase())
}


function ImageViewer({ docId, filename }) {
  const [blobUrl, setBlobUrl] = useState(null)
  const [error, setError] = useState(null)
  const [scale, setScale] = useState(1)
  const [rotation, setRotation] = useState(0)   // degrees, multiples of 90

  // Fetch with the auth header (img tags can't carry it) → blob URL
  useEffect(() => {
    let revoked = false
    let createdUrl = null
    setBlobUrl(null); setError(null)
    fetch(`/api/billing/documents/${docId}/file`, {
      headers: { Authorization: `Bearer ${localStorage.getItem('session_token') || ''}` },
    })
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.blob()
      })
      .then(blob => {
        if (revoked) return
        createdUrl = URL.createObjectURL(blob)
        setBlobUrl(createdUrl)
      })
      .catch(e => { if (!revoked) setError(e.message || 'Failed to load') })
    return () => {
      revoked = true
      if (createdUrl) URL.revokeObjectURL(createdUrl)
    }
  }, [docId])

  function zoomIn()  { setScale(s => Math.min(4,    +(s + 0.25).toFixed(2))) }
  function zoomOut() { setScale(s => Math.max(0.25, +(s - 0.25).toFixed(2))) }
  function rotateCw()  { setRotation(r => (r + 90) % 360) }
  function rotateCcw() { setRotation(r => (r + 270) % 360) }
  function reset() { setScale(1); setRotation(0) }

  return (
    <div className="h-full flex flex-col">
      {/* Toolbar */}
      <div className="bg-white border-b border-border-subtle px-3 py-2 flex items-center gap-2 sticky top-0 z-10">
        <button type="button" className="text-[12px] px-2 py-1 rounded border border-gray-200 hover:bg-plum-50 flex items-center gap-1"
                onClick={zoomOut} title="Zoom out">
          <ZoomOut size={12} />
        </button>
        <span className="text-[12px] font-mono w-12 text-center">{Math.round(scale * 100)}%</span>
        <button type="button" className="text-[12px] px-2 py-1 rounded border border-gray-200 hover:bg-plum-50 flex items-center gap-1"
                onClick={zoomIn} title="Zoom in">
          <ZoomIn size={12} />
        </button>
        <div className="border-l border-gray-200 h-5 mx-1" />
        <button type="button" className="text-[12px] px-2 py-1 rounded border border-gray-200 hover:bg-plum-50 flex items-center gap-1"
                onClick={rotateCcw} title="Rotate left 90°">
          <RotateCcw size={12} />
        </button>
        <span className="text-[12px] font-mono w-12 text-center">{rotation}°</span>
        <button type="button" className="text-[12px] px-2 py-1 rounded border border-gray-200 hover:bg-plum-50 flex items-center gap-1"
                onClick={rotateCw} title="Rotate right 90°">
          <RotateCw size={12} />
        </button>
        <div className="border-l border-gray-200 h-5 mx-1" />
        <button type="button" className="text-[12px] px-2 py-1 rounded border border-gray-200 hover:bg-plum-50 flex items-center gap-1"
                onClick={reset} title="Reset zoom + rotation">
          <Maximize size={12} /> Reset
        </button>
        <div className="ml-auto text-[11px] text-muted truncate">{filename}</div>
      </div>

      {/* Image area */}
      <div className="flex-1 overflow-auto p-4 flex items-center justify-center bg-gray-200">
        {error && (
          <div className="text-red-600 text-[12px]">Failed to load image: {error}</div>
        )}
        {!error && !blobUrl && (
          <div className="text-gray-400 text-[12px]">Loading image…</div>
        )}
        {blobUrl && (
          <img src={blobUrl}
                alt={filename || 'Document image'}
                style={{
                  transform: `rotate(${rotation}deg) scale(${scale})`,
                  transformOrigin: 'center center',
                  transition: 'transform 80ms ease-out',
                  // Container overflow handles scrolling for large/rotated images
                  maxWidth: 'none', maxHeight: 'none',
                }}
                draggable={false} />
        )}
      </div>
    </div>
  )
}


function PdfViewer({ docId, totalPages }) {
  const [pageNum, setPageNum] = useState(1)
  const [numPages, setNumPages] = useState(totalPages || null)
  const [width, setWidth] = useState(700)
  const [scale, setScale] = useState(1)
  const [rotation, setRotation] = useState(0)
  const containerRef = useRef(null)
  const scrollAreaRef = useRef(null)
  const pageRefs = useRef({})           // {pageNumber: HTMLDivElement}
  // The auth header gets attached by api(); but react-pdf does its own
  // fetch. We pass the cookie/header via the file URL with credentials.

  useEffect(() => {
    function update() {
      if (containerRef.current) {
        setWidth(Math.max(300, containerRef.current.clientWidth - 32))
      }
    }
    update()
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [])

  // Observe page divs to update pageNum to whichever is most visible.
  // The viewer renders all pages stacked, so the browser's native scroll
  // handles advancing between pages — scrolling past the bottom of page N
  // continues into page N+1 (and the toolbar number updates).
  useEffect(() => {
    if (!numPages || !scrollAreaRef.current) return
    let mostVisible = { page: 1, ratio: 0 }
    const obs = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const p = Number(e.target.getAttribute('data-pagenum'))
          if (!p) continue
          if (e.isIntersecting && e.intersectionRatio > mostVisible.ratio) {
            mostVisible = { page: p, ratio: e.intersectionRatio }
          }
        }
        // After processing this batch, pick the entry with highest ratio.
        // (We reset by walking all observed elements once per batch.)
        let bestP = 1, bestR = 0
        for (const [p, el] of Object.entries(pageRefs.current)) {
          if (!el) continue
          const rect = el.getBoundingClientRect()
          const containerRect = scrollAreaRef.current.getBoundingClientRect()
          const visibleTop = Math.max(rect.top, containerRect.top)
          const visibleBottom = Math.min(rect.bottom, containerRect.bottom)
          const visible = Math.max(0, visibleBottom - visibleTop)
          const ratio = rect.height ? visible / rect.height : 0
          if (ratio > bestR) { bestR = ratio; bestP = Number(p) }
        }
        if (bestR > 0) setPageNum(bestP)
      },
      { root: scrollAreaRef.current, threshold: [0, 0.25, 0.5, 0.75, 1] }
    )
    for (const el of Object.values(pageRefs.current)) {
      if (el) obs.observe(el)
    }
    return () => obs.disconnect()
  }, [numPages])

  function goto(n) {
    const target = Math.max(1, Math.min(numPages || 1, n))
    const el = pageRefs.current[target]
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    setPageNum(target)
  }

  function zoomIn()  { setScale(s => Math.min(4,    +(s + 0.25).toFixed(2))) }
  function zoomOut() { setScale(s => Math.max(0.25, +(s - 0.25).toFixed(2))) }
  function rotateCw()  { setRotation(r => (r + 90) % 360) }
  function rotateCcw() { setRotation(r => (r + 270) % 360) }
  function reset() { setScale(1); setRotation(0) }

  return (
    <div ref={containerRef} className="h-full flex flex-col">
      {/* Toolbar: page nav + zoom + rotate */}
      <div className="bg-white border-b border-border-subtle px-3 py-2 flex items-center gap-2 flex-wrap sticky top-0 z-10">
        {/* Page nav — scrolls to the target page; native scroll between
            pages otherwise (scroll down → next, scroll up → previous). */}
        <button type="button"
                className="text-[12px] px-2 py-1 rounded border border-gray-200 hover:bg-plum-50 disabled:opacity-30 flex items-center gap-1"
                disabled={pageNum <= 1}
                onClick={() => goto(pageNum - 1)}>
          <ChevronLeft size={12} /> Prev
        </button>
        <div className="text-[12px] text-gray-700">
          <input type="number" min={1} max={numPages || 1}
                  className="w-12 input text-[12px] text-center inline-block"
                  value={pageNum}
                  onChange={e => goto(Number(e.target.value) || 1)} />
          {' '}/ {numPages ?? '—'}
        </div>
        <button type="button"
                className="text-[12px] px-2 py-1 rounded border border-gray-200 hover:bg-plum-50 disabled:opacity-30 flex items-center gap-1"
                disabled={!numPages || pageNum >= numPages}
                onClick={() => goto(pageNum + 1)}>
          Next <ChevronRight size={12} />
        </button>

        <div className="border-l border-gray-200 h-5 mx-1" />

        {/* Zoom */}
        <button type="button" className="text-[12px] px-2 py-1 rounded border border-gray-200 hover:bg-plum-50 flex items-center gap-1"
                onClick={zoomOut} title="Zoom out">
          <ZoomOut size={12} />
        </button>
        <span className="text-[12px] font-mono w-12 text-center">{Math.round(scale * 100)}%</span>
        <button type="button" className="text-[12px] px-2 py-1 rounded border border-gray-200 hover:bg-plum-50 flex items-center gap-1"
                onClick={zoomIn} title="Zoom in">
          <ZoomIn size={12} />
        </button>

        <div className="border-l border-gray-200 h-5 mx-1" />

        {/* Rotate */}
        <button type="button" className="text-[12px] px-2 py-1 rounded border border-gray-200 hover:bg-plum-50 flex items-center gap-1"
                onClick={rotateCcw} title="Rotate left 90°">
          <RotateCcw size={12} />
        </button>
        <span className="text-[12px] font-mono w-12 text-center">{rotation}°</span>
        <button type="button" className="text-[12px] px-2 py-1 rounded border border-gray-200 hover:bg-plum-50 flex items-center gap-1"
                onClick={rotateCw} title="Rotate right 90°">
          <RotateCw size={12} />
        </button>

        <div className="border-l border-gray-200 h-5 mx-1" />

        <button type="button" className="text-[12px] px-2 py-1 rounded border border-gray-200 hover:bg-plum-50 flex items-center gap-1"
                onClick={reset} title="Reset zoom + rotation">
          <Maximize size={12} /> Reset
        </button>
      </div>

      <div ref={scrollAreaRef} className="flex-1 overflow-auto p-4 flex flex-col items-center gap-4">
        <Document file={{
                    url: `/api/billing/documents/${docId}/file`,
                    httpHeaders: {
                      Authorization: `Bearer ${localStorage.getItem('session_token') || ''}`,
                    },
                    withCredentials: false,
                  }}
                  onLoadSuccess={({ numPages: n }) => setNumPages(n)}
                  loading={<div className="text-gray-400 mt-8">Loading PDF…</div>}
                  error={<div className="text-red-600 mt-8">Failed to load PDF.</div>}>
          {Array.from({ length: numPages || 0 }, (_, i) => i + 1).map(p => (
            <div key={p}
                  data-pagenum={p}
                  ref={el => { pageRefs.current[p] = el }}
                  className="shadow-sm bg-white">
              <Page pageNumber={p} width={width} scale={scale} rotate={rotation}
                     renderAnnotationLayer={false}
                     renderTextLayer={false} />
            </div>
          ))}
        </Document>
      </div>
    </div>
  )
}


// ─── Notes ─────────────────────────────────────────────────────────

function NotesSection({ docId, notes }) {
  const qc = useQueryClient()
  const [body, setBody] = useState('')
  const add = useMutation({
    mutationFn: () => api.post(`/billing/documents/${docId}/notes`,
                                { body }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['billing-doc', docId] })
      setBody('')
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Save failed'),
  })

  return (
    <section>
      <label className="text-[10px] uppercase text-gray-500 flex items-center gap-1 mb-1">
        <MessageSquare size={11} /> Notes ({notes.length})
      </label>
      <div className="space-y-2 max-h-48 overflow-y-auto mb-2">
        {notes.length === 0 && (
          <div className="text-[11px] text-gray-400 italic">No notes yet.</div>
        )}
        {notes.map(n => (
          <div key={n.id} className="border-l-2 border-plum-200 pl-2 py-0.5">
            <div className="text-[10px] text-gray-500">
              {n.author?.split('@')[0]} · {fmt.date(n.created_at.slice(0, 10))}{' '}
              {n.created_at.slice(11, 16)}
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


// ─── Access audit ──────────────────────────────────────────────────

function AuditSection({ log }) {
  const [open, setOpen] = useState(false)
  return (
    <section>
      <button type="button"
              className="text-[10px] uppercase text-gray-500 flex items-center gap-1 hover:text-plum-700"
              onClick={() => setOpen(o => !o)}>
        <History size={11} /> Access log ({log.length}) {open ? '▾' : '▸'}
      </button>
      {open && (
        <div className="mt-1 space-y-1 max-h-48 overflow-y-auto">
          {log.map(a => (
            <div key={a.id} className="text-[10px] text-gray-600 flex justify-between">
              <span>
                <strong className="text-gray-800">{a.actor?.split('@')[0]}</strong>
                {' '}{a.action.replace(/_/g, ' ')}
              </span>
              <span className="text-gray-400 shrink-0 ml-2">
                {fmt.date(a.at.slice(0, 10))} {a.at.slice(11, 16)}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
