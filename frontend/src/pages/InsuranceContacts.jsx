import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Plus, Trash2, Save, X, Edit3, Phone, Link as LinkIcon, Search,
} from 'lucide-react'
import api from '../utils/api'
import { useCurrentUser } from '../hooks/useCurrentUser'


// New-row placeholder shape — used for in-place creation.
const NEW_ROW = {
  id:           '__new',
  company:      '',
  claims_links: [],
  phones:       [],
  notes:        '',
}


export default function InsuranceContacts() {
  const { isAdmin } = useCurrentUser()
  const qc = useQueryClient()
  const [editingId, setEditingId] = useState(null)   // id or '__new'
  const [draft, setDraft] = useState(null)
  const [filter, setFilter] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['insurance-contacts'],
    queryFn: () => api.get('/insurance-contacts').then(r => r.data),
  })

  const contacts = useMemo(() => {
    const rows = data?.contacts || []
    const q = filter.trim().toLowerCase()
    if (!q) return rows
    return rows.filter(c =>
      (c.company || '').toLowerCase().includes(q) ||
      (c.notes   || '').toLowerCase().includes(q) ||
      (c.claims_links || []).some(l =>
        (l.label || '').toLowerCase().includes(q) ||
        (l.url   || '').toLowerCase().includes(q)) ||
      (c.phones || []).some(p =>
        (p.label  || '').toLowerCase().includes(q) ||
        (p.number || '').toLowerCase().includes(q))
    )
  }, [data, filter])

  const createMut = useMutation({
    mutationFn: (body) => api.post('/insurance-contacts', body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['insurance-contacts'] })
      setEditingId(null); setDraft(null)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Create failed'),
  })

  const patchMut = useMutation({
    mutationFn: ({ id, body }) =>
      api.patch(`/insurance-contacts/${id}`, body).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['insurance-contacts'] })
      setEditingId(null); setDraft(null)
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Update failed'),
  })

  const deleteMut = useMutation({
    mutationFn: (id) => api.delete(`/insurance-contacts/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['insurance-contacts'] }),
    onError: (e) => alert(e?.response?.data?.detail || 'Delete failed'),
  })

  function startEdit(row) {
    setEditingId(row.id)
    setDraft({
      company:      row.company || '',
      claims_links: [...(row.claims_links || [])],
      phones:       [...(row.phones || [])],
      notes:        row.notes || '',
    })
  }

  function cancelEdit() {
    setEditingId(null); setDraft(null)
  }

  function startNewRow() {
    setEditingId('__new')
    setDraft({ company: '', claims_links: [], phones: [], notes: '' })
  }

  function save() {
    if (!draft?.company?.trim()) {
      alert('Company name is required.')
      return
    }
    const body = {
      company:      draft.company.trim(),
      claims_links: (draft.claims_links || [])
                       .filter(l => (l.label || '').trim() || (l.url || '').trim())
                       .map(l => ({ label: (l.label || '').trim(),
                                    url:   (l.url   || '').trim() })),
      phones:       (draft.phones || [])
                       .filter(p => (p.label  || '').trim() || (p.number || '').trim())
                       .map(p => ({ label:  (p.label  || '').trim(),
                                    number: (p.number || '').trim() })),
      notes:        (draft.notes || '').trim() || null,
    }
    if (editingId === '__new') createMut.mutate(body)
    else                       patchMut.mutate({ id: editingId, body })
  }

  function confirmDelete(row) {
    if (!window.confirm(
      `Delete "${row.company}"?\n\nThis cannot be undone from the UI ` +
      `(history table retains the record).`
    )) return
    deleteMut.mutate(row.id)
  }

  const showNewRow = editingId === '__new'
  const rows = showNewRow ? [NEW_ROW, ...contacts] : contacts

  return (
    <div>
      <div className="bg-white rounded-lg border border-border-subtle">
        {/* Card header */}
        <div className="px-5 py-4 border-b border-border-subtle flex items-center gap-3">
          <div className="flex-1 min-w-0">
            <h2 className="text-lg font-semibold text-gray-900">
              Insurance Contacts
            </h2>
            <p className="text-[12px] text-gray-500 mt-0.5">
              Phone numbers, claims portals, and notes for the carriers you bill.
            </p>
          </div>
          <div className="relative">
            <Search size={12}
                    className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
            <input className="input text-sm pl-7 pr-2 py-1 w-56"
                   placeholder="Filter…"
                   value={filter}
                   onChange={e => setFilter(e.target.value)} />
          </div>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={startNewRow}
                  disabled={!!editingId}>
            <Plus size={12} /> Add row
          </button>
        </div>

        {/* Table */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-[11px] uppercase text-gray-500">
              <tr>
                <th className="text-left px-5 py-2 w-[18%]">Company</th>
                <th className="text-left px-3 py-2 w-[28%]">Claims links</th>
                <th className="text-left px-3 py-2 w-[22%]">Phones</th>
                <th className="text-left px-3 py-2">Notes</th>
                <th className="text-right px-5 py-2 w-[120px]">Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr><td colSpan={5} className="px-5 py-6 text-gray-400 text-[12px]">
                  Loading…
                </td></tr>
              )}
              {!isLoading && rows.length === 0 && (
                <tr><td colSpan={5} className="px-5 py-6 text-gray-400 text-[12px] italic">
                  No insurance contacts yet — click <strong>Add row</strong> to start.
                </td></tr>
              )}
              {rows.map(row => {
                const isEditing = editingId === row.id
                return (
                  <tr key={row.id}
                      className={`border-t border-border-subtle ${
                        isEditing ? 'bg-plum-50/40' : 'hover:bg-gray-50'
                      }`}>
                    {isEditing ? (
                      <EditingRow
                        draft={draft}
                        setDraft={setDraft}
                        save={save}
                        cancel={cancelEdit}
                        isSaving={createMut.isPending || patchMut.isPending}
                      />
                    ) : (
                      <DisplayRow
                        row={row}
                        startEdit={() => startEdit(row)}
                        isAdmin={isAdmin}
                        onDelete={() => confirmDelete(row)}
                        disabled={!!editingId}
                      />
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}


// ─── Display row ──────────────────────────────────────────────────

function DisplayRow({ row, startEdit, isAdmin, onDelete, disabled }) {
  return (
    <>
      <td className="px-5 py-3 align-top">
        <div className="font-medium text-gray-900">{row.company}</div>
      </td>
      <td className="px-3 py-3 align-top">
        {(row.claims_links || []).length === 0 ? (
          <span className="text-gray-400 text-[12px] italic">—</span>
        ) : (
          <ul className="space-y-0.5">
            {(row.claims_links || []).map((l, i) => (
              <li key={i} className="text-[12px] flex items-start gap-1">
                <LinkIcon size={11} className="text-plum-600 shrink-0 mt-0.5" />
                <span className="min-w-0">
                  {l.label && (
                    <span className="text-gray-500">{l.label}: </span>
                  )}
                  {l.url ? (
                    <a href={l.url} target="_blank" rel="noopener noreferrer"
                       className="text-plum-700 hover:underline break-all">
                      {l.url}
                    </a>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        )}
      </td>
      <td className="px-3 py-3 align-top">
        {(row.phones || []).length === 0 ? (
          <span className="text-gray-400 text-[12px] italic">—</span>
        ) : (
          <ul className="space-y-0.5">
            {(row.phones || []).map((p, i) => (
              <li key={i} className="text-[12px] flex items-start gap-1">
                <Phone size={11} className="text-plum-600 shrink-0 mt-0.5" />
                <span>
                  {p.label && (
                    <span className="text-gray-500">{p.label}: </span>
                  )}
                  <span className="text-gray-800 font-mono">{p.number}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </td>
      <td className="px-3 py-3 align-top">
        {row.notes ? (
          <div className="text-[12px] text-gray-700 whitespace-pre-wrap">
            {row.notes}
          </div>
        ) : (
          <span className="text-gray-400 text-[12px] italic">—</span>
        )}
      </td>
      <td className="px-5 py-3 align-top text-right">
        <div className="inline-flex items-center gap-1">
          <button className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:bg-plum-50 flex items-center gap-1 disabled:opacity-30"
                  onClick={startEdit}
                  disabled={disabled}
                  title="Edit row">
            <Edit3 size={11} /> Edit
          </button>
          {isAdmin && (
            <button className="text-[11px] px-2 py-1 rounded border border-red-200 text-red-600 hover:bg-red-50 flex items-center gap-1 disabled:opacity-30"
                    onClick={onDelete}
                    disabled={disabled}
                    title="Delete (admin only)">
              <Trash2 size={11} />
            </button>
          )}
        </div>
      </td>
    </>
  )
}


// ─── Editing row ──────────────────────────────────────────────────

function EditingRow({ draft, setDraft, save, cancel, isSaving }) {
  function updateLink(i, patch) {
    const next = [...draft.claims_links]
    next[i] = { ...next[i], ...patch }
    setDraft({ ...draft, claims_links: next })
  }
  function addLink() {
    setDraft({ ...draft, claims_links: [...draft.claims_links, { label: '', url: '' }] })
  }
  function removeLink(i) {
    setDraft({ ...draft, claims_links: draft.claims_links.filter((_, j) => j !== i) })
  }
  function updatePhone(i, patch) {
    const next = [...draft.phones]
    next[i] = { ...next[i], ...patch }
    setDraft({ ...draft, phones: next })
  }
  function addPhone() {
    setDraft({ ...draft, phones: [...draft.phones, { label: '', number: '' }] })
  }
  function removePhone(i) {
    setDraft({ ...draft, phones: draft.phones.filter((_, j) => j !== i) })
  }

  return (
    <>
      <td className="px-5 py-3 align-top">
        <input className="input text-sm w-full"
               placeholder="Company name"
               value={draft.company}
               onChange={e => setDraft({ ...draft, company: e.target.value })}
               autoFocus />
      </td>
      <td className="px-3 py-3 align-top">
        <div className="space-y-1.5">
          {draft.claims_links.map((l, i) => (
            <div key={i} className="flex items-center gap-1">
              <input className="input text-[12px] w-24"
                     placeholder="Label"
                     value={l.label}
                     onChange={e => updateLink(i, { label: e.target.value })} />
              <input className="input text-[12px] flex-1"
                     placeholder="https://…"
                     value={l.url}
                     onChange={e => updateLink(i, { url: e.target.value })} />
              <button type="button"
                      className="text-gray-400 hover:text-red-600 p-1"
                      onClick={() => removeLink(i)}
                      title="Remove link">
                <X size={12} />
              </button>
            </div>
          ))}
          <button type="button"
                  className="text-[11px] text-plum-700 hover:text-plum-800 flex items-center gap-1"
                  onClick={addLink}>
            <Plus size={11} /> Add link
          </button>
        </div>
      </td>
      <td className="px-3 py-3 align-top">
        <div className="space-y-1.5">
          {draft.phones.map((p, i) => (
            <div key={i} className="flex items-center gap-1">
              <input className="input text-[12px] w-24"
                     placeholder="Label"
                     value={p.label}
                     onChange={e => updatePhone(i, { label: e.target.value })} />
              <input className="input text-[12px] flex-1 font-mono"
                     placeholder="555-555-5555"
                     value={p.number}
                     onChange={e => updatePhone(i, { number: e.target.value })} />
              <button type="button"
                      className="text-gray-400 hover:text-red-600 p-1"
                      onClick={() => removePhone(i)}
                      title="Remove phone">
                <X size={12} />
              </button>
            </div>
          ))}
          <button type="button"
                  className="text-[11px] text-plum-700 hover:text-plum-800 flex items-center gap-1"
                  onClick={addPhone}>
            <Plus size={11} /> Add phone
          </button>
        </div>
      </td>
      <td className="px-3 py-3 align-top">
        <textarea className="input text-[12px] w-full min-h-[60px]"
                  placeholder="Notes — payer-id, escalation contact, gotchas, …"
                  value={draft.notes}
                  onChange={e => setDraft({ ...draft, notes: e.target.value })} />
      </td>
      <td className="px-5 py-3 align-top text-right">
        <div className="inline-flex items-center gap-1">
          <button className="text-[11px] px-2 py-1 rounded bg-plum-600 text-white hover:bg-plum-700 flex items-center gap-1 disabled:opacity-50"
                  onClick={save}
                  disabled={isSaving}>
            <Save size={11} /> {isSaving ? 'Saving…' : 'Save'}
          </button>
          <button className="text-[11px] px-2 py-1 rounded border border-gray-200 hover:bg-gray-50"
                  onClick={cancel}
                  disabled={isSaving}>
            Cancel
          </button>
        </div>
      </td>
    </>
  )
}
