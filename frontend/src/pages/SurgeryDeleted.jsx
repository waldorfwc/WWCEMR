import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Search, RotateCcw, Trash2 } from 'lucide-react'
import api, { fmt } from '../utils/api'
import LoadingState from '../components/LoadingState'
import { useConfirm } from '../components/ui/ConfirmDialog'


// Restore view for soft-deleted surgeries. Reached from the Surgery
// "Add ▾ → Restore Deleted" menu item (MANAGE-gated route).
export default function SurgeryDeleted() {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [restoringId, setRestoringId] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), 300)
    return () => clearTimeout(t)
  }, [query])

  const { data, isLoading } = useQuery({
    queryKey: ['surgery-deleted', debounced],
    queryFn: () => api.get('/surgery/deleted', {
      params: debounced ? { search: debounced } : {},
    }).then(r => r.data),
  })
  const rows = data?.surgeries || []

  const restore = useMutation({
    mutationFn: (id) => api.post(`/surgery/${id}/restore`).then(r => r.data),
    onMutate: (id) => { setRestoringId(id); setError(null) },
    onSuccess: () => {
      setRestoringId(null)
      qc.invalidateQueries({ queryKey: ['surgery-deleted'] })
      qc.invalidateQueries({ queryKey: ['surgery-list'] })
      qc.invalidateQueries({ queryKey: ['surgery-dashboard'] })
    },
    onError: (e) => {
      setRestoringId(null)
      const d = e?.response?.data?.detail
      setError(typeof d === 'string' ? d : (e?.message || 'Restore failed'))
    },
  })

  const handleRestore = async (s) => {
    const ok = await confirm({
      title: 'Restore Surgery',
      message: `Restore ${s.patient_name || 'this patient'} to the surgery system?`,
      confirmLabel: 'Restore',
    })
    if (!ok) return
    restore.mutate(s.id)
  }

  return (
    <div>
      <div className="mb-4">
        <h1 className="font-serif font-semibold text-ink text-[22px] m-0 flex items-center gap-2">
          <Trash2 size={20} className="text-plum-700" /> Deleted Surgeries
        </h1>
        <p className="text-muted text-[12px] mt-0.5">
          Soft-deleted surgeries are hidden from the system but recoverable. Restore one to return it to the active list.
        </p>
      </div>

      <div className="relative max-w-md mb-3">
        <Search size={12} className="absolute left-2 top-2.5 text-muted" />
        <input
          className="input text-sm pl-7 w-full"
          placeholder="Patient name, chart #, or surgery #…"
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
      </div>
      {error && <p className="text-xs text-red-700 mb-2">{error}</p>}

      {isLoading ? (
        <LoadingState />
      ) : rows.length === 0 ? (
        <div className="text-sm text-muted py-10 text-center">
          No deleted surgeries{debounced ? ' match your search' : ''}.
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[11px] uppercase text-gray-500 border-b border-border-subtle">
              <th className="py-2 pr-3">Patient</th>
              <th className="py-2 pr-3">Chart #</th>
              <th className="py-2 pr-3">DOB</th>
              <th className="py-2 pr-3">Status</th>
              <th className="py-2 pr-3">Deleted</th>
              <th className="py-2 pr-3">By</th>
              <th className="py-2"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-subtle">
            {rows.map(s => (
              <tr key={s.id}>
                <td className="py-2 pr-3 font-medium text-ink">{s.patient_name}</td>
                <td className="py-2 pr-3">{s.chart_number || '—'}</td>
                <td className="py-2 pr-3">{s.dob ? fmt.date(s.dob) : '—'}</td>
                <td className="py-2 pr-3">{s.status}</td>
                <td className="py-2 pr-3">{s.deleted_at ? fmt.date(s.deleted_at) : '—'}</td>
                <td className="py-2 pr-3 text-muted text-[12px]">{s.deleted_by || '—'}</td>
                <td className="py-2 text-right">
                  <button
                    type="button"
                    onClick={() => handleRestore(s)}
                    disabled={restoringId === s.id}
                    className="text-xs px-3 py-1.5 rounded border border-plum-300 text-plum-700 hover:bg-plum-50 disabled:opacity-50 inline-flex items-center gap-1"
                  >
                    <RotateCcw size={12} />
                    {restoringId === s.id ? 'Restoring…' : 'Restore'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
