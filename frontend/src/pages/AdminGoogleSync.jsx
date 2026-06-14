import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  ArrowLeft, RefreshCw, Plus, Trash2, AlertCircle, CheckCircle2,
  EyeOff, Clock,
} from 'lucide-react'
import api, { fmt } from '../utils/api'


export default function AdminGoogleSync({ embedded = false }) {
  const qc = useQueryClient()

  const { data: status } = useQuery({
    queryKey: ['gsync-status'],
    queryFn: () => api.get('/admin/google-sync/status').then(r => r.data),
  })
  const { data: exclusionsData } = useQuery({
    queryKey: ['gsync-exclusions'],
    queryFn: () => api.get('/admin/google-sync/exclusions').then(r => r.data),
  })
  const { data: preview, refetch: refetchPreview, isFetching: previewLoading } = useQuery({
    queryKey: ['gsync-preview'],
    queryFn: () => api.get('/admin/google-sync/preview').then(r => r.data),
    enabled: !!status?.configured,
  })

  const runNow = useMutation({
    mutationFn: () => api.post('/admin/google-sync/run').then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['gsync-status'] })
      qc.invalidateQueries({ queryKey: ['gsync-preview'] })
      qc.invalidateQueries({ queryKey: ['admin-users'] })
    },
  })

  const last = status?.last_run

  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        {!embedded && (
          <div>
            <Link to="/admin" className="text-[12px] text-muted hover:underline flex items-center gap-1 mb-1">
              <ArrowLeft size={12} /> Back to Admin
            </Link>
            <h1 className="font-serif font-semibold text-ink text-[22px] m-0">Google Workspace sync</h1>
            <p className="text-muted text-[12px] mt-0.5">
              Auto-creates a user when a Google Workspace account appears, and suspends a
              user when their Google account is suspended or deleted.
              Excluded emails are skipped both ways.
            </p>
          </div>
        )}
        <button className="btn-primary text-sm flex items-center gap-1 ml-auto"
                disabled={runNow.isPending || !status?.configured}
                onClick={() => runNow.mutate()}>
          <RefreshCw size={13} className={runNow.isPending ? 'animate-spin' : ''} />
          {runNow.isPending ? 'Syncing…' : 'Run sync now'}
        </button>
      </div>

      {!status?.configured && (
        <div className="card bg-amber-50 border-amber-200 text-sm text-amber-900 mb-4">
          <div className="font-semibold flex items-center gap-1.5 mb-1">
            <AlertCircle size={14} /> Sync is not configured
          </div>
          <p className="text-xs mb-2">
            To enable, set the following environment variables on the backend, then restart:
          </p>
          <ul className="text-xs font-mono space-y-1 pl-4">
            <li><strong>GOOGLE_WORKSPACE_SA_JSON</strong> — full service-account JSON (one line)</li>
            <li><strong>GOOGLE_WORKSPACE_DELEGATED_ADMIN</strong> — super-admin email the service account impersonates</li>
            <li><strong>GOOGLE_WORKSPACE_CUSTOMER_ID</strong> — defaults to <code>my_customer</code></li>
          </ul>
          <p className="text-xs mt-2">
            In Google Workspace Admin Console, grant the service account domain-wide
            delegation with scope <code>admin.directory.user.readonly</code>.
          </p>
        </div>
      )}

      {/* Last run summary */}
      {status?.configured && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4">
          <Tile
            label="Last run"
            value={last ? fmt.dateTime(last.started_at) : '— never —'}
            sub={last?.status}
            icon={<Clock size={16} />}
          />
          <Tile
            label="Created"
            value={last?.created ?? '—'}
            tone="green"
            icon={<Plus size={16} />}
          />
          <Tile
            label="Suspended"
            value={last?.suspended ?? '—'}
            tone="red"
            icon={<EyeOff size={16} />}
          />
          <Tile
            label="Re-activated"
            value={last?.activated ?? '—'}
            tone="blue"
            icon={<CheckCircle2 size={16} />}
          />
        </div>
      )}

      {last?.error_message && (
        <div className="card bg-red-50 border-red-200 text-xs text-red-800 mb-4">
          <div className="font-semibold mb-0.5">Last sync failed</div>
          <div className="font-mono">{last.error_message}</div>
        </div>
      )}

      {runNow.data && (
        <div className="card bg-blue-50 border-blue-200 text-xs text-blue-900 mb-4">
          <div className="font-semibold">
            ✓ Sync complete — {runNow.data.created} created, {runNow.data.activated} re-activated, {runNow.data.suspended} suspended, {runNow.data.excluded} excluded
          </div>
          {runNow.data.detail?.created?.length > 0 && (
            <div className="font-mono text-[10px] mt-1">Created: {runNow.data.detail.created.join(', ')}</div>
          )}
          {runNow.data.detail?.suspended?.length > 0 && (
            <div className="font-mono text-[10px] mt-1">Suspended: {runNow.data.detail.suspended.join(', ')}</div>
          )}
        </div>
      )}

      {/* Preview */}
      {status?.configured && (
        <div className="card mb-4">
          <div className="flex items-baseline justify-between mb-2">
            <div>
              <h2 className="text-sm font-semibold text-gray-800">Pending Creation</h2>
              <p className="text-[11px] text-muted">
                Google emails not yet in this system and not on the exclusion list.
                Add to exclusions to prevent the next sync from creating them.
              </p>
            </div>
            <button className="btn-secondary text-xs flex items-center gap-1"
                    onClick={() => refetchPreview()} disabled={previewLoading}>
              <RefreshCw size={11} className={previewLoading ? 'animate-spin' : ''} /> Refresh
            </button>
          </div>
          {(preview?.would_create || []).length === 0 ? (
            <div className="text-xs text-muted italic">Nothing pending — every Google user is already in this system or excluded.</div>
          ) : (
            <ul className="divide-y divide-gray-100">
              {preview.would_create.map(em => <PreviewRow key={em} email={em} qc={qc} />)}
            </ul>
          )}
        </div>
      )}

      {/* Exclusions */}
      <div className="card">
        <h2 className="text-sm font-semibold text-gray-800 mb-2">Exclusions</h2>
        <p className="text-[11px] text-muted mb-3">
          Emails on this list are <strong>never</strong> auto-provisioned and never
          auto-suspended. Use for service accounts, shared mailboxes, or any Google
          account that shouldn't have a system user.
        </p>
        <AddExclusionForm qc={qc} />
        <ul className="mt-3 divide-y divide-gray-100">
          {(exclusionsData?.exclusions || []).map(e => (
            <li key={e.email} className="py-2 flex items-baseline justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm font-mono">{e.email}</div>
                {e.reason && <div className="text-[11px] text-muted">{e.reason}</div>}
                <div className="text-[10px] text-muted">added {fmt.dateTime(e.added_at)} by {e.added_by || '—'}</div>
              </div>
              <RemoveExclusionButton email={e.email} qc={qc} />
            </li>
          ))}
          {(exclusionsData?.exclusions || []).length === 0 && (
            <li className="text-xs text-muted italic py-2">No exclusions yet.</li>
          )}
        </ul>
      </div>
    </div>
  )
}


