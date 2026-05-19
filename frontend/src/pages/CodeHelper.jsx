import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Upload, FileText, Loader2, AlertTriangle, CheckCircle2,
  ChevronRight, Wand2, Save, X,
} from 'lucide-react'
import api, { fmt } from '../utils/api'
import { Link } from 'react-router-dom'

// TODO(Task 10): import CodeHelperDenials from './CodeHelperDenials'


export default function CodeHelper() {
  const qc = useQueryClient()
  const [mode, setMode] = useState('text')              // 'text' | 'pdf'
  const [noteText, setNoteText] = useState('')
  const [pdfFile, setPdfFile] = useState(null)
  const [payer, setPayer] = useState('')
  const [draft, setDraft] = useState(null)               // AI result before save
  const [editName, setEditName] = useState('')
  const [editDob, setEditDob] = useState('')

  const { data: history } = useQuery({
    queryKey: ['code-helper-requests'],
    queryFn: () => api.get('/billing/code-helper/requests').then(r => r.data),
  })

  const generate = useMutation({
    mutationFn: async () => {
      const fd = new FormData()
      if (mode === 'text') fd.append('note_text', noteText)
      else if (pdfFile)    fd.append('note_pdf', pdfFile)
      if (payer) fd.append('payer_name', payer)
      const res = await api.post('/billing/code-helper/requests', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      return res.data
    },
    onSuccess: (data) => {
      setDraft(data)
      setEditName(data.patient_name || '')
      setEditDob(data.patient_dob   || '')
      qc.invalidateQueries({ queryKey: ['code-helper-requests'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Generation failed'),
  })

  const savePatient = useMutation({
    mutationFn: () =>
      api.patch(`/billing/code-helper/requests/${draft.id}`, {
        patient_name: editName, patient_dob: editDob || null,
      }).then(r => r.data),
    onSuccess: (data) => {
      setDraft(data)
      qc.invalidateQueries({ queryKey: ['code-helper-requests'] })
    },
  })

  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <Wand2 size={22} className="text-plum-700" />
            Code Helper
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">
            AI-assisted CPT + ICD-10 generation from a clinical note.
          </p>
        </div>
        <Link to="/billing/code-helper/denials"
              className="btn-secondary text-sm flex items-center gap-1">
          Manage denial list <ChevronRight size={13} />
        </Link>
      </div>

      {/* INPUT PANEL */}
      <div className="card mb-4">
        <div className="flex gap-2 mb-3">
          <button onClick={() => setMode('text')}
                  className={`text-sm px-3 py-1 rounded ${mode === 'text' ? 'bg-plum-700 text-white' : 'bg-gray-100'}`}>
            Paste note
          </button>
          <button onClick={() => setMode('pdf')}
                  className={`text-sm px-3 py-1 rounded ${mode === 'pdf' ? 'bg-plum-700 text-white' : 'bg-gray-100'}`}>
            Upload PDF
          </button>
        </div>

        {mode === 'text' ? (
          <textarea
            className="input text-sm w-full min-h-[160px] font-mono"
            placeholder="Paste the clinical note here…"
            value={noteText}
            onChange={e => setNoteText(e.target.value)}
          />
        ) : (
          <div>
            <input type="file" accept="application/pdf"
                    onChange={e => setPdfFile(e.target.files?.[0] || null)}
                    className="text-[12px]" />
            {pdfFile && (
              <div className="text-[11px] text-gray-500 mt-1">
                {pdfFile.name} — {(pdfFile.size / 1024).toFixed(0)} KB
              </div>
            )}
          </div>
        )}

        <div className="flex items-center gap-2 mt-3">
          <label className="text-[10px] uppercase text-gray-500">Payer</label>
          <input className="input text-sm" placeholder="Cigna / Aetna / …"
                  value={payer} onChange={e => setPayer(e.target.value)} />
          <button
            className="btn-primary text-sm flex items-center gap-1 ml-auto"
            disabled={generate.isPending || (mode === 'text' ? !noteText : !pdfFile)}
            onClick={() => generate.mutate()}
          >
            {generate.isPending
              ? <><Loader2 size={13} className="animate-spin" /> Calling Claude…</>
              : <><Wand2 size={13} /> Generate codes</>}
          </button>
        </div>
      </div>

      {/* RESULT PANEL */}
      {draft && (
        <div className="card mb-4 border-plum-200">
          <h2 className="font-serif font-semibold text-ink text-[15px] mb-2 flex items-center gap-2">
            <CheckCircle2 size={14} className="text-green-700" />
            AI suggestion
          </h2>

          {/* patient strip */}
          <div className="flex items-end gap-2 mb-3 text-sm">
            <div>
              <label className="text-[10px] uppercase text-gray-500 block">Patient</label>
              <input className="input text-sm" value={editName}
                      onChange={e => setEditName(e.target.value)} />
            </div>
            <div>
              <label className="text-[10px] uppercase text-gray-500 block">DOB</label>
              <input className="input text-sm" type="date" value={editDob}
                      onChange={e => setEditDob(e.target.value)} />
            </div>
            <div className="text-[11px] text-gray-600">
              {draft.patient_id
                ? <>✓ matched chart {draft.patient_id}</>
                : <>no chart match — saves without link</>}
            </div>
            <button className="btn-secondary text-xs ml-auto"
                    onClick={() => savePatient.mutate()}
                    disabled={savePatient.isPending}>
              <Save size={11} className="inline" /> Save patient
            </button>
          </div>

          {/* CPTs */}
          <h3 className="text-[12px] uppercase text-gray-500 mt-3 mb-1">CPT codes</h3>
          <div className="space-y-2">
            {draft.cpt_codes.map((c, i) => (
              <CPTCard key={i} entry={c} />
            ))}
          </div>

          {/* ICD-10 */}
          <h3 className="text-[12px] uppercase text-gray-500 mt-3 mb-1">ICD-10</h3>
          <div className="flex flex-wrap gap-2">
            {draft.icd10_codes.map((d, i) => (
              <span key={i} className="text-[12px] bg-gray-100 px-2 py-1 rounded">
                <strong>Pos {d.position}</strong> · <code>{d.code}</code> — {d.description}
              </span>
            ))}
          </div>

          <button className="text-xs text-muted hover:underline mt-3"
                  onClick={() => setDraft(null)}>
            <X size={11} className="inline" /> Discard this draft
          </button>
        </div>
      )}

      {/* HISTORY */}
      <div className="card !p-0 overflow-hidden">
        <h2 className="font-serif font-semibold text-ink text-[15px] p-3 border-b border-border-subtle">
          History
        </h2>
        <table className="w-full text-sm">
          <thead className="bg-plum-50 text-[11px] uppercase">
            <tr>
              <th className="table-th">Patient</th>
              <th className="table-th">DOB</th>
              <th className="table-th">Date</th>
              <th className="table-th">Payer</th>
              <th className="table-th">CPT</th>
              <th className="table-th">ICD-10</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {(history?.requests || []).map(r => (
              <tr key={r.id} className="hover:bg-plum-50/40 cursor-pointer"
                  onClick={() => setDraft(r)}>
                <td className="table-td">{r.patient_name || '—'}</td>
                <td className="table-td text-[11px]">{r.patient_dob ? fmt.date(r.patient_dob) : '—'}</td>
                <td className="table-td text-[11px]">{r.requested_at ? fmt.date(r.requested_at.slice(0, 10)) : '—'}</td>
                <td className="table-td text-[11px]">{r.payer_name || '—'}</td>
                <td className="table-td text-[11px]">
                  {(r.cpt_codes || []).map(c => c.code).join(', ') || '—'}
                </td>
                <td className="table-td text-[11px]">
                  {(r.icd10_codes || []).map(c => c.code).join(', ') || '—'}
                </td>
              </tr>
            ))}
            {!(history?.requests || []).length && (
              <tr><td colSpan={6} className="table-td text-center text-gray-400 italic py-6">
                No requests yet.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}


function CPTCard({ entry }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border border-border-subtle rounded p-2 text-sm">
      <div className="flex items-center gap-2">
        <code className="font-semibold">{entry.code}</code>
        {(entry.modifiers || []).map(m => (
          <code key={m} className="text-[10px] bg-gray-100 px-1 rounded">-{m}</code>
        ))}
        <span className="text-[10px] text-gray-500">Pos {entry.position}</span>
        <span className="text-[10px] uppercase bg-plum-50 text-plum-700 px-1.5 py-0.5 rounded">
          {entry.justification_type.replace(/_/g, ' ')}
        </span>
        <button className="text-[11px] text-plum-700 hover:underline ml-auto"
                onClick={() => setOpen(o => !o)}>
          {open ? 'Hide' : '▶ View'} justification
        </button>
      </div>
      {entry.denial_flag && (
        <div className="mt-2 text-[11px] bg-amber-50 border border-amber-300 rounded p-2 text-amber-900">
          <AlertTriangle size={11} className="inline mr-1" />
          Likely denied by <strong>{entry.denial_flag.payer}</strong>: {entry.denial_flag.reason}
          {entry.alternative && (
            <div className="mt-1">
              Alternative: <code>{entry.alternative.code}</code>
              {entry.alternative.modifiers?.length ? ' -' + entry.alternative.modifiers.join('-') : ''}
              — {entry.alternative.rationale}
            </div>
          )}
        </div>
      )}
      {open && (
        <div className="mt-2 text-[12px] text-gray-700 bg-gray-50 rounded p-2">
          {typeof entry.justification === 'string' ? (
            <p>{entry.justification}</p>
          ) : (
            <div className="grid grid-cols-3 gap-2">
              <div><strong>Problems:</strong> {entry.justification.problems_addressed}</div>
              <div><strong>Data:</strong> {entry.justification.data_reviewed}</div>
              <div><strong>Risk:</strong> {entry.justification.risk}</div>
            </div>
          )}
          {entry.justification_type === 'e_m_time' && entry.time_minutes != null && (
            <div className="mt-1 text-[11px] text-gray-500">Time documented: {entry.time_minutes} min</div>
          )}
        </div>
      )}
    </div>
  )
}
