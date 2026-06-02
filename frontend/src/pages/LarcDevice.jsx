import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Package, User, FileText, Printer, Trash2 } from 'lucide-react'
import api, { fmt } from '../utils/api'
import { useCurrentUser } from '../hooks/useCurrentUser'


export default function LarcDevice() {
  const { id } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { has } = useCurrentUser()
  const canManage = has?.('larc:manage')
  const { data: d, isLoading, error } = useQuery({
    queryKey: ['larc-device', id],
    queryFn: () => api.get(`/larc/devices/${id}`).then(r => r.data),
  })
  const { data: audit } = useQuery({
    queryKey: ['larc-audit-for-device', id],
    queryFn: () => api.get('/larc/audit', { params: { device_id: id, per_page: 100 } }).then(r => r.data),
  })

  const del = useMutation({
    mutationFn: () => api.delete(`/larc/devices/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['larc-devices'] })
      navigate('/larc/devices')
    },
    onError: (e) => alert(e?.response?.data?.detail || 'Delete failed'),
  })

  if (isLoading) return <div className="p-6 text-gray-400">Loading…</div>
  if (error) return <div className="p-6 text-red-600">{error?.response?.data?.detail || error.message}</div>
  if (!d) return null

  return (
    <div>
      <Link to="/larc/devices" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-2">
        <ArrowLeft size={12} /> All devices
      </Link>

      {/* Header */}
      <div className="card mb-4">
        <div className="flex items-baseline justify-between gap-3 mb-2">
          <div>
            <h1 className="text-xl font-bold text-gray-900 font-mono">{d.our_id}</h1>
            <div className="text-xs text-gray-500 mt-0.5">
              {d.device_type_name}
              {d.manufacturer_lot && <> · Lot <span className="font-mono">{d.manufacturer_lot}</span></>}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <a href={`/api/larc/devices/${d.id}/label.pdf`}
               target="_blank" rel="noopener noreferrer"
               className="btn-secondary text-xs flex items-center gap-1"
               title={`Print 2.25" x 1.25" cabinet label with QR`}>
              <Printer size={12} /> Label
            </a>
            {canManage && (d.assignments || []).length === 0 && (
              <button
                type="button"
                onClick={() => {
                  if (confirm(`Delete device #${d.our_id}? This is for pre-go-live inventory cleanup only — once a device has an assignment it can't be deleted.`)) {
                    del.mutate()
                  }
                }}
                disabled={del.isPending}
                className="text-xs px-2 py-1 rounded border border-red-300 bg-white text-red-700 hover:bg-red-50 flex items-center gap-1"
                title="Delete this device (no assignment history)">
                <Trash2 size={12} /> {del.isPending ? 'Deleting…' : 'Delete'}
              </button>
            )}
            <span className="text-[10px] uppercase tracking-wide bg-plum-100 text-plum-700 px-2 py-1 rounded">
              {d.status.replace(/_/g, ' ')}
            </span>
          </div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs mt-3">
          <Field label="Location">{d.location_label}</Field>
          <Field label="Expiration">
            {d.expiration_date ? fmt.date(d.expiration_date) : '—'}
          </Field>
          <Field label="Purchase price">
            {d.purchase_price ? <span className="font-mono">${d.purchase_price}</span> : '—'}
          </Field>
          <Field label="Purchase date">
            {d.purchase_date ? fmt.date(d.purchase_date) : '—'}
          </Field>
          {d.manufacturer_serial && (
            <Field label="Serial #">
              <span className="font-mono">{d.manufacturer_serial}</span>
            </Field>
          )}
          {d.replacement_device_id && (
            <Field label="Replaced by">
              <Link to={`/larc/devices/${d.replacement_device_id}`} className="text-plum-700 hover:underline">
                another device
              </Link>
            </Field>
          )}
          {d.replaces_device_id && (
            <Field label="Replaces">
              <Link to={`/larc/devices/${d.replaces_device_id}`} className="text-plum-700 hover:underline">
                prior device
              </Link>
            </Field>
          )}
        </div>
        {d.notes && (
          <div className="mt-3 text-xs text-gray-700 italic border-l-2 border-gray-200 pl-2">
            {d.notes}
          </div>
        )}
      </div>

      {/* Assignment history */}
      <div className="card mb-4">
        <div className="flex items-center gap-1.5 mb-2">
          <User size={14} className="text-plum-700" />
          <h2 className="text-sm font-semibold text-gray-800">Assignment history</h2>
          <span className="text-[11px] text-muted">({(d.assignments || []).length})</span>
        </div>
        {(d.assignments || []).length === 0 ? (
          <div className="text-xs text-gray-400 italic">No assignments yet — this device is in stock.</div>
        ) : (
          <ul className="space-y-1">
            {d.assignments.map(a => (
              <li key={a.id}
                  className="flex items-baseline justify-between gap-2 cursor-pointer hover:bg-plum-50 px-2 py-1 rounded"
                  onClick={() => navigate(`/larc/assignments/${a.id}`)}>
                <span>
                  <strong>{a.patient_name}</strong>
                  <span className="text-gray-500 text-[11px] ml-1">chart {a.chart_number}</span>
                </span>
                <span className="text-[10px] uppercase text-gray-600">
                  {a.status.replace(/_/g, ' ')}
                  {!a.is_active && ' · inactive'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Per-device audit trail */}
      <div className="card">
        <div className="flex items-center gap-1.5 mb-2">
          <FileText size={14} className="text-plum-700" />
          <h2 className="text-sm font-semibold text-gray-800">Audit trail</h2>
          <span className="text-[11px] text-muted">({audit?.total || 0})</span>
        </div>
        {(audit?.events || []).length === 0 ? (
          <div className="text-xs text-gray-400 italic">No audit events yet.</div>
        ) : (
          <ul className="text-xs space-y-1 max-h-96 overflow-y-auto">
            {audit.events.map(e => (
              <li key={e.id} className="flex items-baseline gap-2 px-2 py-1 hover:bg-gray-50 rounded">
                <span className="text-[10px] text-gray-500 shrink-0 w-32">
                  {new Date(e.occurred_at).toLocaleString('en-US', {
                    month: 'short', day: 'numeric', year: '2-digit',
                    hour: 'numeric', minute: '2-digit',
                  })}
                </span>
                <code className="text-[10px] text-plum-700 shrink-0 w-44 truncate">{e.action}</code>
                <span className="text-[11px] flex-1">{e.summary}</span>
                <span className="text-[10px] text-gray-500 shrink-0 font-mono">
                  {e.actor?.split('@')[0] || ''}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}


function Field({ label, children }) {
  return (
    <div>
      <div className="text-[9px] uppercase tracking-wide text-gray-400 mb-0.5">{label}</div>
      <div className="text-gray-800">{children}</div>
    </div>
  )
}
