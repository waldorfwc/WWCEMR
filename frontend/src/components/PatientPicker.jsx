import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import api from '../utils/api'

/**
 * Autocomplete picker for Patients.
 * Props:
 * - value: patient id (uuid string) | null
 * - onChange: (newId: string | null) => void
 * - disabled?: boolean
 *
 * Shows the currently-selected patient's name + chart id; clicking opens
 * a small dropdown with a search input. Uses /api/patients?search=&per_page=10.
 */
export default function PatientPicker({ value, onChange, disabled }) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')

  const { data: current } = useQuery({
    queryKey: ['patient', value],
    queryFn: () => api.get(`/patients/${value}`).then(r => r.data),
    enabled: !!value,
  })

  const { data: results = [], isFetching } = useQuery({
    queryKey: ['patients-search', search],
    queryFn: () => api.get('/patients', { params: { search, per_page: 10 } })
      .then(r => r.data.patients || r.data),
    enabled: open && search.length >= 2,
    staleTime: 10_000,
  })

  function pick(p) {
    onChange(p.id)
    setOpen(false)
    setSearch('')
  }

  const label = current
    ? `${current.last_name || ''}, ${current.first_name || ''} (${current.patient_id || '—'})`
    : (value ? 'Loading…' : '— no patient —')

  return (
    <div className="relative">
      <button
        type="button"
        disabled={disabled}
        className="input w-full py-1 text-left text-[12px]"
        onClick={() => setOpen(v => !v)}
      >
        {label}
      </button>
      {open && (
        <div className="absolute z-10 mt-1 w-full card p-2 shadow-lg bg-white">
          <input
            autoFocus
            className="input w-full py-1 text-[12px]"
            placeholder="Search by name or chart #…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          {value && (
            <button
              type="button"
              className="mt-1 text-[11px] text-muted underline"
              onClick={() => { onChange(null); setOpen(false) }}
            >
              Clear selection
            </button>
          )}
          <div className="mt-2 max-h-48 overflow-y-auto">
            {isFetching && <div className="text-[11px] text-muted">Searching…</div>}
            {!isFetching && search.length < 2 && (
              <div className="text-[11px] text-muted">Type 2+ characters to search.</div>
            )}
            {!isFetching && search.length >= 2 && results.length === 0 && (
              <div className="text-[11px] text-muted">No matches.</div>
            )}
            {results.map(p => (
              <button
                key={p.id}
                type="button"
                className="block w-full text-left px-1 py-1 hover:bg-plum-50 text-[12px]"
                onClick={() => pick(p)}
              >
                <span className="font-mono text-muted">{p.patient_id || '—'}</span>{' · '}
                {p.last_name}, {p.first_name}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
