import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import api, { fmt } from '../../utils/api'


// Shared "devices ready to check out" list + per-row direct-checkout form.
// The MA pulls the device from the cabinet and types its label ID back in
// (the /checkouts/ready payload omits our_id on purpose, so staff must read
// the physical label). Used by the LARC nav "Check Out a Device" drawer, the
// Overview "Devices Ready to Check Out" card, and the My Checklist card.
export default function CheckoutReadyList() {
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery({
    queryKey: ['larc-checkouts-ready'],
    queryFn: () => api.get('/larc/checkouts/ready').then(r => r.data),
  })
  const rows = data || []

  if (isLoading) return <div className="text-xs text-gray-400">Loading…</div>
  if (error) return (
    <div className="text-xs text-red-600">
      Couldn't load — {error?.response?.data?.detail || error.message}
    </div>
  )
  if (rows.length === 0) return (
    <div className="text-xs text-gray-400 italic">No devices waiting to be checked out.</div>
  )
  return (
    <div className="space-y-2">
      {rows.map(r => <CheckoutReadyRow key={r.assignment_id} row={r} qc={qc} />)}
    </div>
  )
}


function CheckoutReadyRow({ row, qc }) {
  const [deviceId, setDeviceId] = useState('')
  const [givenTo, setGivenTo] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [done, setDone] = useState(false)

  async function submit() {
    if (!deviceId.trim()) { setErr('Enter the device ID from the label'); return }
    setBusy(true); setErr(null)
    try {
      await api.post(`/larc/assignments/${row.assignment_id}/checkout-direct`, {
        device_our_id: deviceId.trim(),
        given_to: givenTo.trim() || null,
      })
      setDone(true)
      qc.invalidateQueries({ queryKey: ['larc-checkouts-ready'] })
      qc.invalidateQueries({ queryKey: ['larc-dashboard'] })
    } catch (e) {
      setErr(e?.response?.data?.detail || e.message)
    } finally { setBusy(false) }
  }

  if (done) return (
    <div className="bg-green-50 border border-green-200 rounded p-2 text-xs text-green-800">
      ✓ Checked out {row.device_type_name} for {row.patient_name}.
    </div>
  )

  return (
    <div className="bg-white border border-border-subtle rounded p-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-gray-900 truncate">{row.patient_name}</div>
          <div className="text-xs text-gray-600">
            {row.device_type_name || 'Device'}
            {row.appt_date && <> · appt {fmt.date(row.appt_date)}</>}
            {row.chart_number && <> · chart {row.chart_number}</>}
          </div>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input
          className="input text-xs font-mono w-40"
          placeholder="Device ID from label"
          value={deviceId}
          onChange={e => setDeviceId(e.target.value)}
          autoComplete="off"
        />
        <input
          className="input text-xs w-48"
          placeholder="Given to (optional)"
          value={givenTo}
          onChange={e => setGivenTo(e.target.value)}
        />
        <button
          className="btn-primary text-xs"
          onClick={submit}
          disabled={busy || !deviceId.trim()}
        >
          {busy ? 'Checking out…' : 'Check out'}
        </button>
      </div>
      {err && <div className="text-xs text-red-600 mt-1">{err}</div>}
    </div>
  )
}
