import { useState, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { X, Search } from 'lucide-react'
import api, { fmt } from '../../utils/api'
import SurgeryIntakeForm from './SurgeryIntakeForm'
import { useConfirm } from '../ui/ConfirmDialog'
import { useCurrentUser } from '../../hooks/useCurrentUser'
import { MODULE, TIER } from '../../routes.jsx'


export function ManualCreateDrawer({ onClose }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [error, setError] = useState(null)

  const create = useMutation({
    mutationFn: ({ fields }) =>
      api.post('/surgery/manual', fields).then(r => r.data),
    onSuccess: async (data, { orderFile }) => {
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
      // Attach the order PDF (if one was uploaded) to the new surgery.
      if (orderFile) {
        try {
          const fd = new FormData()
          fd.append('file', orderFile)
          await api.post(`/surgery/${data.id}/files?kind=order`, fd, {
            headers: { 'Content-Type': 'multipart/form-data' },
          })
        } catch (e) {
          // Non-fatal: the surgery exists, just warn and continue navigating.
          const d = e?.response?.data?.detail
          alert('Surgery created, but attaching the order PDF failed: '
            + (typeof d === 'string' ? d : (e?.message || 'upload error')))
        }
      }
      onClose()
      navigate(`/surgery/${data.id}`)
    },
    onError: (e) => {
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (e?.message || 'Create failed'))
    },
  })

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-2xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-6 py-4 flex items-center justify-between z-10">
          <h2 className="font-serif font-semibold text-ink text-[18px]">+ New surgery (manual)</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        <SurgeryIntakeForm
          mode="create"
          submitLabel="Create surgery"
          submitting={create.isPending}
          error={error}
          onCancel={onClose}
          onSubmit={(payload) => { setError(null); create.mutate(payload) }}
        />
      </div>
    </div>
  )
}