function Tile({ label, value, sub, tone, icon }) {
  const tones = {
    green: 'bg-green-50 border-green-200 text-green-800',
    red:   'bg-red-50 border-red-200 text-red-800',
    blue:  'bg-blue-50 border-blue-200 text-blue-800',
  }
  return (
    <div className={`card border ${tones[tone] || 'bg-gray-50 border-gray-200 text-gray-800'} flex items-center justify-between`}>
      <div>
        <div className="text-[11px] uppercase tracking-wide opacity-80">{label}</div>
        <div className="text-2xl display-number mt-1">{value}</div>
        {sub && <div className="text-[10px] mt-0.5 capitalize opacity-70">{sub}</div>}
      </div>
      <div className="opacity-60">{icon}</div>
    </div>
  )
}


function PreviewRow({ email, qc }) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)

  async function exclude() {
    setBusy(true)
    try {
      await api.post('/admin/google-sync/exclusions', { email, reason: reason || null })
      qc.invalidateQueries({ queryKey: ['gsync-exclusions'] })
      qc.invalidateQueries({ queryKey: ['gsync-preview'] })
    } finally { setBusy(false) }
  }

  return (
    <li className="py-2 flex items-baseline justify-between gap-3">
      <span className="text-sm font-mono flex-1 min-w-0">{email}</span>
      <input className="input text-xs w-56" placeholder="Reason (optional)"
             value={reason} onChange={e => setReason(e.target.value)} />
      <button className="text-xs text-red-700 hover:underline flex items-center gap-1"
              onClick={exclude} disabled={busy}>
        <EyeOff size={11} /> Exclude
      </button>
    </li>
  )
}


function AddExclusionForm({ qc }) {
  const [email, setEmail] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function add() {
    if (!email.trim()) return
    setBusy(true); setError(null)
    try {
      await api.post('/admin/google-sync/exclusions', { email: email.trim(), reason: reason || null })
      setEmail(''); setReason('')
      qc.invalidateQueries({ queryKey: ['gsync-exclusions'] })
      qc.invalidateQueries({ queryKey: ['gsync-preview'] })
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally { setBusy(false) }
  }

  return (
    <div>
      <div className="flex gap-2">
        <input className="input text-sm font-mono flex-1" placeholder="service-account@waldorfwomenscare.com"
               value={email} onChange={e => setEmail(e.target.value)} />
        <input className="input text-sm flex-1" placeholder="Why? (e.g. shared mailbox, reception bot)"
               value={reason} onChange={e => setReason(e.target.value)} />
        <button className="btn-primary text-sm flex items-center gap-1"
                onClick={add} disabled={busy || !email.trim()}>
          <Plus size={12} /> Add
        </button>
      </div>
      {error && <div className="text-xs text-red-600 mt-1">{error}</div>}
    </div>
  )
}


function RemoveExclusionButton({ email, qc }) {
  const [busy, setBusy] = useState(false)
  async function remove() {
    if (!confirm(`Remove ${email} from the exclusion list? They'll be eligible for auto-provisioning on the next sync.`)) return
    setBusy(true)
    try {
      await api.delete(`/admin/google-sync/exclusions/${encodeURIComponent(email)}`)
      qc.invalidateQueries({ queryKey: ['gsync-exclusions'] })
      qc.invalidateQueries({ queryKey: ['gsync-preview'] })
    } finally { setBusy(false) }
  }
  return (
    <button className="text-xs text-gray-400 hover:text-red-700 flex items-center gap-1 shrink-0"
            onClick={remove} disabled={busy}>
      <Trash2 size={11} /> Remove
    </button>
  )
}
