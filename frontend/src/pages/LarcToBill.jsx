import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Receipt } from 'lucide-react'
import api, { fmt } from '../utils/api'
import EmptyState from '../components/EmptyState'

export default function LarcToBill() {
  const { data } = useQuery({
    queryKey: ['larc-to-bill'],
    queryFn: () => api.get('/larc/to-bill').then(r => r.data),
  })
  const items = data?.items || []

  return (
    <div>
      <h1 className="page-title flex items-center gap-2">
        <Receipt size={22} className="text-plum-700" /> To Bill
      </h1>
      <p className="text-sm text-gray-500 mt-0.5 mb-4">
        Practice-owned devices checked out and awaiting a ModMed claim number.
      </p>
      {items.length === 0 ? (
        <EmptyState icon={Receipt} title="Nothing To Bill"
          body="No practice-owned devices are waiting to be billed." />
      ) : (
        <table className="table w-full text-sm">
          <thead>
            <tr>
              <th className="table-th text-left">Patient</th>
              <th className="table-th text-left">Device</th>
              <th className="table-th text-left">Checked Out</th>
              <th className="table-th text-left">Status</th>
              <th className="table-th text-left">Claim #</th>
            </tr>
          </thead>
          <tbody>
            {items.map(it => <ToBillRow key={it.assignment_id} it={it} />)}
          </tbody>
        </table>
      )}
    </div>
  )
}

function ToBillRow({ it }) {
  const qc = useQueryClient()
  const [claim, setClaim] = useState('')
  const bill = useMutation({
    mutationFn: () => api.post(`/larc/assignments/${it.assignment_id}/bill`,
      { claim_number: claim.trim() }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['larc-to-bill'] })
      qc.invalidateQueries({ queryKey: ['larc-dashboard'] })
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Billing failed'),
  })
  return (
    <tr>
      <td className="table-td">{it.patient_name}<br />
        <span className="text-[11px] text-muted">Chart {it.chart_number}</span></td>
      <td className="table-td">{it.device_our_id}<br />
        <span className="text-[11px] text-muted">{it.device_type_name}</span></td>
      <td className="table-td">{it.checked_out_at ? fmt.date(it.checked_out_at) : '—'}</td>
      <td className="table-td">
        {it.inserted
          ? <span className="text-green-700">Inserted</span>
          : <span className="inline-block rounded bg-amber-100 text-amber-800 px-2 py-0.5 text-[11px]">
              Awaiting insertion</span>}
      </td>
      <td className="table-td">
        {it.inserted ? (
          <div className="flex gap-2 items-center">
            <input className="input w-32" placeholder="Claim #" value={claim}
                   onChange={e => setClaim(e.target.value)} />
            <button className="btn-primary text-xs" disabled={!claim.trim() || bill.isPending}
                    onClick={() => bill.mutate()}>Save</button>
          </div>
        ) : <span className="text-muted text-[11px]">—</span>}
      </td>
    </tr>
  )
}