export function UpdateSurgeryDrawer({ onClose }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const { tier } = useCurrentUser()
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [selectedId, setSelectedId] = useState(null)
  const [error, setError] = useState(null)

  // Debounce the search query (mirrors the Surgery.jsx pattern).
  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), 300)
    return () => clearTimeout(t)
  }, [query])

  const { data: searchData, isFetching: searching } = useQuery({
    queryKey: ['surgery-update-search', debounced],
    queryFn: () => api.get('/surgery', {
      params: { search: debounced, per_page: 25 },
    }).then(r => r.data),
    enabled: debounced.length > 0 && !selectedId,
  })
  const results = searchData?.surgeries || []

  // Full record for the selected surgery (drives the prefill).
  const { data: detail, isLoading: loadingDetail } = useQuery({
    queryKey: ['surgery-update-detail', selectedId],
    queryFn: () => api.get(`/surgery/${selectedId}`).then(r => r.data),
    enabled: !!selectedId,
  })

  const initialValues = detail ? mapDetailToForm(detail) : null

  const update = useMutation({
    mutationFn: ({ fields }) =>
      api.patch(`/surgery/${selectedId}`, fields).then(r => r.data),
    onSuccess: async (data, { orderFile }) => {
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
      // Attach a freshly-uploaded order PDF (if one was used to re-extract).
      if (orderFile) {
        try {
          const fd = new FormData()
          fd.append('file', orderFile)
          await api.post(`/surgery/${selectedId}/files?kind=order`, fd, {
            headers: { 'Content-Type': 'multipart/form-data' },
          })
        } catch (e) {
          const d = e?.response?.data?.detail
          alert('Surgery updated, but attaching the order PDF failed: '
            + (typeof d === 'string' ? d : (e?.message || 'upload error')))
        }
      }
      onClose()
      navigate(`/surgery/${selectedId}`)
    },
    onError: (e) => {
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (e?.message || 'Update failed'))
    },
  })

  const remove = useMutation({
    mutationFn: () => api.post(`/surgery/${selectedId}/delete`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
      qc.invalidateQueries({ queryKey: ['surgery-block-days'] })
      onClose()
    },
    onError: (e) => {
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (e?.message || 'Delete failed'))
    },
  })

  const handleDelete = async () => {
    const ok = await confirm({
      title: 'Delete Patient',
      message: `Soft-delete this surgery for ${detail?.patient_name || 'this patient'}? `
        + 'It will be removed from the surgery system (recoverable by an admin).',
      confirmLabel: 'Delete patient',
      danger: true,
    })
    if (!ok) return
    setError(null)
    remove.mutate()
  }

  const canDelete = !!selectedId && !!detail && tier(MODULE.SURGERY, TIER.MANAGE)

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-2xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-6 py-4 flex items-center justify-between z-10">
          <h2 className="font-serif font-semibold text-ink text-[18px]">Update Surgery</h2>
          <div className="flex items-center gap-3">
            {canDelete && (
              <button
                type="button"
                onClick={handleDelete}
                disabled={remove.isPending}
                className="text-sm px-3 py-1.5 rounded text-white bg-red-700 hover:bg-red-800 disabled:opacity-50"
              >
                {remove.isPending ? 'Deleting…' : 'Delete patient'}
              </button>
            )}
            <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
          </div>
        </div>

        {/* Patient / surgery search */}
        <div className="px-6 pt-5 pb-3 border-b border-border-subtle space-y-2">
          <label className="text-[11px] uppercase text-gray-500 tracking-wide block">
            Find Surgery to Update
          </label>
          <div className="relative">
            <Search size={12} className="absolute left-2 top-2.5 text-muted" />
            <input
              className="input text-sm pl-7 w-full"
              placeholder="Patient name, chart #, or surgery #…"
              value={query}
              onChange={e => { setQuery(e.target.value); setSelectedId(null); setError(null) }}
            />
          </div>
          {!selectedId && debounced.length > 0 && (
            <div className="border border-border-subtle rounded-md divide-y divide-border-subtle max-h-60 overflow-y-auto">
              {searching && results.length === 0 && (
                <div className="px-3 py-2 text-xs text-muted">Searching…</div>
              )}
              {!searching && results.length === 0 && (
                <div className="px-3 py-2 text-xs text-muted">No matching surgeries.</div>
              )}
              {results.map(s => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => { setSelectedId(s.id); setError(null) }}
                  className="w-full text-left px-3 py-2 text-sm hover:bg-plum-50 flex items-baseline justify-between gap-2"
                >
                  <span className="font-medium text-ink">{s.patient_name}</span>
                  <span className="text-[11px] text-muted shrink-0">
                    {s.chart_number || '—'} · {s.dob ? fmt.date(s.dob) : '—'} · {s.status}
                  </span>
                </button>
              ))}
            </div>
          )}
          {selectedId && detail && (
            <div className="text-xs text-gray-600">
              Editing <strong>{detail.patient_name}</strong> (chart {detail.chart_number || '—'}).{' '}
              <button type="button" className="text-plum-700 hover:underline"
                      onClick={() => { setSelectedId(null); setError(null) }}>
                Choose a different surgery
              </button>
            </div>
          )}
        </div>

        {/* Form area */}
        {!selectedId && (
          <div className="p-10 text-center text-sm text-muted">
            Search and select a patient to update.
          </div>
        )}
        {selectedId && loadingDetail && (
          <div className="p-10 text-center text-sm text-muted">Loading surgery…</div>
        )}
        {selectedId && initialValues && (
          <SurgeryIntakeForm
            key={selectedId}
            mode="update"
            initialValues={initialValues}
            submitLabel="Save changes"
            submitting={update.isPending}
            error={error}
            onCancel={onClose}
            onSubmit={(payload) => { setError(null); update.mutate(payload) }}
          />
        )}
      </div>
    </div>
  )
}


