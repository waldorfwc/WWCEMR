import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api from '../../utils/api'


// Reasons by device category — match the consolidated /outcome handlers.
const REASONS = {
  larc: [
    { v: 'appointment_canceled', l: 'Appointment canceled — keep patient' },
    { v: 'returned_mistake',     l: 'Wrong device — keep patient' },
    { v: 'failed_used',          l: 'Failed insertion — flag for replacement' },
  ],
  office_procedure: [
    { v: 'failed_unused',      l: 'Returned unused — back to stock' },
    { v: 'returned_defective', l: 'Returned defective — manufacturer return' },
  ],
}


export default function ReturnDeviceList() {
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery({
    queryKey: ['larc-returnable'],
    queryFn: () => api.get('/larc/checkouts/returnable').then(r => r.data),
  })
  if (isLoading) return <div className="text-xs text-gray-400">Loading…</div>
  if (error) return <div className="text-xs text-red-600">{error?.response?.data?.detail || error.message}</div>
  const rows = data || []
  if (rows.length === 0) {
    return <div className="text-xs text-gray-500">No devices are currently checked out.</div>
  }
  return (
    <div className="divide-y divide-gray-100">
      {rows.map(r => <ReturnRow key={r.assignment_id} row={r} qc={qc} />)}
    </div>
  )
}


function ReturnRow({ row, qc }) {
  const reasons = REASONS[row.category] || REASONS.larc
  const [reason, setReason] = useState(reasons[0].v)
  const [notes, setNotes] = useState('')
  const save = useMutation({
    mutationFn: () => api.post(`/larc/assignments/${row.assignment_id}/outcome`,
      { outcome: reason, notes: notes || null }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['larc-returnable'] })
      qc.invalidateQueries({ queryKey: ['larc-checkouts-ready'] })
      qc.invalidateQueries({ queryKey: ['larc-dashboard'] })
      qc.invalidateQueries({ queryKey: ['larc-devices'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Return failed'),
  })
  return (
    <div className="py-2.5 space-y-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-[13px] font-medium text-gray-900">{row.patient_name}</div>
        <div className="text-[11px] text-gray-500">
          <span className="font-mono">#{row.device_our_id}</span> · {row.device_type_name}
          {' · '}{(row.device_status || '').replace(/_/g, ' ')}
        </div>
      </div>
      <select className="input text-[12px] w-full" value={reason}
              onChange={e => setReason(e.target.value)}>
        {reasons.map(r => <option key={r.v} value={r.v}>{r.l}</option>)}
      </select>
      <input className="input text-[11px] w-full" placeholder="Notes (optional)"
             value={notes} onChange={e => setNotes(e.target.value)} />
      <div className="flex justify-end">
        <button className="btn-primary text-[11px]"
                onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? 'Updating…' : 'Update status'}
        </button>
      </div>
    </div>
  )
}