// Dedicated "Delete Surgery" drawer: search a patient and soft-delete them
// from the surgery system. Peer of Add/Update in the "Add ▾" menu.
export function DeleteSurgeryDrawer({ onClose }) {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [error, setError] = useState(null)
  const [deletedIds, setDeletedIds] = useState([])
  const [deletingId, setDeletingId] = useState(null)

  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), 300)
    return () => clearTimeout(t)
  }, [query])

  const { data: searchData, isFetching: searching } = useQuery({
    queryKey: ['surgery-delete-search', debounced],
    queryFn: () => api.get('/surgery', {
      params: { search: debounced, per_page: 25 },
    }).then(r => r.data),
    enabled: debounced.length > 0,
  })
  const results = (searchData?.surgeries || []).filter(s => !deletedIds.includes(s.id))

  const remove = useMutation({
    mutationFn: (id) => api.post(`/surgery/${id}/delete`).then(r => r.data),
    onMutate: (id) => { setDeletingId(id); setError(null) },
    onSuccess: (_data, id) => {
      setDeletedIds(prev => [...prev, id])
      setDeletingId(null)
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
      qc.invalidateQueries({ queryKey: ['surgery-block-days'] })
    },
    onError: (e) => {
      setDeletingId(null)
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (e?.message || 'Delete failed'))
    },
  })

  const handleDelete = async (s) => {
    const ok = await confirm({
      title: 'Delete Patient',
      message: `Soft-delete this surgery for ${s.patient_name || 'this patient'}? `
        + 'It will be removed from the surgery system (recoverable by an admin).',
      confirmLabel: 'Delete patient',
      danger: true,
    })
    if (!ok) return
    remove.mutate(s.id)
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/30" />
      <div className="relative w-full max-w-2xl bg-white shadow-xl overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 bg-white border-b border-border-subtle px-6 py-4 flex items-center justify-between z-10">
          <h2 className="font-serif font-semibold text-ink text-[18px]">Delete Surgery</h2>
          <button onClick={onClose} className="text-muted hover:text-ink"><X size={18} /></button>
        </div>

        <div className="px-6 pt-5 pb-3 space-y-2">
          <label className="text-[11px] uppercase text-gray-500 tracking-wide block">
            Find Surgery to Delete
          </label>
          <div className="relative">
            <Search size={12} className="absolute left-2 top-2.5 text-muted" />
            <input
              className="input text-sm pl-7 w-full"
              placeholder="Patient name, chart #, or surgery #…"
              value={query}
              onChange={e => { setQuery(e.target.value); setError(null) }}
            />
          </div>
          <p className="text-[11px] text-muted">
            Soft-delete removes the patient from the surgery system. Recoverable by an admin.
          </p>
          {error && <p className="text-xs text-red-700">{error}</p>}

          {debounced.length > 0 && (
            <div className="border border-border-subtle rounded-md divide-y divide-border-subtle max-h-[28rem] overflow-y-auto">
              {searching && results.length === 0 && (
                <div className="px-3 py-2 text-xs text-muted">Searching…</div>
              )}
              {!searching && results.length === 0 && (
                <div className="px-3 py-2 text-xs text-muted">No matching surgeries.</div>
              )}
              {results.map(s => (
                <div key={s.id} className="px-3 py-2 text-sm flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-medium text-ink truncate">{s.patient_name}</div>
                    <div className="text-[11px] text-muted">
                      {s.chart_number || '—'} · {s.dob ? fmt.date(s.dob) : '—'} · {s.status}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleDelete(s)}
                    disabled={deletingId === s.id}
                    className="text-xs px-3 py-1.5 rounded text-white bg-red-700 hover:bg-red-800 disabled:opacity-50 shrink-0"
                  >
                    {deletingId === s.id ? 'Deleting…' : 'Delete'}
                  </button>
                </div>
              ))}
            </div>
          )}
          {deletedIds.length > 0 && (
            <p className="text-xs text-emerald-700">
              Deleted {deletedIds.length} surger{deletedIds.length === 1 ? 'y' : 'ies'}.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}


// Map a GET /surgery/{id} dict into the shared form's initialValues.
function mapDetailToForm(d) {
  const procedures = Array.isArray(d.procedures) && d.procedures.length
    ? d.procedures.map(p => ({ cpt: p.cpt || '', description: p.description || '' }))
    : [{ cpt: '', description: '' }]
  const diagnoses = Array.isArray(d.diagnoses) && d.diagnoses.length
    ? d.diagnoses.map(x => ({ icd: x.icd || '', description: x.description || '' }))
    : [{ icd: '', description: '' }]
  // No surgery_name column — derive from the first procedure description.
  const surgery_name = d.surgery_name || procedures[0]?.description || ''
  const clearance_types = Array.isArray(d.clearance_types) && d.clearance_types.length
    ? d.clearance_types : ['None']
  const device_types = Array.isArray(d.device_types) && d.device_types.length
    ? d.device_types : ['None']
  const eligible_facilities = Array.isArray(d.eligible_facilities) && d.eligible_facilities.length
    ? d.eligible_facilities : ['medstar']

  return {
    chart_number: d.chart_number || '',
    first_name: d.first_name || '',
    last_name: d.last_name || '',
    dob: d.dob || '',
    phone: d.phone || '',
    email: d.email || '',
    address_street: d.address_street || '',
    address_city: d.address_city || '',
    address_state: d.address_state || '',
    address_zip: d.address_zip || '',
    primary_insurance: d.primary_insurance || '',
    primary_member_id: d.primary_member_id || '',
    payer_id: d.primary_payer_id || '',
    secondary_insurance: d.secondary_insurance || '',
    secondary_member_id: d.secondary_member_id || '',
    surgeon_primary: d.surgeon_primary || 'Aryian Cooke, MD',
    assistant_surgeon_name: d.assistant_surgeon_name || 'None',
    clearance_types,
    device_types,
    surgery_name,
    procedures,
    diagnoses,
    eligible_facilities,
    estimated_minutes: d.estimated_minutes ?? 180,
    preop_date: d.preop_date || '',
    is_robotic: !!d.is_robotic,
    is_urgent: d.is_urgent ?? (d.urgency === 'urgent'),
    notes: d.notes || '',
    consent_template_ids: d.consent_template_ids || [],
    consent_overrides: d.consent_overrides || { added: [], removed: [] },
  }
}
